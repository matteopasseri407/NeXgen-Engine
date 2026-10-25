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
missing figure is never written as a zero.
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
    """One window of one provider: what was used, in what unit, and what it cannot say."""

    label: str
    used: float | None = None
    unit: str = ""
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"window": self.label, "used": self.used, "unit": self.unit, "note": self.note}


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
        return [render_provider(provider) for provider in self.providers]

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
        _codex(home_dir),
        _antigravity(home_dir, moment),
    ))


def render_provider(provider: Provider) -> str:
    """One provider, on one line, in the terms a person reads."""
    if not provider.windows:
        return f"{provider.name}: {provider.note or t('nothing is readable on this machine')}"
    line = f"{provider.name}: " + ", ".join(render_window(window) for window in provider.windows)
    return f"{line} ({provider.note})" if provider.note else line


def render_window(window: Window) -> str:
    """One window as the shortest sentence that carries the number and its unit."""
    if window.used is None or not window.unit:
        return window.note or t("not measured")
    if window.unit == "usd":
        return t("{amount} in the last {window}", amount=f"{window.used:.2f} $", window=window.label)
    if window.unit == "tokens":
        return t("{tokens} tokens in the last {window}", tokens=f"{int(window.used):,}", window=window.label)
    return t("{percent} of the {window} window used", percent=quota.percent_text(window.used),
             window=window.label)


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


def _codex(home: Path) -> Provider:
    """Codex: the percentage each window reports in its own rate-limit event."""
    usage = quota_codex.read_usage(codex_home(home))
    if not usage.known:
        return Provider(name="Codex", provider_id="openai", note=usage.note or t("no reading on this machine"))
    windows = tuple(
        Window(label=quota.window_label(limit.window_minutes), used=limit.used_percent, unit="percent")
        for limit in (usage.primary, usage.secondary) if limit is not None
    )
    return Provider(name=f"Codex ({usage.plan})" if usage.plan else "Codex", provider_id="openai", windows=windows)


def _antigravity(home: Path, now: float) -> Provider:
    """Antigravity: the prompt-credit percentage its own quota log carries."""
    usage = quota_antigravity.read_usage(home=home, now=now)
    if not usage.known:
        return Provider(name="Antigravity", note=usage.note or t("not measured on this machine"))
    return Provider(name="Antigravity", windows=(
        Window(label=ANTIGRAVITY_WINDOW, used=usage.prompt_credits_used_percent, unit="percent"),
    ))
