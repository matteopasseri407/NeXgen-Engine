"""The `nexgen` plugin: one id, one deposit, two jobs, and a parity control that proves it.

These tests run the real adapter against a temporary home: the plugin is deposited under
`<state dir>/extensions/nexgen/`, the old `nexgen-guardrail` folder is taken back, the
engine-owned switches (briefing, posture) land in `nexgen.config.json`, and
`openclaw.json` is never written. The parity control is exercised on a machine built to be
aligned and on one built to diverge, because a check that only ever says PASS proves nothing.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import REAL_UL
from nexgen_core.paths import claude_settings, openclaw_config
from nexgen_core.runtimes.openclaw import (
    BRIEFING_NAME,
    LEGACY_PLUGIN_ID,
    PLUGIN_CONFIG_NAME,
    PLUGIN_ID,
    OpenClawRuntime,
)

HOOKS = REAL_UL / "hooks"
PLUGIN_SOURCE = HOOKS / "openclaw-guardrail-plugin"

OPENCLAW_JSON = {
    "gateway": {"port": 18789},
    "agents": {"defaults": {"workspace": "~/work"}},
    "mcp": {"servers": {"alpha": {"command": "npx"}}},
}


def a_home(tmp_path: Path, config: dict | None = None) -> Path:
    home = tmp_path / "home"
    state = home / ".openclaw"
    state.mkdir(parents=True)
    if config is not None:
        (state / "openclaw.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
    return home


def a_body(tmp_path: Path) -> Path:
    body = tmp_path / "guardrail-body.mjs"
    body.write_text("// the private policy, in the test only\n", encoding="utf-8")
    return body


def plugin_dir(home: Path) -> Path:
    return home / ".openclaw" / "extensions" / PLUGIN_ID


def plugin_config(home: Path) -> dict:
    return json.loads((plugin_dir(home) / PLUGIN_CONFIG_NAME).read_text(encoding="utf-8"))


def deposit(home: Path, tmp_path: Path) -> str | None:
    return OpenClawRuntime().install_guardrail(home, a_body(tmp_path), HOOKS)


def claude_settings_with(*events: tuple[str, str]) -> dict:
    """Claude's own `hooks` shape, with one command per (event, hook file)."""
    hooks: dict[str, list] = {}
    for event, command in events:
        hooks.setdefault(event, []).append({"hooks": [{"type": "command", "command": command}]})
    return {"hooks": hooks}


# ---------------------------------------------------------------- one plugin id


def test_the_plugin_id_is_nexgen_and_the_shipped_files_agree():
    package = json.loads((PLUGIN_SOURCE / "package.json").read_text(encoding="utf-8"))
    manifest = json.loads((PLUGIN_SOURCE / "openclaw.plugin.json").read_text(encoding="utf-8"))
    assert PLUGIN_ID == "nexgen"
    assert manifest["id"] == PLUGIN_ID
    assert package["name"] == PLUGIN_ID
    assert package["openclaw"]["extensions"] == ["./index.mjs"]


def test_the_deposit_lands_in_the_new_folder_with_the_two_jobs(tmp_path):
    home = a_home(tmp_path, OPENCLAW_JSON)

    action = deposit(home, tmp_path)

    assert action and str(plugin_dir(home)) in action
    for name in ("index.mjs", "guardrail.mjs", "briefing.mjs"):
        assert (plugin_dir(home) / name).read_bytes() == (PLUGIN_SOURCE / name).read_bytes(), name


def test_the_shared_briefing_text_is_deposited_beside_the_plugin(tmp_path):
    home = a_home(tmp_path, OPENCLAW_JSON)

    deposit(home, tmp_path)

    assert (plugin_dir(home) / BRIEFING_NAME).read_bytes() == (HOOKS / BRIEFING_NAME).read_bytes()


