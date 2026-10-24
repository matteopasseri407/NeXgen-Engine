"""`nexgen openclaw setup`: the nine steps, driven entirely by fakes.

Every test here builds a fake machine in `tmp_path`: a fake detector, a fake
executor that records the command line it was given, and a fake service that
records stop/start. Nothing reads the real home, runs `openclaw`, or opens a
socket to the real port.
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
from nexgen_core.openclaw.detect import Detection, Installation, LocalModelInfo, LocalModels, Login  # noqa: E402
from nexgen_core.openclaw.setup import run_setup  # noqa: E402
from nexgen_core.openclaw_service import ProcessInfo, RunResult  # noqa: E402
from nexgen_core.runtimes.openclaw import PLUGIN_ID  # noqa: E402

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
    """The executor: records every command, and can play OpenClaw's part."""

    def __init__(self, onboard=None):
        self.calls: list[tuple[list[str], dict]] = []
        self.onboard = onboard
        self.fail_validate = False
        self.service: FakeService | None = None
        self.validate_while_stopped: bool | None = None
        self.node_version = "v24.22.1"
        self.npm_prefix = ""
        self.systemctl_error = ""

    def __call__(self, argv, env=None):
        self.calls.append((list(argv), dict(env or {})))
        if argv[0].endswith("node") and argv[1:] == ["--version"]:
            return RunResult(0, self.node_version + "\n", "")
        if argv[1:3] == ["config", "get"]:
            return RunResult(0, self.npm_prefix + "\n" if self.npm_prefix else "", "")
        if argv[0] == "systemctl":
            # The host preflight's only question: a healthy session's user bus answers.
            return RunResult(1, "", self.systemctl_error) if self.systemctl_error else RunResult(0, "", "")
        verbs = list(argv[1:3])
        if "onboard" in verbs:
            if self.onboard is not None:
                self.onboard()
            return RunResult(0, "", "")
        if verbs == ["config", "validate"]:
            self.validate_while_stopped = bool(self.service and self.service.stopping)
            if self.fail_validate:
                return RunResult(1, "", "invalid config: agents.entries.sviluppo.runtime.acp is unknown")
            return RunResult(0, "", "")
        return RunResult(0, "", "")

    def commands(self) -> list[str]:
        return [" ".join(argv) for argv, _ in self.calls]


