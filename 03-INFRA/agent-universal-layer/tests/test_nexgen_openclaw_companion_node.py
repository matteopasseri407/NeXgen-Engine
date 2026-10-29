"""One node per PC: the app's node mode, the CLI service beside it, and the way out.

Every test builds its folders under `tmp_path` and injects the command runner, so
no test asks the real machine whether it has the `OpenClaw Node` task and none
runs `openclaw`. The last test is the promise this unit was written for: the app's
own folder is read for diagnosis and never opened for writing.
"""
from __future__ import annotations

import builtins
import json
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import i18n, roles  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.cli import openclaw_cmds  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw import doctor_runtime  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw.companion import Companion, probe  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw.detect import Detection, Installation, LocalModels, Login  # noqa: E402
from nexgen_core.openclaw.doctor import collect  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw.doctor_runtime import check_companion_gateway, check_companion_node  # noqa: E402
from nexgen_core.openclaw.node_app import (  # noqa: E402 - sys.path shim for cloned checkout
    NODE_TASK_NAME,
    CliNodeService,
    detect_cli_node_service,
    use_app,
)
from nexgen_core.openclaw_service import RunResult  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.report import Severity  # noqa: E402 - sys.path shim for cloned checkout

NODE_MODE_KEY = "EnableNodeMode"
CREDENTIAL_ERROR = (
    "[2026-10-29 05:37:37.914] [ERROR] [App] Local NodeService auto-connect failed: "
    "No node credential available. Re-pair this PC or add a shared/bootstrap gateway token."
)
NODE_CONNECTED = "[2026-10-29 06:00:00.000] [INFO] [App] Local NodeService connected to the gateway"


@pytest.fixture(autouse=True)
def _english():
    i18n.set_language("en")
    yield
    i18n.set_language(None)


def machine(tmp_path: Path) -> tuple[Path, dict]:
    """A fake home and the two app-data folders the app would write into."""
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    return home, {
        "LOCALAPPDATA": str(tmp_path / "local"),
        "APPDATA": str(tmp_path / "roaming"),
    }


def app_folder(env: dict, name: str = "OpenClawTray") -> Path:
    folder = Path(env["APPDATA"]) / name
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def write(folder: Path, name: str, body) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(body if isinstance(body, str) else json.dumps(body), encoding="utf-8")
    return path


def app_settings(env: dict, **extra) -> Path:
    return write(app_folder(env), "settings.json", {"GatewayUrl": "wss://gateway.example.com", **extra})


def app_log(env: dict, lines: list[str] | str) -> Path:
    body = lines if isinstance(lines, str) else "\n".join(lines) + "\n"
    folder = Path(env["LOCALAPPDATA"]) / "OpenClawTray"
    return write(folder, "openclaw-tray.log", body)


def scan(env: dict) -> Companion:
    home, _ = machine(Path(env["LOCALAPPDATA"]).parent)
    return probe(home, env, "win32", wsl_distributions=lambda: [])


class Recorder:
    """A command runner that answers with one code, or a function of the argv, and remembers every call."""

    def __init__(self, code: int | Callable[[list[str]], int] = 0) -> None:
        self.code = code
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], timeout: float) -> RunResult:
        self.calls.append(list(argv))
        code = self.code(argv) if callable(self.code) else self.code
        return RunResult(code)


# ------------------------------------------------------------- what the app says


def test_a_settings_file_without_the_key_says_nothing_about_a_node(tmp_path):
    home, env = machine(tmp_path)
    app_settings(env)

    found = scan(env)

    assert found.wants_node is False
    assert found.node_credential_missing is False
    assert "wants_node" not in found.to_dict()


def test_a_node_mode_that_is_off_says_nothing_about_a_node(tmp_path):
    home, env = machine(tmp_path)
    app_settings(env, **{NODE_MODE_KEY: False})

    assert scan(env).wants_node is False


def test_a_node_mode_that_is_on_is_read_from_the_apps_own_file(tmp_path):
    home, env = machine(tmp_path)
    app_settings(env, **{NODE_MODE_KEY: True})

    found = scan(env)

    assert found.wants_node is True
    assert found.to_dict()["wants_node"] is True


