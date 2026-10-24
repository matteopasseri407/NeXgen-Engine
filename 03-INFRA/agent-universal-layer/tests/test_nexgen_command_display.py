"""A command shown to a person has to be pasteable, spaces included."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import processes  # noqa: E402 - sys.path shim for cloned checkout

WINDOWS_NPM = r"C:\Program Files\nodejs\npm.cmd"
POSIX_NPM = "/opt/my tools/npm"


def test_on_windows_a_path_with_a_space_is_quoted_the_way_createprocess_reads_it(pretend_os):
    pretend_os(processes, "nt")

    line = processes.command_line_text([WINDOWS_NPM, "install", "-g", "openclaw@1"])

    assert line == subprocess.list2cmdline([WINDOWS_NPM, "install", "-g", "openclaw@1"])
    assert f'"{WINDOWS_NPM}" install' in line


def test_on_posix_a_path_with_a_space_is_shell_quoted(pretend_os):
    pretend_os(processes, "posix")

    line = processes.command_line_text([POSIX_NPM, "install"])

    assert line == "'/opt/my tools/npm' install"


def test_a_plain_command_is_left_alone(pretend_os):
    pretend_os(processes, "posix")

    assert processes.command_line_text(["openclaw", "gateway", "start"]) == "openclaw gateway start"
