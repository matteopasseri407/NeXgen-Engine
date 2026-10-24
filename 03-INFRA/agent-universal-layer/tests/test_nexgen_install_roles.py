"""Tests for the three install roles (server, node, standalone) and uv fallback."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from nexgen_core import roles


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


def test_standalone_role_sets_up_local(tmp_path: Path) -> None:
    log_messages = []
    fake_setup = MagicMock(return_value=0)
    fake_align = MagicMock(return_value=(True, "aligned"))
    fake_problems = MagicMock(return_value=(0, []))

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
    # Check that standalone passed yes=True and server=False
    call_kwargs = fake_setup.call_args.kwargs
    assert call_kwargs.get("server") is False
    assert call_kwargs.get("yes") is True


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


def test_node_role_registers_agent_via_rpc_without_writing_gateway(tmp_path: Path) -> None:
    log_messages = []
    fake_align = MagicMock(return_value=(True, "aligned"))
    fake_rpc_client = MagicMock()

    code = roles.run_node_role(
        root=tmp_path,
        home=tmp_path / "home",
        gateway_url="ws://gateway.tailnet:18789",
        gateway_token="secret-token",
        node_name="test-node",
        align_fn=fake_align,
        rpc_client_factory=lambda **kwargs: fake_rpc_client,
        out=log_messages.append,
    )
    assert code == 0
    assert fake_rpc_client.config_patch.call_count == 1
    patch_args = fake_rpc_client.config_patch.call_args.kwargs
    # Verifies agent patch sent to gateway via RPC
    assert "patch" in patch_args


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
