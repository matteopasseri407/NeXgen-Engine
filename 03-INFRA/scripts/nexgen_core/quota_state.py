"""The readings the beat leaves where the plugin reads them before a turn.

One job: write the percentages the alerts already judge into one small JSON file
inside the deployed plugin, so `before_model_resolve` can decide without running
the engine and without computing a percentage of its own. The plugin never trusts
an old file: it judges the age against the policy's own limit, and a missing or
unreadable file changes nothing about a turn.

One entry per provider, carrying its worst window: a plan is spent as soon as one
of its windows is, and a provider that is fine everywhere is simply quiet. Only
providers with a real percentage are written, so a lane whose ceiling nobody
declared is absent rather than guessed at zero.
"""
from __future__ import annotations

import json
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from nexgen_core import quota
from nexgen_core.files import atomic_write_text
from nexgen_core.runtimes.openclaw import governor_state_path


def payload(rows: Iterable[quota.QuotaRow], now: float) -> dict[str, Any]:
    """The worst window of every provider, and the moment the reading was taken."""
    providers: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not row.provider_id or row.percent is None:
            continue
        current = providers.get(row.provider_id)
        if current is None or row.percent > current["percent"]:
            providers[row.provider_id] = {"percent": round(float(row.percent), 1), "window": row.window}
    return {"written_at": round(float(now), 3), "providers": providers}


def write(home: Path | None, rows: Iterable[quota.QuotaRow], *, now: float | None = None) -> str:
    """Publishes the state beside the plugin: `written`, `skipped` or `failed`.

    Nothing is written when the plugin was never deposited: a file nobody reads
    is a file that goes stale and confuses the next reader of the state directory.
    A Vault or a home that has become read-only costs this file and never the
    beat, whose liveness answer is what the person is waiting for.
    """
    target = governor_state_path(home)
    if not target.parent.is_dir():
        return "skipped"
    moment = time.time() if now is None else float(now)
    try:
        atomic_write_text(target, json.dumps(payload(rows, moment), indent=2, sort_keys=True) + "\n")
    except OSError:  # nexgen: allow reason="a state file that cannot be written costs the early handover, not the beat that also answers whether the machine is alive"
        return "failed"
    return "written"
