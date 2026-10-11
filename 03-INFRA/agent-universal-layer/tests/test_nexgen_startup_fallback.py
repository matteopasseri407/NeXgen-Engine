"""The Startup-folder logon fallback keeps backups outside Startup.

Windows executes everything inside the Startup folder, including `.bak`
files, so rotated backups must live in a sibling directory that the logon
sequence never runs. Strays left by previous versions are removed.
"""
from __future__ import annotations

import sys
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.scheduler import _install_startup_fallback  # noqa: E402


def _live(appdata: Path) -> Path:
    return (
        appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        / "Startup" / "KnowledgeVault Agent Sync.vbs"
    )


def _backup_dir(appdata: Path) -> Path:
    return (
        appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        / "NeXgen-Backup"
    )


def _backups(appdata: Path) -> list[Path]:
    return sorted(_backup_dir(appdata).glob("KnowledgeVault Agent Sync.vbs.pre-startup-*.bak"))


def _strays(appdata: Path) -> list[Path]:
    return sorted(_live(appdata).parent.glob("KnowledgeVault Agent Sync.vbs*.bak"))


def test_startup_fallback_writes_once_and_rotates(tmp_path):
    logs: list[str] = []
    assert _install_startup_fallback(tmp_path, "v1", True, logs.append) is True
    assert _live(tmp_path).read_text(encoding="utf-8") == "v1"
    assert _backups(tmp_path) == []
    assert _strays(tmp_path) == []

    # Unchanged content: no backup, no rewrite noise.
    assert _install_startup_fallback(tmp_path, "v1", True, logs.append) is True
    assert _backups(tmp_path) == []
    assert _strays(tmp_path) == []

    # A version flap mid-update keeps at most 3 previous scripts,
    # none of them inside the Startup folder.
    for i in range(2, 8):
        assert _install_startup_fallback(tmp_path, f"v{i}", True, logs.append) is True
    assert _live(tmp_path).read_text(encoding="utf-8") == "v7"
    assert len(_backups(tmp_path)) == 3
    assert _strays(tmp_path) == []


def test_startup_fallback_removes_stray_backups(tmp_path):
    _live(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    _live(tmp_path).write_text("v0", encoding="utf-8")
    for tag in ("old-a", "old-b"):
        _live(tmp_path).parent.joinpath(
            f"KnowledgeVault Agent Sync.vbs.pre-startup-20200101-000000-{tag}.bak"
        ).write_text("stale", encoding="utf-8")

    logs: list[str] = []
    assert _install_startup_fallback(tmp_path, "v1", True, logs.append) is True
    assert _live(tmp_path).read_text(encoding="utf-8") == "v1"
    assert _strays(tmp_path) == []
    assert {p.read_text(encoding="utf-8") for p in _backups(tmp_path)} == {"v0"}
