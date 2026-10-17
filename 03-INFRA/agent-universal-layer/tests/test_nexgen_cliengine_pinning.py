"""Pinning del dispatcher engine: fissa il comportamento attuale.

La register() verra suddivisa in sottofunzioni senza cambiare nomi,
help o comportamenti: questi test devono passare prima e dopo.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.cli import build_parser  # noqa: E402
from nexgen_core.cli import main as nexgen_main  # noqa: E402
from nexgen_core.cli import engine as engine_cmds  # noqa: E402

EXPECTED_ENGINE_COMMANDS = [
    "release",
    "sync",
    "plan",
    "init",
    "runtime",
    "import",
    "guard",
    "pull",
    "preflight",
    "doctor",
    "info",
    "shell",
    "update",
    "upgrades",
    "inventory",
    "config",
    "heartbeat",
    "notify-failure",
    "bootstrap-alerts",
]

EXPECTED_ALIASES = {
    "apply": "sync",
    "status": "info",
    "interactive": "shell",
    "repl": "shell",
}


def _engine_choices():
    parser = argparse.ArgumentParser(prog="nexgen")
    sub = parser.add_subparsers(dest="command", metavar="command")
    engine_cmds.register(sub)
    action = parser._subparsers._group_actions[0]
    return action.choices, action._choices_actions


def test_engine_registers_all_expected_commands():
    choices, _ = _engine_choices()
    for cmd in EXPECTED_ENGINE_COMMANDS:
        assert cmd in choices, f"comando engine mancante: {cmd}"
    for alias, canonical in EXPECTED_ALIASES.items():
        assert alias in choices, f"alias mancante: {alias}"
        assert choices[alias] is choices[canonical]


def test_every_engine_command_has_nonempty_help():
    _, choice_actions = _engine_choices()
    by_dest = {a.dest: a for a in choice_actions}
    for cmd in EXPECTED_ENGINE_COMMANDS:
        assert cmd in by_dest, f"help non trovato per: {cmd}"
        assert by_dest[cmd].help and by_dest[cmd].help.strip(), f"help vuoto per: {cmd}"


def test_build_parser_includes_engine_commands_and_help():
    parser = build_parser()
    action = parser._subparsers._group_actions[0]
    for cmd in EXPECTED_ENGINE_COMMANDS + list(EXPECTED_ALIASES):
        assert cmd in action.choices, f"build_parser senza: {cmd}"
    for choice_action in action._choices_actions:
        assert choice_action.help and choice_action.help.strip(), (
            f"help vuoto per: {choice_action.dest}"
        )


def _isolate_fake_vault(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    fake_home = tmp_path / "fake-home"
    fake_vault = tmp_path / "fake-vault"
    fake_home.mkdir(parents=True, exist_ok=True)
    fake_vault.mkdir(parents=True, exist_ok=True)
    (fake_vault / "03-INFRA" / "agent-universal-layer" / "instructions").mkdir(
        parents=True, exist_ok=True
    )
    (fake_vault / "03-INFRA" / "agent-universal-layer" / "instructions" / "AGENTS.md").write_text(
        "# canonical bootstrap\n", encoding="utf-8"
    )
    monkeypatch.setenv("NEXGEN_HOME", str(fake_home))
    monkeypatch.setenv("KNOWLEDGE_VAULT_PATH", str(fake_vault))
    monkeypatch.setenv("NEXGEN_DISABLE_HOST_MUTATIONS", "1")
    for key in ("AGENT_VAULT_DATA", "AGENT_ENGINE_ROOT", "AGENT_STATE_DIR"):
        monkeypatch.delenv(key, raising=False)
    return fake_home, fake_vault


def test_info_runs_on_fake_vault_without_writing_outside(tmp_path, monkeypatch, capsys):
    fake_home, fake_vault = _isolate_fake_vault(tmp_path, monkeypatch)
    from nexgen_core.paths import resolve_home, resolve_vault_data

    assert str(resolve_home()).startswith(str(tmp_path))
    assert str(resolve_vault_data()).startswith(str(tmp_path))
    rc = engine_cmds.cmd_info(SimpleNamespace(json=False, interactive=False, all=False))
    assert rc == 0
    out = capsys.readouterr().out
    assert "neXgen Engine" in out
    # Tutto cio che il comando ha risolto resta dentro tmp_path.
    assert str(resolve_home()).startswith(str(tmp_path))
    assert str(resolve_vault_data()).startswith(str(tmp_path))


def test_inventory_runs_on_fake_vault_without_writing_outside(tmp_path, monkeypatch, capsys):
    fake_home, fake_vault = _isolate_fake_vault(tmp_path, monkeypatch)
    from nexgen_core.paths import resolve_home, resolve_vault_data

    assert str(resolve_home()).startswith(str(tmp_path))
    assert str(resolve_vault_data()).startswith(str(tmp_path))
    rc = engine_cmds.cmd_inventory(SimpleNamespace())
    assert rc in (0, 2)
    out = capsys.readouterr().out
    assert "claude:" in out
    assert str(resolve_home()).startswith(str(tmp_path))
    assert str(resolve_vault_data()).startswith(str(tmp_path))


def test_unknown_command_exits_with_error():
    parser = build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["questo-comando-non-esiste-xyz"])
    assert exc.value.code != 0
    with pytest.raises(SystemExit) as exc2:
        nexgen_main(["questo-comando-non-esiste-xyz"])
    assert exc2.value.code != 0