def test_the_last_node_line_of_the_log_is_the_missing_credential(tmp_path):
    home, env = machine(tmp_path)
    app_settings(env, **{NODE_MODE_KEY: True})
    app_log(env, ["[2026-10-29 05:37:36.000] [INFO] [App] starting", CREDENTIAL_ERROR])

    assert scan(env).node_credential_missing is True


def test_a_node_that_connected_after_the_error_makes_the_error_stale(tmp_path):
    home, env = machine(tmp_path)
    app_settings(env, **{NODE_MODE_KEY: True})
    app_log(env, [CREDENTIAL_ERROR, NODE_CONNECTED])

    assert scan(env).node_credential_missing is False


def test_the_log_is_read_from_its_end_and_not_whole(tmp_path):
    """A 2 MB log with the only error at the top is beyond the tail: the app moved on."""
    home, env = machine(tmp_path)
    app_settings(env, **{NODE_MODE_KEY: True})
    filler = " ".join("[2026-10-29 09:00:00.000] [INFO] [EVENT] Received event: tick" for _ in range(30))
    app_log(env, CREDENTIAL_ERROR + "\n" + "\n".join([filler] * 1200))

    assert scan(env).node_credential_missing is False


def test_node_mode_off_does_not_read_the_log(tmp_path):
    home, env = machine(tmp_path)
    app_settings(env, **{NODE_MODE_KEY: False})
    app_log(env, [CREDENTIAL_ERROR])

    assert scan(env).node_credential_missing is False


# ---------------------------------------------------------- the CLI node service


def test_the_command_file_is_the_cli_node_service(tmp_path):
    home, env = machine(tmp_path)
    write(home / ".openclaw", "node.cmd", "@echo off\r\n")
    runner = Recorder(code=1)

    found = detect_cli_node_service(home=home, platform="win32", runner=runner)

    assert found.present is True
    assert found.command_file is True
    assert found.scheduled_task is False
    assert found.mechanism == "file"
    assert runner.calls == [["schtasks.exe", "/Query", "/TN", NODE_TASK_NAME]]


def test_the_scheduled_task_is_the_cli_node_service(tmp_path):
    home, env = machine(tmp_path)
    runner = Recorder(code=0)

    found = detect_cli_node_service(home=home, platform="win32", runner=runner, query=True)

    assert found.present is True
    assert found.scheduled_task is True
    assert found.mechanism == "schtasks"


def test_a_pc_with_neither_is_not_a_cli_node(tmp_path):
    home, env = machine(tmp_path)

    found = detect_cli_node_service(home=home, platform="win32", runner=Recorder(code=1), query=True)

    assert found == CliNodeService()


def test_off_windows_no_task_is_ever_asked(tmp_path):
    home, env = machine(tmp_path)
    runner = Recorder(code=0)

    found = detect_cli_node_service(home=home, platform="linux", runner=runner, query=True)

    assert found == CliNodeService()
    assert runner.calls == []


# --------------------------------------------------------------- the doctor line


def test_node_mode_off_adds_no_line(tmp_path):
    app_gateway = Companion(present=True, points_remote=True)

    assert check_companion_node(app_gateway, "win32", CliNodeService()) is None
    gateway = check_companion_gateway(app_gateway, False, "win32", None, CliNodeService())
    assert gateway is not None
    assert gateway.id == "openclaw.companion_gateway"
    assert gateway.severity == Severity.OK


def test_two_nodes_on_one_pc_name_the_service_and_the_way_out(tmp_path):
    app_gateway = Companion(present=True, points_remote=True, wants_node=True)

    outcome = check_companion_node(app_gateway, "win32", CliNodeService(present=True, scheduled_task=True))

    assert outcome is not None
    assert outcome.id == "openclaw.companion_node"
    assert outcome.severity == Severity.WARN
    assert NODE_TASK_NAME in outcome.message
    assert "two nodes" in outcome.message
    assert "nexgen openclaw node --use-app --apply" in outcome.action
    assert "openclaw devices approve <id>" in outcome.action


