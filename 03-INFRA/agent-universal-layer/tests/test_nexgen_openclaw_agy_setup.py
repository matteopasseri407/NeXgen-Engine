"""The `agy` plugin in the setup, and the doctor's three lines about that backend.

The plugin `google-antigravity-cli` is third-party, so the engine installs it at
one exact release and only when the team asked for it. Every test here builds the
same fake machine the setup suite already drives: a fake detector, a fake
executor that records the command line it was given, and a fake service. Nothing
reads the real home, runs `openclaw`, or opens a socket.

One promise per test:

- a squad with no `agy` agent runs no install and writes the same `plugins` block;
- a squad with one installs the pinned release exactly once, then allows, enables
  and gives it a guarded mode;
- a plugin already at the pin runs nothing;
- a plugin at another release is reinstalled at the pin;
- `--dry-run` prints the command and executes nothing;
- a failed install stops the setup with nothing written;
- the doctor names the missing plugin and the exact command, the missing `agy`
  command, an untrusted workspace, and keeps the permission-mode line for a
  backend whose plugin is there.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from test_nexgen_openclaw_setup import (  # noqa: E402 - the fake machine this suite already drives
    ENGINE_ROOT,
    Machine,
    detection,
    onboard_writer,
    session_env,
)

from nexgen_core import i18n  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw import agy_prereqs  # noqa: E402
from nexgen_core.openclaw.doctor_agy import check_local_agy  # noqa: E402
from nexgen_core.openclaw.plan import build_plan  # noqa: E402
from nexgen_core.openclaw_service import RunResult  # noqa: E402
from nexgen_core.report import Severity  # noqa: E402

PIN_FILE = Path(__file__).resolve().parent.parent / "openclaw" / "agy-plugin.json"
BACKEND = "google-antigravity-cli"
AGY_MODEL = "google-antigravity-cli/gemini-3.8-flash"

#: A squad that runs its assistant on the local `agy` CLI.
SQUAD_WITH_AGY = f"""agents:
  main:
    name: assistente
    model: {AGY_MODEL}
  capo:
    model: opencode-go/deepseek-v4-pro
"""

#: The same team, on a model that needs no plugin at all.
SQUAD_WITHOUT_AGY = """agents:
  main:
    name: assistente
    model: opencode-go/deepseek-v4.1-flash
  capo:
    model: opencode-go/deepseek-v4-pro
