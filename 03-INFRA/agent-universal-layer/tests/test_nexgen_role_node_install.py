"""The node role installs OpenClaw before it asks OpenClaw anything.

One job: pin the step that turns a PC with no OpenClaw into the machine this role
was written for. `install.ps1 -Role node` used to stop on such a PC with "stato
del nodo non leggibile", because nothing installed the package that answers the
question. The role now runs the setup's own pinned install first: these tests
drive it with a fake host and a recorder, so no npm runs and no real home is read.
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
from nexgen_core.openclaw.pin import pinned_version  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw_service import RunResult  # noqa: E402 - sys.path shim for cloned checkout

#: The release the engine itself would install here: read through the same resolver
#: the step uses, so a checkout beside an installed engine cannot disagree with it.
PINNED = pinned_version()

#: What `openclaw node status --json` answers on a PC that is not a node yet.
CLEAN_NODE_STATUS = json.dumps(
    {"service": {"loaded": False, "command": None, "runtime": {"status": "stopped"}}}
)


@pytest.fixture(autouse=True)
def _english(monkeypatch):
    """English output to assert on, whatever language this machine speaks."""
    i18n.set_language("en")
    yield
    i18n.set_language(None)


def healthy_session(monkeypatch) -> None:
    """What a normal login session exports, so the Linux host preflight meets this machine.

    The preflight stops a Linux install that has no systemd user bus, and the fake
    machine is a healthy desktop: in CI the variable a desktop brings is the one
    thing a bare pytest session does not have.
    """
    if sys.platform.startswith("linux"):
        monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1000")


class FakeHost:
    """The executor the install step gets: records every command, runs none.

    It answers the four questions the step really asks (Node's version, npm's
    global root, npm's prefix, the systemd user bus) from the facts the test set,
    and treats everything else as a command that succeeded without output.
    """

    def __init__(self, npm_root: Path, node_version: str = "v24.22.1"):
        self.npm_root = npm_root
        self.node_version = node_version
        self.calls: list[list[str]] = []

    def __call__(self, argv, env=None, **_ignored) -> RunResult:
        self.calls.append(list(argv))
        if str(argv[0]).endswith("node") and argv[1:] == ["--version"]:
            return RunResult(0, self.node_version + "\n", "")
        if argv[1:4] == ["root", "-g"]:
            return RunResult(0, str(self.npm_root) + "\n", "")
        if argv[1:3] == ["config", "get"]:
            return RunResult(0, "", "")
        return RunResult(0, "", "")

    def installs(self) -> list[list[str]]:
        """The pinned-install commands this host was asked to run."""
        return [argv for argv in self.calls if argv[1:3] == ["install", "-g"]]


class NodeCommands:
    """The role's own runner: OpenClaw's answers about the node, and a recorder.

    A PC with no gateway answers `openclaw node status` with the clean "not a node"
    payload, and every other command (Windows tasks, systemd) as absent: that is
    the machine the role is for.
    """

    def __init__(self, status: str = CLEAN_NODE_STATUS, code: int = 0, error: str = ""):
        self.status = status
        self.code = code
        self.error = error
        self.calls: list[list[str]] = []

    def __call__(self, argv, timeout) -> RunResult:
        self.calls.append(list(argv))
        if argv[:3] == ["openclaw", "node", "status"]:
            return RunResult(self.code, self.status if self.code == 0 else "", self.error)
        return RunResult(1, "", "")

    def asked_for_node_status(self) -> bool:
        """Whether the role got as far as reading the node's own state."""
        return any(argv[:3] == ["openclaw", "node", "status"] for argv in self.calls)


def lookup(present: tuple[str, ...] = ("node", "npm")):
    """A PATH where only `present` exists: OpenClaw appears only after an install."""
    found = {name: f"/fake/bin/{name}" for name in present}
    return found.get


def npm_root_with_openclaw(root: Path, version: str) -> Path:
    """A global npm tree that already holds OpenClaw at `version`."""
    package = root / "openclaw" / "package.json"
    package.parent.mkdir(parents=True, exist_ok=True)
    package.write_text(json.dumps({"version": version, "engines": {"node": ">=24.22.1"}}), encoding="utf-8")
    return root


def test_a_pc_without_openclaw_gets_the_pin_and_the_role_goes_on(tmp_path, monkeypatch) -> None:
    """The machine the role exists for: no OpenClaw, and a node to configure."""
    healthy_session(monkeypatch)
    host = FakeHost(npm_root=tmp_path / "npm-root")
    commands = NodeCommands()
    said: list[str] = []

    code = roles.run_node_role(
        root=tmp_path,
        home=tmp_path / "home",
        runner=commands,
        install_run=host,
        which=lookup(),
        out=said.append,
    )

    assert code == 0
    installs = host.installs()
    assert len(installs) == 1, host.calls
    assert installs[0][1:4] == ["install", "-g", f"openclaw@{PINNED}"]
    assert installs[0][-1].startswith("--allow-scripts=")
    assert commands.asked_for_node_status(), commands.calls
    assert "openclaw devices join-code" in "\n".join(said)


def test_a_pc_already_on_the_pin_installs_nothing(tmp_path, monkeypatch) -> None:
    """A second run must not reinstall what is already the pinned release."""
    healthy_session(monkeypatch)
    npm_root = npm_root_with_openclaw(tmp_path / "npm-root", PINNED)
    host = FakeHost(npm_root=npm_root)
    commands = NodeCommands()
    said: list[str] = []

    code = roles.run_node_role(
        root=tmp_path,
        home=tmp_path / "home",
        runner=commands,
        install_run=host,
        which=lookup(),
        out=said.append,
    )

    assert code == 0
    assert host.installs() == []
    assert f"OpenClaw {PINNED} is already the pinned release." in "\n".join(said)
    assert commands.asked_for_node_status(), commands.calls


def test_an_old_node_stops_the_role_with_the_setup_message(tmp_path, monkeypatch) -> None:
    """OpenClaw declares a Node range, and a Node outside it is installed around, never onto."""
    healthy_session(monkeypatch)
    host = FakeHost(npm_root=tmp_path / "npm-root", node_version="v22.0.0")
    commands = NodeCommands()
    said: list[str] = []

    code = roles.run_node_role(
        root=tmp_path,
        home=tmp_path / "home",
        runner=commands,
        install_run=host,
        which=lookup(),
        out=said.append,
    )

    assert code == 1
    assert host.installs() == []
    output = "\n".join(said)
    assert f"Node v22.0.0 does not satisfy what OpenClaw {PINNED} needs" in output
    assert not commands.asked_for_node_status(), commands.calls


def test_a_check_installs_nothing_even_on_a_pc_without_openclaw(tmp_path, monkeypatch) -> None:
    """`--check` is the promise of a look-only run, and npm is not a read."""
    healthy_session(monkeypatch)
    host = FakeHost(npm_root=tmp_path / "npm-root")
    # Before anything is installed the command does not exist, which is exactly
    # what the role used to stop on: the plan says so, and the run installs.
    commands = NodeCommands(code=127, error="openclaw: command not found")

    code = roles.run_node_role(
        root=tmp_path,
        home=tmp_path / "home",
        check=True,
        runner=commands,
        install_run=host,
        which=lookup(),
        out=lambda _line: None,
    )

    assert code == 1
    assert host.calls == []
    assert commands.asked_for_node_status(), commands.calls
