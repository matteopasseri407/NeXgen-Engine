"""Unit test per Fase 2: skills.py e renderer.py."""
from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.renderer import McpRenderer
from nexgen_core.skills import SkillMaterializer


def _manifest(vault: Path, body: str) -> Path:
    mcp_dir = vault / "03-INFRA" / "agent-universal-layer" / "mcp"
    mcp_dir.mkdir(parents=True)
    manifest = mcp_dir / "manifest.yaml"
    manifest.write_text(body, encoding="utf-8")
    return manifest


def test_skills_materialization(tmp_path: Path):
    vault = tmp_path / "vault"
    vault_skills = vault / "03-INFRA" / "agent-universal-layer" / "skills"
    vault_skills.mkdir(parents=True)

    # Crea una skill di prova
    sample_skill = vault_skills / "sample-skill"
    sample_skill.mkdir()
    (sample_skill / "SKILL.md").write_text("# Sample Skill", encoding="utf-8")

    # Manifest delle skill
    manifest = vault_skills / "skills.manifest.yaml"
    manifest.write_text("""
schema_version: 1
skills:
  sample-skill:
    origin: vault
    exposure: eager
    description: "Una skill di prova"
""", encoding="utf-8")

    home = tmp_path / "home"
    mat = SkillMaterializer(vault_data=vault, home=home)
    changes, actions = mat.materialize(apply=True)

    assert (home / ".agents" / "skill-library" / "sample-skill").exists()
    assert (home / ".agents" / "skills" / "sample-skill").exists()
    assert (home / ".agents" / "skills" / "INDEX.md").exists()


def test_mcp_renderer_generation(tmp_path: Path):
    vault = tmp_path / "vault"
    vault_mcp = vault / "03-INFRA" / "agent-universal-layer" / "mcp"
    vault_mcp.mkdir(parents=True)

    manifest = vault_mcp / "manifest.yaml"
    manifest.write_text("""
schema_version: 1
servers:
  vault-mcp:
    tier: core
    command: ["python3", "-m", "vault_mcp_server"]
    env:
      VAULT_PATH: "${AGENT_VAULT_DATA}"
    targets: ["claude", "antigravity", "opencode"]
""", encoding="utf-8")

    home = tmp_path / "home"
    renderer = McpRenderer(vault_data=vault, home=home)

    # Render per Claude
    renderer.render_claude(write=True)
    claude_cfg = home / ".claude.json"
    assert claude_cfg.exists()
    claude_data = json.loads(claude_cfg.read_text(encoding="utf-8"))
    assert "vault-mcp" in claude_data["mcpServers"]
    expected_python = "python" if sys.platform == "win32" else "python3"
    assert claude_data["mcpServers"]["vault-mcp"]["command"] == expected_python

    # Render per Antigravity
    renderer.render_antigravity(write=True)
    agy_cfg = home / ".gemini" / "antigravity-ide" / "mcp_config.json"
    assert agy_cfg.exists()
    agy_data = json.loads(agy_cfg.read_text(encoding="utf-8"))
    assert "vault-mcp" in agy_data["mcpServers"]


def test_codex_render_is_additive_like_the_other_clis(tmp_path: Path, monkeypatch):
    """Codex must not lose a server it cannot resolve right now.

    The recurring guard runs without the user's shell environment: a server
    gated on an env var can't be resolved there, and a codex render that
    rebuilt the whole section from scratch deleted it from disk — making the
    interactive doctor report it missing twice an hour, forever.
    """
    monkeypatch.delenv("GATED_SERVER_TOKEN", raising=False)
    vault = tmp_path / "vault"
    _manifest(vault, """
schema_version: 1
servers:
  gated-server:
    enabled: true
    command: ["npx", "-y", "some-package"]
    require_env: GATED_SERVER_TOKEN
    targets: ["codex"]
""")

    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    codex_cfg = home / ".codex" / "config.toml"
    # A previous render (with the env set) left the entry on disk.
    codex_cfg.write_text(
        "[model]\nprovider = \"openai\"\n\n"
        "[mcp_servers.custom-live]\ncommand = \"echo\"\n\n"
        "[mcp_servers.gated_server]\ncommand = \"npx\"\nargs = [\"-y\", \"some-package\"]\n",
        encoding="utf-8",
    )

    renderer = McpRenderer(vault_data=vault, engine_root=tmp_path / "engine", home=home)
    renderer.render_codex(write=True)

    text = codex_cfg.read_text(encoding="utf-8")
    assert "[model]" in text                       # non-MCP sections survive
    assert "[mcp_servers.custom-live]" in text     # additive preservation
    assert "[mcp_servers.gated_server]" in text    # env-gated entry kept, not deleted


def test_codex_render_removes_a_retired_server_but_keeps_the_rest(tmp_path: Path):
    vault = tmp_path / "vault"
    _manifest(vault, """
schema_version: 1
servers:
  keeper:
    tier: core
    command: ["echo", "hi"]
    targets: ["codex"]
retired_servers:
  - old-connector
""")

    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    codex_cfg = home / ".codex" / "config.toml"
    codex_cfg.write_text(
        "[mcp_servers.old_connector]\ncommand = \"fake-old-cmd\"\n\n"
        "[mcp_servers.custom-live]\ncommand = \"echo\"\n",
        encoding="utf-8",
    )

    renderer = McpRenderer(vault_data=vault, engine_root=tmp_path / "engine", home=home)
    renderer.render_codex(write=True)

    text = codex_cfg.read_text(encoding="utf-8")
    assert "old_connector" not in text
    assert "custom-live" in text      # additive preservation still applies
    assert "keeper" in text


def test_render_does_not_rewrite_a_byte_identical_config(tmp_path: Path):
    """A config that already matches is left untouched: no backup, no mtime
    churn. The guard cycle runs twice an hour; rewriting identical files
    every cycle piles up backups and touches live configs for nothing."""
    vault = tmp_path / "vault"
    _manifest(vault, """
schema_version: 1
servers:
  demo:
    command: ["echo", "hi"]
    targets: ["codex"]
""")

    home = tmp_path / "home"
    renderer = McpRenderer(vault_data=vault, engine_root=tmp_path / "engine", home=home)
    renderer.render_codex(write=True)

    codex_cfg = home / ".codex" / "config.toml"
    assert codex_cfg.is_file()
    first_mtime = codex_cfg.stat().st_mtime_ns
    assert not list(codex_cfg.parent.glob(f"{codex_cfg.name}.bak-*"))

    renderer.render_codex(write=True)
    assert codex_cfg.stat().st_mtime_ns == first_mtime
    assert not list(codex_cfg.parent.glob(f"{codex_cfg.name}.bak-*"))


def test_mcp_lazy_tier_filtering(tmp_path: Path):
    vault = tmp_path / "vault"
    _manifest(vault, """
schema_version: 1
retired_servers: []
servers:
  sempre:
    tier: core
    command: echo
    args: ["a"]
  opzionale:
    tier: optional
    command: echo
    args: ["b"]
  opzionale_attivo:
    tier: optional
    enabled: true
    command: echo
    args: ["c"]
  senza_tier:
    command: echo
    args: ["d"]
""")
    renderer = McpRenderer(vault_data=vault, engine_root=Path(__file__).resolve().parents[2], home=tmp_path / "home")
    mounted = renderer.load_resolved_servers("claude")
    assert set(mounted) == {"sempre", "opzionale_attivo"}
    assert renderer.list_lazy_servers("claude") == ["opzionale", "senza_tier"]
