"""The app's node before the CLI service: the one order that leaves a node on the PC.

The app can only be handed a setup code through its own local MCP server
(`app.connection.applySetupCode`), and that server starts only while the app's
node is connected -- the very credential the setup code would provide. The way
out of that circle is a person's, so what the engine owes them is the order:
pair the app, approve it on the gateway, and remove the CLI node service last.
These tests pin that order and the guard that refuses to break it.

Everything here is fake: the app's two folders live under `tmp_path`, the
commands are recorded instead of run, and no test asks the real machine whether
it has the `OpenClaw Node` task. The last two tests are the promises: the setup
code never reaches any output, and the app's folder is never opened for writing.
"""
from __future__ import annotations

import builtins
import json
import re
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import i18n, roles  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw import doctor_runtime  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw.companion import Companion, probe  # noqa: E402 - sys.path shim
from nexgen_core.openclaw.doctor_runtime import check_companion_node  # noqa: E402
from nexgen_core.openclaw.node_app import (  # noqa: E402 - sys.path shim for cloned checkout
    NODE_TASK_NAME,
    CliNodeService,
    use_app,
)
from nexgen_core.openclaw_service import RunResult  # noqa: E402 - sys.path shim
from nexgen_core.report import Severity  # noqa: E402 - sys.path shim for cloned checkout

NODE_MODE_KEY = "EnableNodeMode"
CREDENTIAL_ERROR = (
    "[2026-10-29 05:37:37.914] [ERROR] [App] Local NodeService auto-connect failed: "
    "No node credential available. Re-pair this PC or add a shared/bootstrap gateway token."
)
NODE_CONNECTED = "[2026-10-29 06:00:00.000] [INFO] Node status: Connected"
NODE_CONNECTING = "[2026-10-29 05:59:58.000] [INFO] Node status: Connecting"
NODE_DISCONNECTED = "[2026-10-29 06:10:00.000] [INFO] Node status: Disconnected"
#: A setup code looks like a long unbroken run of URL-safe characters. Nothing the
#: engine prints may look like one.
LONG_SECRET_RUN = re.compile(r"[A-Za-z0-9_\-]{32,}")


@pytest.fixture(autouse=True)
def _english():
    i18n.set_language("en")
    yield
    i18n.set_language(None)


def home_with_app(tmp_path: Path, *, node_mode: bool = True, log: list[str] | None = None) -> Path:
    """A fake home whose app data is where the engine looks for it when a home is named."""
    home = tmp_path / "home"
    (home / "AppData" / "Roaming" / "OpenClawTray").mkdir(parents=True, exist_ok=True)
    (home / "AppData" / "Roaming" / "OpenClawTray" / "settings.json").write_text(
        json.dumps({"GatewayUrl": "wss://gateway.example.com", NODE_MODE_KEY: node_mode}),
        encoding="utf-8",
    )
    local = home / "AppData" / "Local" / "OpenClawTray"
    local.mkdir(parents=True, exist_ok=True)
    (local / "openclaw-tray.log").write_text("\n".join(log or []) + "\n", encoding="utf-8")
    return home


def scan(home: Path) -> Companion:
    """The app as the doctor reads it: no WSL list, no real registry, no real app folder."""
    return probe(home, {}, "win32", wsl_distributions=lambda: [])


class Recorder:
    """A command runner that answers with one code, or a function of the argv, and remembers every call."""

    def __init__(self, code: int | Callable[[list[str]], int] = 0) -> None:
        self.code = code
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], timeout: float) -> RunResult:
        self.calls.append(list(argv))
        code = self.code(argv) if callable(self.code) else self.code
        return RunResult(code)


def uninstalls(recorder: Recorder) -> list[list[str]]:
    return [call for call in recorder.calls if call[:3] == ["openclaw", "node", "uninstall"]]


# ------------------------------------------------------- what the app's log says


def test_the_last_node_status_line_says_whether_the_node_is_up(tmp_path):
    from nexgen_core.openclaw.companion_log import log_says_node_connected

    log = tmp_path / "openclaw-tray.log"
    log.write_text("\n".join([NODE_CONNECTED, NODE_DISCONNECTED]) + "\n", encoding="utf-8")
    assert log_says_node_connected(log) is False

    log.write_text("\n".join([NODE_DISCONNECTED, NODE_CONNECTED]) + "\n", encoding="utf-8")
    assert log_says_node_connected(log) is True


