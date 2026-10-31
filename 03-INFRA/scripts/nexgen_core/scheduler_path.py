"""One job: the PATH a guard unit declares, as a fixed point of itself.

Why it exists: the guard runs with the PATH its own unit wrote, so a PATH that
depends on the shell that installed it grows on every cycle and the unit is
rewritten and reloaded each time. This module keeps the value stable: the same
two runs produce the same string.

It also follows nvm's default Node. After `nvm install` plus `nvm alias default`,
the old `~/.nvm/versions/node/<old>/bin` used to stay ahead forever, because
every directory an earlier unit declared was kept in order and only new ones
were added: the guard kept seeing the old Node and the old global OpenClaw. When
`~/.nvm/alias/default` resolves to an installed version directory, that version's
`bin` is placed where node directories go and every other nvm version directory
inherited from the old unit is dropped. An alias that cannot be resolved is left
alone: inventing a Node the machine does not have is worse than a stale order.
"""
from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path

#: The directories every POSIX service gets, after the machine's own.
STANDARD_DIRS = (
    "/usr/local/sbin",
    "/usr/local/bin",
    "/usr/sbin",
    "/usr/bin",
    "/sbin",
    "/bin",
    "/snap/bin",
)

#: `~/.nvm/versions/node/<version>/bin`: one bin folder per installed Node.
_NVM_VERSIONS = ("versions", "node")


def compose(home: Path, inherited: Sequence[str], tool_dirs: Sequence[str], executable: str | None) -> str:
    """The PATH string from what the unit inherited, what this shell sees, and nvm's default."""
    head = [
        str(home / ".local" / "bin"),
        str(home / ".opencode" / "bin"),
        str(Path(executable).parent) if executable else "",
    ]
    default_bin = nvm_default_bin(home)
    if default_bin is not None:
        head.append(str(default_bin))
        inherited = [entry for entry in inherited if not _is_nvm_version_bin(home, entry)]
        tool_dirs = [entry for entry in tool_dirs if not _is_nvm_version_bin(home, entry)]
    entries = [*head, *inherited, *tool_dirs, *STANDARD_DIRS]
    seen: set[str] = set()
    unique: list[str] = []
    for entry in entries:
        key = os.path.normcase(entry)
        if not entry or key in seen:
            continue
        seen.add(key)
        unique.append(entry)
    return os.pathsep.join(unique)


def nvm_default_bin(home: Path) -> Path | None:
    """`~/.nvm/versions/node/<default>/bin` for the version `nvm alias default` names.

    The alias file holds a version (`24.22.1` or `v24.22.1`) or the name of another
    alias under the same folder; one level of that indirection is followed, which is
    what `nvm alias default lts/*` writes. Anything else returns None, and the caller
    keeps the PATH it had.
    """
    alias_dir = home / ".nvm" / "alias"
    version = _alias_text(alias_dir / "default")
    if version is None:
        return None
    if not version[0].isdigit():
        version = _alias_text(alias_dir / version)
        if version is None:
            return None
    return _installed_bin(home, version)


def _alias_text(path: Path) -> str | None:
    """The trimmed first line of one alias file, or None when it is absent or empty."""
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:  # nexgen: allow reason="no default alias is the ordinary case on a machine without nvm"
        return None
    return text or None


def _installed_bin(home: Path, version: str) -> Path | None:
    """The `bin` of one installed Node under nvm, named with or without its `v`."""
    for name in (f"v{version}", version):
        candidate = home.joinpath(".nvm", *_NVM_VERSIONS, name, "bin")
        if candidate.is_dir():
            return candidate
    return None


def _is_nvm_version_bin(home: Path, entry: str) -> bool:
    """Whether one PATH entry is `~/.nvm/versions/node/<version>/bin`."""
    root = home.joinpath(".nvm", *_NVM_VERSIONS)
    try:
        path = Path(entry)
        relative = path.relative_to(root)
    except (OSError, ValueError):  # nexgen: allow reason="an entry outside nvm's own tree is simply not one of its node directories"
        return False
    return len(relative.parts) == 2 and relative.parts[1] == "bin"
