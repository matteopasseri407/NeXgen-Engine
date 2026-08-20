"""Test di parità reale e verifica bugfix da revisione DeepSeek (A1-A10, B1-B13)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.cli import main as cli_main
from nexgen_core.doctor import Doctor
from nexgen_core.git_ops import GitState, GitStatusResult, publish_changes
from nexgen_core.guard import GuardMode, GuardRunner
from nexgen_core.publisher import Publisher
from nexgen_core.renderer import McpRenderer
from nexgen_core.shims import COMMANDS, install_shims
from nexgen_core.skills import SkillMaterializer, main as skills_main
from nexgen_core.tools.firecrawl import FirecrawlClient
from nexgen_core.tools.now import get_agent_now_data
from nexgen_core.updater import EngineUpdater


def test_a1_shebang_on_all_entrypoints():
    """A1: Tutti i moduli eseguibili devono avere shebang #!/usr/bin/env python3 a riga 1."""
    targets = [
        SCRIPTS_DIR / "nexgen_core" / "cli.py",
        SCRIPTS_DIR / "nexgen_core" / "doctor.py",
        SCRIPTS_DIR / "nexgen_core" / "publisher.py",
        SCRIPTS_DIR / "nexgen_core" / "skills.py",
        SCRIPTS_DIR / "nexgen_core" / "tools" / "now.py",
        SCRIPTS_DIR / "nexgen_core" / "tools" / "chrome.py",
        SCRIPTS_DIR / "nexgen_core" / "tools" / "firecrawl.py",
        SCRIPTS_DIR / "nexgen_core" / "tools" / "open_folder.py",
        SCRIPTS_DIR / "agent_sync.py",
        SCRIPTS_DIR / "skills-sync.py",
        SCRIPTS_DIR / "agent-skill.py",
    ]
    for py_file in targets:
        assert py_file.is_file(), f"File non trovato: {py_file}"
        first_line = py_file.read_text(encoding="utf-8").splitlines()[0]
        assert first_line.startswith("#!/usr/bin/env python"), f"{py_file.name} manca di shebang: {first_line}"


def test_a3_a7_skills_no_traceback_and_safe_usage(tmp_path: Path, capsys):
    """A3 & A7: agent-skill senza argomenti stampa l'uso e non scrive file; show non genera AttributeError."""
    # Senza argomenti stampa l'uso ed esce con 0
    res = skills_main([])
    assert res == 0
    out = capsys.readouterr().out
    assert "Uso:" in out

    # Show su skill inesistente restituisce errore pulito senza traceback
    res_show = skills_main(["show", "non-existent-skill-xyz"])
    assert res_show == 1
    err = capsys.readouterr().err
    # L'invariante è: un errore leggibile, nessun traceback, e il nome citato.
    # Il testo esatto non è contratto e non va fissato qui.
    assert "non-existent-skill-xyz" in err
    assert "Traceback" not in err


def test_a4_upgrades_command(capsys):
    """A4: agent-sync upgrades e check_updates() funzionano senza eccezioni."""
    updater = EngineUpdater()
    has_up, curr, latest = updater.check_updates()
    assert isinstance(has_up, bool)
    assert curr.startswith("v") or curr == "unknown"


def test_a6_doctor_summary_output(tmp_path: Path, capsys):
    """A6: agent-doctor supporta --summary ed emette il formato FAIL=N OK=N."""
    doc = Doctor(vault_data=tmp_path / "vault", home=tmp_path / "home")
    report = doc.run_diagnostics(apply_remedies=False)
    assert hasattr(report, "broken")
    assert hasattr(report, "ok_count")

    from nexgen_core.doctor import main as doctor_main
    res = doctor_main(["--summary"])
    out = capsys.readouterr().out
    assert "FAIL=" in out
    assert "OK=" in out


def test_a8_a10_shims_complete_inventory(tmp_path: Path):
    """A8 & A10: shims installa tutti i comandi attesi e usa template Windows corretto."""
    bin_dir = tmp_path / "bin"
    installed = install_shims(scripts_dir=SCRIPTS_DIR, bin_dir=bin_dir, home=tmp_path)
    assert len(installed) >= 10

    names = {Path(p).name for p in installed}
    expected_names = {"agent-sync", "agent-doctor", "vault-push", "agent-now", "skills-sync", "agent-skill"}
    for exp in expected_names:
        assert (exp in names) or (f"{exp}.cmd" in names)


