"""Tests for NeXgen Engine visual info banner and interactive shell."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.cli import build_parser
from nexgen_core.tools.info import get_engine_info, render_info
from nexgen_core.tools.shell import NeXgenShell


def test_get_engine_info_structure():
    info = get_engine_info()
    assert "version" in info
    assert "os" in info
    assert "host" in info
    assert "python" in info
    assert "runtimes" in info
    assert "modules" in info
    assert "secrets" in info
    assert "doctor" in info


def test_render_info_text_and_json():
    text_out = render_info(as_json=False)
    assert "neXgen Engine" in text_out
    assert "AI OPERATING LAYER" in text_out
    assert "PLANES & RUNTIMES" in text_out
    assert "MODULES" in text_out

    json_out = render_info(as_json=True)
    data = json.loads(json_out)
    assert "version" in data
    assert "vault_path" in data


def test_shell_help_and_info(capsys):
    shell = NeXgenShell()
    shell.do_help("")
    out = capsys.readouterr().out
    assert "SELECTABLE ACTIONS" in out
    assert "doctor" in out
    assert "sync" in out

    shell.do_info("")
    info_out = capsys.readouterr().out
    assert "neXgen Engine" in info_out

    # Test numeric shortcuts
    assert shell.default("1") is False
    assert shell.default("7") is False
    assert shell.default("0") is True


def test_cli_parser_has_info_and_shell():
    parser = build_parser()
    actions = {a.dest: a for a in parser._subparsers._group_actions}
    choices = actions["command"].choices
    assert "info" in choices
    assert "status" in choices
    assert "shell" in choices
    assert "interactive" in choices
