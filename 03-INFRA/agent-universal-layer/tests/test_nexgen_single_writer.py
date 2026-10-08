"""Files the engine owns are written through one atomic writer.

`Path.write_text` truncates the file and then writes it: a crash, a full disk or a second writer in
between leaves a half-written config, which for a CLI settings file or the shared module state
means the next start fails. `nexgen_core.files` has the one implementation (temp file, fsync,
rename, mode carried over, Windows lock retries). This scan keeps new code from going around it:
a direct write needs a line in ALLOWED below, with the reason.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

CORE = Path(__file__).resolve().parents[2] / "scripts" / "nexgen_core"

#: file -> why it may write directly. Keep this short; a config or a state file does not belong here.
ALLOWED = {
    "files.py": "the single writer itself",
    "release_trust.py": "a signers file inside a throwaway directory that is deleted right after",
    "tools/firecrawl.py": "the output file the user named on the command line",
    "vault/groom.py": "per-run log files under the state directory, never read back as state",
    "stack/secrets.py": "creates the file 0600 with os.open before the first byte, which the writer cannot",
    "tools/notifier_boot.py": "its own mkstemp + fdopen sequence for a boot script",
}


def _direct_writes(tree: ast.AST) -> list[int]:
    return [
        node.lineno for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"write_text", "write_bytes"}
    ]


@pytest.mark.parametrize("path", sorted(CORE.rglob("*.py")), ids=lambda p: str(p.relative_to(CORE)))
def test_no_direct_file_write_outside_the_allowlist(path):
    rel = path.relative_to(CORE).as_posix()
    if rel in ALLOWED or "__pycache__" in path.parts:
        return
    lines = _direct_writes(ast.parse(path.read_text(encoding="utf-8")))
    assert not lines, f"{rel}:{lines} writes with Path.write_text/write_bytes; use nexgen_core.files.atomic_write_text"


def test_the_allowlist_has_no_stale_entries():
    for rel in ALLOWED:
        assert (CORE / rel).is_file(), f"{rel} no longer exists: drop it from ALLOWED"


def test_the_scan_sees_what_it_is_for():
    assert _direct_writes(ast.parse("from pathlib import Path\nPath('x').write_text('y')\nPath('x').write_bytes(b'')\n")) == [2, 3]
    assert _direct_writes(ast.parse("atomic_write_text(p, 'y')")) == []