"""


def pinned_plugin() -> dict[str, str]:
    """The plugin and release the pin file declares, read rather than repeated."""
    return json.loads(PIN_FILE.read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _english_and_no_sockets(monkeypatch):
    """English output to assert on, and never a real TCP connect to the gateway port."""
    i18n.set_language("en")
    monkeypatch.setattr("nexgen_core.openclaw.doctor_runtime.default_connect", lambda host, port, timeout: False)
    monkeypatch.delenv("OPENCLAW_CONFIG_PATH", raising=False)
    monkeypatch.delenv("OPENCLAW_STATE_DIR", raising=False)

    def _fake_plugin_step(setup):
        setup.out(i18n.t("The engine plugin is deposited and enabled."))
        return True

    monkeypatch.setattr("nexgen_core.openclaw.setup.plugin_step", _fake_plugin_step)
    yield
    i18n.set_language(None)


class AgyRunner:
    """The executor: records every command, and plays OpenClaw's plugin installer.

    `version` is the release the install leaves on disk, so a test can play a
    gateway whose install worked, one that installed another release, and one
    that failed.
    """

    def __init__(self, state_dir: Path, version: str | None = None):
        from test_nexgen_openclaw_setup import FakeRunner

        self._inner = FakeRunner()
        self.state_dir = state_dir
        self.version = version or pinned_plugin()["version"]
        self.install_fails = False
        self.service = None

    def __call__(self, argv, env=None):
        if argv[1:3] == ["plugins", "install"]:
            self._inner.calls.append((list(argv), dict(env or {})))
            if self.install_fails:
                return RunResult(1, "", "npm ERR! 404 Not Found")
            self.write_manifest(self.version)
            return RunResult(0, "", "")
        return self._inner(argv, env)

    def write_manifest(self, version: str) -> None:
        directory = agy_prereqs.extension_dir(self.state_dir)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "package.json").write_text(
            json.dumps({"name": pinned_plugin()["package"], "version": version}), encoding="utf-8",
        )

    def commands(self) -> list[str]:
        return self._inner.commands()

    @property
    def calls(self):
        return self._inner.calls

    @property
    def service(self):
        return self._inner.service

    @service.setter
    def service(self, value):
        self._inner.service = value


def machine_with_squad(tmp_path: Path, squad: str) -> tuple[Machine, AgyRunner]:
    """A fake machine whose Vault carries this squad, and the executor that installs plugins."""
    machine = Machine(tmp_path)
    machine.vault.mkdir(parents=True, exist_ok=True)
    (machine.vault / "squad.yaml").write_text(squad, encoding="utf-8")
    runner = AgyRunner(machine.state_dir)
    machine.runner = runner
    runner.service = machine.service
    runner._inner.onboard = onboard_writer(machine.config, machine.state_dir / "workspace")
    return machine, runner


def run(machine: Machine, runner: AgyRunner, *, dry_run: bool = False) -> int:
    """The whole setup, with a healthy session so a Linux runner meets the preflight."""
    system = sys.platform
    return machine.run(
        detected=detection(machine.config),
        env={"PATH": "/fake/bin", **session_env(system)},
        dry_run=dry_run,
    )


def install_commands(runner: AgyRunner) -> list[str]:
    """The plugin installs this run executed, as a person would type them back."""
    return [command for command in runner.commands() if "plugins install" in command]


def plugin_entry(machine: Machine) -> dict:
    return machine.config_json()["plugins"]["entries"][BACKEND]


def test_a_squad_without_an_agy_agent_installs_nothing_and_writes_the_same_plugins(tmp_path):
    machine, runner = machine_with_squad(tmp_path / "with-squad", SQUAD_WITHOUT_AGY)
    other = Machine(tmp_path / "no-squad")
    other.runner.onboard = onboard_writer(other.config, other.state_dir / "workspace")

    assert run(machine, runner) == 0, machine.output()
    assert run(other, other.runner) == 0, other.output()

    assert install_commands(runner) == [], "a team that names no agy model must not install a plugin"
    assert BACKEND not in machine.config_json()["plugins"]["allow"]
    assert machine.config_json()["plugins"] == other.config_json()["plugins"], (
        "the plugins block differs from a machine with no squad at all"
    )


def test_a_squad_with_an_agy_agent_installs_the_pinned_plugin_once_and_enables_it(tmp_path):
    machine, runner = machine_with_squad(tmp_path, SQUAD_WITH_AGY)

    assert run(machine, runner) == 0, machine.output()

    plugin = pinned_plugin()
    assert install_commands(runner) == [
        f"/fake/bin/openclaw plugins install npm:{plugin['package']}@{plugin['version']} --force --no-enable",
    ]
    plugins = machine.config_json()["plugins"]
    assert BACKEND in plugins["allow"]
    assert plugin_entry(machine) == {"enabled": True, "config": {"permissionMode": "sandbox"}}
    assert plugin["version"] in machine.output(), "the release installed is named"


def test_the_pinned_plugin_already_on_disk_runs_no_install(tmp_path):
    machine, runner = machine_with_squad(tmp_path, SQUAD_WITH_AGY)
    runner.write_manifest(pinned_plugin()["version"])

    assert run(machine, runner) == 0, machine.output()

    assert install_commands(runner) == []
    assert plugin_entry(machine)["enabled"] is True


def test_another_plugin_version_is_reinstalled_at_the_pin(tmp_path):
    machine, runner = machine_with_squad(tmp_path, SQUAD_WITH_AGY)
    runner.write_manifest("0.0.9")

    assert run(machine, runner) == 0, machine.output()

    plugin = pinned_plugin()
    assert install_commands(runner) == [
        f"/fake/bin/openclaw plugins install npm:{plugin['package']}@{plugin['version']} --force --no-enable",
    ]
    assert agy_prereqs.installed_version(machine.state_dir) == plugin["version"]


def test_a_dry_run_prints_the_install_command_and_runs_nothing(tmp_path):
    machine, runner = machine_with_squad(tmp_path, SQUAD_WITH_AGY)

    assert run(machine, runner, dry_run=True) == 0, machine.output()

    plugin = pinned_plugin()
    assert f"plugins install npm:{plugin['package']}@{plugin['version']} --force --no-enable" in machine.output()
    assert runner.calls == [], "a dry run executed a command"
    assert not machine.config.exists(), "a dry run wrote the configuration"


def test_a_failed_install_stops_the_setup_with_nothing_written(tmp_path):
    machine, runner = machine_with_squad(tmp_path, SQUAD_WITH_AGY)
    runner.install_fails = True

    assert run(machine, runner) == 1

    assert "The Antigravity plugin install failed" in machine.output()
    assert list(machine.state_dir.glob("openclaw.json.pre-nexgen-*")) == [], (
        "the write step ran after a failed install"
    )
    assert BACKEND not in machine.config_json().get("plugins", {}).get("allow", [])


def test_a_guarded_mode_already_chosen_is_kept(tmp_path):
    """`settings` is the person's choice: the engine aligns the plugin and leaves the mode alone."""
    machine, runner = machine_with_squad(tmp_path, SQUAD_WITH_AGY)
    runner.write_manifest(pinned_plugin()["version"])
    machine.config.parent.mkdir(parents=True, exist_ok=True)
    machine.config.write_text(json.dumps({
        "plugins": {"allow": [BACKEND], "entries": {BACKEND: {"enabled": True, "config": {"permissionMode": "settings"}}}},
        "agents": {"entries": {"main": {"name": "main", "model": AGY_MODEL}}},
    }, indent=2) + "\n", encoding="utf-8")

    assert run(machine, runner) == 0, machine.output()

    assert plugin_entry(machine)["config"]["permissionMode"] == "settings"


