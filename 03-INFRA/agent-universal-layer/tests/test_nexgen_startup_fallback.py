"""The Startup-folder logon fallback rotates its backups instead of littering."""
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


def _backups(appdata: Path) -> list[Path]:
    return sorted(_live(appdata).parent.glob("KnowledgeVault Agent Sync.vbs.pre-startup-*.bak"))


def test_startup_fallback_writes_once_and_rotates(tmp_path):
    logs: list[str] = []
    assert _install_startup_fallback(tmp_path, "v1", True, logs.append) is True
    assert _live(tmp_path).read_text(encoding="utf-8") == "v1"
    assert _backups(tmp_path) == []

    # Unchanged content: no backup, no rewrite noise.
    assert _install_startup_fallback(tmp_path, "v1", True, logs.append) is True
    assert _backups(tmp_path) == []

    # A version flap mid-update keeps at most 3 previous scripts.
    for i in range(2, 8):
        assert _install_startup_fallback(tmp_path, f"v{i}", True, logs.append) is True
    assert _live(tmp_path).read_text(encoding="utf-8") == "v7"
    assert len(_backups(tmp_path)) == 3
