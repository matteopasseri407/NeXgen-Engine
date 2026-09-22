"""Unit test per il seggio opencode nel Council (compatibilita OpenCode 2.0+)."""
from __future__ import annotations

import sys
from pathlib import Path


COUNCIL_DIR = Path(__file__).resolve().parents[2] / "agent-universal-layer" / "council"
if str(COUNCIL_DIR) not in sys.path:
    sys.path.insert(0, str(COUNCIL_DIR))

from seat_process import OPENCODE_ATTACHED_PROMPT, _build_seat_command  # noqa: E402


def test_opencode_build_seat_command_no_dir_flag(tmp_path: Path) -> None:
    seat = {
        "cli": "opencode",
        "model": "opencode-go/muse-spark-1.2-contributor",
    }
    invocation = _build_seat_command(seat, "Test prompt text", tmp_path)
    # OpenCode 2.0+ non supporta il flag --dir nel comando 'run'; usa cwd di processo
    assert "--dir" not in invocation.argv
    assert invocation.cwd == tmp_path
    assert invocation.argv[0:3] == ["opencode", "run", OPENCODE_ATTACHED_PROMPT]
    assert invocation.argv[3:5] == ["-m", "opencode-go/muse-spark-1.2-contributor"]


def test_render_opencode_websearch_containment(tmp_path: Path) -> None:
    import json
    from nexgen_core.renderer import McpRenderer

    home = tmp_path / "home"
    vault = tmp_path / "vault"
    mcp_dir = vault / "03-INFRA" / "agent-universal-layer" / "mcp"
    mcp_dir.mkdir(parents=True)
    manifest = mcp_dir / "manifest.yaml"
    manifest.write_text("servers: {}\n", encoding="utf-8")

    renderer = McpRenderer(vault_data=vault, home=home)
    success, _msg = renderer.render_opencode(write=True)
    assert success is True

    cfg_file = home / ".config" / "opencode" / "opencode.jsonc"
    if not cfg_file.is_file():
        cfg_file = home / ".config" / "opencode" / "opencode.json"
    assert cfg_file.is_file()

    data = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert data.get("websearch") == "parallel"
    assert data.get("tools", {}).get("websearch") is False