class FakeService:
    """The service controller: records stop/start and answers a fake clock."""

    def __init__(self, port: int = 18789, listening: bool = True, installed: bool = True, stop_ok: bool = True):
        self.port = port
        self.listening = listening
        self.starts_listening = True
        self.installed = installed
        self.stop_ok = stop_ok
        self.install_process_count = 0
        self.install_grace_seconds = 0
        self.sleeps: list[float] = []
        self.events: list[str] = []
        self.stopping = False
        self.now = 0.0
        self.restart_result = {"ok": True, "commands": [], "elapsed_s": 12.0, "port_listening": True}

    def status(self, dry_run: bool = False):
        self.events.append("status")
        return {"installed": self.installed, "mechanism": "vbs" if self.installed else None, "dry_run": dry_run}

    def install(self, dry_run: bool = False):
        self.events.append("install")
        self.installed = True
        return {
            "ok": True,
            "commands": [["openclaw", "gateway", "install"]],
            "status": {"installed": True, "mechanism": "vbs", "process_count": self.install_process_count},
            "dry_run": dry_run,
        }

    def start(self, dry_run: bool = False):
        self.events.append("start")
        self.listening = self.starts_listening
        return {"ok": True, "commands": [["openclaw", "gateway", "start"]], "mechanism": "vbs"}

    @contextlib.contextmanager
    def stopped(self, wait: bool = True, timeout_s: float = 600.0, dry_run: bool = False):
        self.events.append("stop")
        self.stopping = True
        try:
            yield {
                "ok": self.stop_ok,
                "commands": [] if self.stop_ok else [["taskkill.exe", "/PID", "4321", "/T", "/F"]],
                "stopped_pids": [] if self.stop_ok else [4321],
                "remaining_pids": [] if self.stop_ok else [4321],
                "dry_run": False,
            }
        finally:
            self.stopping = False
            self.events.append("start")

    def port_probe(self, port: int, host: str = "127.0.0.1") -> bool:
        return self.listening

    def process_lister(self):
        return []

    def time_provider(self) -> float:
        return self.now

    def sleeper(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += max(float(seconds), 0.5)

    def restart(self, wait: bool = True, timeout_s: float = 600.0, dry_run: bool = False):
        self.events.append("restart")
        return dict(self.restart_result)


def detection(config_path: Path, **overrides) -> Detection:
    """One snapshot of the fake machine, with only the asked-for facts."""
    installed = overrides.pop("installed", True)
    version = overrides.pop("version", pinned() if installed else None)
    claude = overrides.pop("claude", False)
    codex = overrides.pop("codex", False)
    go = overrides.pop("go", True)
    opencode = overrides.pop("opencode", True)
    n8n = overrides.pop("n8n", True)
    ollama = overrides.pop("ollama", ())
    companion = overrides.pop("companion", Companion())
    assert not overrides, f"unexpected overrides: {sorted(overrides)}"
    return Detection(
        openclaw=Installation(
            installed=installed, version=version, config_path=str(config_path),
            config_present=config_path.is_file(),
        ),
        claude=Login(binary=claude, login=True if claude else None),
        codex=Login(binary=codex, login=True if codex else None),
        opencode_go=go,
        local=LocalModels(ollama=tuple(LocalModelInfo(name=name) for name in ollama)),
        n8n=n8n,
        opencode=opencode,
        companion=companion,
    )


def which_map(missing: tuple[str, ...] = ()) -> dict[str, str]:
    return {
        name: f"/fake/bin/{name}"
        for name in ("node", "npm", "openclaw")
        if name not in missing
    }


def session_env(platform: str) -> dict[str, str]:
    """What a healthy login session exports, so the host preflight meets this machine.

    The preflight stops a Linux setup that has no systemd user bus. The fake machine
    is a healthy desktop, so it brings the variable a real desktop brings; the tests
    that want the broken session pass their own `env` and keep the stop.
    """
    return {"XDG_RUNTIME_DIR": "/run/user/1000"} if platform.startswith("linux") else {}


def onboard_writer(config_path: Path, workspace: Path, agents: dict[str, Path] | None = None):
    """What OpenClaw does on a fresh non-interactive onboarding, near enough."""

    def write() -> None:
        entries = {"main": {"name": "main", "workspace": str(workspace)}}
        for agent_id, directory in (agents or {}).items():
            entries[agent_id] = {"name": agent_id, "workspace": str(directory)}
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps({
            "gateway": {"mode": "local", "port": 18789, "bind": "loopback",
                        "auth": {"mode": "token", "token": "onboarding-token"}},
            "agents": {"entries": entries},
            "channels": {"telegram": {"enabled": True}},
        }, indent=2) + "\n", encoding="utf-8")
        for directory in [workspace, *(agents or {}).values()]:
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "AGENTS.md").write_text("OpenClaw's generic manual\n", encoding="utf-8")
            (directory / "BOOTSTRAP.md").write_text("first run rite\n", encoding="utf-8")

    return write


class Machine:
    """One fake machine: home, vault, config path, and the cast of fakes."""

    def __init__(self, root: Path):
        self.root = root
        self.home = root / "home"
        self.home.mkdir(parents=True, exist_ok=True)
        self.vault = root / "vault"
        self.config = self.home / ".openclaw" / "openclaw.json"
        self.state_dir = self.config.parent
        self.service = FakeService()
        self.runner = FakeRunner()
        self.runner.service = self.service
        self.lines: list[str] = []

    def run(self, *, detected=None, which=None, yes=True, env=None, platform=None, **kwargs) -> int:
        system = platform or sys.platform
        lookup = which if callable(which) else (which or which_map()).get
        return run_setup(
            home=self.home,
            env=session_env(system) if env is None else env,
            vault_data=self.vault,
            detected=detected if detected is not None else detection(self.config),
            which=lookup,
            run=self.runner,
            service=self.service,
            out=self.lines.append,
            stamp="20261023-120000",
            engine_root=ENGINE_ROOT,
            platform=system,
            yes=yes,
            **kwargs,
        )

    def output(self) -> str:
        return "\n".join(self.lines)

    def config_json(self) -> dict:
        return json.loads(self.config.read_text(encoding="utf-8"))


def test_a_fresh_machine_is_installed_configured_and_started(tmp_path):
    machine = Machine(tmp_path)
    workspace = machine.state_dir / "workspace"
    machine.runner.onboard = onboard_writer(machine.config, workspace)

    code = machine.run(detected=detection(machine.config, installed=False))

    assert code == 0
    commands = machine.runner.commands()
    assert f"{which_map()['npm']} install -g openclaw@{pinned()} " \
           "--allow-scripts=@google/genai,esbuild,koffi,protobufjs,openclaw" in commands
    assert any(" onboard --non-interactive --accept-risk " in command for command in commands)
    assert any(" config validate" in command for command in commands)
    assert machine.config.is_file()
    assert machine.service.events[0] == "stop" and "start" in machine.service.events
    assert (machine.home / ".openclaw" / "workspace" / "AGENTS.md").is_file()


