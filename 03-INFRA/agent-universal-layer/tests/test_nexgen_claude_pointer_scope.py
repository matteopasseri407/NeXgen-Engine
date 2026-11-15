r"""The Claude pointer lives in `~/.claude/CLAUDE.md`, the file Claude reads everywhere.

Claude Code documents that path as user scope, loaded for every project, while
`~/CLAUDE.md` is only a home-directory project file: sessions started in `D:\`
or under `C:\WINDOWS` saw no pointer at all. These tests pin the new location,
the migration of the old one, and the files the engine must not touch: the
person's own instructions can legitimately live at both paths.
"""
from __future__ import annotations

from pathlib import Path

from nexgen_core import action_notes, files
from nexgen_core.checks.instructions_checks import check_claude_pointer
from nexgen_core.guard import GuardRunner
from nexgen_core.report import Severity


def _canon(sandbox) -> Path:
    """The canonical bootstrap the pointer has to name."""
    canon = sandbox.ul / "instructions" / "AGENTS.md"
    canon.parent.mkdir(parents=True, exist_ok=True)
    canon.write_text("# Canonical rules\n", encoding="utf-8")
    return canon


def _engine_pointer(canon: Path) -> str:
    """The pointer exactly as the engine writes it; a literal, not the builder."""
    return (
        "# Claude compatibility pointer\n\n"
        "Canonical instructions live at:\n"
        f"{canon}\n\n"
        "At session start, read and follow that file when the user-specific agent policy is needed.\n"
        "Do not duplicate the full bootstrap in CLAUDE.md.\n"
    )


def test_a_fresh_home_gets_the_pointer_in_claude_user_scope(sandbox):
    canon = _canon(sandbox)

    actions = GuardRunner(vault_data=sandbox.vault, home=sandbox.home).align_instructions()

    pointer = sandbox.home / ".claude" / "CLAUDE.md"
    assert pointer.read_text(encoding="utf-8") == _engine_pointer(canon)
    assert not (sandbox.home / "CLAUDE.md").exists(), "the home file is no longer written"
    assert any(str(pointer) in action for action in actions)


