"""Reading OpenClaw's configuration file, and never writing it.

Both `nexgen openclaw plan` and `nexgen openclaw doctor` need the file in force
before they can say anything useful, and both must be able to say "there is no
file" and "the file is not JSON" differently. The read lives here once, so the
two commands cannot disagree about which of those two states the machine is in.

Nothing in this module opens a credential: the configuration is not one.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def read_config(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    """The configuration in force, or None and the reason it cannot be used.

    A JSON document that is not an object is not a configuration OpenClaw can
    use, so it is reported as an error rather than handed back as data.
    """
    if not path.is_file():
        return None, None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, f"{type(exc).__name__}: {exc}"
    if not isinstance(loaded, dict):
        return None, "the file is JSON but not an object"
    return loaded, None