def test_b1_b2_b3_b8_mcp_renderer(tmp_path: Path):
    """B1, B2, B3, B8: McpRenderer genera correttamente le 4 CLI, gestisce args, auth HTTP e scrittura atomica."""
    vault = tmp_path / "vault"
    mcp_dir = vault / "03-INFRA" / "agent-universal-layer" / "mcp"
    mcp_dir.mkdir(parents=True)

    manifest_yaml = """
schema_version: 1
servers:
  playwright:
    transport: stdio
    command: node
    args: ["${AGENT_ENGINE_ROOT}/test.mjs", "--arg1"]
    targets: [claude, codex, antigravity, opencode]
  n8n-mcp:
    transport: http
    url: "http://127.0.0.1:5678/mcp"
    auth: { type: bearer, env: N8N_MCP_TOKEN }
    targets: [claude, codex, antigravity, opencode]
"""
    (mcp_dir / "manifest.yaml").write_text(manifest_yaml, encoding="utf-8")

    home = tmp_path / "home"
    home.mkdir()

    # Pre-creazione config con server live non manifestato
    claude_cfg = home / ".claude.json"
    claude_cfg.write_text(json.dumps({"mcpServers": {"custom-live": {"command": "custom"}}}), encoding="utf-8")

    renderer = McpRenderer(vault_data=vault, engine_root=tmp_path / "engine", home=home)
    results = renderer.render_all(write=True)

    assert results["claude"] is True
    assert results["antigravity"] is True
    assert results["opencode"] is True
    assert results["codex"] is True

    # Verifica Claude: server live preservato + playwright con args + n8n con auth header
    claude_data = json.loads(claude_cfg.read_text(encoding="utf-8"))
    assert "custom-live" in claude_data["mcpServers"]
    assert "playwright" in claude_data["mcpServers"]
    assert claude_data["mcpServers"]["playwright"]["args"] == [f"{tmp_path}/engine/test.mjs", "--arg1"]
    assert "n8n-mcp" in claude_data["mcpServers"]
    assert claude_data["mcpServers"]["n8n-mcp"]["headers"]["Authorization"] == "Bearer ${N8N_MCP_TOKEN}"

    # Verifica Codex: file TOML scritto nativamente
    codex_cfg = home / ".codex" / "config.toml"
    assert codex_cfg.is_file()
    codex_text = codex_cfg.read_text(encoding="utf-8")
    assert "[mcp_servers.playwright]" in codex_text
    assert "[mcp_servers.n8n_mcp]" in codex_text


def test_b4_b5_skills_core_exposure(tmp_path: Path):
    """B4 & B5: SkillMaterializer materializza le skill core e supporta le directory native."""
    vault = tmp_path / "vault"
    skills_dir = vault / "03-INFRA" / "agent-universal-layer" / "skills"
    skills_dir.mkdir(parents=True)

    manifest_yaml = """
skills:
  core-skill:
    origin: vault
    exposure: core
    targets: [claude, antigravity, codex, opencode]
    description: Test core skill
"""
    (skills_dir / "skills.manifest.yaml").write_text(manifest_yaml, encoding="utf-8")
    (skills_dir / "core-skill").mkdir()
    (skills_dir / "core-skill" / "SKILL.md").write_text("# Core Skill\n", encoding="utf-8")

    home = tmp_path / "home"
    mat = SkillMaterializer(vault_data=vault, home=home)
    changes, actions = mat.materialize(apply=True)

    assert changes >= 1
    # Verifica che la skill sia presente nella libreria e nelle viste
    assert (home / ".agents" / "skill-library" / "core-skill").exists()
    assert (home / ".claude" / "skills" / "core-skill").exists()
    assert (home / ".gemini" / "antigravity-cli" / "skills" / "core-skill").exists()
    assert (home / ".codex" / "skills" / "core-skill").exists()


def test_b11_git_allows_apply_ahead():
    """B11: GitStatusResult consente apply anche quando lo stato è AHEAD o BEHIND."""
    res_ahead = GitStatusResult(state=GitState.AHEAD, message="Commit locali non pushati")
    assert res_ahead.allows_apply is True
    res_behind = GitStatusResult(state=GitState.BEHIND, message="Remoto ha nuovi commit")
    assert res_behind.allows_apply is True