def test_openclaw_missing_is_installed_at_the_pin(tmp_path):
    machine = Machine(tmp_path)
    machine.runner.onboard = onboard_writer(machine.config, machine.state_dir / "workspace")

    code = machine.run(detected=detection(machine.config, installed=False))

    assert code == 0
    install = next(
        call for call, _env in machine.runner.calls
        if call[1:3] == ["install", "-g"]
    )
    assert install[1:4] == ["install", "-g", f"openclaw@{pinned()}"]
    assert install[-1] == "--allow-scripts=@google/genai,esbuild,koffi,protobufjs,openclaw"


def test_another_version_is_realigned_to_the_pin(tmp_path):
    machine = Machine(tmp_path)
    machine.runner.onboard = onboard_writer(machine.config, machine.state_dir / "workspace")

    code = machine.run(detected=detection(machine.config, version="2026.1.1"))

    assert code == 0
    assert any(f"install -g openclaw@{pinned()}" in command for command in machine.runner.commands())


def test_a_machine_already_on_the_pin_runs_no_install(tmp_path):
    machine = Machine(tmp_path)
    machine.config.parent.mkdir(parents=True)
    machine.config.write_text(json.dumps({"gateway": {"auth": {"mode": "token", "token": "t"}}}), encoding="utf-8")

    assert machine.run() == 0
    assert not any(" install -g " in command for command in machine.runner.commands())


def test_an_old_node_stops_the_setup_before_installing(tmp_path):
    """OpenClaw declares a Node range; a 22.x Node fails on its native modules."""
    machine = Machine(tmp_path)
    machine.runner.node_version = "v22.0.0"

    code = machine.run(detected=detection(machine.config, installed=False))

    assert code == 1
    assert not any(" install -g " in command for command in machine.runner.commands())
    assert ">=24.22.1" in machine.output()
    assert "nodejs.org" in machine.output()


def test_a_linux_session_without_the_user_bus_stops_with_an_action(tmp_path):
    """Simulated Linux on every host: no session bus, so the gateway cannot be managed."""
    machine = Machine(tmp_path)

    code = machine.run(platform="linux", env={})

    assert code == 1
    assert "systemd user bus" in machine.output()
    assert not any(" install -g " in command for command in machine.runner.commands())
    assert not any(command.startswith("systemctl") for command in machine.runner.commands()), (
        "the setup asked systemctl about a session that has no bus"
    )
    assert machine.service.events == [], "the service was touched"
    assert not machine.config.exists(), "the setup wrote a configuration"


def test_a_linux_bus_that_cannot_be_reached_is_the_same_stop(tmp_path):
    machine = Machine(tmp_path)
    machine.runner.systemctl_error = "Failed to connect to bus: No such file or directory"

    code = machine.run(platform="linux", env={"XDG_RUNTIME_DIR": "/run/user/1000"})

    assert code == 1
    assert "systemd user bus" in machine.output()
    assert not any(" install -g " in command for command in machine.runner.commands())
    assert machine.service.events == [], "the service was touched"
    assert not machine.config.exists(), "the setup wrote a configuration"


def test_a_non_writable_npm_prefix_stops_with_the_command(tmp_path, monkeypatch):
    machine = Machine(tmp_path)
    machine.runner.npm_prefix = str(tmp_path / "global")
    monkeypatch.setattr("nexgen_core.openclaw.host_preflight.is_writable", lambda directory: False)

    code = machine.run(
        platform="linux", env={"XDG_RUNTIME_DIR": "/run/user/1000"},
        detected=detection(machine.config, installed=False),
    )

    assert code == 1
    assert "npm config set prefix ~/.local" in machine.output()
    assert not any(" install -g " in command for command in machine.runner.commands())


def test_a_machine_already_on_the_pin_is_not_blocked_by_an_unwritable_npm_prefix(tmp_path, monkeypatch):
    """No install runs, so npm's global prefix is not this setup's business."""
    machine = Machine(tmp_path)
    machine.config.parent.mkdir(parents=True)
    machine.config.write_text(json.dumps({"gateway": {"auth": {"mode": "token", "token": "t"}}}), encoding="utf-8")
    machine.runner.npm_prefix = str(tmp_path / "global")
    monkeypatch.setattr("nexgen_core.openclaw.host_preflight.is_writable", lambda directory: False)

    code = machine.run(platform="linux", env={"XDG_RUNTIME_DIR": "/run/user/1000"})

    assert code == 0
    assert "npm config set prefix" not in machine.output()


