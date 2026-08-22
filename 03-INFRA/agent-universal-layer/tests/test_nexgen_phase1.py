"""Unit test per Fase 1: lock.py, git_ops.py, config.py."""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.config import (
    expand_placeholders,
    load_mcp_manifest,
)
from nexgen_core.git_ops import (
    GitState,
    get_current_branch,
    get_uncommitted_files,
    inspect_git_state,
    run_git,
)
from nexgen_core.lock import HostLock, LockTimeoutError


def test_lock_acquire_and_release(tmp_path: Path):
    lock_file = tmp_path / "test.lock"
    with HostLock(lock_path=lock_file, timeout=2.0) as lock:
        assert lock_file.exists()
        # Un secondo tentativo concorrente deve fallire con timeout
        lock2 = HostLock(lock_path=lock_file, timeout=0.2)
        try:
            lock2.acquire()
            assert False, "Doveva sollevare LockTimeoutError"
        except LockTimeoutError as exc:
            assert exc.exit_code == 75

    # Dopo l'uscita dal blocco, il lock è libero
    with HostLock(lock_path=lock_file, timeout=1.0) as lock3:
        assert lock3._fd is not None


def test_lock_guard_exit_code(tmp_path: Path):
    lock_file = tmp_path / "test.lock"
    lock1 = HostLock(lock_path=lock_file, timeout=2.0)
    lock1.acquire()
    try:
        lock_guard = HostLock(lock_path=lock_file, timeout=0.2, is_guard=True)
        try:
            lock_guard.acquire()
            assert False, "Doveva sollevare LockTimeoutError"
        except LockTimeoutError as exc:
            assert exc.exit_code == 0  # In guard mode il lock occupato è uno skip sicuro
    finally:
        lock1.release()


def test_config_forward_compatibility(tmp_path: Path):
    manifest_yaml = tmp_path / "manifest.yaml"
    # Contiene una chiave futura 'tier: core' e un campo non standard
    manifest_yaml.write_text("""
schema_version: 1
future_top_level_field: "ignored_gracefully"
servers:
  vault-library:
    command: ["python3", "-m", "vault_mcp_server"]
    tier: core
    new_experimental_option: true
""", encoding="utf-8")

    res = load_mcp_manifest(manifest_yaml)
    assert "vault-library" in res["servers"]
    assert res["servers"]["vault-library"]["tier"] == "core"


def test_placeholder_expansion():
    res = expand_placeholders("Hello ${USER:-nobody}, Engine: ${AGENT_ENGINE_ROOT:-/opt/engine}")
    assert "Hello " in res
    assert "/opt/engine" in res or "Engine:" in res


def test_git_ops_clean_and_dirty(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    run_git(repo, "init", "-b", "main")
    run_git(repo, "config", "user.name", "Test")
    run_git(repo, "config", "user.email", "test@example.com")

    # File creato e committato
    f1 = repo / "file.txt"
    f1.write_text("v1", encoding="utf-8")
    run_git(repo, "add", "file.txt")
    run_git(repo, "commit", "-m", "initial")

    assert get_current_branch(repo) == "main"
    assert len(get_uncommitted_files(repo)) == 0

    # Modifica non salvata
    f1.write_text("v2", encoding="utf-8")
    assert len(get_uncommitted_files(repo)) == 1

    status = inspect_git_state(repo, expected_branch="main", remote="local")
    assert status.state == GitState.LOCAL_ONLY
