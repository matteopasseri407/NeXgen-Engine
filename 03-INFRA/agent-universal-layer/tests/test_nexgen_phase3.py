"""Unit test per Fase 3: guard.py, publisher.py, beat.py, megaphone.py."""
from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.beat import Heartbeat
from nexgen_core.git_ops import publish_changes, run_git
from nexgen_core.guard import GuardMode, GuardRunner
from nexgen_core.i18n import set_language
from nexgen_core.megaphone import Megaphone


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


def test_publish_says_nothing_to_publish_when_there_is_nothing(tmp_path: Path):
    """A clean, aligned tree must not be reported as 'Published successfully':
    the doctor tells a user with unsaved changes to run vault-push, and a
    push that does nothing while claiming success is how the loop never ends."""
    set_language("en")
    try:
        work = tmp_path / "work"
        work.mkdir()
        run_git(work, "init", "-b", "main")
        run_git(work, "config", "user.name", "Test")
        run_git(work, "config", "user.email", "test@example.com")
        (work / "note.md").write_text("content\n", encoding="utf-8")
        run_git(work, "add", "note.md")
        run_git(work, "commit", "-m", "init")

        remote = tmp_path / "remote.git"
        run_git(work, "clone", "--bare", str(work), str(remote))
        run_git(work, "remote", "add", "origin", str(remote))
        run_git(work, "push", "-u", "origin", "main")

        ok, msg = publish_changes(
            repo_dir=work, branch="main", remote="origin", commit_msg="update: vault sync"
        )
        assert ok is True
        assert msg == "Nothing to publish"
    finally:
        set_language(None)


def test_opencode_instructions_deduped_by_resolved_path(tmp_path: Path, monkeypatch):
    """The same canonical file declared as '~/…' must not be appended again
    as its absolute path: the guard used to compare strings, and a machine
    whose config spelled it with a tilde ended up with it twice."""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    vault = home / "KnowledgeVault"
    canon = vault / "03-INFRA" / "agent-universal-layer" / "instructions" / "AGENTS.md"
    canon.parent.mkdir(parents=True)
    canon.write_text("# rules\n", encoding="utf-8")

    cfg_dir = home / ".config" / "opencode"
    cfg_dir.mkdir(parents=True)
    cfg = cfg_dir / "opencode.json"
    tilde_entry = f"~/{vault.relative_to(home)}/03-INFRA/agent-universal-layer/instructions/AGENTS.md"
    cfg.write_text(json.dumps({"instructions": [tilde_entry]}), encoding="utf-8")

    runner = GuardRunner(vault_data=vault, home=home)
    assert runner._align_opencode_instructions(canon) is None
    assert len(json.loads(cfg.read_text(encoding="utf-8"))["instructions"]) == 1


def test_opencode_instructions_append_when_the_file_is_truly_missing(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    vault = home / "KnowledgeVault"
    canon = vault / "03-INFRA" / "agent-universal-layer" / "instructions" / "AGENTS.md"
    canon.parent.mkdir(parents=True)
    canon.write_text("# rules\n", encoding="utf-8")

    cfg_dir = home / ".config" / "opencode"
    cfg_dir.mkdir(parents=True)
    cfg = cfg_dir / "opencode.json"
    cfg.write_text(json.dumps({"instructions": ["~/somewhere/else.md"]}), encoding="utf-8")

    runner = GuardRunner(vault_data=vault, home=home)
    assert runner._align_opencode_instructions(canon) is not None
    entries = json.loads(cfg.read_text(encoding="utf-8"))["instructions"]
    assert str(canon) in entries
    assert len(entries) == 2