def test_lingering_off_is_told_without_stopping(tmp_path, monkeypatch):
    monkeypatch.setattr("nexgen_core.openclaw.host_preflight.LINGER_DIR", tmp_path / "linger")
    machine = Machine(tmp_path)
    machine.config.parent.mkdir(parents=True)
    machine.config.write_text(json.dumps({"gateway": {"auth": {"mode": "token", "token": "t"}}}), encoding="utf-8")

    code = machine.run(platform="linux", env={"XDG_RUNTIME_DIR": "/run/user/1000", "USER": "matte"})

    assert code == 0
    assert "loginctl enable-linger matte" in machine.output()


def test_lingering_on_says_nothing(tmp_path, monkeypatch):
    linger = tmp_path / "linger"
    linger.mkdir()
    (linger / "matte").write_text("", encoding="utf-8")
    monkeypatch.setattr("nexgen_core.openclaw.host_preflight.LINGER_DIR", linger)
    machine = Machine(tmp_path)
    machine.config.parent.mkdir(parents=True)
    machine.config.write_text(json.dumps({"gateway": {"auth": {"mode": "token", "token": "t"}}}), encoding="utf-8")

    assert machine.run(platform="linux", env={"XDG_RUNTIME_DIR": "/run/user/1000", "USER": "matte"}) == 0
    assert "loginctl enable-linger" not in machine.output()


def test_without_node_the_setup_stops_and_names_where_to_get_it(tmp_path):
    machine = Machine(tmp_path)

    code = machine.run(detected=detection(machine.config, installed=False), which=which_map(("node",)).get)

    assert code == 1
    assert machine.runner.calls == []
    assert "https://nodejs.org/en/download" in machine.output()
    assert not machine.config.exists()


def test_onboarding_runs_only_when_there_is_no_configuration(tmp_path):
    machine = Machine(tmp_path)
    machine.runner.onboard = onboard_writer(machine.config, machine.state_dir / "workspace")

    assert machine.run(dry_run=True) == 0
    assert any(" onboard " in command for command in machine.runner.commands()) is False

    machine2 = Machine(tmp_path / "second")
    machine2.config.parent.mkdir(parents=True)
    machine2.config.write_text(json.dumps({"gateway": {"auth": {"mode": "token", "token": "t"}}}), encoding="utf-8")
    assert machine2.run() == 0
    assert not any(" onboard " in command for command in machine2.runner.commands())


def test_dry_run_writes_nothing_and_runs_nothing(tmp_path):
    machine = Machine(tmp_path)
    machine.runner.onboard = onboard_writer(machine.config, machine.state_dir / "workspace")

    code = machine.run(dry_run=True)

    assert code == 0
    assert machine.runner.calls == []
    assert machine.service.events == []
    assert not machine.config.exists()
    assert not (machine.state_dir / "workspace").exists()
    assert "openclaw onboard" in machine.output()
    assert "gateway install" in machine.output()
    assert "config validate" in machine.output()
    assert "Dry run" in machine.output()


def test_the_plan_is_printed_and_a_no_leaves_everything_alone(tmp_path):
    machine = Machine(tmp_path)
    machine.config.parent.mkdir(parents=True)
    machine.config.write_text(json.dumps({"gateway": {"auth": {"mode": "token", "token": "t"}}}), encoding="utf-8")
    before = machine.config.read_bytes()
    asked: list[str] = []

    code = machine.run(yes=False, confirm=lambda question: asked.append(question) or False)

    assert code == 0
    assert "OpenClaw plan for" in machine.output()
    assert asked and "start the gateway?" in asked[0]
    assert machine.config.read_bytes() == before
    assert "stop" not in machine.service.events


def test_keys_and_plugins_of_the_person_survive_the_merge(tmp_path):
    machine = Machine(tmp_path)
    machine.config.parent.mkdir(parents=True)
    machine.config.write_text(json.dumps({
        "channels": {"telegram": {"enabled": True}},
        "mcp": {"servers": {"vault-library": {"command": "vault"}}},
        "plugins": {"allow": ["browser", "my-plugin"], "entries": {"my-plugin": {"enabled": True}}},
        "agents": {"entries": {"mio": {"name": "mio", "workspace": str(tmp_path / "mio")}}},
        "models": {"providers": {"ollama": {"models": [
            {"id": "mio-modello", "name": "mio-modello", "note": "mine"},
            {"id": "qwen3:8b", "name": "qwen3:8b", "contextWindow": 4096},
        ]}}},
    }, indent=2), encoding="utf-8")

    code = machine.run(detected=detection(machine.config, ollama=("qwen3:8b",)))

    assert code == 0
    written = machine.config_json()
    assert written["channels"] == {"telegram": {"enabled": True}}
    assert written["mcp"] == {"servers": {"vault-library": {"command": "vault"}}}
    assert "my-plugin" in written["plugins"]["allow"]
    assert written["plugins"]["allow"] == sorted(written["plugins"]["allow"])
    assert written["plugins"]["entries"]["my-plugin"] == {"enabled": True}
    assert written["agents"]["entries"]["mio"]["name"] == "mio"
    models = written["models"]["providers"]["ollama"]["models"]
    assert "mio-modello" in [entry["id"] for entry in models]
    kept = next(entry for entry in models if entry["id"] == "mio-modello")
    assert kept["note"] == "mine"
    caged = next(entry for entry in models if entry["id"] == "qwen3:8b")
    assert caged["contextWindow"] == 4096


