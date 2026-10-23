"""The OpenClaw version the engine configures against, read from one file.

The pin is data, not a constant in the code: the module catalog declares the
same file through `upstream.pinned_in`, so the dependency watch and the doctor
cannot disagree about which version is expected. A file that cannot be read is
reported as an unknown pin, never replaced by a guess compiled into this
package, because a second copy of a version number is a copy that goes stale.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from nexgen_core.paths import resolve_engine_root

#: Where the pin lives, relative to the engine root (`03-INFRA`).
PIN_RELATIVE = Path("agent-universal-layer") / "openclaw" / "pin.json"

#: The engine reads the version out of the JSON, not out of a regex applied to
#: the whole file: the same file is also read by the catalog's `pattern`.
_VERSION_RE = re.compile(r'"version"\s*:\s*"([^"]+)"')


def pin_path(engine_root: Path | None = None) -> Path:
    """The pin file in the installed engine, falling back to the source tree."""
    candidates = [resolve_engine_root(engine_root) / PIN_RELATIVE]
    # A cloned checkout runs from 03-INFRA/scripts/nexgen_core/openclaw/pin.py:
    # four parents up is the 03-INFRA that holds the layer.
    candidates.append(Path(__file__).resolve().parents[3] / PIN_RELATIVE)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


def pinned_version(engine_root: Path | None = None) -> str | None:
    """The pinned release, or None when the file says nothing readable."""
    try:
        text = pin_path(engine_root).read_text(encoding="utf-8")
    except OSError:  # nexgen: allow reason="a missing pin file is reported as an unknown version, never as a crash"
        return None
    try:
        loaded = json.loads(text)
    except ValueError:
        loaded = None
    if isinstance(loaded, dict) and loaded.get("version"):
        return str(loaded["version"])
    found = _VERSION_RE.search(text)
    return found.group(1) if found else None
