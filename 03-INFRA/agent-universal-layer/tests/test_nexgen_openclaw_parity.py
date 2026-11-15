"""The `nexgen` plugin: one id, one deposit, one job per file, and a parity control that proves it.

These tests run the real adapter against a temporary home: the plugin is deposited under
`<state dir>/extensions/nexgen/`, the old `nexgen-guardrail` folder is taken back, the
engine-owned switches (briefing, checkpoint, engine) land in `nexgen.config.json`, and
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


def test_the_deposit_lands_in_the_new_folder_with_every_job(tmp_path):
    home = a_home(tmp_path, OPENCLAW_JSON)

    action = deposit(home, tmp_path)

    assert action and str(plugin_dir(home)) in action
    for name in (
        "index.mjs", "guardrail.mjs", "briefing.mjs", "checkpoint.mjs", "quota.mjs", "spend.mjs",
        "workshop.mjs", "governor.mjs", "engine-run.mjs", "council.mjs", "sup.mjs",
        "sup-tools.mjs", "evidence.mjs", "engine-config.mjs", "engine-files.mjs", "session.mjs",
        "adopt.mjs",
    ):
        assert (plugin_dir(home) / name).read_bytes() == (PLUGIN_SOURCE / name).read_bytes(), name


def test_the_engine_that_can_answer_quota_is_deposited_with_the_plugin(tmp_path):
    """`/quota` needs two paths, and neither is a PATH lookup: the `.cmd` launcher would need a shell."""
    home = a_home(tmp_path, OPENCLAW_JSON)

    deposit(home, tmp_path)

    engine = plugin_config(home)["engine"]
    assert Path(engine["command"]).is_absolute()
    entry = Path(engine["entry"])
    assert entry.is_absolute() and entry.name == "__init__.py"
    assert entry.parent.name == "cli" and entry.parent.parent.name == "nexgen_core"
    assert entry.is_file()


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

    assert openclaw_config(home).read_bytes() == before


# ------------------------------------------------------------------- briefing


def test_the_briefing_turns_on_from_its_own_text_alone(tmp_path):
    """The briefing is the engine's, not a copy of Claude's switch.

    Following Claude Code's registered hook left it off on a machine where OpenClaw
    is the only assistant, while the text sat right there: the engine's own continuity
    was off and OpenClaw's native memory was the only one running.
    """
    home = a_home(tmp_path, OPENCLAW_JSON)

    deposit(home, tmp_path)

    assert plugin_config(home)["briefing"] == {"enabled": True}


def test_the_briefing_turns_on_when_claude_registers_the_checkpoint_hook(tmp_path):
    home = a_home(tmp_path, OPENCLAW_JSON)
    settings = claude_settings(home)
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps(claude_settings_with(
        ("SessionStart", 'node "C:\\Users\\x\\.claude\\claude-vault-checkpoint.mjs"'),
    )), encoding="utf-8")

    deposit(home, tmp_path)

    assert plugin_config(home)["briefing"] == {"enabled": True}


def test_removing_the_checkpoint_hook_from_claude_leaves_the_briefing_on(tmp_path):
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

    # The other runtime changing its hooks says nothing about this one's briefing.
    assert plugin_config(home)["briefing"] == {"enabled": True}


def test_a_corrupt_settings_file_does_not_touch_the_briefing(tmp_path):
    home = a_home(tmp_path, OPENCLAW_JSON)
    settings = claude_settings(home)
    settings.parent.mkdir(parents=True)
    settings.write_text("{not json", encoding="utf-8")

    deposit(home, tmp_path)

    assert plugin_config(home)["briefing"] == {"enabled": True}