def test_the_old_home_pointer_is_removed_after_a_backup(sandbox):
    """An engine pointer at `~/CLAUDE.md` is loaded twice by a home session."""
    canon = _canon(sandbox)
    legacy = sandbox.home / "CLAUDE.md"
    legacy.write_text(_engine_pointer(canon), encoding="utf-8")

    actions = GuardRunner(vault_data=sandbox.vault, home=sandbox.home).align_instructions()

    assert not legacy.exists()
    backups = list(sandbox.home.glob("CLAUDE.md.pre-instructions-*.bak"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == _engine_pointer(canon)
    assert (sandbox.home / ".claude" / "CLAUDE.md").is_file()
    assert any(str(legacy) in action for action in actions)


def test_a_home_pointer_the_person_wrote_is_left_alone(sandbox):
    canon = _canon(sandbox)
    legacy = sandbox.home / "CLAUDE.md"
    legacy.write_text("# Le mie regole personali\n", encoding="utf-8")

    actions = GuardRunner(vault_data=sandbox.vault, home=sandbox.home).align_instructions()

    assert legacy.read_text(encoding="utf-8") == "# Le mie regole personali\n"
    assert list(sandbox.home.glob("CLAUDE.md.pre-instructions-*.bak")) == []
    assert (sandbox.home / ".claude" / "CLAUDE.md").read_text(encoding="utf-8") == _engine_pointer(canon)
    assert any(str(legacy) in action for action in actions)


def test_the_old_home_pointer_stays_when_the_new_path_is_taken(sandbox):
    """Never remove the last working pointer: home sessions keep reading it."""
    canon = _canon(sandbox)
    legacy = sandbox.home / "CLAUDE.md"
    legacy.write_text(_engine_pointer(canon), encoding="utf-8")
    personal = sandbox.home / ".claude" / "CLAUDE.md"
    personal.parent.mkdir(parents=True)
    personal.write_text("# Il mio stile\n", encoding="utf-8")

    actions = GuardRunner(vault_data=sandbox.vault, home=sandbox.home).align_instructions()

    assert legacy.read_text(encoding="utf-8") == _engine_pointer(canon)
    assert personal.read_text(encoding="utf-8") == "# Il mio stile\n"
    assert list(sandbox.home.glob("CLAUDE.md.pre-instructions-*.bak")) == []
    assert any(str(legacy) in action for action in actions)


def test_personal_instructions_in_claude_user_scope_are_never_overwritten(sandbox):
    """That path is documented user scope: it can hold the person's own rules."""
    _canon(sandbox)
    personal = sandbox.home / ".claude" / "CLAUDE.md"
    personal.parent.mkdir(parents=True)
    personal.write_text("# Il mio stile\n", encoding="utf-8")

    actions = GuardRunner(vault_data=sandbox.vault, home=sandbox.home).align_instructions()

    assert personal.read_text(encoding="utf-8") == "# Il mio stile\n"
    assert list(personal.parent.glob("CLAUDE.md.pre-instructions-*.bak")) == []
    assert any(str(personal) in action for action in actions)


def test_a_pointer_an_older_engine_wrote_is_refreshed(sandbox):
    """The header is what says "ours": a stale canonical path is still ours to fix."""
    canon = _canon(sandbox)
    pointer = sandbox.home / ".claude" / "CLAUDE.md"
    pointer.parent.mkdir(parents=True)
    pointer.write_text(
        "# Claude compatibility pointer\n\nCanonical instructions live at:\n/tmp/old-vault/AGENTS.md\n",
        encoding="utf-8",
    )

    GuardRunner(vault_data=sandbox.vault, home=sandbox.home).align_instructions()

    assert pointer.read_text(encoding="utf-8") == _engine_pointer(canon)


def test_a_second_cycle_changes_nothing(sandbox):
    canon = _canon(sandbox)
    runner = GuardRunner(vault_data=sandbox.vault, home=sandbox.home)
    runner.align_instructions()

    second = runner.align_instructions()

    assert second == []
    assert (sandbox.home / ".claude" / "CLAUDE.md").read_text(encoding="utf-8") == _engine_pointer(canon)


def test_a_failed_backup_keeps_the_old_home_pointer(sandbox, monkeypatch):
    """No safety copy, no deletion: the file may hold the only copy of those lines."""
    canon = _canon(sandbox)
    legacy = sandbox.home / "CLAUDE.md"
    legacy.write_text(_engine_pointer(canon), encoding="utf-8")

    def no_space(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(files, "backup_file", no_space)

    actions = GuardRunner(vault_data=sandbox.vault, home=sandbox.home).align_instructions()

    assert legacy.read_text(encoding="utf-8") == _engine_pointer(canon)
    assert any(action_notes.is_warning(action) and str(legacy) in action for action in actions)


def test_a_link_to_the_old_home_file_is_not_deleted_through(sandbox):
    """`~/.claude/CLAUDE.md -> ~/CLAUDE.md` (a dotfiles layout inside home).

    The pointer write goes through the link into `~/CLAUDE.md`, and the
    retirement step must not delete that file: it is the one the link points
    at, so the cycle used to leave a dangling link, no instructions at all and
    one more safety copy on every run.
    """
    canon = _canon(sandbox)
    legacy = sandbox.home / "CLAUDE.md"
    legacy.write_text(_engine_pointer(canon), encoding="utf-8")
    link = sandbox.home / ".claude" / "CLAUDE.md"
    link.parent.mkdir()
    link.symlink_to(legacy)

    runner = GuardRunner(vault_data=sandbox.vault, home=sandbox.home)
    actions = runner.align_instructions()

    assert legacy.read_text(encoding="utf-8") == _engine_pointer(canon)
    assert link.is_symlink() and link.resolve() == legacy.resolve()
    assert list(sandbox.home.glob("CLAUDE.md.pre-instructions-*.bak")) == []
    assert any(str(legacy) in action for action in actions)
    # The layout is reported again, as any file the engine leaves alone is; what
    # must not happen again is a backup and a deletion.
    runner.align_instructions()
    assert legacy.read_text(encoding="utf-8") == _engine_pointer(canon)
    assert list(sandbox.home.glob("CLAUDE.md.pre-instructions-*.bak")) == []


def test_a_dotfiles_pointer_that_already_matches_is_left_alone(sandbox):
    """A link into a dotfiles repository whose file is already the pointer.

    Rewriting it every cycle gave the repository a new inode twice an hour and
    reported a change that never happened: the comparison has to happen behind
    the link, where the bytes Claude reads live.
    """
    canon = _canon(sandbox)
    dotfiles = sandbox.home.parent / "dotfiles"
    dotfiles.mkdir()
    real = dotfiles / "CLAUDE.md"
    real.write_text(_engine_pointer(canon), encoding="utf-8")
    link = sandbox.home / ".claude" / "CLAUDE.md"
    link.parent.mkdir()
    link.symlink_to(real)

    runner = GuardRunner(vault_data=sandbox.vault, home=sandbox.home)
    runner.align_instructions()
    before = real.stat()

    actions = runner.align_instructions()

    after = real.stat()
    assert actions == []
    assert (after.st_mtime_ns, after.st_size) == (before.st_mtime_ns, before.st_size)
    assert list(real.parent.glob("CLAUDE.md.pre-instructions-*.bak")) == []
    assert link.is_symlink() and link.resolve() == real.resolve()


def test_the_doctor_validates_the_user_scope_file_only(tmp_path):
    vault = tmp_path / "vault"
    canon = vault / "03-INFRA" / "agent-universal-layer" / "instructions" / "AGENTS.md"
    canon.parent.mkdir(parents=True)
    canon.write_text("# Canonical rules\n", encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    # A correct pointer at the old home path is not what Claude reads: still broken.
    (home / "CLAUDE.md").write_text(_engine_pointer(canon), encoding="utf-8")

    assert check_claude_pointer(vault, home).severity == Severity.BROKEN

    (home / ".claude").mkdir()
    (home / ".claude" / "CLAUDE.md").write_text(_engine_pointer(canon), encoding="utf-8")

    assert check_claude_pointer(vault, home).severity == Severity.OK


def test_the_doctor_accepts_the_dotfiles_symlink_the_guard_writes_through(tmp_path):
    """A dotfiles-managed link is a supported layout: the guard writes through it, so the
    doctor must not report red for the file the guard itself just produced."""
    vault = tmp_path / "vault"
    canon = vault / "03-INFRA" / "agent-universal-layer" / "instructions" / "AGENTS.md"
    canon.parent.mkdir(parents=True)
    canon.write_text("# Canonical rules\n", encoding="utf-8")
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    dotfiles = tmp_path / "dotfiles" / "CLAUDE.md"
    dotfiles.parent.mkdir()
    dotfiles.write_text(_engine_pointer(canon), encoding="utf-8")
    (home / ".claude" / "CLAUDE.md").symlink_to(dotfiles)

    assert check_claude_pointer(vault, home).severity == Severity.OK


def test_an_engine_pointer_with_a_stale_path_is_ours_to_refresh(tmp_path):
    """The header marks the file as the engine's: "run sync" repairs it, and never a
    instruction to overwrite a file the person wrote."""
    vault = tmp_path / "vault"
    canon = vault / "03-INFRA" / "agent-universal-layer" / "instructions" / "AGENTS.md"
    canon.parent.mkdir(parents=True)
    canon.write_text("# Canonical rules\n", encoding="utf-8")
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "CLAUDE.md").write_text(
        "# Claude compatibility pointer\n\nCanonical instructions live at:\n/tmp/old-vault/AGENTS.md\n",
        encoding="utf-8",
    )

    outcome = check_claude_pointer(vault, home)

    assert outcome.severity == Severity.BROKEN
    assert "agent-sync apply" in (outcome.action or "")
