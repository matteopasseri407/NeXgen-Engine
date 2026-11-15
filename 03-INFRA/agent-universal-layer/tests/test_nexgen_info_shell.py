"""Tests for NeXgen Engine visual info banner and interactive shell retirement."""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.cli import build_parser
from nexgen_core.cli.engine import cmd_info  # noqa: E402
from nexgen_core.marks import safe_text  # noqa: E402
from nexgen_core.tools.info import get_engine_info, render_info


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


def test_render_info_all_flag():
    text_all = render_info(as_json=False, show_all=True)
    assert "neXgen Engine" in text_all
    assert len(text_all.splitlines()) > 5
    assert "MODULES" in text_all


def test_info_interactive_prints_openclaw_guidance(capsys):
    assert cmd_info(SimpleNamespace(json=False, interactive=True, all=False)) == 0
    out = capsys.readouterr().out
    assert "openclaw tui" in out or "OpenClaw" in out
    assert "Dashboard" in out or "dashboard" in out
    assert len(out.strip()) > 0


def test_info_interactive_does_not_crash(capsys):
    rc = cmd_info(SimpleNamespace(json=False, interactive=True, all=True))
    assert rc == 0
    out = capsys.readouterr().out
    assert "neXgen Engine" in out
    assert "openclaw" in out.lower()


def test_cli_parser_has_info_and_not_the_retired_shell():
    parser = build_parser()
    actions = {a.dest: a for a in parser._subparsers._group_actions}
    choices = actions["command"].choices
    assert "info" in choices
    assert "status" in choices
    for retired in ("shell", "interactive", "repl"):
        assert retired not in choices


def test_shell_module_is_completely_removed():
    import importlib.util
    import pytest

    spec = importlib.util.find_spec("nexgen_core.tools.shell")
    assert spec is None
    with pytest.raises(ModuleNotFoundError):
        import nexgen_core.tools.shell  # noqa: F401
    assert "nexgen_core.tools.shell" not in sys.modules
    assert not hasattr(sys.modules.get("nexgen_core.tools"), "shell")
    assert "tools.shell" not in sys.modules


def test_info_cmd_prints_on_cp1252_console(monkeypatch):
    """`nexgen info` on a Windows console whose code page is not UTF-8.

    Regression: the dashboard glyphs crashed print with
    "'charmap' codec can't encode characters" instead of degrading.
    """
    buf = io.BytesIO()
    fake = io.TextIOWrapper(buf, encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", fake)
    assert cmd_info(SimpleNamespace(json=False, interactive=False, all=False)) == 0
    fake.flush()
    out = buf.getvalue().decode("cp1252")
    assert "neXgen Engine" in out


def test_safe_text_keeps_unicode_where_it_fits():
    text = render_info(as_json=False)
    assert safe_text(text, _stream("utf-8")) == text
    assert len(text) > 0


def test_the_claude_bootstrap_row_is_the_user_scope_pointer(tmp_path):
    """`nexgen info` must census the file the guard writes, not the one it removes.

    The pointer moved to `~/.claude/CLAUDE.md` and `~/CLAUDE.md` goes away, so the row kept
    reporting "absent" on every machine while Claude was reading the pointer fine.
    """
    from nexgen_core.tools.info import _bootstrap_targets, _instruction_state

    vault = tmp_path / "vault"
    canon = vault / "03-INFRA" / "agent-universal-layer" / "instructions" / "AGENTS.md"
    canon.parent.mkdir(parents=True)
    canon.write_text("# Canonical rules\n", encoding="utf-8")
    home = tmp_path / "home"
    pointer = home / ".claude" / "CLAUDE.md"
    pointer.parent.mkdir(parents=True)
    pointer.write_text(f"# Claude compatibility pointer\n\n{canon}\n", encoding="utf-8")
    (home / "CLAUDE.md").write_text("# an old home file\n", encoding="utf-8")

    rows = {label: (path, kind) for label, path, kind in _bootstrap_targets(home)}

    assert rows["claude"][0] == pointer
    assert _instruction_state(rows["claude"][0], rows["claude"][1], vault) == "pointer"


def test_safe_text_degrades_gracefully_on_cp1252():
    assert safe_text("plain ascii", _stream("cp1252")) == "plain ascii"
    degraded = safe_text("a ─ b • c", _stream("cp1252"))
    degraded.encode("cp1252")
    assert "neXgen" in safe_text("neXgen", _stream("cp1252"))
    assert len(degraded) > 0


def test_safe_text_maps_the_arrow_to_ascii():
    assert safe_text("a → b", _stream("cp1252")) == "a -> b"
    assert "->" in safe_text("->", _stream("cp1252"))


def test_safe_mark_uses_the_stdout_of_the_moment(monkeypatch):
    from nexgen_core.marks import safe_mark

    monkeypatch.setattr(sys, "stdout", _stream("cp850"))
    assert safe_mark("✓") == "[OK]"
    assert safe_mark("✗") == "[X]"


def test_info_cmd_json_output(capsys):
    rc = cmd_info(SimpleNamespace(json=True, interactive=False, all=False))
    assert rc == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert isinstance(data, dict)
    assert "version" in data
    assert "system" in data or "git" in data or len(data) > 0


def _stream(encoding: str):
    return io.TextIOWrapper(io.BytesIO(), encoding=encoding)