def test_the_gateway_token_is_generated_only_when_missing_and_never_printed(tmp_path):
    machine = Machine(tmp_path)
    machine.config.parent.mkdir(parents=True)
    machine.config.write_text(json.dumps({"gateway": {"mode": "local"}}), encoding="utf-8")

    assert machine.run() == 0
    token = machine.config_json()["gateway"]["auth"]["token"]
    assert token and token != "generated-by-nexgen-setup"
    assert len(token) >= 32
    assert token not in machine.output()

    machine2 = Machine(tmp_path / "existing")
    machine2.config.parent.mkdir(parents=True)
    machine2.config.write_text(json.dumps({
        "gateway": {"mode": "local", "auth": {"mode": "token", "token": "the-person-token"}},
    }), encoding="utf-8")

    assert machine2.run() == 0
    assert machine2.config_json()["gateway"]["auth"]["token"] == "the-person-token"


def test_the_engine_plugin_is_enabled_only_when_it_is_deposited(tmp_path):
    without = Machine(tmp_path / "without")
    without.config.parent.mkdir(parents=True)
    without.config.write_text(json.dumps({"gateway": {"auth": {"mode": "token", "token": "t"}}}), encoding="utf-8")

    assert without.run() == 0
    assert PLUGIN_ID not in without.config_json()["plugins"]["allow"]

    with_plugin = Machine(tmp_path / "with")
    with_plugin.config.parent.mkdir(parents=True)
    with_plugin.config.write_text(json.dumps({"gateway": {"auth": {"mode": "token", "token": "t"}}}), encoding="utf-8")
    plugin = with_plugin.state_dir / "extensions" / PLUGIN_ID
    plugin.mkdir(parents=True)
    (plugin / "index.mjs").write_text("// plugin\n", encoding="utf-8")

    assert with_plugin.run() == 0
    written = with_plugin.config_json()
    assert PLUGIN_ID in written["plugins"]["allow"]
    assert written["plugins"]["entries"][PLUGIN_ID]["enabled"] is True


def test_a_rejected_configuration_puts_the_previous_file_back(tmp_path):
    machine = Machine(tmp_path)
    machine.config.parent.mkdir(parents=True)
    original = json.dumps({"gateway": {"auth": {"mode": "token", "token": "t"}}, "mine": 1}, indent=2) + "\n"
    machine.config.write_text(original, encoding="utf-8")
    machine.runner.fail_validate = True

    code = machine.run()

    assert code == 1
    assert machine.config.read_text(encoding="utf-8") == original
    backup = machine.state_dir / "openclaw.json.pre-nexgen-20261023-120000"
    assert backup.is_file()
    assert backup.read_text(encoding="utf-8") == original
    assert "rejected the configuration" in machine.output()
    assert "invalid config" in machine.output()


def test_the_write_happens_between_stop_and_start_even_on_failure(tmp_path):
    machine = Machine(tmp_path)
    machine.config.parent.mkdir(parents=True)
    machine.config.write_text(json.dumps({"gateway": {"auth": {"mode": "token", "token": "t"}}}), encoding="utf-8")
    machine.runner.fail_validate = True

    assert machine.run() == 1
    assert machine.service.events == ["stop", "start"]
    assert machine.runner.validate_while_stopped is True


def test_a_second_identical_setup_writes_nothing_and_restarts_nothing(tmp_path):
    machine = Machine(tmp_path)
    machine.config.parent.mkdir(parents=True)
    machine.config.write_text(json.dumps({"gateway": {"auth": {"mode": "token", "token": "t"}}}), encoding="utf-8")
    assert machine.run() == 0
    before = machine.config.read_bytes()
    stamp = machine.config.stat().st_mtime_ns

    machine.service = FakeService()
    machine.runner = FakeRunner()
    machine.runner.service = machine.service
    machine.lines = []

    assert machine.run() == 0
    assert machine.config.read_bytes() == before
    assert machine.config.stat().st_mtime_ns == stamp
    assert "stop" not in machine.service.events
    assert not any("validate" in command for command in machine.runner.commands())
    assert "nothing to write" in machine.output()