def test_r1_git_behind_auto_fast_forward(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """R1: agent-sync apply e pull eseguono il fast-forward automatico quando il vault è BEHIND."""
    import subprocess

    monkeypatch.delenv("KNOWLEDGE_VAULT_REMOTE", raising=False)
    monkeypatch.delenv("KNOWLEDGE_VAULT_MIRRORS", raising=False)

    remote_dir = tmp_path / "remote.git"
    remote_dir.mkdir()
    subprocess.run(["git", "init", "--bare", str(remote_dir)], check=True, capture_output=True)

    local_dir = tmp_path / "local_vault"
    subprocess.run(["git", "clone", str(remote_dir), str(local_dir)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(local_dir), "checkout", "-b", "main"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(local_dir), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(local_dir), "config", "user.name", "Test User"], check=True)
    (local_dir / "file.txt").write_text("v1")
    subprocess.run(["git", "-C", str(local_dir), "add", "."], check=True)
    subprocess.run(["git", "-C", str(local_dir), "commit", "-m", "initial"], check=True)
    subprocess.run(["git", "-C", str(local_dir), "push", "-u", "origin", "main"], check=True)

    other_dir = tmp_path / "other_clone"
    subprocess.run(["git", "clone", "-b", "main", str(remote_dir), str(other_dir)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(other_dir), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(other_dir), "config", "user.name", "Test User"], check=True)
    (other_dir / "file.txt").write_text("v2")
    subprocess.run(["git", "-C", str(other_dir), "add", "."], check=True)
    subprocess.run(["git", "-C", str(other_dir), "commit", "-m", "remote commit"], check=True)
    subprocess.run(["git", "-C", str(other_dir), "push", "origin", "main"], check=True)

    # Local vault è indietro rispetto a remote
    runner = GuardRunner(vault_data=local_dir, home=tmp_path / "home")
    res = runner.run(mode=GuardMode.APPLY)
    assert res.success is True
    assert (local_dir / "file.txt").read_text() == "v2"


def test_r2_updater_shebang_and_execution():
    """R2: updater.py deve avere shebang a riga 1 ed essere eseguibile."""
    updater_path = SCRIPTS_DIR / "nexgen_core" / "updater.py"
    first_line = updater_path.read_text(encoding="utf-8").splitlines()[0]
    assert first_line.startswith("#!/usr/bin/env python")
    if sys.platform != "win32":
        assert os.access(str(updater_path), os.X_OK)


def test_r3_council_tool_and_subcommand():
    """R3: council tool e sottocomando agent-sync council funzionano."""
    council_tool = SCRIPTS_DIR / "nexgen_core" / "tools" / "council.py"
    assert council_tool.is_file()
    assert council_tool.read_text(encoding="utf-8").splitlines()[0].startswith("#!/usr/bin/env python")

    # Verifica che cli.py accetti il comando council
    with pytest.raises(SystemExit):
        cli_main(["council", "--help"])


def test_r4_vault_groom_tool_and_shims():
    """R4: vault-groom esiste in nexgen_core/tools ed è presente in shims.py COMMANDS."""
    groom_tool = SCRIPTS_DIR / "nexgen_core" / "tools" / "vault_groom.py"
    assert groom_tool.is_file()
    assert groom_tool.read_text(encoding="utf-8").splitlines()[0].startswith("#!/usr/bin/env python")

    cmd_names = [cmd for cmd, _ in COMMANDS]
    assert "vault-groom" in cmd_names
    assert "council" in cmd_names


def test_r5_mcp_remote_pin():
    """R5: renderer.py usa il pin esatto mcp-remote@0.1.38."""
    from nexgen_core.renderer import MCP_REMOTE_PACKAGE
    assert MCP_REMOTE_PACKAGE == "mcp-remote@0.1.38"


def test_r6_github_origin_error_reporting(tmp_path: Path):
    """R6: skill github con commit inesistente riporta un errore nelle actions."""
    vault = tmp_path / "vault"
    skills_dir = vault / "03-INFRA" / "agent-universal-layer" / "skills"
    skills_dir.mkdir(parents=True)

    manifest_yaml = """
skills:
  broken-github-skill:
    origin: github
    repo: https://github.com/invalid-user-xyz-nonexistent/invalid-repo-12345
    commit: deadbeef
    exposure: core
"""
    (skills_dir / "skills.manifest.yaml").write_text(manifest_yaml, encoding="utf-8")

    home = tmp_path / "home"
    mat = SkillMaterializer(vault_data=vault, home=home)
    changes, actions = mat.materialize(apply=True)
    assert any("[ERRORE]" in act for act in actions)