def by_id(outcomes) -> dict:
    """The doctor's lines, keyed by check id."""
    return {outcome.id: outcome for outcome in outcomes}


def agy_config(workspace: Path | None = None) -> dict:
    """A configuration that runs an agent on the local `agy` CLI, with a guarded mode."""
    entry: dict = {"model": AGY_MODEL}
    if workspace is not None:
        entry["workspace"] = str(workspace)
    return {
        "plugins": {"allow": ["browser", BACKEND], "entries": {BACKEND: {"enabled": True, "config": {"permissionMode": "sandbox"}}}},
        "agents": {"entries": {"main": entry}},
    }


def settings_with(home: Path, trusted: list[str]) -> None:
    """`agy`'s own settings, with the folders it was trusted with."""
    path = agy_prereqs.settings_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"trustedWorkspaces": trusted}), encoding="utf-8")


def test_the_doctor_names_the_missing_plugin_and_the_command(tmp_path):
    outcomes = by_id(check_local_agy(agy_config(), state_dir=tmp_path / "state", home=tmp_path / "home",
                                     which=lambda name: "/fake/bin/agy"))

    plugin = pinned_plugin()
    assert "openclaw.google.cli_backend.plugin" in outcomes
    outcome = outcomes["openclaw.google.cli_backend.plugin"]
    assert outcome.severity == Severity.WARN
    assert BACKEND in outcome.message and "not installed" in outcome.message
    assert f"npm:{plugin['package']}@{plugin['version']}" in (outcome.action or "")
    assert "--force" in (outcome.action or "")


def test_the_doctor_names_a_plugin_at_another_version(tmp_path):
    state = tmp_path / "state"
    directory = agy_prereqs.extension_dir(state)
    directory.mkdir(parents=True)
    (directory / "package.json").write_text(json.dumps({"version": "0.0.9"}), encoding="utf-8")

    outcomes = by_id(check_local_agy(agy_config(), state_dir=state, home=tmp_path / "home",
                                     which=lambda name: "/fake/bin/agy"))

    outcome = outcomes["openclaw.google.cli_backend.plugin"]
    assert outcome.severity == Severity.WARN
    assert "0.0.9" in outcome.message and pinned_plugin()["version"] in outcome.message


