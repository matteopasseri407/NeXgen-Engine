"""A dead view entry must not fail the whole skills phase, nor pile backups."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.skill_sources import make_link_or_copy, same_tree_content  # noqa: E402

windows_only = pytest.mark.skipif(os.name != "nt", reason="junctions")


def _write_skill(directory: Path, body: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(body, encoding="utf-8")


def _backups(dst: Path) -> list[Path]:
    return sorted(dst.parent.glob(f"{dst.name}.bak-*")) + sorted(
        dst.parent.glob(f"{dst.name}.pre-*.bak")
    )


def test_link_replaces_diverged_dir_and_rotates_backups(tmp_path):
    import shutil as _shutil

    src = tmp_path / "src"
    dst = tmp_path / "views" / "myskill"
    dst.parent.mkdir(parents=True)
    for round in range(5):
        _write_skill(src, f"v{round}")
        # A stale real dir at dst, as a copy-fallback view would be.
        if dst.is_symlink() or dst.is_file():
            dst.unlink()
        elif dst.is_dir():
            _shutil.rmtree(dst)
        _write_skill(dst, "stale")
        assert make_link_or_copy(src, dst) is True
        assert (dst / "SKILL.md").read_text(encoding="utf-8") == f"v{round}"
    assert len(_backups(dst)) <= 3


@windows_only
def test_link_replaces_dangling_junction(tmp_path):
    """A junction whose target is gone is invisible to is_dir/is_symlink
    yet blocks symlink_to with WinError 183. It used to fail the whole
    skills phase on every guard cycle, leaving every skill view broken.
    """
    src = tmp_path / "src"
    _write_skill(src, "live")
    target = tmp_path / "gone"
    target.mkdir()
    dst = tmp_path / "views" / "myskill"
    dst.parent.mkdir(parents=True)
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(dst), str(target)],
        check=True, capture_output=True, text=True,
    )
    target.rmdir()
    assert os.path.lexists(dst) and not dst.is_dir() and not dst.is_symlink()
    assert make_link_or_copy(src, dst) is True
    assert (dst / "SKILL.md").read_text(encoding="utf-8") == "live"


def test_same_size_same_mtime_different_content_is_different(tmp_path):
    """Timestamps are not content: equal size plus equal mtime must still
    compare bytes. The shortcut once turned stale views into false greens.
    """
    a = tmp_path / "a"
    b = tmp_path / "b"
    _write_skill(a, "old")
    _write_skill(b, "new")
    stamp = 1_700_000_000
    os.utime(a / "SKILL.md", (stamp, stamp))
    os.utime(b / "SKILL.md", (stamp, stamp))
    assert not same_tree_content(a, b)
    _write_skill(b, "old")
    os.utime(b / "SKILL.md", (stamp, stamp))
    assert same_tree_content(a, b)
