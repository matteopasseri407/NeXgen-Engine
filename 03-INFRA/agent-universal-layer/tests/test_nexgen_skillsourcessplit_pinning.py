"""Pinning dello split di skill_sources prima del refactor.

Fissa il comportamento attuale con fixture finte in tmp_path:
link-or-copy stabile, install_third_party che delega, firme invariate.
Solo tmp_path, nessuna rete, nessun clone vero.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = TESTS_DIR.parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import skill_sources as sources_mod  # noqa: E402


def test_link_or_copy_su_file_finto_stabile(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir(parents=True)
    (src / "SKILL.md").write_bytes(b"# stabile\n")
    dst = tmp_path / "views" / "demo-link"
    assert sources_mod.make_link_or_copy(src, dst) is True
    assert (dst / "SKILL.md").read_bytes() == b"# stabile\n"
    assert sources_mod.make_link_or_copy(src, dst) is False


def test_install_body_delegato(tmp_path: Path) -> None:
    src = Path(sources_mod.__file__).read_text(encoding="utf-8")
    assert "def _link_or_copy_body" in src
    assert "def _install_third_party_body" in src
    assert "return _link_or_copy_body(src, dst)" in src
    assert "return self._install_third_party_body(entry, lib_dest, discovery_dirs)" in src


def test_firme_pubbliche_invariate() -> None:
    assert list(inspect.signature(sources_mod.make_link_or_copy).parameters) == ["src", "dst"]
    assert list(inspect.signature(sources_mod.clone_url).parameters) == ["repo"]
    params = list(inspect.signature(sources_mod.SkillFetcher.install_third_party).parameters)
    assert params == ["self", "entry", "lib_dest", "discovery_dirs"]
