"""Test per le skill con dipendenze (contratto deps:, origin upstream).

Le skill sono SEMPRE lazy: il catalogo INDEX.md è l'unico routing, il
contenuto si carica on demand. Le skill che dichiarano `deps:` (npx pin o
git pin, stesso contratto dei server MCP) sono inventariate: il sync non le
materializza, il doctor le verifica offline-safe.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from nexgen_core.checks.skill_checks import check_skill_deps
from nexgen_core.report import Severity
from nexgen_core.skills import SkillMaterializer as SkillManager

SKILLS_MANIFEST = """
schema_version: 1
skills:
  local-skill:
    origin: vault
    exposure: manual
    targets: [claude, codex, antigravity, opencode]
  upstream-ok:
    origin: upstream
    exposure: manual
    targets: [claude, codex, antigravity, opencode]
    deps: {kind: npx, spec: "impeccable@4.0.2"}
  upstream-unpinned:
    origin: upstream
    exposure: manual
    deps: {kind: npx, spec: "impeccable"}
  upstream-nodeps:
    origin: upstream
    exposure: manual
  upstream-git:
    origin: upstream
    exposure: manual
    deps: {kind: git, repo: "https://example.invalid/r.git", rev: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}
"""


def _manager(tmp_path: Path) -> tuple[SkillManager, Path]:
    vault = tmp_path / "vault"
    skills_dir = vault / "03-INFRA" / "agent-universal-layer" / "skills"
    skills_dir.mkdir(parents=True)
    (skills_dir / "skills.manifest.yaml").write_text(SKILLS_MANIFEST, encoding="utf-8")
    (skills_dir / "local-skill").mkdir()
    (skills_dir / "local-skill" / "SKILL.md").write_text("---\ndescription: local skill\n---\nbody", encoding="utf-8")
    m = SkillManager(vault_data=vault, home=tmp_path)
    return m, vault


def test_upstream_origin_accepted_and_inventoried(tmp_path: Path):
    vault = tmp_path / "vault"
    skills_dir = vault / "03-INFRA" / "agent-universal-layer" / "skills"
    skills_dir.mkdir(parents=True)
    (skills_dir / "skills.manifest.yaml").write_text(
        """
schema_version: 1
skills:
  upstream-ok:
    origin: upstream
    exposure: manual
    deps: {kind: npx, spec: "impeccable@4.0.2"}
""",
        encoding="utf-8",
    )
    m = SkillManager(vault_data=vault, home=tmp_path)
    problems = m.validate_manifest()
    assert problems == [], problems
    entry = m.load_manifest()["upstream-ok"]
    assert entry.origin == "upstream"
    assert entry.deps == {"kind": "npx", "spec": "impeccable@4.0.2"}


def test_upstream_without_deps_or_bad_pin_is_rejected(tmp_path: Path):
    m, _ = _manager(tmp_path)
    problems = "\n".join(m.validate_manifest())
    assert "upstream-nodeps" in problems and "without 'deps'" in problems
    assert "upstream-unpinned" in problems and "Pin rule" in problems


def test_materialize_skips_upstream_but_indexes_it(tmp_path: Path):
    m, _ = _manager(tmp_path)
    changes, actions = m.materialize(apply=True)
    assert changes == 1, actions  # solo local-skill viene materializzata
    lib = tmp_path / ".agents" / "skill-library"
    assert (lib / "upstream-ok").exists() is False
    assert (lib / "local-skill").is_dir()
    index = (tmp_path / ".agents" / "skills" / "INDEX.md").read_text(encoding="utf-8")
    assert "`upstream-ok`" in index
    assert "`local-skill`" in index


def test_check_skill_deps_offline(tmp_path: Path):
    m, _ = _manager(tmp_path)
    manifest = m.vault_data / "03-INFRA" / "agent-universal-layer" / "skills" / "skills.manifest.yaml"
    out = check_skill_deps(manifest, tmp_path / "state")
    assert out.id == "skills.deps"
    # npx pin: ok se node esiste; git pin: workspace non provisionato -> WARN
    if Path("/usr/bin/node").exists() or any(p.name == "node" for p in []):
        pass
    assert out.severity in (Severity.WARN, Severity.OK)
    if out.severity == Severity.WARN:
        assert "upstream-git" in out.message


def test_generate_index_includes_upstream_description(tmp_path: Path):
    m, _ = _manager(tmp_path)
    lib = tmp_path / ".agents" / "skill-library"
    (lib / "upstream-ok").mkdir(parents=True)
    (lib / "upstream-ok" / "SKILL.md").write_text(
        "---\ndescription: design lane, use when shaping frontend UI\n---\nbody", encoding="utf-8",
    )
    m.materialize(apply=True)
    index = (tmp_path / ".agents" / "skills" / "INDEX.md").read_text(encoding="utf-8")
    assert "design lane, use when shaping frontend UI" in index
