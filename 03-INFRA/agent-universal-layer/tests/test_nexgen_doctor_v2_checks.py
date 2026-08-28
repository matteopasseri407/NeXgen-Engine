"""Unit test per i controlli del doctor portati dal doctor bash della release
(v2 audit): istruzioni canoniche, igiene del bootstrap, raggiungibilità MCP,
drift puntuale nelle config MCP rese, commit non pubblicati da troppo tempo,
allineamento dei mirror, salute della libreria skill.

Ogni test asserisce la REGOLA (quale severità e perché), mai l'output
testuale esatto o un conteggio che può legittimamente crescere.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.checks import git_checks, instructions_checks, mcp_checks, reachability_checks, skill_checks
from nexgen_core.doctor import Doctor
from nexgen_core.paths import canonical_instructions
from nexgen_core.report import Severity
from nexgen_core.skills import SkillMaterializer

pytestmark = pytest.mark.filterwarnings("ignore")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_executable_stub(directory: Path, name: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        stub = directory / f"{name}.cmd"
        stub.write_text("@echo off\nexit /b 0\n", encoding="utf-8")
    else:
        stub = directory / name
        stub.write_text("#!/bin/sh\nexit 0\n")
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)


def _write_canon(vault_data: Path, content: str = "# Agents Rules\n") -> Path:
    canon = canonical_instructions(vault_data)
    canon.parent.mkdir(parents=True, exist_ok=True)
    canon.write_text(content, encoding="utf-8")
    return canon


def _git(repo: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    e = dict(os.environ)
    e.setdefault("GIT_AUTHOR_NAME", "Test")
    e.setdefault("GIT_AUTHOR_EMAIL", "test@example.com")
    e.setdefault("GIT_COMMITTER_NAME", "Test")
    e.setdefault("GIT_COMMITTER_EMAIL", "test@example.com")
    if env:
        e.update(env)
    return subprocess.run(
        ["git", "-C", str(repo), *args], env=e, capture_output=True, text=True, check=True
    )


def _init_repo_with_remote(tmp_path: Path, name: str = "vault") -> tuple[Path, Path]:
    """Un repo locale 'vault' con un remoto 'origin' bare, un commit già pubblicato."""
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)

    work = tmp_path / name
    subprocess.run(["git", "init", str(work)], check=True, capture_output=True)
    _git(work, "checkout", "-B", "main")
    (work / "README.md").write_text("hello\n")
    _git(work, "add", "README.md")
    _git(work, "commit", "-m", "init")
    _git(work, "remote", "add", "origin", str(bare))
    _git(work, "push", "-u", "origin", "main")
    return work, bare


# ---------------------------------------------------------------------------
# 1+2. Istruzioni canoniche e igiene del bootstrap
# ---------------------------------------------------------------------------

def test_canonical_instructions_present_flags_missing(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    outcome = instructions_checks.check_canonical_instructions_present(vault)
    assert outcome.severity == Severity.BROKEN

    _write_canon(vault)
    outcome = instructions_checks.check_canonical_instructions_present(vault)
    assert outcome.severity == Severity.OK


def test_claude_pointer_rules(tmp_path: Path):
    vault = tmp_path / "vault"
    home = tmp_path / "home"
    home.mkdir()
    canon = _write_canon(vault)

    # Assente: BROKEN
    outcome = instructions_checks.check_claude_pointer(vault, home)
    assert outcome.severity == Severity.BROKEN

    # Symlink invece di un puntatore leggero: BROKEN
    claude_file = home / "CLAUDE.md"
    claude_file.symlink_to(canon)
    outcome = instructions_checks.check_claude_pointer(vault, home)
    assert outcome.severity == Severity.BROKEN
    claude_file.unlink()

    # Copia che non referenzia il canonico: BROKEN
    claude_file.write_text("# Solo testo, nessun riferimento\n")
    outcome = instructions_checks.check_claude_pointer(vault, home)
    assert outcome.severity == Severity.BROKEN

    # Puntatore corretto: OK
    claude_file.write_text(f"Le istruzioni sono in {canon}\n")
    outcome = instructions_checks.check_claude_pointer(vault, home)
    assert outcome.severity == Severity.OK


def test_cli_instruction_pointers_not_installed_is_not_reported(tmp_path: Path, monkeypatch):
    vault = tmp_path / "vault"
    home = tmp_path / "home"
    home.mkdir()
    _write_canon(vault)
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))  # né codex né agy sul PATH
    (tmp_path / "empty-bin").mkdir()

    outcomes = instructions_checks.check_cli_instruction_pointers(vault, home)
    assert outcomes == []  # scelta dell'utente di non installarle: non si riporta


def test_cli_instruction_pointers_broken_when_installed_but_missing(tmp_path: Path, monkeypatch):
    vault = tmp_path / "vault"
    home = tmp_path / "home"
    home.mkdir()
    _write_canon(vault)
    bin_dir = tmp_path / "bin"
    _make_executable_stub(bin_dir, "codex")
    monkeypatch.setenv("PATH", str(bin_dir))

    outcomes = instructions_checks.check_cli_instruction_pointers(vault, home)
    codex_outcomes = [o for o in outcomes if o.id == "instructions.codex_pointer"]
    assert len(codex_outcomes) == 1
    assert codex_outcomes[0].severity == Severity.BROKEN


def test_cli_instruction_pointers_ok_when_aligned(tmp_path: Path, monkeypatch):
    vault = tmp_path / "vault"
    home = tmp_path / "home"
    home.mkdir()
    canon = _write_canon(vault)
    bin_dir = tmp_path / "bin"
    _make_executable_stub(bin_dir, "codex")
    monkeypatch.setenv("PATH", str(bin_dir))

    pointer = home / ".codex" / "AGENTS.md"
    pointer.parent.mkdir(parents=True)
    pointer.symlink_to(canon)

    outcomes = instructions_checks.check_cli_instruction_pointers(vault, home)
    codex_outcomes = [o for o in outcomes if o.id == "instructions.codex_pointer"]
    assert codex_outcomes[0].severity == Severity.OK


def test_opencode_instructions_rules(tmp_path: Path, monkeypatch):
    vault = tmp_path / "vault"
    home = tmp_path / "home"
    home.mkdir()
    canon = _write_canon(vault)
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))  # opencode non sul PATH
    (tmp_path / "empty-bin").mkdir()

    # Nessuna config e OpenCode non installato: non si riporta
    assert instructions_checks.check_opencode_instructions(vault, home) is None

    cfg = home / ".config" / "opencode" / "opencode.json"
    cfg.parent.mkdir(parents=True)

    # Config presente ma senza il canonico: BROKEN
    cfg.write_text(json.dumps({"instructions": ["something/else.md"]}))
    outcome = instructions_checks.check_opencode_instructions(vault, home)
    assert outcome.severity == Severity.BROKEN

    # Config con il canonico: OK
    cfg.write_text(json.dumps({"instructions": [str(canon)]}))
    outcome = instructions_checks.check_opencode_instructions(vault, home)
    assert outcome.severity == Severity.OK


def test_bootstrap_size_budget(tmp_path: Path):
    vault = tmp_path / "vault"
    _write_canon(vault, content="short\n")
    outcome = instructions_checks.check_bootstrap_size_budget(vault)
    assert outcome.severity == Severity.OK

    oversized = "x" * (instructions_checks.BOOTSTRAP_MAX_BYTES + 1)
    _write_canon(vault, content=oversized)
    outcome = instructions_checks.check_bootstrap_size_budget(vault)
    assert outcome.severity == Severity.BROKEN


def test_bootstrap_notes_size(tmp_path: Path):
    vault = tmp_path / "vault"
    _write_canon(vault)
    notes_dir = vault / "03-INFRA"

    outcome = instructions_checks.check_bootstrap_notes_size(vault)
    assert outcome.severity == Severity.OK

    (notes_dir / "huge-note.md").write_text("x" * (instructions_checks.NOTE_MAX_BYTES + 1))
    outcome = instructions_checks.check_bootstrap_notes_size(vault)
    assert outcome.severity == Severity.WARN


def test_bootstrap_pointer_integrity(tmp_path: Path):
    vault = tmp_path / "vault"
    (vault / "03-INFRA" / "existing-note.md").parent.mkdir(parents=True)
    (vault / "03-INFRA" / "existing-note.md").write_text("nota reale\n")

    _write_canon(vault, content="Vedi `03-INFRA/existing-note.md` per i dettagli.\n")
    outcome = instructions_checks.check_bootstrap_pointer_integrity(vault)
    assert outcome.severity == Severity.OK

    _write_canon(vault, content="Vedi `03-INFRA/note-rimossa.md` per i dettagli.\n")
    outcome = instructions_checks.check_bootstrap_pointer_integrity(vault)
    assert outcome.severity == Severity.BROKEN


# ---------------------------------------------------------------------------
# 3. Raggiungibilità dei connettori MCP
# ---------------------------------------------------------------------------

_REACHABILITY_MANIFEST = """
schema_version: 1
servers:
  probe-server:
    transport: http
    tier: core
    url: "http://127.0.0.1:65533/"
    require_env: PROBE_ENABLED
    targets: [claude, codex, antigravity, opencode]
  optional-server:
    transport: http
    tier: optional
    url: "http://127.0.0.1:65534/"
    targets: [claude]
