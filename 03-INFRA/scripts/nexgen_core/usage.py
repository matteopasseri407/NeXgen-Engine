"""What this machine spent, and what is left of each plan, one provider at a time.

One job: answer the question about money with the numbers the engine can actually
read -- OpenCode Go and Zen from the harness archive, Claude Code from its own
transcripts, Codex from the rate limits it writes, Antigravity from its quota log --
grouped by the provider that was paid rather than by window.

OpenClaw's own `/usage cost` sums its session logs, so a subscription agent reports
$0 and a flat lane the person really paid for is invisible. This is the engine's
answer to that, and it is the text `/spend` inside OpenClaw forwards.

No number is computed here: each lane's reader owns its own arithmetic, and this
module decides only what a person reads. A lane that cannot be read says so, and a
missing figure is never written as a zero. A percentage carries the age of the
reading behind it, and one that `quota` has already declared too old to describe
today keeps its age and loses the number, so the two commands cannot start
disagreeing about the same reading.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from nexgen_core import quota, quota_antigravity, quota_claude, quota_codex
from nexgen_core.i18n import t
from nexgen_core.paths import codex_home, resolve_home

#: The two paid lanes the harness archive splits, by the provider id it stores, and
#: the name a person reads. `opencode-go` is the flat subscription, `opencode` is Zen:
#: two invoices, so a total over both would answer the wrong question.
HARNESS_PROVIDERS: dict[str, str] = {"opencode-go": "OpenCode Go", "opencode": "OpenCode Zen"}

#: The one window Antigravity reports, its monthly prompt credits.
ANTIGRAVITY_WINDOW = "30d"


@dataclass(frozen=True)
class Window:
    """One window of one provider: what was used, in what unit, and how old the reading is.

    `state` speaks the same language as `quota.QuotaRow.state`: `ok` while the
    reading still describes the window it was written for, `stale` when `quota`'s
    own rule for that provider has already declared it too old. A stale reading
    keeps the provider's number and loses the claim to be current.
    """

    label: str
    used: float | None = None
    unit: str = ""
    note: str = ""
    read_at: float | None = None
    state: str = "ok"

    def to_dict(self) -> dict[str, Any]:
        """The window as JSON, with the moment of its reading and whether it still describes today.

        The human line already says how old a percentage is and drops one declared
        too old; a consumer of `--json` has to be able to make the same distinction,
        and `quota._iso` is the report's one way of writing a moment.
        """
        return {
            "window": self.label, "used": self.used, "unit": self.unit, "note": self.note,
            "read_at": quota._iso(self.read_at), "state": self.state,
        }


@dataclass(frozen=True)
class Provider:
    """One provider: its windows, and what is worth knowing about them."""

    name: str
    provider_id: str = ""
    windows: tuple[Window, ...] = ()
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "provider_id": self.provider_id,
            "windows": [window.to_dict() for window in self.windows],
            "note": self.note,
        }


@dataclass(frozen=True)
class Usage:
    """Every provider this machine can answer for, at the moment it was read."""

    read_at: float
    providers: tuple[Provider, ...] = ()

    @property
    def lines(self) -> list[str]:
        """One line per provider: what a person reads, and what a chat forwards."""
        return [render_provider(provider, self.read_at) for provider in self.providers]

    def to_dict(self) -> dict[str, Any]:
        return {
            "read_at": datetime.fromtimestamp(self.read_at).isoformat(timespec="seconds"),
            "providers": [provider.to_dict() for provider in self.providers],
            "lines": self.lines,
        }


def build(home: Path | None = None, now: float | None = None) -> Usage:
    """What every lane on this machine answers, at one moment."""
    moment = time.time() if now is None else float(now)
    home_dir = resolve_home(home)
    return Usage(read_at=moment, providers=(
        *_harness(home_dir, moment),
        _claude(home_dir, moment),
        _codex(home_dir, moment),
        _antigravity(home_dir, moment),
    ))


def render_provider(provider: Provider, now: float | None = None) -> str:
    """One provider, on one line, in the terms a person reads."""
    if not provider.windows:
        return f"{provider.name}: {provider.note or t('nothing is readable on this machine')}"
    line = f"{provider.name}: " + ", ".join(render_window(window, now) for window in provider.windows)
    return f"{line} ({provider.note})" if provider.note else line


def render_window(window: Window, now: float | None = None) -> str:
    """One window as the shortest sentence that carries the number, its unit and its age.

    `now` is the moment a person reads: without it a reading has no age, and the
    sentence says what was used and nothing more.
    """
    if window.used is None or not window.unit:
        return window.note or t("not measured")
    if window.unit == "usd":
        return t("{amount} in the last {window}", amount=f"{window.used:.2f} $", window=window.label)
    if window.unit == "tokens":
        return t("{tokens} tokens in the last {window}", tokens=f"{int(window.used):,}", window=window.label)
    return _percent_line(window, now)


def _percent_line(window: Window, now: float | None) -> str:
    """A percentage with the age of its reading; a stale one keeps the age and loses the number.

    The rule that decides staleness is `quota`'s, the command a handover acts on,
    so this line repeats that verdict instead of holding a second opinion.
    """
    age = _read_age(window, now)
    if window.state != "stale":
        line = t("{percent} of the {window} window used", percent=quota.percent_text(window.used),
                 window=window.label)
        return f"{line}, " + t("read {age} ago", age=age) if age else line
    if age is None:  # a stale reading with no moment cannot even be dated
        return t("not measured")
    return t("the {window} reading is {age} old, too old to say what is left", window=window.label, age=age)


def _read_age(window: Window, now: float | None) -> str | None:
    """How old a window's reading is, or None when it carries no moment or nobody asked."""
    if window.read_at is None or now is None:
        return None
    return quota.age_text(now - window.read_at)


