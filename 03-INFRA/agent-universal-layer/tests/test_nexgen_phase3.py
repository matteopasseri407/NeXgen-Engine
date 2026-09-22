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


def test_opencode_scope_file_symlinked_to_canonical_when_missing(tmp_path: Path, monkeypatch):
    """V2 loads the global scope file, not the `instructions` array: on a
    machine without one the guard symlinks it at the canonical bootstrap
    (copy fallback where symlinks need privileges), the same way Codex and
    Antigravity already work. No content is ever copied."""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    vault = home / "KnowledgeVault"
    canon = vault / "03-INFRA" / "agent-universal-layer" / "instructions" / "AGENTS.md"
    canon.parent.mkdir(parents=True)
    canon.write_text("# rules\n", encoding="utf-8")

    runner = GuardRunner(vault_data=vault, home=home)
    assert runner._align_opencode_instructions(canon) is not None
    scope = home / ".config" / "opencode" / "AGENTS.md"
    assert scope.resolve() == canon.resolve()

    # Idempotenza: il link giusto non riscrive niente.
    assert runner._align_opencode_instructions(canon) is None


def test_opencode_scope_file_real_file_is_never_clobbered(tmp_path: Path, monkeypatch):
    """A real file at the scope path is the private identity layer's
    derivative: replacing it with a pointer would destroy the persona, so
    the guard leaves it exactly as it is. The doctor reports it, it never
    "fixes" it here."""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    vault = home / "KnowledgeVault"
    canon = vault / "03-INFRA" / "agent-universal-layer" / "instructions" / "AGENTS.md"
    canon.parent.mkdir(parents=True)
    canon.write_text("# rules\n", encoding="utf-8")

    scope = home / ".config" / "opencode" / "AGENTS.md"
    scope.parent.mkdir(parents=True)
    scope.write_text("# private derivative\n", encoding="utf-8")

    runner = GuardRunner(vault_data=vault, home=home)
    assert runner._align_opencode_instructions(canon) is None
    assert scope.read_text(encoding="utf-8") == "# private derivative\n"
    assert not scope.is_symlink()


def test_opencode_dead_instructions_array_migrated_once(tmp_path: Path, monkeypatch):
    """The one V1->V2 migration test that stays: V2 accepts the
    `instructions` array but never resolves it, so canonical entries the
    old guard added are dead weight. They go away (backup first); a human
    choice stays."""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    vault = home / "KnowledgeVault"
    canon = vault / "03-INFRA" / "agent-universal-layer" / "instructions" / "AGENTS.md"
    canon.parent.mkdir(parents=True)
    canon.write_text("# rules\n", encoding="utf-8")

    cfg_dir = home / ".config" / "opencode"
    cfg_dir.mkdir(parents=True)
    cfg = cfg_dir / "opencode.json"
    cfg.write_text(json.dumps({"instructions": [str(canon), "~/somewhere/else.md"]}), encoding="utf-8")

    runner = GuardRunner(vault_data=vault, home=home)
    assert runner._drop_dead_opencode_instructions_array() is not None
    entries = json.loads(cfg.read_text(encoding="utf-8"))["instructions"]
    assert entries == ["~/somewhere/else.md"]

    # Idempotenza: niente da migrare quando l'array non nomina il canonico.
    assert runner._drop_dead_opencode_instructions_array() is None


def test_guard_phases_run_in_order_without_writes_in_preflight(tmp_path: Path, monkeypatch):
    """The phase split is structural, not cosmetic: PREFLIGHT must answer
    without creating anything, and each write phase must be callable alone."""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    vault = home / "KnowledgeVault"
    canon = vault / "03-INFRA" / "agent-universal-layer" / "instructions" / "AGENTS.md"
    canon.parent.mkdir(parents=True)
    canon.write_text("# rules\n", encoding="utf-8")

    runner = GuardRunner(vault_data=vault, home=home)
    actions: list[str] = []
    assert runner._phase_git(GuardMode.PREFLIGHT, True, "origin", "main", actions) is None
    assert runner._phase_preflight(GuardMode.PREFLIGHT) is not None
    runner._phase_mcp(actions, skip_mcp=True)
    assert any("explicitly requested" in a or "esplicita" in a for a in actions)
    # Nothing materialized: no scope file, no MCP config.
    assert not (home / ".config" / "opencode" / "AGENTS.md").exists()
    assert not (home / ".config" / "opencode" / "opencode.jsonc").exists()
    assert not (home / ".config" / "opencode" / "opencode.json").exists()
