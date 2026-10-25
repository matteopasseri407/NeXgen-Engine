"""`nexgen openclaw setup --server`: a Linux machine with no screen, on a tailnet.

Every case builds a fake machine in `tmp_path`: a fake detector, a fake executor
that plays `tailscale`, `id`, `sudo` and `systemctl`, and a fake service that
records stop/start. Nothing here reads the real home, runs a real command or
opens a socket, and every stopping case checks that nothing was installed and
nothing was written.

The expected sentences are the contract: a person on a fresh VPS has to know
which of the four prerequisites is missing and what to do about it.
"""
from __future__ import annotations

import contextlib
import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import i18n  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw.companion import Companion  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw.detect import Detection, Installation, LocalModels, Login  # noqa: E402
from nexgen_core.openclaw.setup import run_setup  # noqa: E402
from nexgen_core.openclaw_service import RunResult  # noqa: E402

TESTS_DIR = Path(__file__).resolve().parent
PIN_FILE = TESTS_DIR.parent / "openclaw" / "pin.json"
ENGINE_ROOT = TESTS_DIR.parents[1]


@pytest.fixture(autouse=True)
def _english_and_no_sockets(monkeypatch):
    """English output to assert on, and never a real TCP connect to port 18789."""
    i18n.set_language("en")
    monkeypatch.setattr("nexgen_core.openclaw.doctor_runtime.default_connect", lambda host, port, timeout: False)
    yield
    i18n.set_language(None)


def pinned() -> str:
    return json.loads(PIN_FILE.read_text(encoding="utf-8"))["version"]


class FakeRunner:
    """The machine's programs: tailscale, id, sudo, systemctl, node, openclaw."""

    def __init__(self):
        self.calls: list[list[str]] = []
        self.backend_state = "Running"
        self.status_json = True
        self.uid = "1000"
        self.groups = "someone adm"
        self.sudo_ok = False

    def __call__(self, argv, env=None):
        self.calls.append(list(argv))
        name = Path(argv[0]).name
        if name == "tailscale":
            if self.status_json:
                return RunResult(0, json.dumps({"BackendState": self.backend_state}), "")
            return RunResult(0 if self.backend_state == "Running" else 1, "Logged out.\n", "")
        if name == "id" and argv[1:] == ["-u"]:
            return RunResult(0, self.uid + "\n", "")
        if name == "id" and argv[1:] == ["-nG"]:
            return RunResult(0, self.groups + "\n", "")
        if name == "sudo":
            return RunResult(0 if self.sudo_ok else 1, "", "")
        if name == "node" and argv[1:] == ["--version"]:
            return RunResult(0, "v24.22.1\n", "")
        if name == "systemctl":
            return RunResult(0, "", "")
        return RunResult(0, "", "")

    def commands(self) -> list[str]:
        return [" ".join(argv) for argv in self.calls]

    def ran(self, program: str) -> bool:
        return any(Path(argv[0]).name == program for argv in self.calls)


class FakeService:
    """The service controller: records stop/start, answers a fake clock."""

    def __init__(self):
        self.port = 18789
        self.installed = True
        self.install_grace_seconds = 0
        self.events: list[str] = []
        self.now = 0.0

    def status(self, dry_run: bool = False):
        self.events.append("status")
        return {"installed": self.installed, "mechanism": "systemd" if self.installed else None}

    def install(self, dry_run: bool = False):
        self.events.append("install")
        self.installed = True
        return {"ok": True, "commands": [], "status": {"installed": True, "process_count": 0}}

    def start(self, dry_run: bool = False):
        self.events.append("start")
        return {"ok": True, "commands": [], "mechanism": "systemd"}

    @contextlib.contextmanager
    def stopped(self, wait: bool = True, timeout_s: float = 600.0, dry_run: bool = False):
        self.events.append("stop")
        try:
            yield {"ok": True, "commands": [], "stopped_pids": [], "remaining_pids": [], "dry_run": False}
        finally:
            self.events.append("start")

    def port_probe(self, port: int, host: str = "127.0.0.1") -> bool:
        return True

    def process_lister(self):
        return []

    def time_provider(self) -> float:
        return self.now

    def sleeper(self, seconds: float) -> None:
        self.now += 1.0

    def restart(self, wait: bool = True, timeout_s: float = 600.0, dry_run: bool = False):
        return {"ok": True, "commands": [], "elapsed_s": 1.0}


class Machine:
    """One fake Linux server: home, vault, config path, and the cast of fakes."""

    def __init__(self, root: Path):
        self.root = root
        self.home = root / "home"
        self.home.mkdir(parents=True, exist_ok=True)
        self.vault = root / "vault"
        self.config = self.home / ".openclaw" / "openclaw.json"
        self.service = FakeService()
        self.runner = FakeRunner()
        self.lines: list[str] = []
        self.config.parent.mkdir(parents=True, exist_ok=True)
        self.config.write_text(
            json.dumps({"gateway": {"auth": {"mode": "token", "token": "a-real-token"}}}),
            encoding="utf-8",
        )

    def run(self, *, platform: str = "linux", env=None, **kwargs) -> int:
        return run_setup(
            home=self.home,
            env={"XDG_RUNTIME_DIR": "/run/user/1000", "USER": "someone"} if env is None else env,
            vault_data=self.vault,
            detected=detection(self.config),
            which=self.which,
            run=self.runner,
            service=self.service,
            out=self.lines.append,
            stamp="20261024-120000",
            engine_root=ENGINE_ROOT,
            platform=platform,
            yes=True,
            **kwargs,
        )

    @staticmethod
    def which(name: str) -> str | None:
        return {"node": "/fake/bin/node", "npm": "/fake/bin/npm", "openclaw": "/fake/bin/openclaw",
                "tailscale": "/fake/bin/tailscale", "id": "/fake/bin/id", "sudo": "/fake/bin/sudo"}.get(name)

    def output(self) -> str:
        return "\n".join(self.lines)

    def config_json(self) -> dict:
        return json.loads(self.config.read_text(encoding="utf-8"))


