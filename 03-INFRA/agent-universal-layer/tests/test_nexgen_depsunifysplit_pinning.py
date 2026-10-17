"""Pinning del pattern deps + allowlist prima dell'unify.

Fissa il comportamento attuale (non quello desiderato): check_mcp_deps e
check_skill_deps su manifest finti coerenti danno l'esito atteso, le
allowlist orfani sono rispettate in entrambi i domini, le firme pubbliche
restano invariate. Solo manifest finti in tmp_path, mai manifest veri,
nessuna rete, nessuna HOME reale.
"""
from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.checks import mcp_checks, skill_checks  # noqa: E402
from nexgen_core.report import Severity  # noqa: E402

_GIT_REV = "b" * 40

_MCP_BASE = """\
schema_version: 1
servers:
  demo-server:
    tier: core
    transport: stdio
    command: echo
    args: ["hi"]
    targets: [claude]
"""

_MCP_GIT_EXTRA = (
    "\n  need-git:\n"
    "    tier: core\n"
    "    transport: stdio\n"
    "    command: echo\n"
    '    args: ["hi"]\n'
    "    targets: [claude]\n"
    f'    deps: {{kind: git, repo: "https://example.invalid/r.git", rev: "{_GIT_REV}"}}\n'
)

_SKILLS_BASE = """\
schema_version: 1
skills:
  vault-skill-a:
    origin: vault
    targets: [claude]
    exposure: manual
"""

_SKILLS_GIT_EXTRA = (
    "\n  need-git-skill:\n"
    "    origin: upstream\n"
    "    exposure: manual\n"
    "    targets: [claude]\n"
    f'    deps: {{kind: git, repo: "https://example.invalid/r.git", rev: "{_GIT_REV}"}}\n'
)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _mcp_manifest(vault: Path) -> Path:
    return vault / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml"


def _vault_with_mcp(tmp_path: Path, text: str) -> tuple[Path, Path]:
    vault = tmp_path / "vault"
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    _write(_mcp_manifest(vault), text)
    return vault, home


def _write_claude(home: Path, *names: str) -> Path:
    cfg = home / ".claude.json"
    cfg.write_text(json.dumps({"mcpServers": {n: {} for n in names}}), encoding="utf-8")
    return cfg


def _skills_manifest(vault: Path) -> Path:
    return vault / "03-INFRA" / "agent-universal-layer" / "skills" / "skills.manifest.yaml"


def _vault_with_skills(tmp_path: Path, text: str) -> tuple[Path, Path]:
    vault = tmp_path / "vault"
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    _write(_skills_manifest(vault), text)
    return vault, home


def _library_skill(home: Path, name: str) -> Path:
    entry = home / ".agents" / "skill-library" / name
    entry.mkdir(parents=True, exist_ok=True)
    (entry / "SKILL.md").write_text("# fake\n", encoding="utf-8")
    return entry


def test_mcp_deps_ok_su_manifest_finto_coerente(tmp_path: Path) -> None:
    vault, _home = _vault_with_mcp(tmp_path, _MCP_BASE)
    outcome = mcp_checks.check_mcp_deps(_mcp_manifest(vault), tmp_path / "state")
    assert outcome.id == "mcp.deps"
    assert outcome.severity == Severity.OK


def test_mcp_deps_warn_quando_workspace_mancante(tmp_path: Path) -> None:
    vault, _home = _vault_with_mcp(tmp_path, _MCP_BASE + _MCP_GIT_EXTRA)
    outcome = mcp_checks.check_mcp_deps(_mcp_manifest(vault), tmp_path / "state")
    assert outcome.id == "mcp.deps"
    assert outcome.severity == Severity.WARN
    assert "need-git" in outcome.message


def test_mcp_deps_indeterminato_quando_manifest_illeggibile(tmp_path: Path) -> None:
    outcome = mcp_checks.check_mcp_deps(tmp_path / "nope.yaml", tmp_path / "state")
    assert outcome.id == "mcp.deps"
    assert outcome.severity == Severity.UNDETERMINED