def test_a_missing_briefing_text_is_an_error_not_a_silent_skip(tmp_path):
    import shutil

    from nexgen_core.runtimes.base import GuardrailError

    engine_hooks = tmp_path / "engine-hooks"
    engine_hooks.mkdir()
    shutil.copytree(PLUGIN_SOURCE, engine_hooks / "openclaw-guardrail-plugin")
    shutil.copy2(HOOKS / "nexgen-guardrail-core.mjs", engine_hooks / "nexgen-guardrail-core.mjs")
    home = a_home(tmp_path, OPENCLAW_JSON)

    with pytest.raises(GuardrailError):
        OpenClawRuntime().install_guardrail(home, a_body(tmp_path), engine_hooks)


# ------------------------------------------------------------------ migration


def legacy_folder(home: Path, manifest_id: str = LEGACY_PLUGIN_ID) -> Path:
    legacy = home / ".openclaw" / "extensions" / LEGACY_PLUGIN_ID
    legacy.mkdir(parents=True, exist_ok=True)
    (legacy / "openclaw.plugin.json").write_text(json.dumps({"id": manifest_id}), encoding="utf-8")
    (legacy / "index.mjs").write_text("// old plugin\n", encoding="utf-8")
    return legacy


def test_the_old_guardrail_folder_is_taken_back_on_the_first_deposit(tmp_path):
    home = a_home(tmp_path, OPENCLAW_JSON)
    legacy = legacy_folder(home)

    deposit(home, tmp_path)

    assert not legacy.exists()
    assert plugin_dir(home).is_dir()


def test_a_folder_that_is_not_provably_ours_is_never_deleted(tmp_path):
    home = a_home(tmp_path, OPENCLAW_JSON)
    legacy = legacy_folder(home, manifest_id="someone-else")

    deposit(home, tmp_path)

    assert legacy.is_dir()


def test_a_legacy_folder_without_a_manifest_is_left_alone(tmp_path):
    home = a_home(tmp_path, OPENCLAW_JSON)
    legacy = home / ".openclaw" / "extensions" / LEGACY_PLUGIN_ID
    legacy.mkdir(parents=True)
    (legacy / "index.mjs").write_text("// unknown\n", encoding="utf-8")

    deposit(home, tmp_path)

    assert legacy.is_dir()


def test_the_second_deposit_changes_nothing(tmp_path):
    home = a_home(tmp_path, OPENCLAW_JSON)
    runtime = OpenClawRuntime()

    runtime.install_guardrail(home, a_body(tmp_path), HOOKS)
    assert runtime.install_guardrail(home, a_body(tmp_path), HOOKS) is None


def test_the_permissions_phase_never_touches_openclaw_json(tmp_path):
    home = a_home(tmp_path, OPENCLAW_JSON)
    before = openclaw_config(home).read_bytes()

    deposit(home, tmp_path)
    OpenClawRuntime().apply_posture(home, "ask")

    assert openclaw_config(home).read_bytes() == before


# ------------------------------------------------------------------- briefing


def test_the_briefing_stays_off_when_claude_has_no_checkpoint_hook(tmp_path):
    home = a_home(tmp_path, OPENCLAW_JSON)

    deposit(home, tmp_path)

    assert plugin_config(home)["briefing"] == {"enabled": False}


def test_the_briefing_turns_on_when_claude_registers_the_checkpoint_hook(tmp_path):
    home = a_home(tmp_path, OPENCLAW_JSON)
    settings = claude_settings(home)
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps(claude_settings_with(
        ("SessionStart", 'node "C:\\Users\\x\\.claude\\claude-vault-checkpoint.mjs"'),
    )), encoding="utf-8")

    deposit(home, tmp_path)

    assert plugin_config(home)["briefing"] == {"enabled": True}


