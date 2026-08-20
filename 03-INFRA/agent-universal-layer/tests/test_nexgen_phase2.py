"""Unit test per Fase 2: skills.py e renderer.py."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.renderer import McpRenderer
from nexgen_core.skills import SkillMaterializer


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
    assert claude_data["mcpServers"]["vault-mcp"]["command"] == "python3"

    # Render per Antigravity
    renderer.render_antigravity(write=True)
    agy_cfg = home / ".gemini" / "antigravity-ide" / "mcp_config.json"
    assert agy_cfg.exists()
    agy_data = json.loads(agy_cfg.read_text(encoding="utf-8"))
    assert "vault-mcp" in agy_data["mcpServers"]
