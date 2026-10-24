"""Scratch files: outside the home on Windows, and pytest told about it.

The point of these tests is the one decision, not the disk: which candidate wins,
and when the answer is "keep the default". The last two exercise the real
machine, because a rule that only works in a fake environment would leave the
suite writing into the home exactly where it hurts.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import scratch  # noqa: E402 - sys.path shim for cloned checkout

HOME = Path("C:/work/person")
INSIDE = HOME / "AppData" / "Local" / "Temp"
DRIVE_TEMP = Path("C:/Temp") / f"nexgen-{scratch.user_tag()}"
PUBLIC_TEMP = Path("C:/shared") / f"nexgen-{scratch.user_tag()}"


def windows_env(**overrides) -> dict[str, str]:
    values = {"TEMP": str(INSIDE), "SystemDrive": "C:", "PUBLIC": "C:/shared"}
    values.update(overrides)
    return values


def only(*allowed: Path):
    """A `can_write` that says yes for these folders and no for every other one."""
    return lambda candidate: candidate in allowed


def test_the_override_wins_even_inside_the_home():
    env = windows_env(NEXGEN_SCRATCH_ROOT=str(HOME / "scratch"))
    root = scratch.scratch_root(home=HOME, platform="win32", env=env, can_write=only(HOME / "scratch"))
    assert root == HOME / "scratch"


def test_a_system_temp_already_outside_the_home_is_left_alone():
    """Nothing to move: the answer is None, and pytest keeps what the system gives it."""
    env = windows_env(TEMP="D:/Temp")
    assert scratch.scratch_root(home=HOME, platform="win32", env=env, can_write=only(Path("D:/Temp"))) is None


def test_the_drive_root_temp_is_the_answer_when_the_override_cannot_be_written():
    root = scratch.scratch_root(home=HOME, platform="win32", env=windows_env(), can_write=only(DRIVE_TEMP))
    assert root == DRIVE_TEMP


def test_the_public_folder_is_the_last_resort():
    root = scratch.scratch_root(home=HOME, platform="win32", env=windows_env(), can_write=only(PUBLIC_TEMP))
    assert root == PUBLIC_TEMP


def test_nothing_writable_means_keep_the_default():
    """No candidate, no change: the suite still runs, it is just noisier for the gateway."""
    assert scratch.scratch_root(home=HOME, platform="win32", env=windows_env(), can_write=lambda _: False) is None


def test_linux_has_nothing_to_change_when_the_temp_is_outside_the_home():
    env = {"TMPDIR": "/tmp"}
    assert scratch.scratch_root(home=Path("/srv/person"), platform="linux", env=env, can_write=lambda _: True) is None


def test_pytest_is_pointed_at_the_new_root():
    environ = windows_env()
    root = scratch.apply_to_pytest(environ, home=HOME, platform="win32", can_write=only(DRIVE_TEMP))
    assert root == DRIVE_TEMP
    assert environ["PYTEST_DEBUG_TEMPROOT"] == str(DRIVE_TEMP)


def test_a_root_the_caller_already_chose_is_never_overruled():
    """A CI job that puts temporary files on a fast disk keeps them there."""
    environ = windows_env(PYTEST_DEBUG_TEMPROOT="D:/ci-temp")
    root = scratch.apply_to_pytest(environ, home=HOME, platform="win32", can_write=only(DRIVE_TEMP))
    assert root is None
    assert environ["PYTEST_DEBUG_TEMPROOT"] == "D:/ci-temp"


def test_every_reading_of_the_platform_spells_windows_the_same_way():
    assert scratch.is_windows("win32") is True
    assert scratch.is_windows("linux") is False
    assert scratch.is_windows("nt") is True


@pytest.mark.skipif(not scratch.is_windows(), reason="the default temp directory is inside the home only on Windows")
def test_on_this_machine_the_root_is_real_and_outside_the_home():
    """The rule as it lands here: a folder this account can write, outside the home, created already."""
    home = Path.home()
    if not scratch.system_temp().is_relative_to(home):
        pytest.skip("this machine already keeps its temporary files outside the home")
    root = scratch.scratch_root(home=home)
    assert root is not None
    assert root.is_dir()
    assert not root.is_relative_to(home)
