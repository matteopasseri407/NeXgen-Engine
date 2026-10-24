"""Starting an npm shim on Windows: node and the entry, never a command interpreter.

`npx` on Windows is `C:\\Program Files\\nodejs\\npx.cmd`. Windows starts a batch file only by handing the
command line to `cmd.exe`, and that interpreter splits the path at its space: every MCP server the manifest
declares as `command: npx` died with `"C:\\Program" is not recognized as an internal or external command`.
The same interpreter re-reads `&`, `|`, `^` and `%` inside the arguments, which are whatever a caller passed.

npm's shim is a wrapper around a JavaScript entry that sits beside it, and node runs that entry as a program
with an argument list. Every launcher in the engine goes through `nexgen_core.processes`, so the rule is
proved there, on the platform the module reads, without flipping `os.name` process-wide (that changes
`pathlib`'s flavour for pytest itself: see the header of `test_nexgen_no_window.py`).
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

INFRA = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = INFRA / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import processes  # noqa: E402

PACKAGE = "@modelcontextprotocol/server-everything@2.0.0"


def npm_prefix(tmp_path: Path, *, entry: bool = True) -> tuple[Path, Path, Path]:
    """The layout npm leaves on Windows: the shim in a folder whose name has a space in it.

    `npx.cmd` sits in the Node installation (`Program Files`), the entry it wraps is
    `node_modules/npm/bin/npx-cli.js` in the same folder, and node is beside both.
    """
    prefix = tmp_path / "Program Files" / "nodejs"
    shim = prefix / "npx.cmd"
    shim.parent.mkdir(parents=True, exist_ok=True)
    shim.write_text('@echo off\r\n"%~dp0\\node.exe" "%~dp0\\node_modules\\npm\\bin\\npx-cli.js" %*\r\n',
                    encoding="utf-8")
    start = prefix / "node_modules" / "npm" / "bin" / "npx-cli.js"
    if entry:
        start.parent.mkdir(parents=True, exist_ok=True)
        start.write_text("// synthetic npm entry\n", encoding="utf-8")
    node = prefix / "node.exe"
    node.write_text("", encoding="utf-8")
    return shim, start, node


def windows(monkeypatch) -> None:
    """The platform `nexgen_core.processes` reads, faked on that module alone."""
    monkeypatch.setattr(processes, "os", SimpleNamespace(name="nt"))


def test_an_npm_shim_in_a_folder_with_a_space_starts_node_and_its_entry(tmp_path, monkeypatch):
    shim, start, node = npm_prefix(tmp_path)
    windows(monkeypatch)
    monkeypatch.setattr(processes.shutil, "which",
                        lambda name: {"npx": str(shim), "node": str(node)}.get(name))

    argv = processes.windows_command_argv(["npx", "-y", PACKAGE])

    assert argv == [str(node), str(start), "-y", PACKAGE]
    assert "cmd.exe" not in argv


def test_a_shim_that_lost_its_entry_is_refused_by_name(tmp_path, monkeypatch):
    shim, start, node = npm_prefix(tmp_path, entry=False)
    windows(monkeypatch)
    monkeypatch.setattr(processes.shutil, "which",
                        lambda name: {"npx": str(shim), "node": str(node)}.get(name))

    with pytest.raises(processes.ShimEntryError) as refused:
        processes.windows_command_argv(["npx", "-y", PACKAGE])

    said = str(refused.value)
    assert str(shim) in said, "the reason names the shim the person has to look at"
    assert str(start) in said, "and the entry that is missing beside it"


def test_on_posix_the_command_stays_what_the_caller_asked(tmp_path, monkeypatch):
    # Linux and macOS have no shims to resolve: the argv is passed through as it arrived.
    shim, _start, _node = npm_prefix(tmp_path)
    monkeypatch.setattr(processes, "os", SimpleNamespace(name="posix"))
    monkeypatch.setattr(processes.shutil, "which", lambda name: str(shim))

    assert processes.windows_command_argv(["npx", "-y", PACKAGE]) == ["npx", "-y", PACKAGE]


def test_a_shim_that_is_not_npms_keeps_the_command_interpreter(tmp_path, monkeypatch):
    # The rule is npm's own shims; anything else stays on the road it already had.
    shim = tmp_path / "codex.cmd"
    shim.write_text("@echo off\r\n", encoding="utf-8")
    windows(monkeypatch)
    monkeypatch.setattr(processes.shutil, "which", lambda name: str(shim) if name == "codex" else None)

    assert processes.windows_command_argv(["codex", "exec"]) == ["cmd.exe", "/d", "/s", "/c", str(shim), "exec"]
