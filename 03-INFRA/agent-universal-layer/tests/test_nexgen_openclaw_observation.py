"""The watcher: when OpenClaw's root is the home, and what the engine writes about it.

The facts are OpenClaw's, not the engine's: hot reload admits `path.dirname` of the
state directory as its root, and on Windows that root is watched with one
recursive handle. `test_...the_engine_tells_the_gateway_to_poll` is the end of the
chain: the proposal carries `env.vars.CHOKIDAR_USEPOLLING`, which OpenClaw applies
to the gateway's process environment before the watcher exists.

Linux is deliberately left alone: the same step-up happens, but the watch is
non-recursive per directory, so the home is not walked. The test says so in the
one place a reader would otherwise expect symmetry.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import i18n  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw import observation  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw.detect import Detection, Installation, LocalModels, Login  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw.plan import build_plan  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw.profile import build_config  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw.setup_write import merged_document  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.report import Severity  # noqa: E402 - sys.path shim for cloned checkout

HOME = Path("C:/work/person")
DEFAULT_STATE = HOME / ".openclaw"


@pytest.fixture(autouse=True)
def _english():
    i18n.set_language("en")
    yield
    i18n.set_language(None)


def machine(config_path: Path) -> Detection:
    return Detection(
        openclaw=Installation(installed=True, version="2026.10.6", config_path=str(config_path)),
        claude=Login(binary=False),
        codex=Login(binary=False),
        opencode_go=True,
        local=LocalModels(),
        n8n=False,
    )


def test_the_root_is_one_level_above_the_state_directory():
    assert observation.observation_root(DEFAULT_STATE) == HOME


def test_the_default_state_directory_puts_the_home_under_the_watcher():
    assert observation.watches_the_home(DEFAULT_STATE, HOME) is True


def test_a_state_directory_deeper_under_the_home_does_not():
    """`~/work/oc` is watched from `~/work`: the home's temp folders are not in that tree."""
    assert observation.watches_the_home(HOME / "work" / "oc", HOME) is False


def test_a_state_directory_outside_the_home_does_not():
    outside = Path("/var/lib/openclaw") if Path("/var/lib").is_absolute() else Path("D:/state/openclaw")
    assert observation.watches_the_home(outside, HOME) is False


def test_the_comparison_ignores_case_the_way_windows_does():
    assert observation.is_inside(Path("C:/work/Person/AppData"), Path("c:/work/person"), platform="win32") is True


def test_the_comparison_respects_case_on_linux():
    assert observation.is_inside(Path("/work/Person/AppData"), Path("/work/person"), platform="linux") is False
    assert observation.is_inside(Path("/work/person/AppData"), Path("/work/person"), platform="linux") is True


def test_the_engine_tells_the_gateway_to_poll_on_windows():
    config = build_config(machine(DEFAULT_STATE / "openclaw.json"), home=HOME, platform="win32")
    assert config["env"] == {"vars": {"CHOKIDAR_USEPOLLING": "1"}}


def test_linux_is_not_told_to_poll():
    """The watch there is one directory, not the tree, so polling would only cost events."""
    config = build_config(machine(DEFAULT_STATE / "openclaw.json"), home=HOME, platform="linux")
    assert "env" not in config


def test_a_state_directory_outside_the_home_leaves_the_environment_alone():
    config = build_config(machine(Path("/srv/openclaw/openclaw.json")), home=HOME, platform="win32")
    assert "env" not in config


def test_without_a_home_nothing_is_proposed():
    """The safe direction: a caller that does not say where the home is gets no key."""
    assert "env" not in build_config(machine(DEFAULT_STATE / "openclaw.json"), platform="win32")


def test_the_person_s_own_environment_variables_are_kept(tmp_path):
    current = {"env": {"vars": {"OPENROUTER_API_KEY": "not-a-real-key", "CHOKIDAR_USEPOLLING": "1"}}}
    proposed = build_config(machine(DEFAULT_STATE / "openclaw.json"), current=current, home=HOME, platform="win32")
    merged = merged_document(current, proposed)
    assert merged["env"]["vars"]["OPENROUTER_API_KEY"] == "not-a-real-key"


