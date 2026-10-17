"""No engine source starts with a UTF-8 byte-order mark.

A BOM is invisible in an editor but not to every reader: the discipline hook
read a BOM-prefixed module as unparsable and judged every change to it wrong.
PowerShell 5 writes one by default, so an edit made from it is caught here.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BOM = b"\xef\xbb\xbf"


def test_no_python_source_of_the_engine_starts_with_a_bom():
    offenders = [
        str(path.relative_to(ROOT))
        for base in (ROOT / "scripts", ROOT / "agent-universal-layer" / "tests")
        for path in base.rglob("*.py")
        if path.read_bytes().startswith(BOM)
    ]
    assert offenders == [], f"UTF-8 BOM at the start of: {offenders}"