def test_a_log_with_no_node_status_line_is_not_a_connected_node(tmp_path):
    from nexgen_core.openclaw.companion_log import log_says_node_connected

    log = tmp_path / "openclaw-tray.log"
    log.write_text(CREDENTIAL_ERROR + "\n", encoding="utf-8")

    assert log_says_node_connected(log) is False


def test_node_mode_off_never_reads_the_log_for_a_connection(tmp_path):
    home = home_with_app(tmp_path, node_mode=False, log=[NODE_CONNECTED])

    assert scan(home).node_connected is False


def test_the_snapshot_carries_a_connected_node_only_when_there_is_one(tmp_path):
    home = home_with_app(tmp_path, log=[NODE_CONNECTED])

    found = scan(home)

    assert found.wants_node is True
    assert found.node_connected is True
    assert found.to_dict()["node_connected"] is True


# ------------------------------------------------------------- the order itself


def test_the_cli_service_stays_while_the_apps_node_is_not_connected(tmp_path):
    """The whole point: today's PC, where the app has node mode on and no credential."""
    home = home_with_app(tmp_path, log=[CREDENTIAL_ERROR])
    runner = Recorder(code=0)
    printed: list[str] = []

    code = use_app(apply=True, home=home, platform="win32", runner=runner, out=printed.append)

    assert code == 1
    assert uninstalls(runner) == []
    assert not roles.role_record_path(home).exists()
    text = "\n".join(printed)
    assert NODE_TASK_NAME in text
    assert "stays" in text


def test_a_node_that_is_still_connecting_keeps_the_cli_service(tmp_path):
    home = home_with_app(tmp_path, log=[CREDENTIAL_ERROR, NODE_CONNECTING])
    runner = Recorder(code=0)

    assert use_app(apply=True, home=home, platform="win32", runner=runner, out=lambda _: None) == 1
    assert uninstalls(runner) == []


def test_a_node_that_disconnected_again_keeps_the_cli_service(tmp_path):
    """`Disconnected` contains `connected`: the words are matched whole, not by substring."""
    home = home_with_app(tmp_path, log=[NODE_CONNECTED, NODE_DISCONNECTED])
    runner = Recorder(code=0)

    assert use_app(apply=True, home=home, platform="win32", runner=runner, out=lambda _: None) == 1
    assert uninstalls(runner) == []


def test_the_cli_service_goes_only_after_the_apps_node_is_connected(tmp_path):
    home = home_with_app(tmp_path, log=[CREDENTIAL_ERROR, NODE_CONNECTED])
    runner = Recorder(code=0)

    code = use_app(apply=True, home=home, platform="win32", runner=runner, out=lambda _: None)

    assert code == 0
    assert runner.calls == [
        ["schtasks.exe", "/Query", "/TN", NODE_TASK_NAME],
        ["openclaw", "node", "uninstall"],
    ]
    assert json.loads(roles.role_record_path(home).read_text(encoding="utf-8"))["role"] == roles.NODE_ROLE


def test_a_connected_node_whose_uninstall_fails_records_no_role(tmp_path):
    home = home_with_app(tmp_path, log=[NODE_CONNECTED])
    runner = Recorder(lambda argv: 0 if argv[0] == "schtasks.exe" else 1)
    printed: list[str] = []

    code = use_app(apply=True, home=home, platform="win32", runner=runner, out=printed.append)

    assert code == 1
    assert not roles.role_record_path(home).exists()
    assert any("exit code 1" in line for line in printed)


def test_a_pc_with_no_app_keeps_the_transition_it_had(tmp_path):
    """No app, no node to wait for: the CLI service is still the one that goes."""
    home = tmp_path / "bare-home"
    home.mkdir()
    runner = Recorder(code=0)

    code = use_app(apply=True, home=home, platform="win32", runner=runner, out=lambda _: None)

    assert code == 0
    assert uninstalls(runner) == [["openclaw", "node", "uninstall"]]


def test_the_plan_prints_the_safe_order_and_runs_nothing(tmp_path):
    home = home_with_app(tmp_path, log=[CREDENTIAL_ERROR])
    runner = Recorder(code=0)
    printed: list[str] = []

    code = use_app(home=home, platform="win32", runner=runner, out=printed.append)

    assert code == 0
    assert uninstalls(runner) == []
    assert not roles.role_record_path(home).exists()
    text = "\n".join(printed)
    pair = text.index("open Connections")
    approve = text.index("openclaw devices approve <id>")
    remove = text.index("--use-app --apply")
    assert pair < approve < remove
    assert "--apply" in printed[-1]


