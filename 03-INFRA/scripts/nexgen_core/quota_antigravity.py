"""What Antigravity itself says is left of its prompt credits and quota, read from local logs.

One job: find the newest quota record written by Antigravity under
AppData / ~/.config logs (such as the extension log `*-Antigravity Quota.log`)
and hand it back with the moment it was written.

When no log file exists on disk, or when the CLI (`agy`) holds quota only in
memory behind a live Google Cloud Code API call, this reader reports that state
gracefully without raising exceptions.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from nexgen_core.i18n import t
from nexgen_core.paths import resolve_home

#: Substrings identifying quota logs written by the Antigravity quota extension.
LOG_NAME_PATTERNS = ("quota.log", "quota.log")

#: Header prefix logged immediately before the JSON snapshot.
QUOTA_UPDATE_PREFIX = "Quota update received:"

#: How many bytes of a log file tail to inspect for the newest snapshot.
TAIL_BUDGET_BYTES = 512 * 1024


@dataclass(frozen=True)
class AntigravityUsage:
    """The newest reading Antigravity left in local logs, and why there may be none."""

    read_at: float | None = None
    prompt_credits_used_percent: float | None = None
    prompt_credits_available: int | None = None
    prompt_credits_monthly: int | None = None
    resets_at: float | None = None
    models_count: int | None = None
    source: str = ""
    note: str = ""

    @property
    def known(self) -> bool:
        """Whether a valid prompt credits percentage was found."""
        return self.prompt_credits_used_percent is not None


def read_usage(log_dir: Path | None = None, *, home: Path | None = None,
               now: float | None = None) -> AntigravityUsage:
    """The last quota reading in local Antigravity logs, or an empty usage object.

    One job: search candidate log folders for the newest quota log and extract
    its most recent prompt credits reading.
    """
    moment = time.time() if now is None else float(now)
    dirs = [Path(log_dir)] if log_dir is not None else _candidate_log_dirs(home)
    files = _find_quota_logs(dirs)
    if not files:
        return AntigravityUsage(
            note=t("No Antigravity quota log found on this machine."),
        )
    for path in files:
        record = _parse_log_file(path, moment)
        if record is not None and record.known:
            return record
    return AntigravityUsage(
        note=t("No valid Antigravity quota snapshot found in logs."),
    )


def _candidate_log_dirs(home: Path | None) -> list[Path]:
    """Candidate directories where Antigravity logs might reside, in search order.

    One job: gather known log directories under `home` and Windows APPDATA without
    raising if they do not exist.
    """
    resolved = resolve_home(home)
    candidates: list[Path] = [
        resolved / "AppData" / "Roaming" / "Antigravity IDE" / "logs",
        resolved / "AppData" / "Roaming" / "Antigravity" / "logs",
        resolved / ".config" / "Antigravity IDE" / "logs",
        resolved / ".config" / "Antigravity" / "logs",
        resolved / ".gemini" / "antigravity" / "logs",
        resolved / ".gemini" / "antigravity-cli" / "log",
        resolved / ".gemini" / "logs",
    ]
    appdata = os.environ.get("APPDATA")
    if appdata and home is None:
        candidates.insert(0, Path(appdata) / "Antigravity IDE" / "logs")
        candidates.insert(1, Path(appdata) / "Antigravity" / "logs")
    return [c for c in candidates if c.is_dir()]


def _find_quota_logs(directories: list[Path]) -> list[Path]:
    """All candidate quota log files under `directories`, newest first.

    One job: find files matching quota log patterns across given roots.
    """
    found: list[Path] = []
    for root in directories:
        try:
            for path in root.rglob("*.log"):
                if "quota" in path.name.lower():
                    found.append(path)
        except OSError:  # nexgen: allow reason="an unreadable log directory is skipped rather than failing the quota check"
            continue
    return sorted(found, key=lambda p: _safe_mtime(p), reverse=True)


def _safe_mtime(path: Path) -> float:
    """File modification time, or 0.0 on filesystem error."""
    try:
        return path.stat().st_mtime
    except OSError:  # nexgen: allow reason="a file that vanished mid-walk gets a zero mtime and sorts last"
        return 0.0


def _parse_log_file(path: Path, moment: float) -> AntigravityUsage | None:
    """Extract the newest quota snapshot from `path`.

    One job: read the tail of the log file, find the JSON payload after the
    quota update marker, and construct an `AntigravityUsage`.
    """
    try:
        size = path.stat().st_size
        read_bytes = min(size, TAIL_BUDGET_BYTES)
        with open(path, "rb") as fp:
            if size > read_bytes:
                fp.seek(size - read_bytes)
            content = fp.read().decode("utf-8", "replace")
    except OSError:  # nexgen: allow reason="an unreadable log file is skipped so earlier logs can still answer"
        return None

    idx = content.rfind(QUOTA_UPDATE_PREFIX)
    if idx == -1:
        return None

    payload_text = content[idx + len(QUOTA_UPDATE_PREFIX):].strip()
    data = _extract_json_object(payload_text)
    if not isinstance(data, dict):
        return None

    prompt_credits = data.get("prompt_credits") if isinstance(data.get("prompt_credits"), dict) else {}
    available = prompt_credits.get("available")
    monthly = prompt_credits.get("monthly")
    used_pct = prompt_credits.get("used_percentage")

    if used_pct is None and isinstance(available, (int, float)) and isinstance(monthly, (int, float)) and monthly > 0:
        used_pct = round(((monthly - available) / monthly) * 100.0, 1)

    if used_pct is None:
        return None

    read_at = _epoch(data.get("timestamp")) or _safe_mtime(path) or moment
    models_count = data.get("models_count")

    return AntigravityUsage(
        read_at=read_at,
        prompt_credits_used_percent=float(used_pct),
        prompt_credits_available=int(available) if isinstance(available, (int, float)) else None,
        prompt_credits_monthly=int(monthly) if isinstance(monthly, (int, float)) else None,
        models_count=int(models_count) if isinstance(models_count, (int, float)) else None,
        source=path.name,
    )


def _extract_json_object(text: str) -> dict | None:
    """Parse the first balanced JSON object from `text`.

    One job: find the outer braces of a JSON object and load it.
    """
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    end = -1
    for i, char in enumerate(text[start:], start=start):
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end == -1:
        return None
    try:
        parsed = json.loads(text[start:end])
        return parsed if isinstance(parsed, dict) else None
    except ValueError:  # nexgen: allow reason="a truncated or malformed JSON payload is skipped gracefully"
        return None


def _epoch(stamp: str | None) -> float | None:
    """ISO-8601 string to epoch seconds, or None if invalid."""
    if not isinstance(stamp, str) or not stamp:
        return None
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
    except ValueError:  # nexgen: allow reason="an unparseable timestamp falls back to file modification time"
        return None
