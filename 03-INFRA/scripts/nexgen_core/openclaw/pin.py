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


def required_node_version(engine_root: Path | None = None) -> str | None:
    """The Node range this OpenClaw release declares, or None when unreadable."""
    try:
        loaded = json.loads(pin_path(engine_root).read_text(encoding="utf-8"))
    except (OSError, ValueError):  # nexgen: allow reason="a missing or broken pin means the Node range is unknown, not a crash"
        return None
    if isinstance(loaded, dict):
        value = loaded.get("node")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


#: One token of a range: an optional operator and one to three numbers.
_COMPARATOR = re.compile(r"^(>=|<=|>|<|=)?v?(\d+)(?:\.(\d+))?(?:\.(\d+))?$")


def _version_tuple(text: str | None) -> tuple[int, int, int] | None:
    """`v24.22.1` as `(24, 22, 1)`; missing parts are zero."""
    match = re.match(r"^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?", (text or "").strip())
    if not match:
        return None
    return tuple(int(part or 0) for part in match.groups())  # type: ignore[return-value]


def _compare(current: tuple[int, int, int], operator: str, target: tuple[int, int, int]) -> bool:
    if operator == ">=":
        return current >= target
    if operator == "<=":
        return current <= target
    if operator == ">":
        return current > target
    if operator == "<":
        return current < target
    return current == target


def node_satisfies(version: str | None, requirement: str | None) -> bool:
    """Whether a Node version satisfies an `||`-joined range of comparators.

    The declared range uses only `>=`, `<` and `||`. An unknown version or a
    range this parser cannot read is not a failure: the setup reports what it
    cannot prove, and a guess about the host is worse than a clear note.
    """
    current = _version_tuple(version)
    if current is None or not requirement:
        return True
    parsed_any = False
    for clause in requirement.split("||"):
        tokens = clause.split()
        if not tokens:
            continue
        clause_ok = True
        clause_parsed = True
        for token in tokens:
            match = _COMPARATOR.match(token)
            if not match:
                clause_parsed = False
                break
            operator = match.group(1) or "="
            target = tuple(int(match.group(index) or 0) for index in (2, 3, 4))
            parsed_any = True
            if not _compare(current, operator, target):
                clause_ok = False
                break
        if clause_parsed and clause_ok:
            return True
    return False if parsed_any else True
