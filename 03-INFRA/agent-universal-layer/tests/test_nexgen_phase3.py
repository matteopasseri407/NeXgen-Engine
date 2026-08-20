"""Unit test per Fase 3: guard.py, publisher.py, beat.py, megaphone.py."""
from __future__ import annotations

import sys
import time
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.beat import Heartbeat
from nexgen_core.git_ops import run_git
from nexgen_core.guard import GuardMode, GuardRunner
from nexgen_core.megaphone import Megaphone
from nexgen_core.publisher import Publisher


def test_megaphone_debounce(tmp_path: Path):
    state_dir = tmp_path / "state"
    mega = Megaphone(state_dir=state_dir)

    assert mega.should_notify("alert-1", debounce_hours=1.0) is True
    mega.mark_notified("alert-1")
    assert mega.should_notify("alert-1", debounce_hours=1.0) is False
    assert mega.should_notify("alert-2", debounce_hours=1.0) is True


def test_heartbeat_liveness(tmp_path: Path):
    state_dir = tmp_path / "state"
    beat = Heartbeat(state_dir=state_dir)

    # Prima della registrazione
    ok, msg = beat.check_liveness()
    assert ok is False

    # Dopo la registrazione
    beat.record_liveness()
    ok, msg = beat.check_liveness()
    assert ok is True


def test_guard_runner_cycle(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    run_git(vault, "init", "-b", "main")
    run_git(vault, "config", "user.name", "Test")
    run_git(vault, "config", "user.email", "test@example.com")

    # Struttura cartelle minima
    (vault / "03-INFRA" / "agent-universal-layer" / "mcp").mkdir(parents=True)
    (vault / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml").write_text("schema_version: 1\nservers: {}\n", encoding="utf-8")
    (vault / "03-INFRA" / "agent-universal-layer" / "skills").mkdir(parents=True)
    (vault / "03-INFRA" / "agent-universal-layer" / "skills" / "skills.manifest.yaml").write_text("schema_version: 1\nskills: {}\n", encoding="utf-8")
    (vault / "03-INFRA" / "agent-universal-layer" / "instructions").mkdir(parents=True)
    (vault / "03-INFRA" / "agent-universal-layer" / "instructions" / "AGENTS.md").write_text("# Agents Rules\n", encoding="utf-8")

    run_git(vault, "add", "-A")
    run_git(vault, "commit", "-m", "init")

    home = tmp_path / "home"
    runner = GuardRunner(vault_data=vault, home=home)

    # Test preflight
    res_pf = runner.run(mode=GuardMode.PREFLIGHT)
    assert res_pf.success is True
    assert res_pf.exit_code == 0

    # Test apply (modalità local/offline)
    res_apply = runner.run(mode=GuardMode.APPLY, allow_offline=True)
    assert res_apply.success is True
    assert res_apply.exit_code == 0
    assert (home / "CLAUDE.md").exists()