def test_skill_deps_ok_su_manifest_finto_coerente(tmp_path: Path) -> None:
    manifest = _write(tmp_path / "skills.manifest.yaml", _SKILLS_BASE)
    outcome = skill_checks.check_skill_deps(manifest, tmp_path / "state")
    assert outcome.id == "skills.deps"
    assert outcome.severity == Severity.OK


def test_skill_deps_warn_quando_workspace_mancante(tmp_path: Path) -> None:
    manifest = _write(tmp_path / "skills.manifest.yaml", _SKILLS_BASE + _SKILLS_GIT_EXTRA)
    outcome = skill_checks.check_skill_deps(manifest, tmp_path / "state")
    assert outcome.id == "skills.deps"
    assert outcome.severity == Severity.WARN
    assert "need-git-skill" in outcome.message


def test_skill_deps_indeterminato_quando_manifest_illeggibile(tmp_path: Path) -> None:
    outcome = skill_checks.check_skill_deps(tmp_path / "nope.yaml", tmp_path / "state")
    assert outcome.id == "skills.deps"
    assert outcome.severity == Severity.UNDETERMINED


def test_mcp_allowlist_orfani_rispettata(tmp_path: Path) -> None:
    vault, home = _vault_with_mcp(tmp_path, _MCP_BASE)
    _write_claude(home, "demo-server", "stray-server")
    before = mcp_checks.check_mcp_orphans(vault, home)
    assert before.id == "mcp.orphans"
    assert before.severity == Severity.WARN
    assert "stray-server" in before.message
    _write(_mcp_manifest(vault), _MCP_BASE + '\norphans_allowlist: ["stray-server"]\n')
    after = mcp_checks.check_mcp_orphans(vault, home)
    assert after.id == "mcp.orphans"
    assert after.severity == Severity.OK
    assert "stray-server" not in after.message


def test_skill_allowlist_orfani_rispettata(tmp_path: Path) -> None:
    vault, home = _vault_with_skills(tmp_path, _SKILLS_BASE)
    _library_skill(home, "vault-skill-a")
    _library_skill(home, "stray-skill")
    before = skill_checks.check_skills_out_of_manifest(vault, home)
    assert before.id == "skills.out_of_manifest"
    assert before.severity == Severity.WARN
    assert "stray-skill" in before.message
    manifest = _skills_manifest(vault)
    manifest.write_text(
        manifest.read_text(encoding="utf-8") + "\norphans_allowlist: [stray-skill]\n",
        encoding="utf-8",
    )
    after = skill_checks.check_skills_out_of_manifest(vault, home)
    assert after.id == "skills.out_of_manifest"
    assert after.severity == Severity.OK
    assert "stray-skill" not in after.message


def test_esiti_stabili_a_parita_di_manifest_finti(tmp_path: Path) -> None:
    vault, home = _vault_with_mcp(tmp_path, _MCP_BASE + _MCP_GIT_EXTRA)
    first = mcp_checks.check_mcp_deps(_mcp_manifest(vault), tmp_path / "state")
    second = mcp_checks.check_mcp_deps(_mcp_manifest(vault), tmp_path / "state")
    assert (second.severity, second.id, second.message) == (
        first.severity, first.id, first.message,
    )
    skills_manifest = _write(tmp_path / "skills.manifest.yaml", _SKILLS_BASE + _SKILLS_GIT_EXTRA)
    first_s = skill_checks.check_skill_deps(skills_manifest, tmp_path / "state")
    second_s = skill_checks.check_skill_deps(skills_manifest, tmp_path / "state")
    assert (second_s.severity, second_s.id, second_s.message) == (
        first_s.severity, first_s.id, first_s.message,
    )


def test_firme_pubbliche_invariate() -> None:
    assert list(inspect.signature(mcp_checks.check_mcp_deps).parameters) == [
        "manifest_path",
        "state_dir",
    ]
    assert list(inspect.signature(skill_checks.check_skill_deps).parameters) == [
        "manifest_path",
        "state_dir",
    ]
    assert list(inspect.signature(mcp_checks._orphans_allowlist).parameters) == [
        "manifest_path",
    ]
    assert list(inspect.signature(skill_checks._skills_orphans_allowlist).parameters) == [
        "vault_data",
    ]
