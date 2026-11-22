"""The OpenClaw family inside `nexgen doctor`.

The doctor asks every installed module to prove it works, so OpenClaw's own
checks are a family of their own rather than a special case in `doctor.py`: the
same function serves `nexgen openclaw doctor` and this registration, and a
machine that never chose OpenClaw gets an empty list, not a clean bill of health
about software it does not have.

One line of that family is not part of `collect`: the flat-model guard, which the
OpenClaw command prints among its spend readings and the scheduled doctor runs on
its own, because asking `collect` for it would print the two transcript readings a
second time.
"""
from __future__ import annotations

from pathlib import Path

from nexgen_core.report import CheckOutcome


def check_openclaw(home: Path, engine_root: Path, vault_data: Path) -> list[CheckOutcome]:
    """Every OpenClaw check that applies here, or none when the machine never chose it."""
    from nexgen_core.openclaw.doctor import collect

    return collect(home=home, engine_root=engine_root, vault_data=vault_data)


def check_openclaw_flat(home: Path, vault_data: Path) -> CheckOutcome | None:
    """The owner's flat provider list against the configuration in force, or no line at all.

    `nexgen openclaw doctor` prints this line among its spend readings; the scheduled
    `nexgen doctor` asks it here, because a model ref outside the list is a bill the
    next turn has not made yet and no other check looks at one. Silence, never a pass:
    a machine with no OpenClaw file has nothing for this guard to judge, and a node's
    file is the copy `node_retire` kept, whose agents live on the gateway it points at.
    """
    from nexgen_core.openclaw.config_file import read_config
    from nexgen_core.openclaw.doctor_spend import flat_outcome
    from nexgen_core.openclaw.node_only import detect_node_only
    from nexgen_core.paths import openclaw_config

    path = openclaw_config(home)
    if not path.is_file() or detect_node_only(home):
        return None
    config, _error = read_config(path)
    if config is None:
        return None
    return flat_outcome(home, config, vault=vault_data)