def test_removing_the_checkpoint_hook_from_claude_switches_the_briefing_off(tmp_path):
    home = a_home(tmp_path, OPENCLAW_JSON)
    settings = claude_settings(home)
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps(claude_settings_with(
        ("SessionStart", "node claude-vault-checkpoint.mjs"),
    )), encoding="utf-8")
    deposit(home, tmp_path)
    assert plugin_config(home)["briefing"] == {"enabled": True}

    settings.write_text(json.dumps({"hooks": {}}), encoding="utf-8")
    deposit(home, tmp_path)

    assert plugin_config(home)["briefing"] == {"enabled": False}


def test_a_corrupt_settings_file_is_read_as_no_briefing(tmp_path):
    home = a_home(tmp_path, OPENCLAW_JSON)
    settings = claude_settings(home)
    settings.parent.mkdir(parents=True)
    settings.write_text("{not json", encoding="utf-8")

    deposit(home, tmp_path)

    assert plugin_config(home)["briefing"] == {"enabled": False}


# ----------------------------------------------------------- event sink cleanup


def test_openclaw_cleanup_removes_legacy_event_sink_and_events_config(tmp_path):
    home = a_home(tmp_path, OPENCLAW_JSON)
    deposit(home, tmp_path)
    pdir = plugin_dir(home)
    (pdir / "nexgen-event-sink.mjs").write_text("// old sink", encoding="utf-8")
    (pdir / "events.mjs").write_text("// old events", encoding="utf-8")
    config_path = pdir / PLUGIN_CONFIG_NAME
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["events"] = {"enabled": True}
    config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")

    runtime = OpenClawRuntime()
    action = runtime.remove_event_sink(home)

    assert action and "removed" in action
    assert not (pdir / "nexgen-event-sink.mjs").exists()
    assert not (pdir / "events.mjs").exists()
    assert "events" not in plugin_config(home)
    assert runtime.remove_event_sink(home) is None


def test_openclaw_deposit_purges_obsolete_plugin_files(tmp_path):
    home = a_home(tmp_path, OPENCLAW_JSON)
    deposit(home, tmp_path)
    pdir = plugin_dir(home)
    (pdir / "events.mjs").write_text("// old events", encoding="utf-8")
    (pdir / "nexgen-event-sink.mjs").write_text("// old sink", encoding="utf-8")

    # Re-depositing must purge obsolete files
    deposit(home, tmp_path)
    assert not (pdir / "events.mjs").exists()
    assert not (pdir / "nexgen-event-sink.mjs").exists()


# -------------------------------------------------------------------- posture


@pytest.mark.parametrize("posture", ["bypass", "accept-edits", "ask"])
def test_each_neutral_posture_is_written_where_the_plugin_reads_it(tmp_path, posture):
    home = a_home(tmp_path, OPENCLAW_JSON)
    deposit(home, tmp_path)

    action = OpenClawRuntime().apply_posture(home, posture)

    assert action and posture in action
    assert plugin_config(home)["posture"] == posture
    assert OpenClawRuntime().read_posture(home) == posture


def test_the_same_posture_twice_writes_nothing(tmp_path):
    home = a_home(tmp_path, OPENCLAW_JSON)
    deposit(home, tmp_path)
    runtime = OpenClawRuntime()
    runtime.apply_posture(home, "ask")

    assert runtime.apply_posture(home, "ask") is None


def test_a_posture_with_no_guardrail_deposited_is_nothing_to_apply(tmp_path):
    home = a_home(tmp_path, OPENCLAW_JSON)

    assert OpenClawRuntime().apply_posture(home, "bypass") is None
    assert not (plugin_dir(home) / PLUGIN_CONFIG_NAME).exists()


def test_an_unknown_posture_is_never_guessed(tmp_path):
    home = a_home(tmp_path, OPENCLAW_JSON)
    deposit(home, tmp_path)

    assert OpenClawRuntime().apply_posture(home, "yolo") is None
    assert "posture" not in plugin_config(home)


def test_the_adapter_declares_the_three_neutral_postures(tmp_path):
    assert set(OpenClawRuntime().rendered_postures()) == {"bypass", "accept-edits", "ask"}