"""


def _write_reachability_manifest(vault: Path) -> None:
    manifest = vault / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(_REACHABILITY_MANIFEST, encoding="utf-8")


def test_mcp_reachability_skips_unsatisfied_precondition(tmp_path: Path, monkeypatch):
    vault = tmp_path / "vault"
    home = tmp_path / "home"
    _write_reachability_manifest(vault)
    monkeypatch.delenv("PROBE_ENABLED", raising=False)

    outcomes = reachability_checks.check_mcp_reachability(vault, home)
    # require_env non soddisfatta = scelta non attivata dall'utente: niente da riportare
    assert outcomes == []


def test_a_local_service_that_is_simply_not_running_is_not_a_fault(tmp_path: Path, monkeypatch):
    """Su questa macchina, «rifiutato» vuol dire spento, non rotto.

    Chi installa rispondendo «nessun servizio» si vedeva un guasto rosso per
    un connettore che aveva appena declinato. Un'installazione corretta che
    sembra rotta insegna a ignorare il referto, che è il danno peggiore.
    Resta riportato, come non verificabile, col passo successivo.
    """
    vault = tmp_path / "vault"
    home = tmp_path / "home"
    _write_reachability_manifest(vault)
    monkeypatch.setenv("PROBE_ENABLED", "1")

    outcomes = reachability_checks.check_mcp_reachability(vault, home, timeout=1.0)
    by_id = {o.id: o for o in outcomes}
    assert "mcp.reachable.probe-server" in by_id, "non deve sparire: va detto"
    assert by_id["mcp.reachable.probe-server"].severity == Severity.UNDETERMINED
    assert by_id["mcp.reachable.probe-server"].action, "e deve dire cosa fare"
    # tier: optional non viene mai sondato, indipendentemente da require_env
    assert "mcp.reachable.optional-server" not in by_id


def test_a_declared_server_that_refuses_is_a_fault(tmp_path: Path, monkeypatch):
    """Fuori da questa macchina è un'altra cosa: qualcuno ha dichiarato un
    server, e il server non risponde."""
    vault = tmp_path / "vault"
    home = tmp_path / "home"
    manifest = vault / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        "schema_version: 1\n"
        "servers:\n"
        "  remote-server:\n"
        "    transport: http\n"
        "    tier: core\n"
        '    url: "http://198.51.100.7:65533/"\n'
        "    require_env: PROBE_ENABLED\n"
        "    targets: [claude]\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PROBE_ENABLED", "1")

    outcomes = reachability_checks.check_mcp_reachability(vault, home, timeout=1.0)
    by_id = {o.id: o for o in outcomes}
    severity = by_id["mcp.reachable.remote-server"].severity
    # Una rete che scarta i pacchetti dà timeout, non rifiuto: entrambi sono
    # "non è arrivato", e nessuno dei due è OK. Ciò che il test fissa è che
    # un host remoto non gode dell'indulgenza riservata a localhost.
    assert severity in (Severity.BROKEN, Severity.UNDETERMINED)


# ---------------------------------------------------------------------------
# 4. Drift puntuale nelle config MCP rese
# ---------------------------------------------------------------------------

_MCP_MANIFEST = """
schema_version: 1
servers:
  demo-server:
    tier: core
    transport: stdio
    command: echo
    args: ["hi"]
    targets: [claude, codex, antigravity, opencode]
