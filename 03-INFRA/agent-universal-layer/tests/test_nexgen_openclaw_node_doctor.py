"""A PC that is only a node is not judged as a gateway.

One job: pin the one fact `nexgen doctor` and `nexgen update` need -- on a machine
whose OpenClaw is a node of a gateway elsewhere, the gateway's checks retire and the
leftover `openclaw.json` is not a fault -- and pin the silence that must stay: a real
gateway keeps exactly the report it had.

The machine reproduced here is the owner's PC: it was a gateway, it became a node of a
VPS, and the file from the gateway days stayed where it was. `capo` still names a
Claude model with no `tools.deny`, which the loopback check reads as `BROKEN`, and
`nexgen update` rolls the release back when the count of failures grows. The role file
is the answer when the path that made the node recorded it; the service file and the
Companion app's node switch are the answer for the machines already migrated without
one. Every fact is a fixture under `tmp_path`: no real home, no command, no service.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import i18n, roles  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.checks.anthropic_catalog_checks import check_anthropic_catalog  # noqa: E402
from nexgen_core.checks.claude_loopback_checks import check_claude_loopback  # noqa: E402
from nexgen_core.checks.openclaw_parity_checks import check_openclaw_parity  # noqa: E402
from nexgen_core.openclaw import node_only  # noqa: E402
from nexgen_core.openclaw.companion import APP_REMOTE, Companion  # noqa: E402
from nexgen_core.openclaw.detect import Detection, Installation, LocalModels, Login  # noqa: E402
from nexgen_core.openclaw.doctor import collect, exit_code  # noqa: E402
from nexgen_core.openclaw.node_apply import apply_node  # noqa: E402
from nexgen_core.openclaw_service import RunResult  # noqa: E402
from nexgen_core.paths import claude_settings, openclaw_config  # noqa: E402
from nexgen_core.report import CheckOutcome, Severity  # noqa: E402

CLAUDE_REF = "anthropic/claude-opus-5-5"
GO_REF = "opencode-go/deepseek-v4.1-flash"
REMOTE_HOST = "nexgen-vps.tail5517e9.ts.net"

#: What a stopped node service answers on `openclaw node status --json`.
STOPPED_NODE = {
    "service": {"label": "systemd user", "loaded": False, "command": None, "runtime": {"status": "stopped"}},
}


@pytest.fixture(autouse=True)
def _a_home_of_its_own(monkeypatch):
    """No test reads or writes the real machine-local state, and the lines are English."""
    for name in ("XDG_STATE_HOME", "AGENT_STATE_DIR", "NEXGEN_HOME",
                 "OPENCLAW_CONFIG_PATH", "OPENCLAW_STATE_DIR", "APPDATA", "LOCALAPPDATA"):
        monkeypatch.delenv(name, raising=False)
    i18n.set_language("en")
    yield
    i18n.set_language(None)


def _machine(root: Path, name: str = "home") -> Path:
    home = root / name
    home.mkdir()
    return home


def _gateway_left_behind(home: Path) -> Path:
    """The file the PC kept from when it was the gateway: a local mode and `capo` on Claude."""
    config = {
        "gateway": {
            "mode": "local",
            "port": 18789,
            "bind": "loopback",
            "auth": {"mode": "token", "token": "a" * 48},
        },
        "agents": {
            "defaults": {"models": {CLAUDE_REF: {"agentRuntime": {"id": "claude-cli"}}}},
            "entries": {"main": {"model": GO_REF}, "capo": {"model": CLAUDE_REF}},
        },
        "mcp": {"servers": {"vault-library": {"command": "node", "args": ["vault.mjs"]}}},
    }
    path = openclaw_config(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    return path


def _claude_settings(home: Path) -> Path:
    """Claude Code's own file, so the parity check has both sides to compare."""
    path = claude_settings(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}\n", encoding="utf-8")
    return path


