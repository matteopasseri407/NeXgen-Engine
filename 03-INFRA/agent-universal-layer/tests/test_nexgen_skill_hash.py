"""The skill content hash, and the doctor line that uses it.

The vectors in the first test are the ones vercel-labs/skills publishes in
`tests/skill-hash.test.ts` and documents in its README: they are copied here as
literals so a reimplementation is checked against the project that owns the
algorithm, never against itself.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import i18n  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.checks.skill_checks import check_skill_view_drift  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.report import Severity  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.skill_hash import hash_skill_files, skill_folder_hash  # noqa: E402 - sys.path shim for cloned checkout


@pytest.fixture(autouse=True)
def _english():
    """The source language, whatever the machine reads: these lines are English by construction."""
    i18n.set_language("en")
    yield
    i18n.set_language(None)

#: What the upstream project's own test suite asserts for these three inputs.
EMPTY_HASH = "e3b0c44298fc1c149afbf4c8996fb924" "27ae41e4649b934ca495991b7852b855"
SKILL_ONLY_HASH = "93c36952d8843b33e68ae970ebabcc03" "e1687c9bb0dd1ecaf357060318952f55"
TWO_FILES_HASH = "b22c4d22aa319d2924312fa18fcbfedc" "88fa89c74dbd8f298c683e1caf7b7f24"


def test_the_published_vectors():
    assert hash_skill_files([]) == EMPTY_HASH
    assert hash_skill_files([("SKILL.md", b"# hi\n")]) == SKILL_ONLY_HASH
    assert hash_skill_files([("docs/b.md", b"b\n"), ("SKILL.md", b"a\n")]) == TWO_FILES_HASH


def test_paths_are_sorted_by_their_bytes_and_not_by_a_locale():
    files = [("b.md", b"1"), ("B.md", b"2"), ("\u00e4.md", b"3")]

    assert hash_skill_files(files) == hash_skill_files(list(reversed(files)))
    assert hash_skill_files(files) != hash_skill_files([("B.md", b"1"), ("b.md", b"2"), ("\u00e4.md", b"3")])


def test_each_file_is_framed_so_content_cannot_move_across_a_boundary():
    assert hash_skill_files([("ab", b"c")]) != hash_skill_files([("a", b"bc")])
    assert hash_skill_files([("a", b"x"), ("b", b"")]) != hash_skill_files([("a", b"xb")])


def test_a_text_file_hashes_the_same_whatever_its_line_endings():
    assert hash_skill_files([("SKILL.md", b"# hi\r\nthere\r\n")]) == hash_skill_files(
        [("SKILL.md", b"# hi\nthere\n")]
    )


def test_a_binary_file_keeps_its_bytes():
    assert hash_skill_files([("a.bin", bytes([0, 13, 10]))]) != hash_skill_files([("a.bin", bytes([0, 10]))])


def test_a_windows_separator_names_the_same_file():
    assert hash_skill_files([("docs\\b.md", b"b")]) == hash_skill_files([("docs/b.md", b"b")])


def test_a_folder_hashes_like_the_same_files_in_memory(tmp_path):
    skill = tmp_path / "skill"
    (skill / "docs").mkdir(parents=True)
    (skill / "SKILL.md").write_bytes(b"a\n")
    (skill / "docs" / "b.md").write_bytes(b"b\n")

    assert skill_folder_hash(skill) == TWO_FILES_HASH


def test_the_names_the_installer_never_writes_are_left_out(tmp_path):
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_bytes(b"# hi\n")
    (skill / "metadata.json").write_text("{}", encoding="utf-8")
    (skill / ".git").mkdir()
    (skill / ".git" / "HEAD").write_text("ref: main\n", encoding="utf-8")
    (skill / "__pycache__").mkdir()
    (skill / "__pycache__" / "x.pyc").write_bytes(b"cache")

    assert skill_folder_hash(skill) == SKILL_ONLY_HASH


def test_a_link_inside_the_folder_counts_as_the_file_it_points_at(tmp_path):
    skill = tmp_path / "skill"
    (skill / "docs").mkdir(parents=True)
    (skill / "SKILL.md").write_bytes(b"a\n")
    (skill / "source.md").write_bytes(b"b\n")
    try:
        (skill / "docs" / "b.md").symlink_to(skill / "source.md")
    except OSError:
        pytest.skip("this machine cannot create a symlink")

    assert skill_folder_hash(skill) == hash_skill_files(
        [("SKILL.md", b"a\n"), ("source.md", b"b\n"), ("docs/b.md", b"b\n")]
    )


def test_a_link_that_leaves_or_breaks_is_left_out(tmp_path):
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_bytes(b"# hi\n")
    (tmp_path / "secret.txt").write_bytes(b"secret")
    try:
        (skill / "secret.txt").symlink_to(tmp_path / "secret.txt")
        (skill / "broken.txt").symlink_to(tmp_path / "nowhere")
    except OSError:
        pytest.skip("this machine cannot create a symlink")

    assert skill_folder_hash(skill) == SKILL_ONLY_HASH


def _home_with_copies(tmp_path: Path, *, claude_body: bytes | None = None) -> Path:
    """A home whose library holds one skill and whose Claude view is a copy of it."""
    home = tmp_path / "home"
    library = home / ".agents" / "skill-library" / "scrittura"
    library.mkdir(parents=True)
    (library / "SKILL.md").write_bytes(b"# scrittura\n")
    view = home / ".claude" / "skills" / "scrittura"
    view.mkdir(parents=True)
    (view / "SKILL.md").write_bytes(b"# scrittura\n" if claude_body is None else claude_body)
    return home


def test_a_view_that_holds_the_library_bytes_passes(tmp_path):
    outcome = check_skill_view_drift(tmp_path / "vault", _home_with_copies(tmp_path))

    assert outcome.severity == Severity.OK
    assert outcome.id == "skills.view_drift"


def test_a_view_edited_by_hand_is_named_with_its_runtime(tmp_path):
    home = _home_with_copies(tmp_path, claude_body=b"# scrittura, edited here\n")

    outcome = check_skill_view_drift(tmp_path / "vault", home)

    assert outcome.severity == Severity.WARN
    assert "scrittura (claude)" in outcome.message
    assert "agent-sync apply" in (outcome.action or "")


def test_a_linked_view_is_the_library_and_never_read_twice(tmp_path):
    home = tmp_path / "home"
    library = home / ".agents" / "skill-library" / "scrittura"
    library.mkdir(parents=True)
    (library / "SKILL.md").write_bytes(b"# scrittura\n")
    view_root = home / ".claude" / "skills"
    view_root.mkdir(parents=True)
    try:
        (view_root / "scrittura").symlink_to(library, target_is_directory=True)
    except OSError:
        pytest.skip("this machine cannot create a symlink")

    outcome = check_skill_view_drift(tmp_path / "vault", home)

    assert outcome.severity == Severity.OK
