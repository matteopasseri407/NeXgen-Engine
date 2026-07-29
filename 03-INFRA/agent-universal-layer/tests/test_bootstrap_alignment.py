"""The per-CLI bootstrap inventory must report ALIGNMENT, not existence.

Reported from a Windows 11 host on 2026-07-26: Antigravity spent an afternoon
working from an `AGENTS.md` that was 42 lines behind the canonical one. On a
host without symlink privilege `make_link()` falls back to a real copy, and the
inventory called that copy `present` exactly as it would a fresh one, so nothing
in the output distinguished "aligned" from "three weeks stale". The CLI reading
it looks confused rather than out of date, which is the hard part to diagnose.

These tests pin the distinction the inventory now draws, including the three
different mechanisms (pointer / mirror / config) that must NOT be judged by the
same rule: Claude's pointer is supposed to differ from the canonical file.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from conftest import load_agent_sync_module

CANON_TEXT = "# canonical bootstrap\n\nrule one\nrule two\n"


@pytest.fixture
def agent_sync(sandbox):
    """The real agent_sync.py, loaded from the sandboxed engine copy — same
    loader every other test in this suite uses, so nothing here can touch the
    runner's real HOME."""
    return load_agent_sync_module(sandbox)


@pytest.fixture
def canon(tmp_path: Path) -> Path:
    path = tmp_path / "instructions" / "AGENTS.md"
    path.parent.mkdir(parents=True)
    path.write_text(CANON_TEXT, encoding="utf-8")
    return path


def describe(agent_sync, kind: str, target: Path, canon: Path) -> str:
    return agent_sync._bootstrap_alignment(kind, target, canon, agent_sync._file_digest(canon))


# --------------------------------------------------------------------------
# mirror: the file the CLI reads directly (symlink where allowed, else a copy)
# --------------------------------------------------------------------------


def test_a_diverged_copy_is_reported_as_diverged(agent_sync, tmp_path: Path, canon: Path):
    """The whole point: a stale copy must not read as healthy."""
    target = tmp_path / ".codex" / "AGENTS.md"
    target.parent.mkdir()
    target.write_text("# canonical bootstrap\n\nrule one\n", encoding="utf-8")
    state = describe(agent_sync, "mirror", target, canon)
    assert "DIVERGED" in state, state
    assert "agent-sync apply" in state, "the line must carry the recovery command"


def test_an_identical_copy_says_it_is_a_copy_not_a_link(agent_sync, tmp_path: Path, canon: Path):
    """Identical today is not the same promise as linked: a copy only re-aligns
    when agent-sync runs, and the user has to know that to trust it."""
    target = tmp_path / ".codex" / "AGENTS.md"
    target.parent.mkdir()
    target.write_text(CANON_TEXT, encoding="utf-8")
    state = describe(agent_sync, "mirror", target, canon)
    assert "DIVERGED" not in state, state
    assert "copy" in state, state
    assert "agent-sync" in state, "a copy's freshness is bounded by the last run; say so"


def test_a_symlink_is_reported_as_a_link(agent_sync, tmp_path: Path, canon: Path):
    target = tmp_path / ".codex" / "AGENTS.md"
    target.parent.mkdir()
    target.symlink_to(canon)
    assert describe(agent_sync, "mirror", target, canon) == "link -> canonical bootstrap"


def test_a_missing_bootstrap_is_not_confused_with_a_broken_one(agent_sync, tmp_path: Path, canon: Path):
    target = tmp_path / ".codex" / "AGENTS.md"
    assert describe(agent_sync, "mirror", target, canon) == "not configured on this machine"


def test_alignment_is_unknown_rather_than_ok_when_the_canonical_file_is_gone(agent_sync, tmp_path: Path):
    """Never report a green state computed from a canonical file we could not
    read: that is how a phantom `ok` gets into a health report."""
    target = tmp_path / ".codex" / "AGENTS.md"
    target.parent.mkdir()
    target.write_text(CANON_TEXT, encoding="utf-8")
    state = agent_sync._bootstrap_alignment("mirror", target, tmp_path / "gone.md", None)
    assert "unknown" in state, state
    assert "DIVERGED" not in state, state


# --------------------------------------------------------------------------
# pointer: Claude reads a short file that REFERENCES the canonical one
# --------------------------------------------------------------------------


def test_a_valid_claude_pointer_is_accepted_even_though_it_differs(agent_sync, tmp_path: Path, canon: Path):
    """Judging the pointer by content hash would flag the correct setup as
    broken: the pointer is meant to be a different, much shorter file."""
    target = tmp_path / "CLAUDE.md"
    target.write_text(f"# pointer\n\nCanonical instructions live at:\n{canon}\n", encoding="utf-8")
    assert describe(agent_sync, "pointer", target, canon) == "pointer -> canonical bootstrap"


def test_a_pointer_that_lost_the_canonical_path_is_flagged(agent_sync, tmp_path: Path, canon: Path):
    target = tmp_path / "CLAUDE.md"
    target.write_text("# pointer\n\nsomeone rewrote this by hand\n", encoding="utf-8")
    state = describe(agent_sync, "pointer", target, canon)
    assert "does NOT reference the canonical bootstrap" in state, state
    assert "agent-sync apply" in state, state


# --------------------------------------------------------------------------
# config: OpenCode carries the canonical PATH inside its own config
# --------------------------------------------------------------------------


def test_opencode_config_is_not_judged_by_content(agent_sync, tmp_path: Path, canon: Path):
    target = tmp_path / "opencode.json"
    target.write_text('{"instructions": []}', encoding="utf-8")
    state = describe(agent_sync, "config", target, canon)
    assert "DIVERGED" not in state and "copy" not in state, state
    assert "config" in state, state


def test_digest_of_an_unreadable_path_is_none_not_an_exception(agent_sync, tmp_path: Path):
    """The inventory is read-only diagnostics: it must never crash the run."""
    assert agent_sync._file_digest(tmp_path / "nope.md") is None
    assert agent_sync._file_digest(tmp_path) is None
