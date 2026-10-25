"""Tests for node role transition on existing machines.

Covers:
(a) Engine installed + local npm gateway active on Windows with old scheduled task and vbs;
(b) Same on Linux with systemd user unit;
(c) Engine installed without OpenClaw;
(d) Already node of the same gateway;
(e) Already node of another gateway (stops and asks confirmation with flag);
(f) Return to standalone.

All commands (openclaw, systemctl, schtasks, nexgen) are replaced by recorders.
No real processes or files outside tmp_path are touched.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import roles  # noqa: E402
from nexgen_core.openclaw.node_transition import (  # noqa: E402
    EngineVersionInfo,
    ExistingNodeInfo,
    LocalGatewayInfo,
    TransitionInspection,
    normalize_gateway_origin,
    plan_node_transition,
)
from nexgen_core.openclaw_service import RunResult  # noqa: E402


class RecordingRunner:
    """Fake command runner that records invocations and returns canned responses."""

    def __init__(self, default_code: int = 0, default_stdout: str = "", default_stderr: str = ""):
        self.calls: list[list[str]] = []
        self.default_code = default_code
        self.default_stdout = default_stdout
        self.default_stderr = default_stderr
        self.canned: dict[tuple[str, ...], RunResult] = {}

    def set_canned(self, argv: list[str], code: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.canned[tuple(argv)] = RunResult(code, stdout, stderr)

    def __call__(self, argv: list[str]) -> RunResult:
        self.calls.append(list(argv))
        key = tuple(argv)
        if key in self.canned:
            return self.canned[key]
        for canned_key, result in self.canned.items():
            if len(canned_key) == len(argv) and all(ck == "*" or ck == a for ck, a in zip(canned_key, argv)):
                return result
        return RunResult(self.default_code, self.default_stdout, self.default_stderr)


def _init_mock_engine_repo(repo_path: Path, version: str = "2026.10.23") -> Path:
    """Creates minimal scaffold for an engine repository."""
    repo_path.mkdir(parents=True, exist_ok=True)
    for d in ("01-NOTES", "02-PROJECTS", "04-NOW", "99-INDEX", "99-SECRETS"):
        (repo_path / d).mkdir(parents=True, exist_ok=True)
    (repo_path / "VERSION").write_text(f"{version}\n", encoding="utf-8")
    (repo_path / "00-START-HERE.md").write_text("# Start\n", encoding="utf-8")
    (repo_path / "INIT.md").write_text("# Init\n", encoding="utf-8")
    idx = repo_path / "99-INDEX"
    (idx / "USER-PROFILE.md").write_text("- **clis**: `[openclaw, claude]`\n- **profile**: `MULTI`\n", encoding="utf-8")
    sync_dir = repo_path / "03-INFRA" / "agent-universal-layer" / "sync"
    sync_dir.mkdir(parents=True, exist_ok=True)
    (sync_dir / "remotes.yaml").write_text("remotes: {}\n", encoding="utf-8")
    skills_dir = repo_path / "03-INFRA" / "agent-universal-layer" / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    (skills_dir / "skills.manifest.yaml").write_text("skills: {}\n", encoding="utf-8")
    instr_dir = repo_path / "03-INFRA" / "agent-universal-layer" / "instructions"
    instr_dir.mkdir(parents=True, exist_ok=True)
    (instr_dir / "AGENTS.md").write_text("# Agents\n", encoding="utf-8")
    scripts = repo_path / "03-INFRA" / "scripts" / "nexgen_core"
    scripts.mkdir(parents=True, exist_ok=True)
    return repo_path


def test_normalize_gateway_origin() -> None:
    assert normalize_gateway_origin("https://gateway.example.com:18789/j/code123") == "gateway.example.com:18789"
    assert normalize_gateway_origin("http://127.0.0.1:18789") == "127.0.0.1:18789"
    assert normalize_gateway_origin("gateway.tailnet:18789") == "gateway.tailnet:18789"
    assert normalize_gateway_origin("gateway.tailnet") == "gateway.tailnet"


def test_plan_node_transition_formatting() -> None:
    inspection = TransitionInspection(
        engine=EngineVersionInfo(installed_version="2026.10.23", target_version="2026.10.24"),
        gateway=LocalGatewayInfo(
            present=True,
            mechanism="schtasks",
            service_active=True,
            scheduled_task_present=True,
            startup_vbs_present=True,
            companion_present=True,
            companion_detail="Store app detected",
            state_dir=Path("/mock/.openclaw"),
            state_dir_exists=True,
        ),
        node=ExistingNodeInfo(
            is_node=False,
            connected=False,
            gateway_url=None,
            gateway_origin=None,
            node_name=None,
        ),
    )
    lines = plan_node_transition(inspection)
    plan_text = "\n".join(lines)
    assert "2026.10.23" in plan_text
    assert "2026.10.24" in plan_text
    assert "Gateway OpenClaw locale: presente" in plan_text
    assert "Companion" in plan_text
    assert "Cosa verrà fermato" in plan_text
    assert "Cosa resta com'è" in plan_text
    assert "~/.openclaw" in plan_text


def _setup_windows_gateway_env(tmp_path: Path) -> tuple[Path, Path, Path, Path, Path, Path, RecordingRunner]:
    """Helper to prepare Windows environment with scheduled task and VBS."""
    engine_root = _init_mock_engine_repo(tmp_path / "engine", version="2026.10.23")
    home = tmp_path / "home"
    appdata = home / "AppData" / "Roaming"
    startup_dir = appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    startup_dir.mkdir(parents=True, exist_ok=True)
    vbs_file = startup_dir / "OpenClaw Gateway.vbs"
    vbs_file.write_text("' mock vbs\n", encoding="utf-8")
    state_dir = home / ".openclaw"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "openclaw.json").write_text('{"mock": true}\n', encoding="utf-8")
    runner = RecordingRunner(default_code=0)
    runner.set_canned(["schtasks.exe", "/Query", "/TN", "OpenClaw Gateway"], code=0, stdout="SUCCESS")
    runner.set_canned(["openclaw", "node", "status", "--json"], code=1, stdout="")
    return engine_root, home, appdata, startup_dir, vbs_file, state_dir, runner


def test_case_a_windows_gateway_check_mode(tmp_path: Path) -> None:
    """Case (a) part 1: Windows check mode dry run prints plan without writes."""
    engine_root, home, appdata, startup_dir, vbs_file, _, runner = _setup_windows_gateway_env(tmp_path)
    log_messages: list[str] = []
    fake_update = MagicMock(return_value=0)
    fake_align = MagicMock(return_value=(True, "aligned"))

    check_code = roles.run_node_role(
        root=engine_root,
        home=home,
        check=True,
        platform="win32",
        appdata=appdata,
        startup_dir=startup_dir,
        runner=runner,
        align_fn=fake_align,
        update_fn=fake_update,
        out=log_messages.append,
    )
    assert check_code == 0
    assert fake_update.call_count == 0
    assert fake_align.call_count == 0
    assert vbs_file.exists()
    check_output = "\n".join(log_messages)
    assert "Piano" in check_output or "piano" in check_output.lower()
    assert "2026.10.23" in check_output
    assert "Gateway OpenClaw locale" in check_output
    assert "Cosa verrà fermato" in check_output


def test_case_a_windows_gateway_transition_execute(tmp_path: Path) -> None:
    """Case (a) part 2: Windows gateway teardown and node service attachment."""
    engine_root, home, appdata, startup_dir, vbs_file, state_dir, runner = _setup_windows_gateway_env(tmp_path)
    log_messages: list[str] = []
    fake_update = MagicMock(return_value=0)
    fake_align = MagicMock(return_value=(True, "aligned"))
    join_target = "https://server.tailnet:18789/j/secret-join-code-123"

    run_code = roles.run_node_role(
        root=engine_root,
        home=home,
        join=join_target,
        platform="win32",
        appdata=appdata,
        startup_dir=startup_dir,
        runner=runner,
        align_fn=fake_align,
        update_fn=fake_update,
        wait_probe_fn=lambda: True,
        out=log_messages.append,
    )
    assert run_code == 0
    assert fake_update.call_count == 1
    assert fake_align.call_count == 1
    recorded_cmds = runner.calls
    assert ["openclaw", "gateway", "stop"] in recorded_cmds
    assert ["openclaw", "gateway", "uninstall"] in recorded_cmds
    assert ["schtasks.exe", "/Delete", "/TN", "OpenClaw Gateway", "/F"] in recorded_cmds
    assert not vbs_file.exists()
    assert state_dir.exists()
    assert (state_dir / "openclaw.json").exists()
    for cmd in recorded_cmds:
        assert "taskkill" not in cmd[0].lower()
    full_output = "\n".join(log_messages)
    assert "secret-join-code-123" not in full_output
    assert "Riepilogo" in full_output or "riepilogo" in full_output.lower()
    assert "server.tailnet:18789" in full_output


def test_case_b_linux_systemd_gateway_transition(tmp_path: Path) -> None:
    """Case (b): Engine installed + Linux systemd user service."""
    engine_root = _init_mock_engine_repo(tmp_path / "engine", version="2026.10.23")
    home = tmp_path / "home"
    systemd_user_dir = home / ".config" / "systemd" / "user"
    systemd_user_dir.mkdir(parents=True, exist_ok=True)
    unit_file = systemd_user_dir / "openclaw-gateway.service"
    unit_file.write_text("[Unit]\nDescription=Mock\n", encoding="utf-8")
    state_dir = home / ".openclaw"
    state_dir.mkdir(parents=True, exist_ok=True)

    runner = RecordingRunner(default_code=0)
    runner.set_canned(["openclaw", "node", "status", "--json"], code=1, stdout="")
    log_messages: list[str] = []
    fake_update = MagicMock(return_value=0)
    fake_align = MagicMock(return_value=(True, "aligned"))

    run_code = roles.run_node_role(
        root=engine_root,
        home=home,
        join="https://server.tailnet:18789/j/secret-join-456",
        platform="linux",
        systemd_user_dir=systemd_user_dir,
        runner=runner,
        align_fn=fake_align,
        update_fn=fake_update,
        wait_probe_fn=lambda: True,
        out=log_messages.append,
    )
    assert run_code == 0
    assert fake_update.call_count == 1
    assert ["openclaw", "gateway", "stop"] in runner.calls
    assert ["openclaw", "gateway", "uninstall"] in runner.calls
    assert state_dir.exists()
    for cmd in runner.calls:
        assert "taskkill" not in cmd[0].lower()


def test_case_c_clean_machine_without_openclaw(tmp_path: Path) -> None:
    """Case (c): Engine installed, but no pre-existing OpenClaw gateway."""
    engine_root = _init_mock_engine_repo(tmp_path / "engine", version="2026.10.24")
    home = tmp_path / "home"
    runner = RecordingRunner(default_code=0)
    runner.set_canned(["schtasks.exe", "/Query", "/TN", "OpenClaw Gateway"], code=1, stdout="")
    runner.set_canned(["openclaw", "node", "status", "--json"], code=1, stdout="")
    log_messages: list[str] = []
    fake_align = MagicMock(return_value=(True, "aligned"))

    run_code = roles.run_node_role(
        root=engine_root,
        home=home,
        join="https://server.tailnet:18789/j/secret-join-789",
        platform="win32",
        runner=runner,
        align_fn=fake_align,
        wait_probe_fn=lambda: True,
        out=log_messages.append,
    )
    assert run_code == 0
    assert ["openclaw", "gateway", "stop"] not in runner.calls
    assert ["openclaw", "gateway", "uninstall"] not in runner.calls
    assert any("connect" in cmd for cmd in runner.calls)


def test_case_d_already_node_of_same_gateway(tmp_path: Path) -> None:
    """Case (d): Machine is already a node of the same gateway; only sync runs."""
    engine_root = _init_mock_engine_repo(tmp_path / "engine", version="2026.10.24")
    home = tmp_path / "home"
    runner = RecordingRunner(default_code=0)
    status_json = '{"connected": true, "gateway": "https://server.tailnet:18789"}'
    runner.set_canned(["openclaw", "node", "status", "--json"], code=0, stdout=status_json)
    log_messages: list[str] = []
    fake_align = MagicMock(return_value=(True, "aligned"))

    run_code = roles.run_node_role(
        root=engine_root,
        home=home,
        join="https://server.tailnet:18789/j/another-join-token",
        runner=runner,
        align_fn=fake_align,
        out=log_messages.append,
    )
    assert run_code == 0
    assert fake_align.call_count == 1
    for cmd in runner.calls:
        assert "connect" not in cmd
    full_output = "\n".join(log_messages)
    assert "già nodo" in full_output.lower()
    assert "server.tailnet:18789" in full_output


def test_case_e_already_node_of_different_gateway(tmp_path: Path) -> None:
    """Case (e): Already node of another gateway; stops and asks for --force."""
    engine_root = _init_mock_engine_repo(tmp_path / "engine", version="2026.10.24")
    home = tmp_path / "home"
    runner = RecordingRunner(default_code=0)
    status_json = '{"connected": true, "gateway": "https://old-server.tailnet:18789"}'
    runner.set_canned(["openclaw", "node", "status", "--json"], code=0, stdout=status_json)
    log_messages: list[str] = []
    fake_align = MagicMock(return_value=(True, "aligned"))

    run_code = roles.run_node_role(
        root=engine_root,
        home=home,
        join="https://new-server.tailnet:18789/j/new-token",
        force=False,
        runner=runner,
        align_fn=fake_align,
        out=log_messages.append,
    )
    assert run_code != 0
    full_output = "\n".join(log_messages)
    assert "--force" in full_output
    assert "old-server.tailnet:18789" in full_output
    assert fake_align.call_count == 0

    log_messages.clear()
    force_code = roles.run_node_role(
        root=engine_root,
        home=home,
        join="https://new-server.tailnet:18789/j/new-token",
        force=True,
        runner=runner,
        align_fn=fake_align,
        wait_probe_fn=lambda: True,
        out=log_messages.append,
    )
    assert force_code == 0
    assert fake_align.call_count == 1
    assert any("connect" in cmd for cmd in runner.calls)


def test_case_f_return_to_standalone(tmp_path: Path) -> None:
    """Case (f): Returning from node to standalone re-activates local gateway."""
    engine_root = _init_mock_engine_repo(tmp_path / "engine", version="2026.10.24")
    home = tmp_path / "home"
    state_dir = home / ".openclaw"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "openclaw.json").write_text('{"preserved_config": true}\n', encoding="utf-8")

    runner = RecordingRunner(default_code=0)
    log_messages: list[str] = []
    fake_setup = MagicMock(return_value=0)
    fake_align = MagicMock(return_value=(True, "aligned"))
    fake_problems = MagicMock(return_value=(0, []))

    run_code = roles.run_standalone_role(
        root=engine_root,
        home=home,
        runner=runner,
        setup_fn=fake_setup,
        align_fn=fake_align,
        problems_fn=fake_problems,
        out=log_messages.append,
    )
    assert run_code == 0
    assert ["openclaw", "node", "stop"] in runner.calls
    assert ["openclaw", "node", "uninstall"] in runner.calls
    assert fake_setup.call_count == 1
    assert (state_dir / "openclaw.json").exists()
    profile = (engine_root / "99-INDEX" / "USER-PROFILE.md").read_text(encoding="utf-8")
    assert "openclaw" in profile


def test_no_link_stops_before_modifying_anything(tmp_path: Path) -> None:
    """Without join link, stops before touching anything and prints instructions."""
    engine_root = _init_mock_engine_repo(tmp_path / "engine", version="2026.10.24")
    home = tmp_path / "home"
    runner = RecordingRunner(default_code=0)
    runner.set_canned(["openclaw", "node", "status", "--json"], code=1, stdout="")

    log_messages: list[str] = []
    fake_align = MagicMock(return_value=(True, "aligned"))

    roles.run_node_role(
        root=engine_root,
        home=home,
        join=None,
        target=None,
        runner=runner,
        align_fn=fake_align,
        out=log_messages.append,
    )
    assert fake_align.call_count == 0
    output = "\n".join(log_messages)
    assert "openclaw devices join-code" in output


def test_bootstrap_dispatches_role_node_check(tmp_path: Path, monkeypatch) -> None:
    engine_root = _init_mock_engine_repo(tmp_path / "engine", version="2026.10.24")
    from nexgen_core import bootstrap

    called: dict = {}

    def fake_run_node(root, **kwargs):
        called["root"] = root
        called["kwargs"] = kwargs
        return 0

    monkeypatch.setattr("nexgen_core.roles.run_node_role", fake_run_node)
    code = bootstrap.main(["--role", "node", "--check", "--root", str(engine_root), "--join", "https://gw.test:18789"])
    assert code == 0
    assert called["root"] == engine_root
    assert called["kwargs"]["check"] is True
    assert called["kwargs"]["join"] == "https://gw.test:18789"
