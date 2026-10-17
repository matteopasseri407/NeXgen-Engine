"""Pinning dello split di files prima del refactor.

Fissa il comportamento attuale (non quello desiderato): backup_file crea
il backup atteso, write_text_if_changed scrive solo se cambia, firme
pubbliche invariate. Solo tmp_path, solo file finti, mai file veri.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import files as files_mod  # noqa: E402
from nexgen_core.files import backup_file, write_text_if_changed  # noqa: E402


def test_backup_file_crea_backup_atteso(tmp_path: Path) -> None:
    assert backup_file(tmp_path / "assente.json") is None
    target = tmp_path / "cfg.json"
    target.write_text("v1", encoding="utf-8")
    plain = backup_file(target)
    assert plain is not None and plain.name.startswith("cfg.json.bak-")
    assert plain.suffix == ".bak"
    assert plain.read_text(encoding="utf-8") == "v1"
    tagged = backup_file(target, tag="permissions")
    assert tagged is not None and ".pre-permissions-" in tagged.name
    assert tagged.read_text(encoding="utf-8") == "v1"
    assert plain != tagged


def test_backup_file_snapshot_da_testo_su_file_mancante(tmp_path: Path) -> None:
    target = tmp_path / "nuovo.json"
    bak = backup_file(target, text="istantanea finta")
    assert bak is not None
    assert bak.read_text(encoding="utf-8") == "istantanea finta"
    assert not target.exists()


def test_write_text_if_changed_scrive_solo_se_cambia(tmp_path: Path) -> None:
    target = tmp_path / "cfg.json"
    target.write_text("uguale", encoding="utf-8")
    assert write_text_if_changed(target, "uguale") is False
    assert list(tmp_path.glob("*.bak-*")) == []
    assert write_text_if_changed(target, "nuovo") is True
    assert target.read_text(encoding="utf-8") == "nuovo"
    sopravvissuti = list(tmp_path.glob("*.bak-*"))
    assert len(sopravvissuti) == 1
    assert sopravvissuti[0].read_text(encoding="utf-8") == "uguale"


def test_write_text_if_changed_crea_file_nuovo_senza_backup(tmp_path: Path) -> None:
    target = tmp_path / "sub" / "cfg.json"
    assert write_text_if_changed(target, "primo") is True
    assert target.read_text(encoding="utf-8") == "primo"
    assert list(tmp_path.rglob("*.bak-*")) == []


def test_firme_pubbliche_invariate() -> None:
    assert list(inspect.signature(files_mod.atomic_write_text).parameters) == [
        "path", "text", "preserve_mode", "exclusive", "newline",
    ]
    assert list(inspect.signature(files_mod.atomic_write_bytes).parameters) == [
        "path", "data", "preserve_mode",
    ]
    assert list(inspect.signature(files_mod.publish_symlink).parameters) == [
        "link", "target",
    ]
    assert list(inspect.signature(files_mod.secure_artifact).parameters) == [
        "directory", "path",
    ]
    assert list(inspect.signature(files_mod.write_private_text).parameters) == [
        "path", "text", "exclusive",
    ]
    assert list(inspect.signature(backup_file).parameters) == [
        "path", "tag", "keep", "text", "skip_if_committed",
    ]
    assert list(inspect.signature(write_text_if_changed).parameters) == [
        "path", "text", "tag", "keep", "skip_if_committed",
    ]
