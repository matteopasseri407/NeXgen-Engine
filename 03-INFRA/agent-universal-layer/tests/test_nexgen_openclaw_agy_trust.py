"""The setup writes `agy`'s trust list instead of asking the person to.

`agy` stops on a trust prompt for every folder an agent starts in, and the
plugin cannot answer that prompt: it is `agy`'s own settings, one absolute
path per folder under `trustedWorkspaces`. The setup knows the folders the
agents of this configuration work in, so it records them there, once, with the
file backed up first.

One promise per test:

- the missing folders are appended, every other key and the existing order stay,
  and the previous file is saved beside it;
- a machine whose folders are all trusted keeps its file byte for byte;
- a missing file is created with the one key, and the parent folder with it;
- a settings file this engine cannot read is left untouched, with the manual
  command the setup printed before this change;
- `--dry-run` prints the plan and writes nothing;
- an agent with no declared `workspace` gets the folder OpenClaw resolves for
  it, not its absence from the list;
- no `agy` on PATH means nothing is written at all.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from test_nexgen_openclaw_agy_setup import (  # noqa: E402 - the fake machine and plugin runner this suite already drives
    SQUAD_WITH_AGY,
    AgyRunner,
    machine_with_squad,
    pinned_plugin,
)
from test_nexgen_openclaw_setup import (  # noqa: E402 - the fake machine this suite already drives
    Machine,
    detection,
    session_env,
    which_map,
)

from nexgen_core import i18n  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw import agy_prereqs  # noqa: E402

AGY_MODEL = "google-antigravity-cli/gemini-3.8-flash"

#: A team whose only agent on `agy` declares no `workspace`: OpenClaw resolves it.
SQUAD_WITH_UNPLACED_AGENT = f"""agents:
  assistente:
    model: {AGY_MODEL}
"""


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


def which_with_agy(name: str) -> str | None:
    """The fake PATH, with the `agy` command a machine that has it would answer."""
    return "/fake/bin/agy" if name == "agy" else which_map().get(name)


def run_setup(machine: Machine, runner: AgyRunner, *, agy: bool = True, dry_run: bool = False) -> int:
    """The whole setup, on a healthy session, with or without the `agy` command."""
    return machine.run(
        detected=detection(machine.config),
        env={"PATH": "/fake/bin", **session_env(sys.platform)},
        which=which_with_agy if agy else which_map().get,
        dry_run=dry_run,
    )


def settings_file(machine: Machine) -> Path:
    """`agy`'s own settings file, where the trust list lives."""
    return agy_prereqs.settings_path(machine.home)


def write_settings(machine: Machine, document: dict) -> bytes:
    """Put a settings file in place and return the bytes, for the backup to match."""
    path = settings_file(machine)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(document, indent=2).encode("utf-8")
    path.write_bytes(data)
    return data


def read_settings(machine: Machine) -> dict:
    return json.loads(settings_file(machine).read_text(encoding="utf-8"))


def trust_backups(machine: Machine) -> list[Path]:
    """The copies this step keeps before a write."""
    return sorted(settings_file(machine).parent.glob("settings.json.pre-agy-trust-*.bak"))


def test_missing_folders_are_appended_with_a_backup_and_every_other_key_kept(tmp_path):
    machine, runner = machine_with_squad(tmp_path, SQUAD_WITH_AGY)
    original = write_settings(machine, {
        "artifactReviewPolicy": "keep",
        "trustedWorkspaces": ["/srv/agy-login"],
        "toolPermission": "ask",
    })

    assert run_setup(machine, runner) == 0, machine.output()

    settings = read_settings(machine)
    assert settings["artifactReviewPolicy"] == "keep", "a key the person wrote was lost"
    assert settings["toolPermission"] == "ask", "a key the person wrote was lost"
    trusted = settings["trustedWorkspaces"]
    assert trusted[0] == "/srv/agy-login", "the existing order was not preserved"
    added = trusted[1:]
    assert added, "the folders the agents work in were not added"
    assert all(str(machine.state_dir) in folder for folder in added), (
        "a folder outside the state folder was recorded"
    )

    backups = trust_backups(machine)
    assert len(backups) == 1, "the previous settings were not saved"
    assert backups[0].read_bytes() == original, "the backup is not the file that was there"
    assert f"trusted with {len(added)} more agent folders" in machine.output()
    assert backups[0].name in machine.output(), "the line must say where the backup is"