def _node_service(home: Path, platform: str) -> Path:
    """The node service file OpenClaw itself writes on this platform, pointing at the VPS."""
    if platform == "win32":
        path = openclaw_config(home).parent / "node.cmd"
        path.write_text(
            "@echo off\r\n"
            '"C:\\Program Files\\nodejs\\node.exe" '
            '"C:\\Users\\matte\\AppData\\Roaming\\npm\\node_modules\\openclaw\\dist\\index.js" '
            f"node run --host {REMOTE_HOST} --port 443 --tls\r\n",
            encoding="utf-8",
        )
        return path
    if platform == "darwin":
        directory = home / "Library" / "LaunchAgents"
        directory.mkdir(parents=True)
        path = directory / "ai.openclaw.node.plist"
        path.write_text(
            "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<plist><array>\n"
            "  <string>node</string><string>dist/index.js</string>\n"
            f"  <string>node</string><string>run</string><string>--host</string><string>{REMOTE_HOST}</string>\n"
            "  <string>--port</string><string>443</string><string>--tls</string>\n"
            "</array></plist>\n",
            encoding="utf-8",
        )
        return path
    directory = home / ".config" / "systemd" / "user"
    directory.mkdir(parents=True)
    path = directory / "openclaw-node.service"
    path.write_text(
        "[Unit]\nDescription=OpenClaw node\n\n[Service]\n"
        f"ExecStart=/usr/bin/node /usr/lib/node_modules/openclaw/dist/index.js node run "
        f"--host {REMOTE_HOST} --port 443 --tls\n",
        encoding="utf-8",
    )
    return path


def _companion_app(home: Path, *, node_mode: bool = True) -> Companion:
    """The app's two files, with the node switch on and one gateway elsewhere."""
    directory = home / "AppData" / "Roaming" / "OpenClawTray"
    directory.mkdir(parents=True)
    (directory / "settings.json").write_text(
        json.dumps({"EnableNodeMode": node_mode}), encoding="utf-8",
    )
    (directory / "gateways.json").write_text(
        json.dumps({
            "activeId": "vps",
            "gateways": [{"id": "vps", "url": f"wss://{REMOTE_HOST}", "isLocal": False}],
        }),
        encoding="utf-8",
    )
    return Companion(present=True, points_remote=True, target=APP_REMOTE)


def _env(home: Path) -> dict[str, str]:
    """A session environment this home owns: no real `APPDATA`, no real CLI on PATH."""
    return {
        "PATH": "/fake/bin",
        "APPDATA": str(home / "AppData" / "Roaming"),
        "LOCALAPPDATA": str(home / "AppData" / "Local"),
    }


def _detection(config_path: Path, companion: Companion | None = None) -> Detection:
    """This machine with OpenClaw installed, built here so detection runs no command."""
    return Detection(
        openclaw=Installation(
            installed=True, version="2026.10.6", config_path=str(config_path), config_present=True,
        ),
        claude=Login(binary=True, login=True),
        codex=Login(binary=False, login=False),
        opencode_go=True,
        local=LocalModels(),
        n8n=False,
        companion=companion or Companion(),
    )


def _report(
    home: Path,
    tmp_path: Path,
    *,
    platform: str,
    companion: Companion | None = None,
) -> list[CheckOutcome]:
    """What `nexgen doctor` prints here: collect plus the three checks registered beside it."""
    vault = tmp_path / "vault"
    engine = tmp_path / "engine"
    outcomes = collect(
        detected=_detection(openclaw_config(home), companion), home=home, env=_env(home),
        engine_root=engine, vault_data=vault, platform=platform,
        wsl_distributions=list, node_version=lambda: None,
    )
    outcomes.extend(check_openclaw_parity(home, vault, platform=platform))
    loopback = check_claude_loopback(home, vault, engine, platform=platform)
    if loopback is not None:
        outcomes.append(loopback)
    catalog = check_anthropic_catalog(home, platform=platform)
    if catalog is not None:
        outcomes.append(catalog)
    return outcomes