def detection(config_path: Path) -> Detection:
    """A machine already on the pin, with the OpenCode Go key and no subscriptions."""
    return Detection(
        openclaw=Installation(
            installed=True, version=pinned(), config_path=str(config_path),
            config_present=config_path.is_file(),
        ),
        claude=Login(binary=False),
        codex=Login(binary=False),
        opencode_go=True,
        local=LocalModels(),
        n8n=False,
        opencode=True,
        companion=Companion(),
    )


def test_a_server_keeps_the_gateway_on_loopback_and_writes_tailscale_serve(tmp_path):
    machine = Machine(tmp_path)

    code = machine.run(server=True)

    assert code == 0
    gateway = machine.config_json()["gateway"]
    assert gateway["bind"] == "loopback"
    assert gateway["tailscale"] == {"mode": "serve"}
    assert "Tailscale is connected" in machine.output()


def test_a_tailscale_section_already_in_the_file_survives_the_server_setup(tmp_path):
    machine = Machine(tmp_path)
    machine.config.write_text(json.dumps({
        "gateway": {
            "auth": {"mode": "token", "token": "a-real-token"},
            "tailscale": {"mode": "off", "preserveFunnel": True},
        },
    }), encoding="utf-8")

    assert machine.run(server=True) == 0
    assert machine.config_json()["gateway"]["tailscale"] == {"mode": "serve", "preserveFunnel": True}


def test_server_and_lan_together_are_refused_before_anything_is_touched(tmp_path):
    machine = Machine(tmp_path)

    code = machine.run(server=True, lan=True)

    assert code == 2
    assert "opposites" in machine.output()
    assert machine.runner.calls == []
    assert machine.service.events == []


def test_a_server_without_tailscale_stops_without_installing_anything(tmp_path):
    machine = Machine(tmp_path)
    machine.which = lambda name: None if name == "tailscale" else Machine.which(name)

    code = machine.run(server=True)

    assert code == 1
    assert "Tailscale is not installed" in machine.output()
    assert "tailscale.com/download" in machine.output()
    assert not any(" install -g " in command for command in machine.runner.commands())
    assert machine.service.events == []
    assert machine.config_json()["gateway"].get("tailscale") is None


def test_a_tailscale_that_is_not_logged_in_stops_with_the_command_that_logs_in(tmp_path):
    machine = Machine(tmp_path)
    machine.runner.backend_state = "NeedsLogin"

    code = machine.run(server=True)

    assert code == 1
    assert "NeedsLogin" in machine.output()
    assert "tailscale up" in machine.output()
    assert not any(" install -g " in command for command in machine.runner.commands())
    assert machine.service.events == []


def test_a_status_command_that_cannot_answer_json_is_read_from_its_exit_code(tmp_path):
    """An older build prints prose: a zero exit is the only signal it gives."""
    machine = Machine(tmp_path)
    machine.runner.status_json = False

    assert machine.run(server=True) == 0
    assert machine.config_json()["gateway"]["tailscale"] == {"mode": "serve"}


def test_an_account_with_passwordless_sudo_stops_the_setup(tmp_path):
    machine = Machine(tmp_path)
    machine.runner.sudo_ok = True

    code = machine.run(server=True)

    assert code == 1
    assert "become root without a password" in machine.output()
    assert "yes-i-know" in machine.output()
    assert not any(" install -g " in command for command in machine.runner.commands())
    assert machine.config_json()["gateway"].get("tailscale") is None


def test_an_account_in_the_docker_group_stops_the_setup(tmp_path):
    machine = Machine(tmp_path)
    machine.runner.groups = "someone adm docker"

    code = machine.run(server=True)

    assert code == 1
    assert "docker" in machine.output()
    assert machine.service.events == []


def test_root_stops_the_setup(tmp_path):
    machine = Machine(tmp_path)
    machine.runner.uid = "0"

    code = machine.run(server=True)

    assert code == 1
    assert "This account is root" in machine.output()


def test_yes_i_know_goes_on_and_says_what_the_gateway_keeps(tmp_path):
    machine = Machine(tmp_path)
    machine.runner.sudo_ok = True

    code = machine.run(server=True, yes_i_know=True)

    assert code == 0
    assert "become root without a password" in machine.output()
    assert "--yes-i-know was given" in machine.output()
    assert machine.config_json()["gateway"]["tailscale"] == {"mode": "serve"}


def test_server_mode_is_refused_on_a_machine_that_is_not_linux(tmp_path):
    machine = Machine(tmp_path)

    code = machine.run(server=True, platform="win32")

    assert code == 1
    assert "for a Linux machine with no screen" in machine.output()
    assert machine.runner.calls == []


def test_a_server_still_gets_the_lingering_line_a_linux_host_already_gets(tmp_path):
    """Without lingering the systemd user service stops when the last session closes."""
    machine = Machine(tmp_path)

    assert machine.run(server=True) == 0
    assert "Lingering is off for someone" in machine.output()
