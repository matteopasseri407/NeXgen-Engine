"""The Council notices when a vendor CLI stops listing a flag a seat is started with.

Every test answers the CLI's `--help` itself: nothing here starts a real vendor CLI, and the
live run of the same check is `nexgen council contract` (the Council module's health command).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
import yaml

COUNCIL_DIR = Path(__file__).resolve().parents[2] / "agent-universal-layer" / "council"
if str(COUNCIL_DIR) not in sys.path:
    sys.path.insert(0, str(COUNCIL_DIR))
SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import cli_contract  # noqa: E402
import council  # noqa: E402
from seat_process import SUPPORTED_CLIS  # noqa: E402


def _help_listing(cli: str, *, without: str | None = None) -> str:
    """A help text that names every flag a seat passes `cli`, minus `without`."""
    flags = [flag for flag in cli_contract.flags_passed(cli) if flag != without]
    return "Usage:\n" + "\n".join(f"  {flag}   something" for flag in flags)


def _installed(monkeypatch) -> None:
    monkeypatch.setattr(cli_contract.shutil, "which", lambda name: f"/usr/bin/{name}")


def test_every_supported_cli_says_where_its_flags_are_listed():
    assert set(cli_contract.HELP_ARGV) == set(SUPPORTED_CLIS)


def test_opencode_is_only_ever_asked_for_its_help_in_standalone_mode():
    assert "--standalone" in cli_contract.HELP_ARGV["opencode"]


@pytest.mark.parametrize("cli", SUPPORTED_CLIS)
def test_the_flags_come_from_the_command_a_seat_really_runs(cli):
    flags = cli_contract.flags_passed(cli)
    assert flags
    assert all(flag.startswith("-") for flag in flags)
    # the effort flag is part of what a seat is started with, so it is part of the contract
    assert any(flag in flags for flag in ("--effort", "-c", "--variant", "--think"))


def test_reading_the_flags_leaves_the_codex_credentials_alone(tmp_path, monkeypatch):
    home = tmp_path / "codex-home"
    home.mkdir()
    (home / "auth.json").write_text('{"token": "must-not-be-copied"}', encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(home))
    copies_before = sorted(Path(os.environ.get("TMPDIR") or "/tmp").glob("council-*"))
    cli_contract.flags_passed("codex")
    assert os.environ["CODEX_HOME"] == str(home)
    assert sorted(Path(os.environ.get("TMPDIR") or "/tmp").glob("council-*")) == copies_before


def test_a_flag_is_found_whole_not_as_the_start_of_another(monkeypatch):
    assert cli_contract.flag_listed("--effort", "  --effort <level>   how hard to think")
    assert cli_contract.flag_listed("-m", "  -m, --model <name>")
    assert not cli_contract.flag_listed("--effort", "  --effort-level <level>")
    assert not cli_contract.flag_listed("-s", "  --no-session-persistence")


@pytest.mark.parametrize("cli", SUPPORTED_CLIS)
def test_a_cli_that_still_lists_every_flag_is_ok(cli, monkeypatch):
    _installed(monkeypatch)
    result = cli_contract.check_cli(cli, lambda argv: (True, _help_listing(cli)))
    assert (result.status, result.missing) == ("ok", ())


@pytest.mark.parametrize("cli", SUPPORTED_CLIS)
def test_a_flag_that_disappears_is_named(cli, monkeypatch):
    _installed(monkeypatch)
    gone = cli_contract.flags_passed(cli)[0]
    result = cli_contract.check_cli(cli, lambda argv: (True, _help_listing(cli, without=gone)))
    assert result.status == "drift"
    assert result.missing == (gone,)
    assert gone in result.detail


def test_a_help_that_cannot_be_read_is_not_taken_for_a_pass(monkeypatch):
    _installed(monkeypatch)
    result = cli_contract.check_cli("codex", lambda argv: (False, "exit 2"))
    assert result.status == "unprobed"


def test_a_cli_that_is_not_installed_is_skipped_not_failed(monkeypatch):
    monkeypatch.setattr(cli_contract.shutil, "which", lambda name: None)
    called = []
    result = cli_contract.check_cli("agy", lambda argv: called.append(argv) or (True, ""))
    assert result.status == "absent"
    assert not called


def test_the_command_exits_non_zero_when_a_cli_drifted(monkeypatch, capsys):
    _installed(monkeypatch)
    monkeypatch.setattr(
        cli_contract, "check_all",
        lambda: [
            cli_contract.ContractResult("codex", "ok", "fine"),
            cli_contract.ContractResult("claude", "absent", "not installed"),
            cli_contract.ContractResult("opencode", "drift", "no longer lists: --variant", ("--variant",)),
        ],
    )
    with pytest.raises(SystemExit) as stopped:
        council.cmd_contract(None)
    assert stopped.value.code == 1
    out = capsys.readouterr().out
    assert "[FAIL] opencode" in out and "[ OK ] codex" in out and "[skip] claude" in out


def test_the_command_passes_when_nothing_drifted(monkeypatch, capsys):
    monkeypatch.setattr(
        cli_contract, "check_all",
        lambda: [cli_contract.ContractResult("codex", "ok", "fine"), cli_contract.ContractResult("agy", "absent", "none")],
    )
    council.cmd_contract(None)
    assert "[FAIL]" not in capsys.readouterr().out


def test_the_council_module_asks_the_doctor_to_run_it():
    catalog = yaml.safe_load(
        (COUNCIL_DIR.parent / "modules" / "modules.yaml").read_text(encoding="utf-8")
    )
    modules = catalog.get("modules", catalog)
    assert modules["council"]["health"] == "nexgen council contract"
