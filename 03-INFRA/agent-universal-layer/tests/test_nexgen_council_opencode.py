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
    assert invocation.argv[invocation.argv.index("-m") + 1] == "opencode-go/muse-spark-1.2-contributor"


def _opencode_argv(tmp_path: Path, **seat_fields) -> list[str]:
    seat = {"cli": "opencode", "model": "opencode-go/some-model", **seat_fields}
    return _build_seat_command(seat, "Test prompt text", tmp_path).argv


def test_an_opencode_seat_always_runs_in_standalone_mode(tmp_path: Path) -> None:
    """Without --standalone, OpenCode 2 `run` talks to the background service: the seat's empty config
    directory would not be the one in force, and the person's own OpenCode would be reached."""
    argv = _opencode_argv(tmp_path)
    assert argv.count("--standalone") == 1
    assert argv.index("--standalone") < argv.index("-m")


def test_opencode_takes_the_effort_inside_the_model_name_not_as_a_flag(tmp_path: Path) -> None:
    """OpenCode 2 has no --variant: the command is refused with its usage text. The variant is written
    provider/model#variant, which the real binary accepts (checked in an isolated home)."""
    argv = _opencode_argv(tmp_path, reasoning_effort="high")
    assert "--variant" not in argv
    assert argv[argv.index("-m") + 1] == "opencode-go/some-model#high"


def test_no_effort_leaves_the_model_name_alone(tmp_path: Path) -> None:
    for fields in ({}, {"reasoning_effort": "none"}, {"reasoning_effort": ""}):
        argv = _opencode_argv(tmp_path, **fields)
        assert argv[argv.index("-m") + 1] == "opencode-go/some-model"


def test_a_model_that_already_names_its_variant_is_not_given_a_second_one(tmp_path: Path) -> None:
    argv = _opencode_argv(tmp_path, model="opencode-go/some-model#max", reasoning_effort="high")
    assert argv[argv.index("-m") + 1] == "opencode-go/some-model#max"


def test_the_effort_is_still_shown_to_the_person(tmp_path: Path) -> None:
    from seat_process import _effort_label

    assert _effort_label({"cli": "opencode", "model": "m", "reasoning_effort": "high"}) == ", effort high"


def test_render_opencode_preserves_native_tool_choices(tmp_path: Path) -> None:
    import json
    from nexgen_core.renderer import McpRenderer

    home = tmp_path / "home"
    vault = tmp_path / "vault"
    mcp_dir = vault / "03-INFRA" / "agent-universal-layer" / "mcp"
    mcp_dir.mkdir(parents=True)
    manifest = mcp_dir / "manifest.yaml"
    manifest.write_text("servers: {}\n", encoding="utf-8")

    cfg_file = home / ".config" / "opencode" / "opencode.json"
    cfg_file.parent.mkdir(parents=True)
    native = {
        "websearch": "parallel",
        "tools": {"websearch": True, "bash": False, "custom_tool": True},
        "permission": {"websearch": "allow"},
    }
    cfg_file.write_text(json.dumps(native), encoding="utf-8")

    renderer = McpRenderer(vault_data=vault, home=home)
    success, _msg = renderer.render_opencode(write=True)
    assert success is True
    data = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert data["websearch"] == native["websearch"]
    assert data["tools"] == native["tools"]
    assert data["permission"] == native["permission"]


def test_render_opencode_does_not_choose_websearch_for_a_new_install(tmp_path: Path) -> None:
    import json
    from nexgen_core.renderer import McpRenderer

    vault = tmp_path / "vault"
    mcp_dir = vault / "03-INFRA" / "agent-universal-layer" / "mcp"
    mcp_dir.mkdir(parents=True)
    (mcp_dir / "manifest.yaml").write_text("servers: {}\n", encoding="utf-8")

    renderer = McpRenderer(vault_data=vault, home=tmp_path / "home")
    assert renderer.render_opencode(write=True)[0]
    cfg_file = renderer.opencode_config_path()
    data = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert "websearch" not in data
    assert "websearch" not in data.get("tools", {})
