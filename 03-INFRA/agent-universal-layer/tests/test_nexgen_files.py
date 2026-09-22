"""One way to write files: every policy pinned, not just documented."""
from __future__ import annotations

import os
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.files import atomic_write_text, backup_file, write_text_if_changed  # noqa: E402


def test_atomic_write_creates_parents_and_replaces(tmp_path: Path):
    target = tmp_path / "sub" / "cfg.json"
    atomic_write_text(target, '{"a": 1}')
    assert target.read_text(encoding="utf-8") == '{"a": 1}'
    atomic_write_text(target, '{"a": 2}')
    assert target.read_text(encoding="utf-8") == '{"a": 2}'
    assert list(tmp_path.rglob("*.tmp*")) == []


def test_atomic_write_preserves_mode(tmp_path: Path):
    if os.name == "nt":
        return  # mode bits are a POSIX concept; the writer still works
    target = tmp_path / "secret.env"
    target.write_text("x", encoding="utf-8")
    os.chmod(target, 0o600)
    atomic_write_text(target, "y")
    assert target.read_text(encoding="utf-8") == "y"
    assert target.stat().st_mode & 0o777 == 0o600


def test_backup_names_reasons_and_skips_missing(tmp_path: Path):
    assert backup_file(tmp_path / "absent.json") is None
    target = tmp_path / "cfg.json"
    target.write_text("v1", encoding="utf-8")
    plain = backup_file(target)
    assert plain is not None and plain.name.startswith("cfg.json.bak-")
    tagged = backup_file(target, tag="permissions")
    assert tagged is not None and ".pre-permissions-" in tagged.name
    assert tagged.read_text(encoding="utf-8") == "v1"


def test_backup_rotation_keeps_only_keep(tmp_path: Path):
    target = tmp_path / "cfg.json"
    target.write_text("v", encoding="utf-8")
    import time as _time

    for _ in range(4):
        backup_file(target, keep=2)
        _time.sleep(1.05)
    survivors = sorted(tmp_path.glob("cfg.json.bak-*"))
    assert len(survivors) == 2, [p.name for p in survivors]


def test_backup_without_keep_accumulates(tmp_path: Path):
    target = tmp_path / "cfg.json"
    target.write_text("v", encoding="utf-8")
    import time as _time

    for _ in range(2):
        backup_file(target)
        _time.sleep(1.05)
    assert len(list(tmp_path.glob("cfg.json.bak-*"))) == 2


def test_write_if_changed_skips_identical(tmp_path: Path):
    target = tmp_path / "cfg.json"
    target.write_text("same", encoding="utf-8")
    assert write_text_if_changed(target, "same", keep=3) is False
    assert list(tmp_path.glob("*.bak-*")) == []
    assert write_text_if_changed(target, "new", keep=3) is True
    assert target.read_text(encoding="utf-8") == "new"
    assert len(list(tmp_path.glob("*.bak-*"))) == 1