def test_a_value_the_person_wrote_is_never_overwritten_even_when_it_says_zero():
    """`0` is a choice: the gateway keeps its event watch, and the engine stays out of it."""
    current = {"env": {"vars": {"CHOKIDAR_USEPOLLING": "0"}}}

    proposed = build_config(machine(DEFAULT_STATE / "openclaw.json"), current=current, home=HOME, platform="win32")
    merged = merged_document(current, proposed)

    assert "env" not in proposed
    assert merged["env"]["vars"]["CHOKIDAR_USEPOLLING"] == "0"


def test_a_person_who_turned_polling_off_gets_no_restart_note(tmp_path):
    home = tmp_path / "home"
    config_path = home / ".openclaw" / "openclaw.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text('{"env": {"vars": {"CHOKIDAR_USEPOLLING": "0"}}}', encoding="utf-8")

    plan = build_plan(
        detected=machine(config_path), home=home, env={}, platform="win32", plugin_deposited=False,
    )

    assert "env.vars.CHOKIDAR_USEPOLLING" not in plan.added
    assert not any("restart" in note.lower() for note in plan.notes)


def test_the_plan_says_that_a_restart_is_what_makes_the_key_take_effect(tmp_path):
    """The setup does not cycle the gateway for this key alone; the plan says what does."""
    home = tmp_path / "home"
    config_path = home / ".openclaw" / "openclaw.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("{}", encoding="utf-8")
    plan = build_plan(
        detected=machine(config_path), home=home, env={}, platform="win32", plugin_deposited=False,
    )

    assert "env.vars.CHOKIDAR_USEPOLLING" in plan.added
    assert any("restart" in note.lower() for note in plan.notes)

    config_path.write_text('{"env": {"vars": {"CHOKIDAR_USEPOLLING": "1"}}}', encoding="utf-8")
    second = build_plan(
        detected=machine(config_path), home=home, env={}, platform="win32", plugin_deposited=False,
    )
    assert not any("restart" in note.lower() for note in second.notes)


def test_every_value_openclaw_reads_as_polling_is_read_the_same_way_here():
    for value in ("1", "true", "yes"):
        assert observation.forces_polling({"env": {"vars": {"CHOKIDAR_USEPOLLING": value}}}) is True
    for value in ("0", "false", "", "  "):
        assert observation.forces_polling({"env": {"vars": {"CHOKIDAR_USEPOLLING": value}}}) is False
    assert observation.forces_polling({}) is False


def test_the_doctor_names_the_recursive_watch_and_its_fix():
    line = observation.report({}, DEFAULT_STATE / "openclaw.json", HOME, "win32")
    assert line.severity is Severity.WARN
    assert "recursive" in line.message
    assert "nexgen openclaw setup" in line.action


def test_the_doctor_is_quiet_once_the_key_is_written():
    config = {"env": {"vars": {"CHOKIDAR_USEPOLLING": "1"}}}
    line = observation.report(config, DEFAULT_STATE / "openclaw.json", HOME, "win32")
    assert line.severity is Severity.OK
    assert "next gateway start" in line.message


def test_the_doctor_leaves_linux_alone():
    line = observation.report({}, DEFAULT_STATE / "openclaw.json", HOME, "linux")
    assert line.severity is Severity.OK
    assert "Linux" in line.message


def test_polling_that_nobody_needs_is_named_as_a_leftover():
    config = {"env": {"vars": {"CHOKIDAR_USEPOLLING": "1"}}}
    line = observation.report(config, Path("/srv/openclaw/openclaw.json"), HOME, "win32")
    assert line.severity is Severity.WARN
    assert "Remove env.vars.CHOKIDAR_USEPOLLING" in line.action