def _one(outcomes: list[CheckOutcome], check_id: str) -> CheckOutcome:
    found = [outcome for outcome in outcomes if outcome.id == check_id]
    assert len(found) == 1, [outcome.id for outcome in outcomes]
    return found[0]


#: The gateway checks a node may not be judged by, and the model checks that read a gateway's agents.
GATEWAY_IDS = (
    "openclaw.gateway.bind",
    "openclaw.gateway.auth",
    "openclaw.exposure",
    "openclaw.plugin",
    "openclaw.agent_models",
    "openclaw.model_overrides",
    "openclaw.ollama_latest",
    "openclaw.cage",
    "openclaw.mcp.lazy",
    "openclaw.tool_search",
    "openclaw.heartbeat",
    "openclaw.fallbacks",
)


def _assert_a_node_is_not_judged(outcomes: list[CheckOutcome]) -> None:
    """The whole point: no failure, the gateway checks gone, and the one line that says why."""
    failed = [outcome.id for outcome in outcomes if outcome.severity == Severity.BROKEN]
    assert failed == []
    ids = {outcome.id for outcome in outcomes}
    assert not [check_id for check_id in GATEWAY_IDS if check_id in ids]
    assert _one(outcomes, "openclaw.node_mode").severity == Severity.OK
    loopback = _one(outcomes, "openclaw.claude_loopback")
    assert loopback.severity == Severity.OK
    assert "This machine is a node" in loopback.message
    parity = _one(outcomes, "openclaw.parity")
    assert parity.severity == Severity.OK
    assert "This machine is a node" in parity.message


# --- (a) the role is recorded: the machine's own answer ----------------------------


def test_a_recorded_node_is_not_judged_as_a_gateway(tmp_path: Path) -> None:
    home = _machine(tmp_path)
    _gateway_left_behind(home)
    _claude_settings(home)
    assert roles.declare_role(roles.NODE_ROLE, home) is True

    outcomes = _report(home, tmp_path, platform=sys.platform)

    _assert_a_node_is_not_judged(outcomes)


def test_the_gateway_checks_run_again_when_the_role_is_not_a_node(tmp_path: Path) -> None:
    """A gateway is not made quiet by the fact existing: only a node retires its checks."""
    home = _machine(tmp_path)
    _gateway_left_behind(home)
    _claude_settings(home)
    assert roles.declare_role(roles.STANDALONE_ROLE, home) is True

    outcomes = _report(home, tmp_path, platform=sys.platform)

    ids = {outcome.id for outcome in outcomes}
    assert "openclaw.gateway.bind" in ids
    assert "openclaw.agent_models" in ids
    assert "openclaw.node_mode" not in ids
    assert _one(outcomes, "openclaw.claude_loopback").severity == Severity.BROKEN


# --- (b) the node service of the CLI, on a machine that recorded no role -------------


def test_the_node_service_of_the_cli_makes_a_standalone_record_a_node(tmp_path: Path) -> None:
    home = _machine(tmp_path)
    _gateway_left_behind(home)
    _claude_settings(home)
    assert roles.declare_role(roles.STANDALONE_ROLE, home) is True
    _node_service(home, "win32")

    answer = node_only.detect_node_only(home, platform="win32", env=_env(home))
    assert bool(answer) is True
    assert answer.reason == node_only.SERVICE

    _assert_a_node_is_not_judged(_report(home, tmp_path, platform="win32"))


def test_the_service_file_of_the_running_platform_makes_a_standalone_record_a_node(tmp_path: Path) -> None:
    """The same fact on the platform this suite runs on: `node.cmd`, the unit or the plist."""
    home = _machine(tmp_path)
    _gateway_left_behind(home)
    _claude_settings(home)
    assert roles.declare_role(roles.STANDALONE_ROLE, home) is True
    written = _node_service(home, sys.platform)

    assert written.is_file()
    assert bool(node_only.detect_node_only(home, platform=sys.platform, env=_env(home))) is True
    _assert_a_node_is_not_judged(_report(home, tmp_path, platform=sys.platform))


