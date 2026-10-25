"""Tests for the three install roles (server, node, standalone) and uv fallback."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from nexgen_core import roles
from nexgen_core.openclaw_service import RunResult


def test_install_sh_contains_uv_user_fallback() -> None:
    root = Path(__file__).resolve().parents[3]
    sh_path = root / "install.sh"
    content = sh_path.read_text(encoding="utf-8")
    assert "UV_NO_CONFIG=1" in content
    assert "uv python" in content
    assert "astral.sh/uv" in content


def test_install_ps1_contains_uv_user_fallback_and_role() -> None:
    root = Path(__file__).resolve().parents[3]
    ps1_path = root / "install.ps1"
    content = ps1_path.read_text(encoding="utf-8")
    assert "UV_NO_CONFIG" in content
    assert "uv python" in content
    assert "-Role" in content or "Role" in content
    assert "Join" in content
    assert "Force" in content


def test_standalone_role_sets_up_local(tmp_path: Path) -> None:
    log_messages = []
    fake_setup = MagicMock(return_value=0)
    fake_align = MagicMock(return_value=(True, "aligned"))
    fake_problems = MagicMock(return_value=(0, []))

    profile_file = tmp_path / "99-INDEX" / "USER-PROFILE.md"
    profile_file.parent.mkdir(parents=True, exist_ok=True)
    profile_file.write_text("- **clis**: `[list of installed CLIs]`\n- **profile**: `[MINIMAL | MULTI]`\n", encoding="utf-8")

    code = roles.run_standalone_role(
        root=tmp_path,
        home=tmp_path / "home",
        setup_fn=fake_setup,
        align_fn=fake_align,
        problems_fn=fake_problems,
        out=log_messages.append,
    )
    assert code == 0
    assert fake_setup.call_count == 1
    call_kwargs = fake_setup.call_args.kwargs
    assert call_kwargs.get("server") is False
    assert call_kwargs.get("yes") is True

    # Check that standalone profile writes 'openclaw' to CLIs
    profile_content = profile_file.read_text(encoding="utf-8")
    assert "openclaw" in profile_content


def test_server_role_invokes_setup_server(tmp_path: Path) -> None:
    log_messages = []
    fake_setup = MagicMock(return_value=0)
    fake_align = MagicMock(return_value=(True, "aligned"))
    fake_problems = MagicMock(return_value=(0, []))

    code = roles.run_server_role(
        root=tmp_path,
        home=tmp_path / "home",
        setup_fn=fake_setup,
        align_fn=fake_align,
        problems_fn=fake_problems,
        out=log_messages.append,
    )
    assert code == 0
    assert fake_setup.call_count == 1
    call_kwargs = fake_setup.call_args.kwargs
    assert call_kwargs.get("server") is True
    assert call_kwargs.get("yes") is True


def test_node_role_connects_service_and_prints_squad_instruction(tmp_path: Path) -> None:
    log_messages = []
    fake_align = MagicMock(return_value=(True, "aligned"))
    fake_runner = MagicMock()
    fake_runner.return_value = RunResult(0, '{"connected": true}\n', "")

    code = roles.run_node_role(
        root=tmp_path,
        home=tmp_path / "home",
        target="https://gateway.tailnet:18789/j/sample-join-code-123",
        node_name="test-node",
        align_fn=fake_align,
        runner=fake_runner,
        wait_probe_fn=lambda: True,
        out=log_messages.append,
    )
    assert code == 0
    # Verifies runner called connect with --target-file
    assert fake_runner.called
    connect_calls = [c[0][0] for c in fake_runner.call_args_list if c[0] and c[0][0][:2] == ["openclaw", "connect"]]
    assert connect_calls, f"Expected openclaw connect call, got {fake_runner.call_args_list}"
    cmd = connect_calls[0]
    assert cmd[0:4] == ["openclaw", "connect", "--service", "--session-host"]
    assert "--target-file" in cmd

    # Verifies squad instruction is printed at the end
    output = "\n".join(log_messages)
    assert "host: node" in output
    assert "node: test-node" in output


def test_node_role_without_target_shows_join_instructions(tmp_path: Path) -> None:
    log_messages = []
    fake_align = MagicMock(return_value=(True, "aligned"))
    clean_status = '{"service": {"loaded": false, "command": null, "runtime": {"status": "stopped"}}}'
    fake_runner = MagicMock(return_value=RunResult(0, clean_status, ""))

    code = roles.run_node_role(
        root=tmp_path,
        home=tmp_path / "home",
        target=None,
        node_name="test-node",
        runner=fake_runner,
        align_fn=fake_align,
        out=log_messages.append,
    )
    assert code == 0
    output = "\n".join(log_messages)
    assert "openclaw devices join-code" in output
    assert "openclaw connect" in output
    assert "host: node" in output


def test_node_role_default_timeout_is_300s(tmp_path: Path, monkeypatch) -> None:
    from nexgen_core.openclaw.node_attach import DEFAULT_NODE_ATTACH_TIMEOUT_S

    assert DEFAULT_NODE_ATTACH_TIMEOUT_S == 300.0

    captured_timeout = None

    def fake_wait(probe_fn, node_name="node", timeout_s=300.0, **kwargs):
        nonlocal captured_timeout
        captured_timeout = timeout_s
        return True, 0.0

    monkeypatch.setattr("nexgen_core.openclaw.node_attach.wait_for_node_connection", fake_wait)
    monkeypatch.setattr("nexgen_core.openclaw.node_attach.connect_node_service", lambda *args, **kwargs: True)

    clean_status = '{"service": {"loaded": false, "command": null, "runtime": {"status": "stopped"}}}'
    fake_runner = MagicMock(return_value=RunResult(0, clean_status, ""))

    code = roles.run_node_role(
        root=tmp_path,
        home=tmp_path / "home",
        target="https://gateway.tailnet:18789/j/sample-join-code-123",
        runner=fake_runner,
        align_fn=lambda: (True, "ok"),
        out=lambda _: None,
    )
    assert code == 0
    assert captured_timeout == 300.0


def test_roles_idempotency(tmp_path: Path) -> None:
    fake_setup = MagicMock(return_value=0)
    fake_align = MagicMock(return_value=(True, "aligned"))
    fake_problems = MagicMock(return_value=(0, []))

    code1 = roles.run_standalone_role(
        root=tmp_path,
        home=tmp_path / "home",
        setup_fn=fake_setup,
        align_fn=fake_align,
        problems_fn=fake_problems,
        out=lambda _: None,
    )
    code2 = roles.run_standalone_role(
        root=tmp_path,
        home=tmp_path / "home",
        setup_fn=fake_setup,
        align_fn=fake_align,
        problems_fn=fake_problems,
        out=lambda _: None,
    )
    assert code1 == 0 and code2 == 0
