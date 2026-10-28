"""A tree on a branch says so, instead of looking up to date and never moving again.

`nexgen update` on a checkout of `developer` printed "already at the requested
released version", because the version matched the newest release. The machine
looked updated and would have stayed on that branch forever without a word: the
next question, months later, would have been "why is this server not updating?".
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import i18n, updater  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.update_notes import tree_off_release  # noqa: E402 - sys.path shim for cloned checkout


def _target() -> SimpleNamespace:
    return SimpleNamespace(release=True, version="2.21.2", ref="v2.21.2", label="v2.21.2", rev="v2.21.2")


def _fake_git(head: str, released: str):
    def fake(repo, *args):
        if args and args[-1] == "HEAD":
            return SimpleNamespace(stdout=head + "\n", returncode=0)
        return SimpleNamespace(stdout=released + "\n", returncode=0)

    return fake


def test_a_tree_off_the_release_names_the_commit_and_the_way_out():
    note = tree_off_release("a" * 40, "b" * 40, "v2.21.2")

    assert note is not None
    assert "aaaaaaaa" in note
    assert "not on v2.21.2" in note
    assert "--channel nightly" in note


def test_a_tree_on_the_release_says_nothing():
    assert tree_off_release("c" * 40, "c" * 40, "v2.21.2") is None


def test_the_update_prints_that_note_and_still_settles(monkeypatch, capsys):
    i18n.set_language("en")
    monkeypatch.setattr(updater, "_git", _fake_git("a" * 40, "b" * 40))

    assert updater._settled(Path("/repo"), "2.21.2", _target(), requested=None) is True

    printed = capsys.readouterr().out
    assert "already at the requested released version" in printed
    assert "not on v2.21.2" in printed
