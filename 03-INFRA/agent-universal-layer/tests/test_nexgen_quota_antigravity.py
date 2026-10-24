"""Antigravity quota reading: prompt credits and usage percentages from local logs.

One job: verify that `quota_antigravity.read_usage` parses local quota logs
(such as `*-Antigravity Quota.log` written by the IDE extension) into structured
readings, tolerates missing or malformed files, and never raises exceptions.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from nexgen_core import quota_antigravity

WHEN = "2026-10-24T12:00:00Z"
READ_AT = datetime(2026, 10, 24, 12, 0, 0, tzinfo=timezone.utc).timestamp()


def write_quota_log(
    folder: Path,
    *,
    used_percent: float = 85.0,
    available: int = 7500,
    monthly: int = 50000,
    timestamp: str = WHEN,
    filename: str = "8-Antigravity Quota.log",
) -> Path:
    """Create a mock Antigravity quota log file matching the extension format."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / filename
    body = {
        "models_count": 14,
        "prompt_credits": {
            "available": available,
            "monthly": monthly,
            "used_percentage": used_percent,
            "remaining_percentage": round(100.0 - used_percent, 1),
        },
        "timestamp": timestamp,
    }
    content = (
        f"{timestamp} INFO  [AGQ][Extension] Extension activating\n"
        f"{timestamp} DEBUG [AGQ][Extension] Quota update received: "
        + json.dumps(body, indent=2)
        + "\n"
    )
    path.write_text(content, encoding="utf-8")
    return path


def test_read_usage_from_quota_log(tmp_path):
    log_dir = tmp_path / "AppData" / "Roaming" / "Antigravity IDE" / "logs" / "session1"
    write_quota_log(log_dir, used_percent=72.5, available=13750, monthly=50000)

    usage = quota_antigravity.read_usage(home=tmp_path)

    assert usage.known is True
    assert usage.prompt_credits_used_percent == 72.5
    assert usage.prompt_credits_available == 13750
    assert usage.prompt_credits_monthly == 50000
    assert usage.models_count == 14
    assert abs(usage.read_at - READ_AT) < 1
    assert "8-Antigravity Quota.log" in usage.source


def test_read_usage_computes_used_percentage_if_missing(tmp_path):
    log_dir = tmp_path / "AppData" / "Roaming" / "Antigravity" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / "2-Antigravity Quota.log"
    body = {
        "prompt_credits": {
            "available": 10000,
            "monthly": 40000,
        },
        "timestamp": WHEN,
    }
    path.write_text(
        f"{WHEN} DEBUG [AGQ][Extension] Quota update received: {json.dumps(body)}\n",
        encoding="utf-8",
    )

    usage = quota_antigravity.read_usage(home=tmp_path)

    assert usage.known is True
    assert usage.prompt_credits_used_percent == 75.0
    assert usage.prompt_credits_available == 10000
    assert usage.prompt_credits_monthly == 40000


def test_read_usage_missing_dir_or_file(tmp_path):
    usage = quota_antigravity.read_usage(home=tmp_path)

    assert usage.known is False
    assert usage.prompt_credits_used_percent is None
    assert usage.read_at is None


def test_read_usage_malformed_file_is_tolerant(tmp_path):
    log_dir = tmp_path / ".config" / "Antigravity IDE" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "bad-Quota.log").write_text("corrupted non-json { [ unclosed\n", encoding="utf-8")

    usage = quota_antigravity.read_usage(home=tmp_path)

    assert usage.known is False
    assert usage.prompt_credits_used_percent is None


def test_read_usage_picks_newest_log_file(tmp_path):
    log_dir = tmp_path / "AppData" / "Roaming" / "Antigravity IDE" / "logs"
    older = write_quota_log(log_dir / "s1", used_percent=20.0, filename="1-Antigravity Quota.log")
    newer = write_quota_log(log_dir / "s2", used_percent=90.0, filename="2-Antigravity Quota.log")

    import os
    os.utime(older, (1000, 1000))
    os.utime(newer, (2000, 2000))

    usage = quota_antigravity.read_usage(home=tmp_path)

    assert usage.known is True
    assert usage.prompt_credits_used_percent == 90.0
