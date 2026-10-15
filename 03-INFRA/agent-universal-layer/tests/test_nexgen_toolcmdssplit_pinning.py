"""Pinning dello split mcp/utilities di tool_cmds prima del refactor.

Fissa il comportamento attuale (non quello desiderato) con comandi finti:
blocco mcp_* registrato con tutti i verbi, utility now e open_folder
finti che girano su tmp, verbo sconosciuto rifiutato con errore chiaro.
Solo comandi finti in tmp_path, mai comandi reali, mai rete.
"""
from __future__ import annotations

import inspect
import subprocess
import sys
import urllib.request
from argparse import Namespace
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.cli import tool_cmds as tc_mod  # noqa: E402

MCP_VERBS = {"add", "try", "trials", "promote", "drop", "list", "bump", "plan", "check"}
TOOL_VERBS = {"now", "open", "chrome", "firecrawl", "update-notifier"}


def _no_real_commands_no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Solo finti in tmp_path: mai comandi reali, mai rete."""

    def _boom(*args, **kwargs):
        raise AssertionError("comando reale o rete toccata")

    monkeypatch.setattr(subprocess, "run", _boom)
    monkeypatch.setattr(subprocess, "Popen", _boom)
    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    if hasattr(__import__("os"), "startfile"):
        monkeypatch.setattr(__import__("os"), "startfile", _boom, raising=False)


def _mcp_choices() -> dict:
    from nexgen_core.cli import build_parser

    parser = build_parser()
    top = None
    for action in parser._subparsers._group_actions:
        top = action.choices
        break
    assert top is not None and "mcp" in top
    mcp_parser = top["mcp"]
    for action in mcp_parser._subparsers._group_actions:
        return dict(action.choices)
    raise AssertionError("mcp senza sotto-verbi")


def _tool_choices() -> dict:
    from nexgen_core.cli import build_parser

    parser = build_parser()
    top = None
    for action in parser._subparsers._group_actions:
        top = action.choices
        break
    assert top is not None and "tool" in top
    tool_parser = top["tool"]
    for action in tool_parser._subparsers._group_actions:
        return dict(action.choices)
    raise AssertionError("tool senza sotto-verbi")


def test_mcp_block_registers_all_verbs() -> None:
    choices = _mcp_choices()
    assert MCP_VERBS <= set(choices), f"verbi mcp mancanti: {MCP_VERBS - set(choices)}"
    for verb in MCP_VERBS:
        assert callable(getattr(choices[verb], "get_default")("func")), f"mcp {verb} senza func"


def test_tool_utilities_registered() -> None:
    choices = _tool_choices()
    assert TOOL_VERBS <= set(choices), f"utility mancanti: {TOOL_VERBS - set(choices)}"
    from nexgen_core.cli import build_parser

    parser = build_parser()
    top = next(iter(parser._subparsers._group_actions)).choices
    assert "council" in top


def test_fake_now_runs_on_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_real_commands_no_network(monkeypatch)
    import nexgen_core.tools.now as now_mod

    seen: dict = {}

    def _fake_now_main(argv: list[str]) -> int:
        seen["argv"] = list(argv)
        (tmp_path / "now.out").write_text("finto now", encoding="utf-8")
        return 0

    monkeypatch.setattr(now_mod, "main", _fake_now_main)
    args = Namespace(json=False, human=False, shell=False, format="json", passthrough=[])
    assert tc_mod.cmd_now(args) == 0
    assert (tmp_path / "now.out").read_text(encoding="utf-8") == "finto now"


def test_fake_open_folder_runs_on_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_real_commands_no_network(monkeypatch)
    import nexgen_core.tools.open_folder as of_mod

    target = tmp_path / "cartella"
    target.mkdir()
    seen: dict = {}

    def _fake_open_main(argv: list[str]) -> int:
        seen["argv"] = list(argv)
        assert argv and Path(argv[0]).resolve() == target.resolve()
        (tmp_path / "open.out").write_text("finto open", encoding="utf-8")
        return 0

    monkeypatch.setattr(of_mod, "main", _fake_open_main)
    args = Namespace(path=str(target), passthrough=[])
    assert tc_mod.cmd_open(args) == 0
    assert (tmp_path / "open.out").read_text(encoding="utf-8") == "finto open"


def test_unknown_verb_rejected_with_clear_error(capsys: pytest.CaptureFixture) -> None:
    from nexgen_core.cli import build_parser

    parser = build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["tool", "verbo-inesistente-xyz"])
    assert exc.value.code != 0
    assert "invalid choice" in capsys.readouterr().err
    with pytest.raises(SystemExit) as exc2:
        parser.parse_args(["mcp", "verbo-inesistente-xyz"])
    assert exc2.value.code != 0
    assert "invalid choice" in capsys.readouterr().err


def test_public_signatures_unchanged() -> None:
    assert list(inspect.signature(tc_mod.register).parameters) == ["sub"]
    assert list(inspect.signature(tc_mod.cmd_now).parameters) == ["args"]
    assert list(inspect.signature(tc_mod.cmd_open).parameters) == ["args"]
    assert list(inspect.signature(tc_mod.cmd_council).parameters) == ["args"]
    assert list(inspect.signature(tc_mod.cmd_mcp_add).parameters) == ["args"]


def test_mcp_utilities_helpers_split() -> None:
    src = Path(tc_mod.__file__).read_text(encoding="utf-8")
    assert "def _register_mcp_block" in src
    assert "def _register_utilities" in src
    body = src[src.index("def register("):]
    assert "_register_mcp_block(" in body
    assert "_register_utilities(" in body