def test_a_file_that_holds_the_same_document_in_another_layout_is_untouched(tmp_path):
    """OpenClaw may rewrite the file with its own indent: same JSON is not a change."""
    machine = Machine(tmp_path)
    machine.config.parent.mkdir(parents=True)
    machine.config.write_text(json.dumps({"gateway": {"auth": {"mode": "token", "token": "t"}}}), encoding="utf-8")
    assert machine.run() == 0
    document = machine.config_json()
    reordered = dict(reversed(list(document.items())))
    machine.config.write_text(json.dumps(reordered, indent=4), encoding="utf-8")
    before = machine.config.read_bytes()
    machine.service = FakeService()
    machine.runner = FakeRunner()
    machine.runner.service = machine.service
    machine.lines = []

    assert machine.run() == 0

    assert machine.config.read_bytes() == before
    assert "stop" not in machine.service.events, "the gateway was cycled for a formatting difference"
    assert "nothing to write" in machine.output()


def test_a_manual_the_person_wrote_is_never_overwritten(tmp_path):
    machine = Machine(tmp_path)
    capo = machine.state_dir / "workspace-capo"
    capo.mkdir(parents=True)
    (capo / "AGENTS.md").write_text("il mio manuale\n", encoding="utf-8")
    (capo / "BOOTSTRAP.md").write_text("mio file\n", encoding="utf-8")
    machine.runner.onboard = onboard_writer(machine.config, machine.state_dir / "workspace")

    assert machine.run() == 0
    assert (capo / "AGENTS.md").read_text(encoding="utf-8") == "il mio manuale\n"
    assert (capo / "BOOTSTRAP.md").is_file()


def test_the_first_run_file_is_removed_only_from_a_folder_onboarding_made(tmp_path):
    machine = Machine(tmp_path)
    machine.runner.onboard = onboard_writer(machine.config, machine.state_dir / "workspace")

    assert machine.run() == 0
    fresh = machine.state_dir / "workspace"
    assert not (fresh / "BOOTSTRAP.md").exists()
    manual = (fresh / "AGENTS.md").read_text(encoding="utf-8")
    assert "OpenClaw's generic manual" not in manual
    assert "main assistant of this installation" in manual


def test_the_opencode_agent_name_is_translated_while_its_id_stays(tmp_path):
    i18n.set_language("it")
    machine = Machine(tmp_path)
    machine.config.parent.mkdir(parents=True)
    machine.config.write_text(json.dumps({"gateway": {"auth": {"mode": "token", "token": "t"}}}), encoding="utf-8")

    assert machine.run(detected=detection(machine.config, opencode=True, go=True)) == 0
    sviluppo = machine.config_json()["agents"]["entries"]["sviluppo"]
    assert sviluppo["name"] == "sviluppo"
    assert sviluppo["identity"] == {"name": "sviluppo"}
    assert sviluppo["model"] == "opencode-go/deepseek-v4.1-flash"


def test_the_opencode_agent_appears_only_with_its_command_and_its_key(tmp_path):
    machine = Machine(tmp_path)
    machine.config.parent.mkdir(parents=True)
    machine.config.write_text(json.dumps({"gateway": {"auth": {"mode": "token", "token": "t"}}}), encoding="utf-8")

    assert machine.run(detected=detection(machine.config, opencode=True, go=True)) == 0
    written = machine.config_json()
    sviluppo = written["agents"]["entries"]["sviluppo"]
    assert sviluppo["name"] == "developer"
    assert sviluppo["identity"] == {"name": "developer"}
    # A direct chat with an ACP-runtime agent answers with the default model, so the agent is native
    # and opens real OpenCode sessions over ACP; the bridge launches the installed OpenCode, not npx's V1.
    assert sviluppo["model"] == "opencode-go/deepseek-v4.1-flash"
    assert "runtime" not in sviluppo
    assert sviluppo["workspace"].endswith("workspace-sviluppo")
    assert written["plugins"]["entries"]["acpx"]["config"]["permissionMode"] == "approve-all"
    assert written["plugins"]["entries"]["acpx"]["config"]["nonInteractivePermissions"] == "deny"
    assert "opencode" in written["acp"]["allowedAgents"]
    assert {"main", "capo", "sviluppo"} <= set(written["tools"]["agentToAgent"]["allow"])
    assert set(written["agents"]["entries"]["sviluppo"]["tools"]["agentToAgent"]["send"]) == {"main", "capo"}

    for kwargs, label in (({"opencode": False, "go": True}, "no command"),
                          ({"opencode": True, "go": False}, "no key")):
        other = Machine(tmp_path / label.replace(" ", "-"))
        other.config.parent.mkdir(parents=True)
        other.config.write_text(json.dumps({"gateway": {"auth": {"mode": "token", "token": "t"}}}), encoding="utf-8")
        assert other.run(detected=detection(other.config, **kwargs)) == 0
        config = other.config_json()
        assert "sviluppo" not in config["agents"]["entries"]
        assert "acp" not in config
        assert "acpx" not in config["plugins"]["allow"]