"""


def _write_mcp_manifest(vault: Path) -> None:
    manifest = vault / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(_MCP_MANIFEST, encoding="utf-8")


def test_mcp_configs_rendered_undetermined_when_never_launched(tmp_path: Path):
    vault = tmp_path / "vault"
    home = tmp_path / "home"
    home.mkdir()
    _write_mcp_manifest(vault)

    outcome = mcp_checks.check_mcp_configs_rendered(vault, home)
    assert outcome.severity == Severity.UNDETERMINED


def test_mcp_configs_rendered_broken_when_server_missing(tmp_path: Path):
    vault = tmp_path / "vault"
    home = tmp_path / "home"
    home.mkdir()
    _write_mcp_manifest(vault)

    (home / ".claude.json").write_text(json.dumps({"mcpServers": {}}))

    outcome = mcp_checks.check_mcp_configs_rendered(vault, home)
    assert outcome.severity == Severity.BROKEN
    assert "demo-server" in outcome.message


def test_mcp_configs_rendered_ok_when_all_clis_aligned(tmp_path: Path):
    vault = tmp_path / "vault"
    home = tmp_path / "home"
    home.mkdir()
    _write_mcp_manifest(vault)

    (home / ".claude.json").write_text(json.dumps({"mcpServers": {"demo-server": {}}}))
    (home / ".gemini" / "antigravity-ide").mkdir(parents=True)
    (home / ".gemini" / "antigravity-ide" / "mcp_config.json").write_text(
        json.dumps({"mcpServers": {"demo-server": {}}})
    )
    (home / ".codex").mkdir(parents=True)
    # Codex normalizza i trattini in underscore nei nomi di sezione TOML.
    (home / ".codex" / "config.toml").write_text('[mcp_servers.demo_server]\ncommand = "echo"\n')
    (home / ".config" / "opencode").mkdir(parents=True)
    (home / ".config" / "opencode" / "opencode.json").write_text(json.dumps({"mcp": {"demo-server": {}}}))

    outcome = mcp_checks.check_mcp_configs_rendered(vault, home)
    assert outcome.severity == Severity.OK


def test_mcp_orphans_warns_with_the_config_path(tmp_path: Path):
    """Un server vivo nella config ma fuori dal manifest è un orfano:
    WARN con il path, mai una rimozione e mai un blocco."""
    vault = tmp_path / "vault"
    home = tmp_path / "home"
    home.mkdir()
    _write_mcp_manifest(vault)

    (home / ".claude.json").write_text(json.dumps({
        "mcpServers": {"demo-server": {}, "stray-server": {}}
    }))

    outcome = mcp_checks.check_mcp_orphans(vault, home)
    assert outcome.severity == Severity.WARN
    assert "stray-server" in outcome.message
    assert ".claude.json" in outcome.message
    # l'orfano non deve comparire come server mancante nel check di render:
    # qui resta tutto allineato tranne le CLI mai lanciate (indeterminato)
    rendered = mcp_checks.check_mcp_configs_rendered(vault, home)
    assert rendered.severity != Severity.BROKEN


def test_mcp_orphans_gone_when_the_entry_is_gone(tmp_path: Path):
    vault = tmp_path / "vault"
    home = tmp_path / "home"
    home.mkdir()
    _write_mcp_manifest(vault)

    (home / ".claude.json").write_text(json.dumps({"mcpServers": {"demo-server": {}}}))

    outcome = mcp_checks.check_mcp_orphans(vault, home)
    assert outcome.severity == Severity.OK


def test_mcp_orphans_honours_the_allowlist(tmp_path: Path):
    """L'allowlist vive nel manifest: bare name vale su ogni CLI,
    'cli:name' solo su quella CLI."""
    vault = tmp_path / "vault"
    home = tmp_path / "home"
    home.mkdir()
    _write_mcp_manifest(vault)
    manifest = vault / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8")
        + "\norphans_allowlist: [\"codex:stray-server\"]\n",
        encoding="utf-8",
    )

    (home / ".claude.json").write_text(json.dumps({"mcpServers": {}}))
    (home / ".codex").mkdir(parents=True)
    (home / ".codex" / "config.toml").write_text(
        '[mcp_servers.demo_server]\ncommand = "echo"\n\n'
        '[mcp_servers.stray_server]\ncommand = "echo"\n'
    )

    outcome = mcp_checks.check_mcp_orphans(vault, home)
    assert outcome.severity == Severity.OK, outcome.message


# ---------------------------------------------------------------------------
# 5. Commit non pubblicati da troppo tempo
# ---------------------------------------------------------------------------

def test_git_alignment_ok_for_recent_unpublished_commit(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("KNOWLEDGE_VAULT_REMOTE", raising=False)
    monkeypatch.delenv("KNOWLEDGE_VAULT_MIRRORS", raising=False)
    work, _bare = _init_repo_with_remote(tmp_path)

    (work / "note.md").write_text("recent change\n")
    _git(work, "add", "note.md")
    _git(work, "commit", "-m", "recent commit")

    outcome = git_checks.check_git_alignment(work, expected_branch="main")
    assert outcome.severity == Severity.OK


def test_git_alignment_broken_for_stale_unpublished_commit(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("KNOWLEDGE_VAULT_REMOTE", raising=False)
    monkeypatch.delenv("KNOWLEDGE_VAULT_MIRRORS", raising=False)
    work, _bare = _init_repo_with_remote(tmp_path)

    old_ts = int(time.time()) - git_checks.STALE_UNPUBLISHED_SECONDS - 3600
    (work / "note.md").write_text("stale change\n")
    _git(work, "add", "note.md")
    _git(
        work, "commit", "-m", "stale commit",
        env={"GIT_AUTHOR_DATE": f"{old_ts} +0000", "GIT_COMMITTER_DATE": f"{old_ts} +0000"},
    )

    outcome = git_checks.check_git_alignment(work, expected_branch="main")
    assert outcome.severity == Severity.BROKEN
    assert outcome.action and "vault-push" in outcome.action


# ---------------------------------------------------------------------------
# 6. Allineamento dei mirror
# ---------------------------------------------------------------------------

def _add_remotes_config(work: Path, mirrors: list[str]) -> None:
    remotes_yaml = work / "03-INFRA" / "agent-universal-layer" / "sync" / "remotes.yaml"
    remotes_yaml.parent.mkdir(parents=True, exist_ok=True)
    mirrors_yaml = ", ".join(mirrors)
    remotes_yaml.write_text(f"authoritative_remote: origin\nmirrors: [{mirrors_yaml}]\n")


def test_mirror_alignment_ok_and_broken(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("KNOWLEDGE_VAULT_REMOTE", raising=False)
    monkeypatch.delenv("KNOWLEDGE_VAULT_MIRRORS", raising=False)
    work, _origin = _init_repo_with_remote(tmp_path)

    mirror_aligned = tmp_path / "mirror-aligned.git"
    mirror_stale = tmp_path / "mirror-stale.git"
    subprocess.run(["git", "init", "--bare", str(mirror_aligned)], check=True, capture_output=True)
    subprocess.run(["git", "init", "--bare", str(mirror_stale)], check=True, capture_output=True)

    _git(work, "remote", "add", "mirror-aligned", str(mirror_aligned))
    _git(work, "remote", "add", "mirror-stale", str(mirror_stale))
    _git(work, "push", "mirror-aligned", "main")
    _git(work, "push", "mirror-stale", "main")

    # Avanza origin e il mirror allineato, ma NON il mirror stale.
    (work / "more.md").write_text("more\n")
    _git(work, "add", "more.md")
    _git(work, "commit", "-m", "more")
    _git(work, "push", "origin", "main")
    _git(work, "push", "mirror-aligned", "main")

    _add_remotes_config(work, ["mirror-aligned", "mirror-stale"])

    outcomes = git_checks.check_mirror_alignment(work, expected_branch="main")
    by_id = {o.id: o for o in outcomes}
    assert by_id["git.mirror_alignment.mirror-aligned"].severity == Severity.OK
    assert by_id["git.mirror_alignment.mirror-stale"].severity == Severity.BROKEN


def test_mirror_alignment_undetermined_when_unreachable(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("KNOWLEDGE_VAULT_REMOTE", raising=False)
    monkeypatch.delenv("KNOWLEDGE_VAULT_MIRRORS", raising=False)
    work, _origin = _init_repo_with_remote(tmp_path)

    _git(work, "remote", "add", "ghost-mirror", str(tmp_path / "does-not-exist.git"))
    _add_remotes_config(work, ["ghost-mirror"])

    outcomes = git_checks.check_mirror_alignment(work, expected_branch="main")
    assert len(outcomes) == 1
    assert outcomes[0].severity == Severity.UNDETERMINED


# ---------------------------------------------------------------------------
# 7. Salute della libreria skill
# ---------------------------------------------------------------------------

_SKILLS_MANIFEST = """
skills:
  vault-skill-a:
    origin: vault
    targets: [claude]
    exposure: manual
  engine-starter:
    origin: engine
    targets: [claude]
    exposure: core