def main(as_json: bool = False) -> int:
    """Prints the reading. Always exit 0: a source that cannot be read is an answer too."""
    usage = build()
    if as_json:
        print(json.dumps(usage.to_dict(), indent=2, ensure_ascii=False))
    else:
        for line in usage.lines:
            print(line)
    return 0


# ------------------------------------------------------------------ the four lanes


def _harness(home: Path, now: float) -> list[Provider]:
    """OpenCode Go and Zen: what the harness archive itself recorded, per window.

    The arithmetic is the harness lane's own (`supervision_spend`), and a checkout
    without that lane is a real state: it costs this source, never the command.
    """
    try:
        from nexgen_local import supervision_spend
    except ImportError:
        return [Provider(name=name, provider_id=pid, note=t("the harness lane is not installed in this engine"))
                for pid, name in HARNESS_PROVIDERS.items()]
    spend = supervision_spend.spend_by_provider(home=home, now=now)
    labels = tuple(key for key, _ in supervision_spend.SPEND_WINDOWS)
    if spend["note"]:
        return [Provider(name=name, provider_id=pid, note=t("the harness archive could not be read"))
                for pid, name in HARNESS_PROVIDERS.items()]
    found = spend["providers"]
    providers: list[Provider] = []
    for pid, name in HARNESS_PROVIDERS.items():
        windows = found.get(pid)
        providers.append(Provider(
            name=name, provider_id=pid,
            windows=_spend_windows(windows, labels) if windows else (),
            note="" if windows else t("no session in the last {window}", window=labels[-1]),
        ))
    for pid in sorted(set(found) - set(HARNESS_PROVIDERS)):
        providers.append(Provider(name=pid, provider_id=pid, windows=_spend_windows(found[pid], labels)))
    return providers


def _spend_windows(windows: dict, labels: tuple[str, ...]) -> tuple[Window, ...]:
    """The archive's windows for one provider, in the order the archive sums them."""
    return tuple(
        Window(label=label, used=float((windows.get(label) or {}).get("cost_usd") or 0.0), unit="usd")
        for label in labels
    )


def _claude(home: Path, now: float) -> Provider:
    """Claude Code: the tokens its transcripts hold, and the plan limit no file carries."""
    usage = quota_claude.read_usage(home / ".claude", now=now)
    if not usage.known:
        return Provider(name="Claude Code", provider_id="anthropic",
                        note=usage.note or t("no reading on this machine"))
    windows = tuple(
        Window(label=label, used=float(usage.windows.get(label, {}).get("tokens") or 0.0), unit="tokens")
        for label, _ in quota_claude.WINDOWS
    )
    return Provider(name="Claude Code", provider_id="anthropic", windows=windows,
                    note=t("the plan's limit is not readable on this machine"))


def _codex(home: Path, now: float) -> Provider:
    """Codex: the percentage each window reports, with its age and whether it still describes the window."""
    usage = quota_codex.read_usage(codex_home(home))
    if not usage.known:
        return Provider(name="Codex", provider_id="openai", note=usage.note or t("no reading on this machine"))
    windows = tuple(
        Window(
            label=quota.window_label(limit.window_minutes),
            used=limit.used_percent,
            unit="percent",
            read_at=usage.read_at,
            # A window read before it could have reset no longer describes today:
            # the rule is `quota`'s, repeated here rather than reworded.
            state="stale" if quota._expired(usage.read_at, limit.window_minutes, now) else "ok",
        )
        for limit in (usage.primary, usage.secondary) if limit is not None
    )
    return Provider(name=f"Codex ({usage.plan})" if usage.plan else "Codex", provider_id="openai", windows=windows)


def _antigravity(home: Path, now: float) -> Provider:
    """Antigravity: the prompt-credit percentage its own quota log carries, and how old that log is."""
    usage = quota_antigravity.read_usage(home=home, now=now)
    if not usage.known:
        return Provider(name="Antigravity", note=usage.note or t("not measured on this machine"))
    stale = quota._older_than(usage.read_at, quota.ANTIGRAVITY_MAX_READING_AGE_SECONDS, now)
    return Provider(name="Antigravity", windows=(
        Window(label=ANTIGRAVITY_WINDOW, used=usage.prompt_credits_used_percent, unit="percent",
               read_at=usage.read_at, state="stale" if stale else "ok"),
    ))
