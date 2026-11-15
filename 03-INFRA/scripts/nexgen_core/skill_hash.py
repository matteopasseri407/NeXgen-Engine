"""The content identity of one skill folder, as vercel-labs/skills computes it.

One job: turn the files of a skill folder into the single string
`skills-lock.json` (version 2) records as `computedHash`, so "does this view
still hold the library's bytes" is one comparison instead of an mtime guess.

The algorithm is the published one (`src/skill-hash.ts`, MIT, Copyright (c) 2026
Vercel, Inc.): the paths of the files the installer would write, sorted by their
UTF-8 bytes, each framed as `path`, NUL, the byte length of that file's content,
NUL, the content, all into one SHA-256. A text file is hashed with CRLF turned
into LF, so the same skill is the same hash on Windows and on Linux, and the
names the installer never writes (`metadata.json`, `.git`, `__pycache__`,
`__pypackages__`) are left out either way.

Reimplemented instead of called, for two reasons: that project is a Node CLI and
this engine declares no Node requirement, and the published test vectors
(`tests/skill-hash.test.ts`) make the reimplementation verifiable rather than a
second opinion. No Python port of this exact framing was found on GitHub before
writing it. CREDITS.md records the project.

Only `computedHash` v2 is here. The v1 hash it replaced was locale-dependent and
is deliberately not reproduced.
"""
from __future__ import annotations

import hashlib
from collections.abc import Iterable
from pathlib import Path

#: The one file name the installer never writes into a skill folder.
EXCLUDED_FILES = frozenset({"metadata.json"})

#: The folder names that never belong to a skill: version control and caches.
EXCLUDED_DIRS = frozenset({".git", "__pycache__", "__pypackages__"})


def hash_skill_files(files: Iterable[tuple[str, bytes]]) -> str:
    """The `computedHash` of a set of files, each named by its path in the skill.

    Paths are `/`-separated whatever the platform wrote, sorted by their UTF-8
    bytes rather than by any locale, and framed with their content length so a
    file's bytes can never move across a boundary and keep the same hash.
    """
    entries = sorted(
        ((path.replace("\\", "/").encode("utf-8"), _text(content)) for path, content in files),
        key=lambda entry: entry[0],
    )
    digest = hashlib.sha256()
    for path, content in entries:
        digest.update(path)
        digest.update(b"\0")
        digest.update(str(len(content)).encode("utf-8"))
        digest.update(b"\0")
        digest.update(content)
    return digest.hexdigest()


def skill_folder_hash(folder: Path) -> str:
    """The `computedHash` of the skill folder at `folder`.

    The folder is hashed the way the installer would copy it: the excluded names
    are skipped, a link counts as the content it points at, and a link that is
    broken, cyclic or leaves the folder is left out, because that is the copy the
    installer would refuse to carry either.
    """
    root = folder.resolve()
    found: list[tuple[str, bytes]] = []
    _collect(folder, "", root, frozenset(), found)
    return hash_skill_files(found)


def _text(content: bytes) -> bytes:
    """`content` with CRLF turned into LF, unless a NUL byte makes it binary.

    The rule is the published one: a file containing a NUL is hashed as it is, so
    an archive or an image keeps both bytes and a text file does not depend on
    the line endings the machine that wrote it used.
    """
    if b"\0" in content or b"\r\n" not in content:
        return content
    return content.replace(b"\r\n", b"\n")


def _collect(
    directory: Path, prefix: str, root: Path, ancestors: frozenset[Path], found: list[tuple[str, bytes]],
) -> None:
    """Adds one folder's files to `found`, refusing to walk a cycle or to leave `root`."""
    real = directory.resolve()
    if real in ancestors:
        return
    branch = ancestors | {real}
    for entry in sorted(directory.iterdir(), key=lambda item: item.name):
        if entry.name in EXCLUDED_FILES:
            continue
        if entry.is_symlink():
            target = _inside(entry, root)
            if target is None:
                continue
            if target.is_dir():
                if entry.name not in EXCLUDED_DIRS:
                    _collect(entry, f"{prefix}{entry.name}/", root, branch, found)
                continue
            found.append((f"{prefix}{entry.name}", target.read_bytes()))
            continue
        if entry.is_dir():
            if entry.name in EXCLUDED_DIRS:
                continue
            _collect(entry, f"{prefix}{entry.name}/", root, branch, found)
        elif entry.is_file():
            found.append((f"{prefix}{entry.name}", entry.read_bytes()))


def _inside(link: Path, root: Path) -> Path | None:
    """What a link points at, or None when it is broken, cyclic or leaves the skill."""
    try:
        target = link.resolve(strict=True)
    except OSError:  # nexgen: allow reason="a broken or cyclic link is the copy the installer refuses, and it is left out of the hash"
        return None
    return target if target == root or root in target.parents else None