def test_the_apps_folder_follows_the_environment_when_one_is_given(tmp_path):
    """A real run passes no home: the app's folders then come from `%APPDATA%`."""
    home = tmp_path / "home"
    home.mkdir()
    roaming = tmp_path / "roaming" / "OpenClawTray"
    roaming.mkdir(parents=True)
    (roaming / "settings.json").write_text(
        json.dumps({NODE_MODE_KEY: True}), encoding="utf-8",
    )
    local = tmp_path / "local" / "OpenClawTray"
    local.mkdir(parents=True)
    (local / "openclaw-tray.log").write_text(CREDENTIAL_ERROR + "\n", encoding="utf-8")
    runner = Recorder(code=0)

    code = use_app(
        apply=True, home=home, platform="win32", runner=runner, out=lambda _: None,
        env={"LOCALAPPDATA": str(tmp_path / "local"), "APPDATA": str(tmp_path / "roaming")},
    )

    assert code == 1
    assert uninstalls(runner) == []


# ----------------------------------------------------------------- the doctor


def test_the_doctor_puts_the_approval_before_the_removal():
    app_gateway = Companion(present=True, points_remote=True, wants_node=True)

    outcome = check_companion_node(app_gateway, "win32", CliNodeService(present=True))

    assert outcome is not None
    assert outcome.severity == Severity.WARN
    approve = outcome.action.index("openclaw devices approve <id>")
    remove = outcome.action.index("nexgen openclaw node --use-app --apply")
    assert approve < remove
    assert "stays until" in outcome.action


def test_the_doctor_says_the_service_goes_last_when_the_credential_is_missing():
    app_gateway = Companion(present=True, wants_node=True, node_credential_missing=True)

    outcome = check_companion_node(app_gateway, "win32", CliNodeService())

    assert outcome is not None
    assert outcome.severity == Severity.WARN
    assert outcome.action.index("openclaw devices list") < outcome.action.index("removed only after")


# ------------------------------------------------- no setup code, no app write


def test_no_line_the_engine_prints_carries_a_setup_code_or_a_token(tmp_path):
    refused = home_with_app(tmp_path / "refused", log=[CREDENTIAL_ERROR])
    applied = home_with_app(tmp_path / "applied", log=[NODE_CONNECTED])
    printed: list[str] = []

    for home in (refused, applied):
        use_app(apply=True, home=home, platform="win32", runner=Recorder(code=0), out=printed.append)
        use_app(home=home, platform="win32", runner=Recorder(code=0), out=printed.append)

    assert printed
    for line in printed:
        assert "setupCode" not in line
        assert "bootstrapToken" not in line
        assert "Bearer" not in line
        assert LONG_SECRET_RUN.search(line) is None


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


def test_no_file_in_the_apps_folders_is_opened_for_writing(tmp_path, guard_writes):
    home = home_with_app(tmp_path, log=[CREDENTIAL_ERROR, NODE_CONNECTED])
    app_folders = [home / "AppData" / "Roaming" / "OpenClawTray", home / "AppData" / "Local" / "OpenClawTray"]
    before = {path: path.read_bytes() for folder in app_folders for path in folder.rglob("*") if path.is_file()}
    guards = [guard_writes(folder) for folder in app_folders]

    found = scan(home)
    check_companion_node(found, "win32", CliNodeService(present=True))
    use_app(home=home, platform="win32", runner=Recorder(code=0), out=lambda _: None)
    use_app(apply=True, home=home, platform="win32", runner=Recorder(code=0), out=lambda _: None)

    after = {path: path.read_bytes() for folder in app_folders for path in folder.rglob("*") if path.is_file()}
    assert after == before
    assert [entry for guard in guards for entry in guard] == []
    assert found.wants_node is True and found.node_connected is True

    (app_folders[0] / "deliberate-write.txt").write_text("x", encoding="utf-8")
    assert [entry for guard in guards for entry in guard], "the guard has to record a write it was built to catch"


def test_the_doctor_line_and_the_plan_never_disagree_about_the_order(tmp_path):
    """Both surfaces say the same three steps in the same order, so one cannot drift."""
    home = home_with_app(tmp_path, log=[CREDENTIAL_ERROR])
    printed: list[str] = []
    use_app(home=home, platform="win32", runner=Recorder(code=0), out=printed.append)
    plan = "\n".join(printed)

    outcome = doctor_runtime.check_companion_node(
        Companion(present=True, wants_node=True, node_credential_missing=True), "win32", CliNodeService(),
    )

    assert outcome is not None
    assert "openclaw devices list" in plan and "openclaw devices list" in outcome.action
    assert "openclaw devices approve <id>" in plan and "openclaw devices approve <id>" in outcome.action
