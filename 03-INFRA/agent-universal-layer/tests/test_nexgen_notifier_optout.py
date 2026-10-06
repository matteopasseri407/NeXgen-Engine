"""The update notice goes where a shell is used, and stays off when the user turned it off.

The guard re-ensures the notice every cycle. `--remove` was therefore undone within half an
hour, and the PowerShell profile was created on Linux machines that have no PowerShell.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.tools import notifier_boot  # noqa: E402
from nexgen_core.tools import update_notifier as notifier  # noqa: E402

pytestmark = pytest.mark.skipif(os.name == "nt", reason="the POSIX shell and autostart lanes")

MARKER = "NeXgen Engine update notice"


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("NEXGEN_HOME", str(home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("AGENT_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(notifier_boot.shutil, "which", lambda name: None)
    return home


def _profile(home: Path) -> Path:
    return home / ".config" / "powershell" / "Microsoft.PowerShell_profile.ps1"


def test_no_powershell_profile_is_created_where_powershell_is_not_installed(home):
    notifier.ensure_shell_hook(home)
    assert (home / ".bashrc").is_file()
    assert not _profile(home).exists()
    assert not _profile(home).parent.exists()


def test_the_profile_is_written_when_pwsh_is_installed(home, monkeypatch):
    monkeypatch.setattr(notifier_boot.shutil, "which", lambda name: "/usr/bin/pwsh" if name == "pwsh" else None)
    notifier.ensure_shell_hook(home)
    assert MARKER in _profile(home).read_text(encoding="utf-8")


def test_an_existing_profile_is_a_target_without_pwsh_on_the_path(home):
    _profile(home).parent.mkdir(parents=True)
    _profile(home).write_text("# mine\n", encoding="utf-8")
    notifier.ensure_shell_hook(home)
    text = _profile(home).read_text(encoding="utf-8")
    assert text.startswith("# mine\n") and MARKER in text


def test_a_removal_stays_removed_across_guard_cycles(home):
    notifier.ensure_shell_hook(home)
    assert MARKER in (home / ".bashrc").read_text(encoding="utf-8")
    assert notifier.cmd_install_shell_hook(remove=True) == 0
    assert MARKER not in (home / ".bashrc").read_text(encoding="utf-8")
    assert notifier.ensure_shell_hook(home) == []
    assert notifier.ensure_shell_hook(home) == []
    assert MARKER not in (home / ".bashrc").read_text(encoding="utf-8")


def test_installing_again_lifts_the_removal(home):
    notifier.ensure_shell_hook(home)
    notifier.cmd_install_shell_hook(remove=True)
    assert notifier.cmd_install_shell_hook() == 0
    assert MARKER in (home / ".bashrc").read_text(encoding="utf-8")
    assert notifier.ensure_shell_hook(home) == ["[shell-hook] already present"]


def test_removing_one_shell_does_not_switch_off_the_others(home):
    (home / ".zshrc").write_text("# zsh\n", encoding="utf-8")
    notifier.ensure_shell_hook(home)
    notifier.cmd_install_shell_hook(remove=True, shell="bash")
    notifier.ensure_shell_hook(home)
    assert MARKER not in (home / ".bashrc").read_text(encoding="utf-8")
    assert MARKER in (home / ".zshrc").read_text(encoding="utf-8")


def test_the_guards_own_install_does_not_lift_a_removal(home):
    """The opt-out record is the user's: the guard installing for the shells left must keep it."""
    (home / ".zshrc").write_text("# zsh\n", encoding="utf-8")
    notifier.cmd_install_shell_hook(remove=True, shell="bash")
    notifier.ensure_shell_hook(home)
    assert notifier_boot._opted_out(home) == {"shell:bash"}


def test_autostart_removal_stays_removed_and_install_lifts_it(home, monkeypatch, capsys):
    monkeypatch.setattr(notifier_boot, "_run_quiet", lambda argv, timeout=60: (0, "enabled"))
    first = notifier.ensure_boot_check(home)
    assert first and (home / ".config" / "autostart" / "nexgen-update-check.desktop").is_file()
    assert notifier.cmd_install_autostart(remove=True) == 0
    assert not (home / ".config" / "autostart" / "nexgen-update-check.desktop").exists()
    assert notifier.ensure_boot_check(home) == []
    assert not (home / ".config" / "autostart" / "nexgen-update-check.desktop").exists()
    assert notifier.cmd_install_autostart() == 0
    assert (home / ".config" / "autostart" / "nexgen-update-check.desktop").is_file()
    capsys.readouterr()
