"""The OpenClaw family inside `nexgen doctor`.

The doctor asks every installed module to prove it works, so OpenClaw's own
checks are a family of their own rather than a special case in `doctor.py`: the
same function serves `nexgen openclaw doctor` and this registration, and a
machine that never chose OpenClaw gets an empty list, not a clean bill of health
about software it does not have.
"""
from __future__ import annotations

from pathlib import Path

from nexgen_core.report import CheckOutcome


def check_openclaw(home: Path, engine_root: Path, vault_data: Path) -> list[CheckOutcome]:
    """Every OpenClaw check that applies here, or none when the machine never chose it."""
    from nexgen_core.openclaw.doctor import collect

    return collect(home=home, engine_root=engine_root, vault_data=vault_data)