"""


def _setup_skills_vault(tmp_path: Path) -> tuple[Path, Path, Path]:
    vault = tmp_path / "vault"
    home = tmp_path / "home"
    engine_root = tmp_path / "engine"

    manifest = vault / "03-INFRA" / "agent-universal-layer" / "skills" / "skills.manifest.yaml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(_SKILLS_MANIFEST, encoding="utf-8")

    vault_skill = vault / "03-INFRA" / "agent-universal-layer" / "skills" / "vault-skill-a"
    vault_skill.mkdir(parents=True)
    (vault_skill / "SKILL.md").write_text("# vault-skill-a\n")

    engine_skill = engine_root / "agent-universal-layer" / "skills" / "engine-starter"
    engine_skill.mkdir(parents=True)
    (engine_skill / "SKILL.md").write_text("# engine-starter\n")

    return vault, home, engine_root


def test_skills_not_materialized_and_remedy(tmp_path: Path, monkeypatch):
    vault, home, engine_root = _setup_skills_vault(tmp_path)
    monkeypatch.setenv("AGENT_ENGINE_ROOT", str(engine_root))

    outcome = skill_checks.check_skills_not_materialized(vault, home)
    assert outcome.severity == Severity.BROKEN
    assert outcome.remedy is not None
    assert outcome.remedy() is not False

    outcome_after = skill_checks.check_skills_not_materialized(vault, home)
    assert outcome_after.severity == Severity.OK


def test_engine_starter_views_broken_then_fixed(tmp_path: Path, monkeypatch):
    vault, home, engine_root = _setup_skills_vault(tmp_path)
    monkeypatch.setenv("AGENT_ENGINE_ROOT", str(engine_root))

    outcome = skill_checks.check_engine_starter_views(vault, home)
    assert outcome.severity == Severity.BROKEN

    mat = SkillMaterializer(vault_data=vault, home=home, engine_root=engine_root)
    mat.materialize(apply=True)

    outcome_after = skill_checks.check_engine_starter_views(vault, home)
    assert outcome_after.severity == Severity.OK


def test_skills_out_of_manifest_warns_and_never_blocks(tmp_path: Path, monkeypatch):
    """Il disallineamento fuori-manifest non deve bloccare: WARN, non
    BROKEN e nemmeno UNDETERMINED (che comparirebbe sempre nel report
    umano). La sostanza resta: non è un guasto, è qualcosa da adottare."""
    vault, home, engine_root = _setup_skills_vault(tmp_path)
    monkeypatch.setenv("AGENT_ENGINE_ROOT", str(engine_root))

    stray = home / ".agents" / "skill-library" / "stray-skill"
    stray.mkdir(parents=True)
    (stray / "SKILL.md").write_text("# stray\n")

    outcome = skill_checks.check_skills_out_of_manifest(vault, home)
    assert outcome.severity == Severity.WARN
    assert "stray-skill" in outcome.message


def test_skills_out_of_manifest_honours_the_allowlist(tmp_path: Path, monkeypatch):
    """Un fuori-manifest dichiarato legittimo nel manifest stesso esce
    dalla segnalazione, e il check torna OK."""
    vault, home, engine_root = _setup_skills_vault(tmp_path)
    monkeypatch.setenv("AGENT_ENGINE_ROOT", str(engine_root))

    manifest = vault / "03-INFRA" / "agent-universal-layer" / "skills" / "skills.manifest.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8") + "\norphans_allowlist: [stray-skill]\n",
        encoding="utf-8",
    )

    stray = home / ".agents" / "skill-library" / "stray-skill"
    stray.mkdir(parents=True)
    (stray / "SKILL.md").write_text("# stray\n")

    outcome = skill_checks.check_skills_out_of_manifest(vault, home)
    assert outcome.severity == Severity.OK
    assert "stray-skill" not in outcome.message


def test_skill_library_symlinks_flags_broken_entry(tmp_path: Path):
    home = tmp_path / "home"
    library = home / ".agents" / "skill-library"
    library.mkdir(parents=True)

    good = library / "good-skill"
    good.mkdir()
    (good / "SKILL.md").write_text("# good\n")

    dangling = library / "dangling-skill"
    dangling.symlink_to(library / "nowhere")

    outcome = skill_checks.check_skill_library_symlinks(home)
    assert outcome.severity == Severity.BROKEN
    assert "dangling-skill" in outcome.message


def test_skills_manifest_semantics_reuses_validate_manifest(tmp_path: Path):
    vault = tmp_path / "vault"
    home = tmp_path / "home"
    manifest = vault / "03-INFRA" / "agent-universal-layer" / "skills" / "skills.manifest.yaml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        "skills:\n"
        "  broken-github-skill:\n"
        "    origin: github\n"
        "    repo: someone/somewhere\n"
        "    targets: [claude]\n",
        encoding="utf-8",
    )

    outcome = skill_checks.check_skills_manifest_semantics(vault, home)
    assert outcome is not None
    assert outcome.severity == Severity.BROKEN


def test_skills_manifest_semantics_none_when_manifest_missing(tmp_path: Path):
    vault = tmp_path / "vault"
    home = tmp_path / "home"
    assert skill_checks.check_skills_manifest_semantics(vault, home) is None


# ---------------------------------------------------------------------------
# Sanity: il wiring in doctor.py non deve mai far esplodere run_diagnostics.
# ---------------------------------------------------------------------------

def test_doctor_run_diagnostics_wires_new_checks_without_crashing(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("KNOWLEDGE_VAULT_REMOTE", raising=False)
    monkeypatch.delenv("KNOWLEDGE_VAULT_MIRRORS", raising=False)
    monkeypatch.delenv("AGENT_ENGINE_ROOT", raising=False)

    home = tmp_path / "home"
    state_dir = tmp_path / "state"
    vault, _bare = _init_repo_with_remote(tmp_path, name="vault")

    _write_canon(vault)
    _write_mcp_manifest(vault)
    manifest = vault / "03-INFRA" / "agent-universal-layer" / "skills" / "skills.manifest.yaml"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text("skills: {}\n", encoding="utf-8")
    _git(vault, "add", "-A")
    _git(vault, "commit", "-m", "add scaffolding")
    _git(vault, "push", "origin", "main")

    doc = Doctor(vault_data=vault, state_dir=state_dir, home=home)
    report = doc.run_diagnostics(apply_remedies=False)

    ids = {o.id for o in report.outcomes}
    assert "instructions.canonical_present" in ids
    assert "instructions.claude_pointer" in ids
    assert "mcp.rendered_configs" in ids


def test_a_manifest_the_renderer_cannot_resolve_is_broken_not_undetermined(tmp_path):
    """Un template che rompe il render farà fallire l'apply allo stesso modo:
    è un guasto, non ignoranza."""
    vault = tmp_path / "vault"
    home = tmp_path / "home"
    home.mkdir()
    manifest = vault / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        "schema_version: 1\n"
        "servers:\n"
        "  demo-server:\n"
        "    transport: stdio\n"
        "    command: echo\n"
        "    tier: core\n"
        '    args: ["{{ .non_esisto }}"]\n',
        encoding="utf-8",
    )
    (home / ".claude.json").write_text(json.dumps({"mcpServers": {}}))

    outcome = mcp_checks.check_mcp_configs_rendered(vault, home)
    assert outcome.severity == Severity.BROKEN
    assert "cannot be rendered" in outcome.message


def test_import_refuses_dry_run_and_apply_together(sandbox, capsys):
    """Le due bandiere erano indipendenti: con entrambe si scriveva davvero."""
    from types import SimpleNamespace

    from nexgen_core.cli import engine as engine_cli

    code = engine_cli.cmd_import(SimpleNamespace(source="claude", dry_run=True, apply=True))
    assert code == 2
    assert "--dry-run and --apply" in capsys.readouterr().err