def test_the_doctor_names_a_missing_agy_command(tmp_path):
    state = tmp_path / "state"
    directory = agy_prereqs.extension_dir(state)
    directory.mkdir(parents=True)
    (directory / "package.json").write_text(json.dumps({"version": pinned_plugin()["version"]}), encoding="utf-8")

    outcomes = by_id(check_local_agy(agy_config(), state_dir=state, home=tmp_path / "home", which=lambda name: None))

    outcome = outcomes["openclaw.google.cli_backend.agy"]
    assert outcome.severity == Severity.WARN
    assert "'agy' command" in outcome.message and "not on PATH" in outcome.message
    assert "Antigravity CLI" in (outcome.action or "")


def test_the_doctor_names_a_workspace_agy_was_not_trusted_with(tmp_path):
    state = tmp_path / "state"
    directory = agy_prereqs.extension_dir(state)
    directory.mkdir(parents=True)
    (directory / "package.json").write_text(json.dumps({"version": pinned_plugin()["version"]}), encoding="utf-8")
    home = tmp_path / "home"
    settings_with(home, [])
    workspace = tmp_path / "workspace"

    outcomes = by_id(check_local_agy(agy_config(workspace), state_dir=state, home=home,
                                     which=lambda name: "/fake/bin/agy"))

    outcome = outcomes["openclaw.google.cli_backend.agy"]
    assert outcome.severity == Severity.WARN
    assert str(workspace) in outcome.message
    assert "trustedWorkspaces" in (outcome.action or "")


def test_a_trusted_workspace_leaves_the_permission_mode_line(tmp_path):
    state = tmp_path / "state"
    directory = agy_prereqs.extension_dir(state)
    directory.mkdir(parents=True)
    (directory / "package.json").write_text(json.dumps({"version": pinned_plugin()["version"]}), encoding="utf-8")
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    settings_with(home, [str(workspace)])

    outcomes = by_id(check_local_agy(agy_config(workspace), state_dir=state, home=home,
                                     which=lambda name: "/fake/bin/agy"))

    assert "openclaw.google.cli_backend.agy" not in outcomes
    assert outcomes["openclaw.google.cli_backend"].severity == Severity.OK
    assert "permissionMode 'sandbox'" in outcomes["openclaw.google.cli_backend"].message


def test_the_plan_shows_the_plugin_it_would_install(tmp_path):
    """`nexgen openclaw plan` writes nothing and shows the entries the setup would add."""
    machine, runner = machine_with_squad(tmp_path, SQUAD_WITH_AGY)

    plan = build_plan(
        detected=detection(machine.config), home=machine.home, vault_data=machine.vault,
        engine_root=ENGINE_ROOT,
    )

    plugins = plan.config["plugins"]
    assert BACKEND in plugins["allow"]
    assert plugins["entries"][BACKEND]["enabled"] is True
    assert plugins["entries"][BACKEND]["config"]["permissionMode"] == "sandbox"
    assert runner.calls == [], "a plan executed a command"
    assert not machine.config.exists(), "a plan wrote the configuration"


def test_the_setup_step_says_what_agy_still_needs(tmp_path):
    """The plugin is installed; the command is not on PATH, so the step says so and continues."""
    machine, runner = machine_with_squad(tmp_path, SQUAD_WITH_AGY)

    assert run(machine, runner) == 0, machine.output()

    assert "The 'agy' command is not on PATH" in machine.output()


def test_no_agy_agent_means_no_step_and_no_line(tmp_path):
    machine, runner = machine_with_squad(tmp_path, SQUAD_WITHOUT_AGY)

    assert run(machine, runner) == 0, machine.output()

    assert "'agy'" not in machine.output(), machine.output()


def test_an_unreadable_pin_installs_nothing_and_says_why(tmp_path, monkeypatch):
    """The pin is data: a machine that cannot read it installs no unknown release."""
    machine, runner = machine_with_squad(tmp_path, SQUAD_WITH_AGY)
    monkeypatch.setattr("nexgen_core.openclaw.setup_agy.agy_prereqs.plugin_pin", lambda engine_root=None: None)

    assert run(machine, runner) == 1

    assert "cannot read its Antigravity plugin pin" in machine.output()
    assert install_commands(runner) == []
    assert list(machine.state_dir.glob("openclaw.json.pre-nexgen-*")) == [], (
        "the write step ran without the plugin"
    )