def test_a_node_without_a_credential_says_how_to_pair_it(tmp_path):
    app_gateway = Companion(present=True, wants_node=True, node_credential_missing=True)

    outcome = check_companion_node(app_gateway, "win32", CliNodeService())

    assert outcome is not None
    assert outcome.severity == Severity.WARN
    assert "No node credential available" in outcome.message
    assert "openclaw devices list" in outcome.action
    assert "openclaw devices approve <id>" in outcome.action


def test_a_node_that_works_adds_no_line(tmp_path):
    app_gateway = Companion(present=True, points_here=True, port=18789, wants_node=True)

    assert check_companion_node(app_gateway, "win32", CliNodeService()) is None
    gateway = check_companion_gateway(app_gateway, True, "win32", None, CliNodeService())
    assert gateway is not None
    assert gateway.id == "openclaw.companion_gateway"


def test_a_gateway_warning_keeps_its_sentence_and_gains_the_node_one(tmp_path):
    app_gateway = Companion(present=True, owns_local_gateway=True, wants_node=True)

    outcome = check_companion_gateway(app_gateway, True, "win32", None, CliNodeService(present=True))

    assert outcome is not None
    assert outcome.severity == Severity.WARN
    assert "two gateways in conflict" in outcome.message
    assert "two nodes on the same PC" in outcome.message


def test_the_doctor_prints_the_node_line(tmp_path, monkeypatch):
    app_gateway = Companion(present=True, points_remote=True, wants_node=True)
    snapshot = Detection(
        openclaw=Installation(installed=False),
        claude=Login(binary=False),
        codex=Login(binary=False),
        opencode_go=False,
        local=LocalModels(),
        n8n=False,
        companion=app_gateway,
    )
    monkeypatch.setattr(
        doctor_runtime, "detect_cli_node_service", lambda **kwargs: CliNodeService(present=True),
    )

    outcomes = collect(
        detected=snapshot,
        home=tmp_path / "home",
        vault_data=tmp_path / "vault",
        engine_root=tmp_path / "engine",
        env={},
        platform="win32",
        wsl_distributions=lambda: [],
    )

    rows = {outcome.id: outcome for outcome in outcomes}
    assert "openclaw.companion_node" in rows
    assert rows["openclaw.companion_node"].severity == Severity.WARN
    assert "openclaw.companion_gateway" not in rows


# ------------------------------------------------------------- the way to the app


def test_the_plan_runs_no_openclaw_command_and_changes_nothing(tmp_path):
    home, env = machine(tmp_path)
    runner = Recorder(code=0)
    printed: list[str] = []

    code = use_app(home=home, platform="win32", runner=runner, out=printed.append)

    assert code == 0
    assert runner.calls == [["schtasks.exe", "/Query", "/TN", NODE_TASK_NAME]]
    assert not roles.role_record_path(home).exists()
    assert any("--apply" in line for line in printed)


def test_apply_removes_only_the_cli_node_service_and_records_the_role(tmp_path):
    home, env = machine(tmp_path)
    runner = Recorder(code=0)

    code = use_app(apply=True, home=home, platform="win32", runner=runner, out=lambda line: None)

    assert code == 0
    assert runner.calls == [
        ["schtasks.exe", "/Query", "/TN", NODE_TASK_NAME],
        ["openclaw", "node", "uninstall"],
    ]
    assert json.loads(roles.role_record_path(home).read_text(encoding="utf-8"))["role"] == roles.NODE_ROLE


def test_a_failing_uninstall_is_said_and_records_no_role(tmp_path):
    home, env = machine(tmp_path)
    runner = Recorder(lambda argv: 0 if argv[0] == "schtasks.exe" else 1)
    printed: list[str] = []

    code = use_app(apply=True, home=home, platform="win32", runner=runner, out=printed.append)

    assert code == 1
    assert not roles.role_record_path(home).exists()
    assert any("exit code 1" in line for line in printed)


