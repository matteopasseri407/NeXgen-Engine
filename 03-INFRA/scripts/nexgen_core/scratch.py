"""Where this process puts its scratch files, and when the default is the wrong place.

One job: answer where temporary files may go so that they do not land under the
owner's home, and make pytest use that answer.

On Windows `%TEMP%` is `C:\\Users\\<name>\\AppData\\Local\\Temp`, inside the home.
OpenClaw's gateway watches the home with one recursive handle (see
`openclaw/observation.py` for the code that says so), so a test suite that
creates and deletes thousands of files there is an event storm the gateway
cannot fold: it ends its reload attempts and turns hot reload off. Linux puts
`/tmp` outside the home, so nothing changes there.

The choice, in order, and the first one that this process can actually create a
directory in wins:

1. `NEXGEN_SCRATCH_ROOT`, when it is set: the person said where;
2. the system temporary directory, when it is already outside the home;
3. `%SystemDrive%\\Temp\\nexgen-<user>`;
4. `%PUBLIC%\\nexgen-<user>`.

Rule 3 is the usual answer on Windows: `C:\\` grants authenticated users
"create folders", so a standard account can make `C:\\Temp` without any
privilege, and the per-user folder keeps two accounts from sharing one root.

When none of them works the answer is None, and the caller keeps the default:
the suite still runs, it is just noisier for the gateway. Silence would be
worse, so `describe` says what happened in one line a caller can print.

Cleanup is pytest's own: `PYTEST_DEBUG_TEMPROOT` makes it keep its numbered
`pytest-of-<user>` trees under that root and rotate the oldest away, which is
the same policy it applies to `%TEMP%` today.
"""
from __future__ import annotations

import getpass
import os
import re
import tempfile
from collections.abc import Iterator, Mapping, MutableMapping
from pathlib import Path

from nexgen_core.paths import is_inside, resolve_home

#: The variable a person or a CI job sets to choose the root themselves.
SCRATCH_VAR = "NEXGEN_SCRATCH_ROOT"
#: pytest's own override for its temporary root. Read from the environment at the
#: first `tmp_path` request, so setting it before that is enough.
PYTEST_TEMPROOT_VAR = "PYTEST_DEBUG_TEMPROOT"

#: The order `tempfile` itself looks at, so this module answers the same place.
TEMP_VARIABLES = ("TMPDIR", "TEMP", "TMP")


def is_windows(platform: str | None = None) -> bool:
    """Whether this is the platform whose default temp directory sits inside the home."""
    return str(platform or os.name).startswith(("win", "nt"))


def user_tag() -> str:
    """The account name as a folder name: no separators, no surprises."""
    try:
        name = (getpass.getuser() or "").strip()
    except (KeyError, OSError):  # nexgen: allow reason="an account with no name still needs a folder"
        name = ""
    return re.sub(r"[^A-Za-z0-9._-]", "_", name) or "unknown"


def system_temp(env: Mapping[str, str] | None = None) -> Path:
    """The temporary directory as this process would see it, without asking the filesystem."""
    values = os.environ if env is None else env
    for name in TEMP_VARIABLES:
        value = values.get(name)
        if value and value.strip():
            return Path(value.strip())
    return Path(tempfile.gettempdir())


def _fallback_roots(platform: str | None, env: Mapping[str, str]) -> Iterator[Path]:
    """The folders worth trying when `%TEMP%` is inside the home, in the order the docstring gives."""
    if not is_windows(platform):
        return
    drive = (env.get("SystemDrive") or "C:").strip() or "C:"
    yield Path(f"{drive}/Temp") / f"nexgen-{user_tag()}"
    public = (env.get("PUBLIC") or "").strip()
    if public:
        yield Path(public) / f"nexgen-{user_tag()}"


def usable(directory: Path) -> bool:
    """Whether this process may create a directory here, proven by creating one."""
    try:
        directory.mkdir(parents=True, exist_ok=True)
        os.rmdir(tempfile.mkdtemp(dir=directory, prefix=".probe-"))
    except OSError:  # nexgen: allow reason="a folder this account cannot write is a candidate that lost, not a failure"
        return False
    return True


def scratch_root(
    *,
    home: Path | None = None,
    platform: str | None = None,
    env: Mapping[str, str] | None = None,
    can_write=usable,
) -> Path | None:
    """The place to put scratch files, or None when the default already is that place.

    None is the normal answer on Linux, and on a Windows machine whose `%TEMP%`
    is on another disk: there is nothing to move. It is also the answer when
    every candidate refuses, because a suite that cannot find a quiet corner
    still has to run.

    `can_write` arrives as a value so a test decides which candidates exist
    without creating any of them; the default proves it on the real filesystem.
    """
    home_dir = resolve_home(home)
    values = os.environ if env is None else env
    override = (values.get(SCRATCH_VAR) or "").strip()
    if override:
        return Path(override) if can_write(Path(override)) else None
    if not is_inside(system_temp(values), home_dir):
        return None
    for candidate in _fallback_roots(platform, values):
        if can_write(candidate):
            return candidate
    return None


def apply_to_pytest(
    environ: MutableMapping[str, str] | None = None,
    *,
    home: Path | None = None,
    platform: str | None = None,
    can_write=usable,
) -> Path | None:
    """Point pytest's temporary root outside the home, once per process, if it is not already set.

    A value already in `PYTEST_DEBUG_TEMPROOT` is the person's or the harness's
    decision and is never overruled: a CI job that puts its temporary files on a
    fast disk keeps them there.
    """
    values = os.environ if environ is None else environ
    if (values.get(PYTEST_TEMPROOT_VAR) or "").strip():
        return None
    root = scratch_root(home=home, platform=platform, env=values, can_write=can_write)
    if root is None:
        return None
    values[PYTEST_TEMPROOT_VAR] = str(root)
    return root


def describe(home: Path | None = None, platform: str | None = None) -> str:
    """One line about the scratch root, for a report or a log: never an exception."""
    root = scratch_root(home=home, platform=platform)
    if root is None:
        return "scratch files stay in the system temporary directory"
    return f"scratch files go to {root}, outside the home"