def test_the_setup_can_leave_the_opencode_agent_out(tmp_path):
    machine = Machine(tmp_path)
    machine.config.parent.mkdir(parents=True)
    machine.config.write_text(json.dumps({"gateway": {"auth": {"mode": "token", "token": "t"}}}), encoding="utf-8")

    assert machine.run(detected=detection(machine.config, opencode=True, go=True), sviluppo=False) == 0
    assert "sviluppo" not in machine.config_json()["agents"]["entries"]


def test_gemini_never_enters_the_acp_allow_list(tmp_path):
    machine = Machine(tmp_path)
    machine.config.parent.mkdir(parents=True)
    machine.config.write_text(json.dumps({"gateway": {"auth": {"mode": "token", "token": "t"}}}), encoding="utf-8")

    assert machine.run(detected=detection(machine.config, claude=True, codex=True, opencode=True, go=True)) == 0
    config = machine.config_json()
    allowed = config["acp"]["allowedAgents"]
    assert allowed == ["claude", "codex", "opencode"]
    assert "gemini" not in allowed
    harnesses = json.dumps({key: config.get(key, {}) for key in ("acp", "plugins", "models")}).lower()
    assert "gemini" not in harnesses


def test_the_module_is_declared_local_for_this_host(tmp_path):
    machine = Machine(tmp_path)
    machine.config.parent.mkdir(parents=True)
    machine.config.write_text(json.dumps({"gateway": {"auth": {"mode": "token", "token": "t"}}}), encoding="utf-8")

    assert machine.run() == 0
    state = (machine.vault / "03-INFRA" / "agent-universal-layer" / "modules.state.yaml").read_text(encoding="utf-8")
    assert "openclaw: local" in state
    assert "hosts:" in state


def test_service_step_does_not_start_a_gateway_the_install_already_launched():
    """`openclaw gateway install` brings the daemon up: starting it again made two."""
    from types import SimpleNamespace

    from nexgen_core.openclaw.setup_steps import service_step

    class InstallLaunched(FakeService):
        def __init__(self):
            super().__init__(installed=False, listening=False)
            self.install_process_count = 1
            self.probes = 0

        def port_probe(self, port: int, host: str = "127.0.0.1") -> bool:
            self.probes += 1
            return self.probes > 2  # the daemon the install launched finishes its cold start

    service = InstallLaunched()
    lines: list[str] = []

    assert service_step(SimpleNamespace(service=service, out=lines.append), restarted=False) is True

    assert service.events.count("install") == 1
    assert "start" not in service.events, "the install already started it"
    assert any("the install started" in line for line in lines)


def test_the_commands_the_setup_prints_quote_a_path_with_a_space(tmp_path):
    """`C:\\Program Files\\nodejs\\npm.cmd` pasted unquoted fails on `C:\\Program`."""
    machine = Machine(tmp_path)
    machine.runner.onboard = onboard_writer(machine.config, machine.state_dir / "workspace")
    npm = r"C:\Program Files\nodejs\npm.cmd"
    which = which_map()
    which["npm"] = npm

    code = machine.run(dry_run=True, detected=detection(machine.config, installed=False), which=which)

    assert code == 0
    output = machine.output()
    assert f"{npm} install" not in output
    assert f'"{npm}" install' in output or f"'{npm}' install" in output


def test_service_step_waits_for_an_install_that_is_still_starting():
    """A slow install must not be doubled by an immediate start: wait, bounded, first."""
    from types import SimpleNamespace

    from nexgen_core.openclaw.setup_steps import service_step

    class SlowInstall(FakeService):
        def __init__(self):
            super().__init__(installed=False, listening=False)
            self.install_process_count = 0
            self.install_grace_seconds = 5
            self.now = 0.0

        def port_probe(self, port: int, host: str = "127.0.0.1") -> bool:
            return self.now >= 3.0

        def process_lister(self):
            if self.now < 2.0:
                return []
            return [ProcessInfo(900, "node.exe", "node C:\\openclaw\\dist\\index.js gateway")]

    service = SlowInstall()
    lines: list[str] = []

    assert service_step(SimpleNamespace(service=service, out=lines.append), restarted=False) is True

    assert "start" not in service.events, "the wait found the gateway the install was bringing up"
    assert service.sleeps[:2] == [1.0, 1.0], "the grace checks once a second, not twice"
    assert any("the install started" in line for line in lines)


