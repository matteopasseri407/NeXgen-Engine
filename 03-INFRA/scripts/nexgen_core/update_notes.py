# nexgen: one-job reason="explain a checkout that is not the release it reports, and name the channel that would move it"
"""What to say when a tree's version matches a release but its commit is not that release.

A checkout of `developer` carries the same version as the newest release, so an
update that compares versions concludes "already there" and stops. The machine is
not broken and it is not updating either: this is the sentence that says so, in one
place, with the command that would move it.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path


def tree_off_release(head: str, released: str, tag: str) -> str | None:
    """A sentence when HEAD is not the release's commit, or None when it is.

    The version in `VERSION` is a fact the release and a branch share; only the
    commit tells them apart, so both are passed in already read.
    """
    if not head or not released or head == released:
        return None
    return (
        f"This tree is on {head[:8]}, not on {tag}: it follows a branch, and this channel will not "
        f"move it. To follow it on purpose: 'nexgen update --channel nightly'."
    )


def say_if_tree_off_release(
    engine_repo: Path, tag: str, read: Callable[..., object], out: Callable[[str], None] = print,
) -> None:
    """Reads the two commits through the caller's own git and prints the sentence when it applies."""
    head = str(getattr(read(engine_repo, "rev-parse", "HEAD"), "stdout", "") or "").strip()
    released = str(getattr(read(engine_repo, "rev-parse", f"{tag}^{{commit}}"), "stdout", "") or "").strip()
    note = tree_off_release(head, released, tag)
    if note:
        out(note)
