# nexgen: one-job reason="ask Antigravity's own CLI how much of its plan is left, and remember the answer for a few minutes"
"""What Antigravity says is left of its plan, asked of its own client.

One job: run `agy -p /usage` (the CLI's own quota command, the same table its
interactive `/usage` shows) and hand back one reading per window, cached in the
machine-local state directory so the hourly beat, the doctor and a session's
first line do not start the client every time.

Why the client and nothing else: the plan's quota lives behind Google's own
service, the CLI keeps it in memory, and the IDE extension whose log the older
reader parsed stopped writing. Asking `agy` is asking Google's client, with the
login it already holds; the engine never touches that login or calls Google
itself. The command answers from the quota service, not from a model, so it
costs no tokens.

Shape it reads (agy 2026-11, one tab-separated row per window):

    Gemini Models<TAB>Weekly Limit Remaining<TAB>49%<TAB>2026-11-21T09:14:46Z

A row in any other shape is skipped; an answer with no readable row is "no
reading", never a guess.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from nexgen_core.files import atomic_write_text
from nexgen_core.paths import resolve_state_dir
from nexgen_core.processes import no_window_kwargs, windows_command_argv

#: The client's own quota command, in print mode.
USAGE_ARGV = ("-p", "/usage", "--output-format", "text")
#: One answer is bought at most this often, like the tokscale backend's.
CACHE_TTL_SECONDS = 600
CACHE_NAME = "agy-usage.json"
#: `off` stops the client from being asked: the test suite and a sandbox set it, like the tokscale switch.
ENV_OVERRIDE = "NEXGEN_QUOTA_AGY"
TIMEOUT_SECONDS = 90
ROW = re.compile(r"^(?P<group>[^\t]+)\t(?P<label>[^\t]+?)\s+Remaining\t(?P<left>\d+(?:\.\d+)?)%\t(?P<reset>\S+)\s*$")

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


@dataclass(frozen=True)
class AgyWindow:
    """One window of the plan as the client printed it."""

    group: str
    window: str
    remaining_percent: float
    resets_at: float | None
    read_at: float


def parse(text: str, read_at: float) -> list[AgyWindow]:
    """The windows in the client's table; rows in another shape are left out."""
    windows: list[AgyWindow] = []
    for line in text.splitlines():
        match = ROW.match(line.strip("\r"))
        if not match:
            continue
        windows.append(AgyWindow(
            group=match["group"].strip(),
            window=_window_name(match["label"]),
            remaining_percent=float(match["left"]),
            resets_at=_moment(match["reset"]),
            read_at=read_at,
        ))
    return windows


def read_windows(
    *,
    home: Path | None = None,
    now: float | None = None,
    run: Runner = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
) -> list[AgyWindow]:
    """The plan's windows, from a fresh cache or from one run of the client; [] when there is none."""
    if os.environ.get(ENV_OVERRIDE, "").strip().casefold() == "off":
        return []
    moment = time.time() if now is None else float(now)
    cache = resolve_state_dir(home) / CACHE_NAME
    cached = _read_cache(cache, moment)
    if cached is not None:
        return cached
    executable = which("agy")
    if not executable:
        return []
    try:
        done = run(
            windows_command_argv([executable, *USAGE_ARGV]), cwd=str(cache.parent) if cache.parent.is_dir() else None,
            stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=TIMEOUT_SECONDS, check=False, **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):  # nexgen: allow reason="a client that cannot run is no reading; the report says the plan is unread"
        return []
    windows = parse(done.stdout or "", moment) if done.returncode == 0 else []
    if windows:
        _write_cache(cache, windows)
    return windows


def _window_name(label: str) -> str:
    """'Weekly Limit' -> 'Weekly', 'Five Hour Limit' -> '5h': the names the other plans use."""
    words = label.replace("Limit", "").strip()
    return {"Five Hour": "5h", "Weekly": "Weekly", "Daily": "Daily"}.get(words, words)


def _moment(text: str) -> float | None:
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:  # nexgen: allow reason="a reset the client printed in another shape is no reset time, the window is still read"
        return None


def _read_cache(path: Path, now: float) -> list[AgyWindow] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        windows = [AgyWindow(**item) for item in data]
    except (OSError, ValueError, TypeError):  # nexgen: allow reason="no cache or an unreadable one means asking the client again"
        return None
    if not windows or any(now - w.read_at > CACHE_TTL_SECONDS for w in windows):
        return None
    return windows


def _write_cache(path: Path, windows: list[AgyWindow]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, json.dumps([asdict(w) for w in windows]) + "\n")
    except OSError:  # nexgen: allow reason="a cache that cannot be written only costs the next caller one more run of the client"
        return


def to_rows(windows: list[AgyWindow]) -> list:
    """The windows as the quota report's rows: used is what the client says is gone."""
    from nexgen_core.quota import QuotaRow, _state  # the report owns the row; imported here to stay one-way

    rows = []
    for window in windows:
        used = round(100.0 - window.remaining_percent, 1)
        gemini = window.group.lower().startswith("gemini")
        rows.append(QuotaRow(
            provider="Antigravity",
            provider_id="google-antigravity-cli" if gemini else "",
            window=f"{window.window} ({window.group})",
            used=used,
            unit="percent",
            percent=used,
            resets_at=window.resets_at,
            read_at=window.read_at,
            state=_state(used),
        ))
    return rows