def test_service_step_starts_the_gateway_when_the_install_only_registered_it():
    from types import SimpleNamespace

    from nexgen_core.openclaw.setup_steps import service_step

    class RegisteredOnly(FakeService):
        def __init__(self):
            super().__init__(installed=False, listening=False)
            self.install_process_count = 0
            self.install_grace_seconds = 0

    service = RegisteredOnly()
    lines: list[str] = []

    assert service_step(SimpleNamespace(service=service, out=lines.append), restarted=False) is True

    assert "start" in service.events
    assert any("Starting the gateway" in line for line in lines)


def test_a_configured_channel_gets_a_binding_without_touching_the_persons(tmp_path):
    machine = Machine(tmp_path)
    machine.config.parent.mkdir(parents=True)
    person = {"agentId": "support", "match": {"channel": "discord", "accountId": "*"}}
    machine.config.write_text(json.dumps({
        "gateway": {"auth": {"mode": "token", "token": "t"}},
        "channels": {"telegram": {"accounts": {"default": {"botToken": "x"}}}},
        "bindings": [person],
    }), encoding="utf-8")

    assert machine.run() == 0

    bindings = machine.config_json()["bindings"]
    assert person in bindings
    assert {"agentId": "main", "match": {"channel": "telegram", "accountId": "*"}} in bindings
    assert "telegram -> main" in machine.output()


def test_the_last_step_runs_the_doctor_and_reports(tmp_path):
    machine = Machine(tmp_path)
    machine.config.parent.mkdir(parents=True)
    machine.config.write_text(json.dumps({"gateway": {"auth": {"mode": "token", "token": "t"}}}), encoding="utf-8")

    assert machine.run() == 0
    assert "openclaw.installed" in machine.output()
    assert "Setup complete." in machine.output()


def test_a_gateway_that_never_answers_is_a_failed_setup(tmp_path):
    machine = Machine(tmp_path)
    machine.config.parent.mkdir(parents=True)
    machine.config.write_text(json.dumps({"gateway": {"auth": {"mode": "token", "token": "t"}}}), encoding="utf-8")
    machine.service.listening = False
    machine.service.starts_listening = False

    code = machine.run()

    assert code == 1
    assert "did not answer" in machine.output()
    assert "Waiting for the gateway the write step restarted" in machine.output()
    assert "seconds so far" in machine.output()
    assert machine.service.events.count("start") == 1


# ------------------------------------------------------------- the Windows app


def app_gateway(port: int | None = None) -> Companion:
    """The app's own gateway, as detection finds it on a Windows machine."""
    return Companion(
        present=True, owns_local_gateway=True, port=port,
        signals=("the app-owned WSL distribution OpenClawGateway",),
    )


def test_the_app_gateway_alone_stops_before_installing_or_writing(tmp_path):
    """Case (b): a native gateway beside the app's one would fight over the same port."""
    machine = Machine(tmp_path)

    code = machine.run(detected=detection(machine.config, installed=False, companion=app_gateway()))

    assert code == 1
    assert machine.runner.commands() == [], "no install, no onboarding, no service"
    assert not machine.config.is_file()
    assert "app-owned WSL distribution OpenClawGateway" in machine.output()
    assert "nothing was installed and nothing was written" in machine.output()
    assert "use the app as it is" in machine.output()
    assert f"install -g openclaw@{pinned()}" in machine.output(), "the second road is named with its command"


def test_a_dry_run_with_only_the_app_gateway_stops_the_same_way(tmp_path):
    machine = Machine(tmp_path)

    code = machine.run(
        detected=detection(machine.config, installed=False, companion=app_gateway()), dry_run=True,
    )

    assert code == 1
    assert "use the app as it is" in machine.output()
    assert not machine.config.is_file()


def test_both_gateways_proceed_and_the_plan_names_the_one_the_app_uses(tmp_path):
    """Case (c): the native setup runs as today, and the plan says the app is here too."""
    machine = Machine(tmp_path)
    machine.config.parent.mkdir(parents=True)
    machine.config.write_text(json.dumps({"gateway": {"auth": {"mode": "token", "token": "t"}}}), encoding="utf-8")

    code = machine.run(detected=detection(machine.config, companion=app_gateway(port=18789)))

    assert code == 0
    assert "has a gateway of its own on this PC" in machine.output()
    assert "connected to this PC's gateway on port 18789" in machine.output()


def test_an_app_gateway_on_its_own_port_is_named_as_that_one(tmp_path):
    machine = Machine(tmp_path)
    machine.config.parent.mkdir(parents=True)
    machine.config.write_text(json.dumps({"gateway": {"auth": {"mode": "token", "token": "t"}}}), encoding="utf-8")

    assert machine.run(detected=detection(machine.config, companion=app_gateway(port=52190))) == 0
    assert "a gateway of its own on port 52190" in machine.output()
