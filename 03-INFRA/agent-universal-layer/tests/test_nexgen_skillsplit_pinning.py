"""Pinning di SkillMaterializer.materialize su vault finto in tmp_path.

Fissa il comportamento attuale, non quello desiderato: lo split di
materialize nei tre helper (_collect_desired, _remove_stale_views,
_write_views) non deve cambiare esiti. Nessuna rete, nessuna HOME reale.
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.skills import SkillMaterializer  # noqa: E402


def _make_fake_vault(tmp_path: Path, manifest_text: str) -> tuple[Path, Path, Path]:
    vault = tmp_path / "vault"
    home = tmp_path / "home"
    engine = tmp_path / "engine"
    home.mkdir(parents=True, exist_ok=True)
    engine.mkdir(parents=True, exist_ok=True)
    skills_dir = vault / "03-INFRA" / "agent-universal-layer" / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    (skills_dir / "skills.manifest.yaml").write_text(manifest_text, encoding="utf-8")
    return vault, home, engine


def _write_vault_skill(vault: Path, name: str, body: str = "# demo\n") -> Path:
    src = vault / "03-INFRA" / "agent-universal-layer" / "skills" / name
    src.mkdir(parents=True, exist_ok=True)
    (src / "SKILL.md").write_text(
        "---\ndescription: demo skill\n---\n" + body, encoding="utf-8"
    )
    return src


_MANIFEST_ONE = """\
skills:
  demo-skill:
    origin: vault
    targets: [claude]
"""


def _mat(vault: Path, home: Path, engine: Path) -> SkillMaterializer:
    return SkillMaterializer(vault_data=vault, engine_root=engine, home=home)


def test_manifest_with_one_skill_materializes(tmp_path: Path):
    vault, home, engine = _make_fake_vault(tmp_path, _MANIFEST_ONE)
    _write_vault_skill(vault, "demo-skill")
    mat = _mat(vault, home, engine)
    changes, _actions = mat.materialize(apply=True)
    assert changes > 0
    assert (mat.library_dir / "demo-skill" / "SKILL.md").is_file()
    assert (mat.claude_dir / "demo-skill" / "SKILL.md").is_file()
    index = mat.active_dir / "INDEX.md"
    assert index.is_file()
    assert "demo-skill" in index.read_text(encoding="utf-8")


def test_second_run_idempotent_without_rewrite(tmp_path: Path):
    vault, home, engine = _make_fake_vault(tmp_path, _MANIFEST_ONE)
    _write_vault_skill(vault, "demo-skill")
    mat = _mat(vault, home, engine)
    mat.materialize(apply=True)
    lib_before = (mat.library_dir / "demo-skill" / "SKILL.md").read_bytes()
    view_before = (mat.claude_dir / "demo-skill" / "SKILL.md").read_bytes()
    changes2, actions2 = mat.materialize(apply=True)
    assert changes2 == 0
    assert actions2 == []
    assert (mat.library_dir / "demo-skill" / "SKILL.md").read_bytes() == lib_before
    assert (mat.claude_dir / "demo-skill" / "SKILL.md").read_bytes() == view_before
    assert list(home.rglob("*.bak-*")) == []


def test_stale_view_removed(tmp_path: Path):
    vault, home, engine = _make_fake_vault(tmp_path, _MANIFEST_ONE)
    _write_vault_skill(vault, "demo-skill")
    mat = _mat(vault, home, engine)
    mat.materialize(apply=True)
    assert (mat.claude_dir / "demo-skill").exists()
    (vault / "03-INFRA" / "agent-universal-layer" / "skills" / "skills.manifest.yaml").write_text(
        "skills: {}\n", encoding="utf-8"
    )
    _changes, actions = mat.materialize(apply=True)
    assert not (mat.claude_dir / "demo-skill").exists()
    assert (mat.library_dir / "demo-skill").exists()
    assert any("demo-skill" in a for a in actions)


def test_skill_outside_manifest_reported_not_deleted(tmp_path: Path):
    vault, home, engine = _make_fake_vault(tmp_path, _MANIFEST_ONE)
    _write_vault_skill(vault, "demo-skill")
    mat = _mat(vault, home, engine)
    mat.materialize(apply=True)
    foreign = mat.claude_dir / "foreign-skill"
    foreign.mkdir(parents=True, exist_ok=True)
    (foreign / "SKILL.md").write_text("# foreign\n", encoding="utf-8")
    orphan = mat.library_dir / "orphan-skill"
    orphan.mkdir(parents=True, exist_ok=True)
    (orphan / "SKILL.md").write_text("# orphan\n", encoding="utf-8")
    before = (foreign / "SKILL.md").read_bytes()
    _changes, _actions = mat.materialize(apply=True)
    assert foreign.is_dir()
    assert (foreign / "SKILL.md").read_bytes() == before
    assert orphan.is_dir()
    from nexgen_core.checks.skill_checks import check_skills_out_of_manifest

    outcome = check_skills_out_of_manifest(vault, home)
    assert "orphan-skill" in outcome.message
    assert orphan.is_dir()