def test_off_windows_the_app_node_is_not_a_choice(tmp_path):
    home, env = machine(tmp_path)
    runner = Recorder(code=0)

    assert use_app(home=home, platform="linux", runner=runner, out=lambda line: None) == 1
    assert runner.calls == []


def test_the_openclaw_verb_accepts_use_app_without_a_host(tmp_path, monkeypatch):
    import argparse

    parser = argparse.ArgumentParser()
    openclaw_cmds.register(parser.add_subparsers(dest="command"))
    args = parser.parse_args(["openclaw", "node", "--use-app"])
    assert args.host is None and args.use_app is True

    asked: list[dict] = []
    monkeypatch.setattr(
        "nexgen_core.openclaw.node_app.use_app",
        lambda **kwargs: asked.append(kwargs) or 0,
    )
    assert args.func(args) == 0
    assert asked == [{"apply": False}]


def test_a_node_command_without_a_host_says_which_flag_is_missing(tmp_path, capsys):
    import argparse

    parser = argparse.ArgumentParser()
    openclaw_cmds.register(parser.add_subparsers(dest="command"))
    args = parser.parse_args(["openclaw", "node"])

    assert args.func(args) == 2
    assert "--host" in capsys.readouterr().out


# ------------------------------------------------------------ the app is read-only


@pytest.fixture
def guard_writes(monkeypatch):
    """`guard_writes(folder)` records every write-mode open inside that folder."""
    recorded: list[tuple[str, str]] = []

    def install(folder: Path) -> list[tuple[str, str]]:
        watched = folder.resolve()

        def inside(path: object) -> bool:
            if not isinstance(path, (str, Path)):
                return False
            try:
                target = Path(path).resolve()
            except OSError:  # nexgen: allow reason="a path that cannot be resolved is not inside the app"
                return False
            return target == watched or watched in target.parents

        def is_write(mode: str) -> bool:
            return any(flag in mode for flag in ("w", "a", "x", "+"))

        real_path_open = Path.open
        real_write = Path.write_text
        real_write_bytes = Path.write_bytes

        def guard_path_open(self, mode="r", *args, **kwargs):
            if is_write(mode) and inside(self):
                recorded.append((str(self), str(mode)))
            return real_path_open(self, mode, *args, **kwargs)

        real_builtin_open = builtins.open

        def guard_builtin_open(file, mode="r", *args, **kwargs):
            if is_write(mode) and inside(file):
                recorded.append((str(file), str(mode)))
            return real_builtin_open(file, mode, *args, **kwargs)

        monkeypatch.setattr(Path, "open", guard_path_open)
        monkeypatch.setattr(builtins, "open", guard_builtin_open)

        def guard_write(self, *args, **kwargs):
            if inside(self):
                recorded.append((str(self), "write"))
            return real_write(self, *args, **kwargs)

        def guard_write_bytes(self, *args, **kwargs):
            if inside(self):
                recorded.append((str(self), "write"))
            return real_write_bytes(self, *args, **kwargs)

        monkeypatch.setattr(Path, "write_text", guard_write)
        monkeypatch.setattr(Path, "write_bytes", guard_write_bytes)
        return recorded

    return install


def test_no_file_in_the_app_folder_is_opened_for_writing(tmp_path, guard_writes):
    home, env = machine(tmp_path)
    app_settings(env, **{NODE_MODE_KEY: True})
    app_log(env, [CREDENTIAL_ERROR])
    folder = app_folder(env)
    before = {path: path.read_bytes() for path in folder.rglob("*") if path.is_file()}
    recorded = guard_writes(folder)

    found = scan(env)
    check_companion_gateway(found, False, "win32", None, CliNodeService(present=True))
    use_app(home=home, platform="win32", runner=Recorder(code=0), out=lambda line: None)

    after = {path: path.read_bytes() for path in folder.rglob("*") if path.is_file()}
    assert after == before
    assert recorded == []
    assert found.wants_node is True and found.node_credential_missing is True

    (folder / "deliberate-write.txt").write_text("x", encoding="utf-8")
    assert recorded, "the guard has to record a write it was built to catch"