def test_a_machine_already_trusted_keeps_its_file_byte_for_byte(tmp_path):
    machine, runner = machine_with_squad(tmp_path, SQUAD_WITH_AGY)
    assert run_setup(machine, runner) == 0, machine.output()
    path = settings_file(machine)
    before_bytes = path.read_bytes()
    before_mtime = path.stat().st_mtime_ns
    before_backups = len(trust_backups(machine))

    assert run_setup(machine, runner) == 0, machine.output()

    assert path.read_bytes() == before_bytes, "a fully trusted file was rewritten"
    assert path.stat().st_mtime_ns == before_mtime, "a fully trusted file was touched"
    assert len(trust_backups(machine)) == before_backups, "a file nothing changed was backed up"


def test_a_missing_settings_file_is_created_with_only_the_trust_key(tmp_path):
    machine, runner = machine_with_squad(tmp_path, SQUAD_WITH_AGY)
    assert not settings_file(machine).exists()

    assert run_setup(machine, runner) == 0, machine.output()

    settings = read_settings(machine)
    assert list(settings) == ["trustedWorkspaces"], "a new file carries a key nobody asked for"
    assert settings["trustedWorkspaces"], "the agent folders were not recorded"
    assert trust_backups(machine) == [], "there was no previous file to save"
    assert "was created" in machine.output()


def test_a_broken_settings_file_is_left_untouched_and_the_manual_step_is_printed(tmp_path):
    machine, runner = machine_with_squad(tmp_path, SQUAD_WITH_AGY)
    path = settings_file(machine)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ this is not json", encoding="utf-8")
    before = path.read_bytes()

    assert run_setup(machine, runner) == 0, machine.output()

    assert path.read_bytes() == before, "a file this engine cannot read was overwritten"
    assert trust_backups(machine) == [], "a broken file was replaced without a backup to restore"
    assert "'agy' has not been trusted with these agent folders" in machine.output()
    assert "Run 'agy' once in each of them" in machine.output(), "the manual command is no longer printed"


def test_a_dry_run_prints_the_trust_plan_and_writes_nothing(tmp_path):
    machine, runner = machine_with_squad(tmp_path, SQUAD_WITH_AGY)
    runner.write_manifest(pinned_plugin()["version"])
    original = write_settings(machine, {"trustedWorkspaces": ["/srv/agy-login"]})

    assert run_setup(machine, runner, dry_run=True) == 0, machine.output()

    assert settings_file(machine).read_bytes() == original, "a dry run wrote the settings"
    assert trust_backups(machine) == [], "a dry run made a backup"
    assert "would be trusted with" in machine.output(), "the dry run must say what a real one would do"
    assert "nothing is written" in machine.output()


def test_an_agent_without_a_declared_workspace_gets_the_folder_openclaw_resolves(tmp_path):
    machine, runner = machine_with_squad(tmp_path, SQUAD_WITH_UNPLACED_AGENT)

    assert run_setup(machine, runner) == 0, machine.output()

    trusted = read_settings(machine)["trustedWorkspaces"]
    assert str(machine.state_dir / "workspace-assistente") in trusted, (
        "the default folder OpenClaw resolves for an agent without `workspace` was not trusted"
    )


def test_agent_workspaces_resolves_the_defaults_openclaw_would(tmp_path):
    defaults = str(tmp_path / "agents")
    declared = str(tmp_path / "one")
    config = {
        "agents": {
            "defaults": {"workspace": defaults},
            "entries": {
                "main": {},
                "capo": {"workspace": declared},
                "assistente": {},
            },
        },
    }

    folders = agy_prereqs.agent_workspaces(config, tmp_path / "state", tmp_path / "home")

    assert folders == [
        defaults,
        declared,
        str(Path(defaults) / "assistente"),
    ], "the order is the configuration's, and the defaults are OpenClaw's own"
    assert agy_prereqs.agent_workspaces(config) == [declared], (
        "a caller with no state folder can only report what the file declares"
    )


def test_no_agy_command_means_no_trust_is_written(tmp_path):
    machine, runner = machine_with_squad(tmp_path, SQUAD_WITH_AGY)
    original = write_settings(machine, {"trustedWorkspaces": []})

    assert run_setup(machine, runner, agy=False) == 0, machine.output()

    assert settings_file(machine).read_bytes() == original, "trust was written for a command that is absent"
    assert trust_backups(machine) == []
    assert "The 'agy' command is not on PATH" in machine.output()