def test_a_local_node_service_is_a_gateway_and_keeps_its_checks(tmp_path: Path) -> None:
    """`node run` on this PC's own loopback is not a node of another gateway."""
    home = _machine(tmp_path)
    _gateway_left_behind(home)
    _claude_settings(home)
    path = _node_service(home, "win32")
    path.write_text(path.read_text(encoding="utf-8").replace(REMOTE_HOST, "127.0.0.1"), encoding="utf-8")

    assert bool(node_only.detect_node_only(home, platform="win32", env=_env(home))) is False


# --- (c) the Companion app, in node mode, pointing at a gateway elsewhere ------------


def test_the_companion_app_in_node_mode_makes_a_standalone_record_a_node(tmp_path: Path) -> None:
    home = _machine(tmp_path)
    _gateway_left_behind(home)
    _claude_settings(home)
    assert roles.declare_role(roles.STANDALONE_ROLE, home) is True
    app = _companion_app(home)

    answer = node_only.detect_node_only(home, platform="win32", env=_env(home))
    assert bool(answer) is True
    assert answer.reason == node_only.APP

    _assert_a_node_is_not_judged(_report(home, tmp_path, platform="win32", companion=app))


def test_the_app_without_the_node_switch_is_not_a_node(tmp_path: Path) -> None:
    home = _machine(tmp_path)
    _gateway_left_behind(home)
    _claude_settings(home)
    _companion_app(home, node_mode=False)

    assert bool(node_only.detect_node_only(home, platform="win32", env=_env(home))) is False


# --- (d) a real gateway is exactly what it was ---------------------------------------


def test_a_gateway_with_capo_on_the_loopback_still_fails(tmp_path: Path) -> None:
    home = _machine(tmp_path)
    _gateway_left_behind(home)
    _claude_settings(home)

    outcomes = _report(home, tmp_path, platform=sys.platform)

    ids = {outcome.id for outcome in outcomes}
    assert "openclaw.gateway.bind" in ids
    assert "openclaw.node_mode" not in ids
    loopback = _one(outcomes, "openclaw.claude_loopback")
    assert loopback.severity == Severity.BROKEN
    assert "capo" in loopback.message
    assert exit_code(outcomes) == 1


# --- (e) every path that makes a node records it ------------------------------------


class _RecordingRunner:
    """Records every command and answers from canned output: nothing real is started."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], timeout: float | None = None) -> RunResult:
        self.calls.append(list(argv))
        if argv[:3] == ["openclaw", "node", "status"]:
            return RunResult(0, json.dumps(STOPPED_NODE), "")
        return RunResult(0, "", "")


def test_apply_node_records_the_node_role(tmp_path: Path) -> None:
    home = _machine(tmp_path)
    _gateway_left_behind(home)
    assert roles.declared_role(home=home) is None

    code = apply_node(
        REMOTE_HOST, name="desktop", join_target=f"https://{REMOTE_HOST}/j/one-shot", home=home,
        platform="linux", runner=_RecordingRunner(), systemd_user_dir=tmp_path / "units",
        wait_probe_fn=lambda: True, out=lambda _line: None,
    )

    assert code == 0
    assert roles.declared_role(home=home) == roles.NODE_ROLE
    assert json.loads(roles.role_record_path(home).read_text(encoding="utf-8")) == {"role": "node"}


def test_a_dry_run_records_no_role(tmp_path: Path) -> None:
    home = _machine(tmp_path)
    _gateway_left_behind(home)

    code = apply_node(
        REMOTE_HOST, name="desktop", dry_run=True, home=home,
        platform="linux", runner=_RecordingRunner(), systemd_user_dir=tmp_path / "units",
        out=lambda _line: None,
    )

    assert code == 0
    assert roles.declared_role(home=home) is None
