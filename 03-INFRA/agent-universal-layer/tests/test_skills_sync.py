"""Regression coverage for the non-discovered lazy skill library."""
from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import load_skills_sync_module


def _populate_library(sandbox):
    sandbox.skill_library.mkdir(parents=True, exist_ok=True)
    for name in ("fake-skill-a", "fake-skill-excluded"):
        shutil.copytree(sandbox.skills_dir / name, sandbox.skill_library / name)


def test_index_lists_every_managed_skill_without_exposing_bodies(sandbox):
    sb = sandbox
    _populate_library(sb)

    claude_rt = sb.home / ".claude" / "skills"
    claude_rt.mkdir(parents=True, exist_ok=True)

    mod = load_skills_sync_module(sb)
    mod.write_index(apply=True)

    index_path = sb.active_skills / "INDEX.md"
    assert index_path.is_file(), "--index non ha generato INDEX.md"
    text = index_path.read_text(encoding="utf-8")
    assert "fake-skill-a" in text
    assert "fake-skill-excluded" in text
    assert "Skill sintetica per i test B1" in text  # descrizione dal frontmatter
    assert text.count("- **") == 2, f"attesa una riga per skill, trovato:\n{text}"
    assert not any(sb.active_skills.glob("*/SKILL.md"))
    assert not any(claude_rt.iterdir())


def test_index_is_idempotent_when_content_unchanged(sandbox):
    sb = sandbox
    _populate_library(sb)
    mod = load_skills_sync_module(sb)

    mod.write_index(apply=True)
    first = (sb.active_skills / "INDEX.md").read_bytes()

    mod2 = load_skills_sync_module(sb)
    mod2.write_index(apply=True)
    second = (sb.active_skills / "INDEX.md").read_bytes()

    assert first == second


def test_manual_skill_stays_out_of_eager_views_but_claude_gets_native_lazy_access(sandbox, monkeypatch):
    mod = load_skills_sync_module(sandbox)
    monkeypatch.setattr(mod.sys, "argv", ["skills-sync.py", "--apply"])

    assert mod.main() == 0
    library = sandbox.skill_library / "fake-skill-a"
    assert (library / "SKILL.md").is_file()
    assert not (sandbox.active_skills / "fake-skill-a").exists()
    assert (sandbox.home / ".claude" / "skills" / "fake-skill-a").resolve() == library.resolve()
    assert not (sandbox.home / ".codex" / "skills" / "fake-skill-a").exists()


def test_core_skill_is_the_only_kind_exposed_to_eager_views(sandbox, monkeypatch):
    (sandbox.skills_dir / "skills.manifest.yaml").write_text(
        "skills:\n"
        "  fake-skill-a:\n"
        "    origin: vault\n"
        "    targets: [claude, codex]\n"
        "    exposure: core\n",
        encoding="utf-8",
    )
    mod = load_skills_sync_module(sandbox)
    monkeypatch.setattr(mod.sys, "argv", ["skills-sync.py", "--apply"])

    assert mod.main() == 0
    library = sandbox.skill_library / "fake-skill-a"
    assert (sandbox.active_skills / "fake-skill-a").resolve() == library.resolve()
    assert (sandbox.home / ".codex" / "skills" / "fake-skill-a").resolve() == library.resolve()


def test_legacy_migration_is_explicit_and_preserves_the_old_body(sandbox, monkeypatch):
    old = sandbox.active_skills / "old-local-skill"
    old.mkdir(parents=True)
    (old / "SKILL.md").write_text("legacy body\n", encoding="utf-8")
    mod = load_skills_sync_module(sandbox)

    monkeypatch.setattr(mod.sys, "argv", ["skills-sync.py", "--apply"])
    assert mod.main() == 0
    assert old.exists(), "ordinary guard must not move unknown local skills"

    monkeypatch.setattr(mod.sys, "argv", ["skills-sync.py", "--apply", "--migrate-legacy"])
    assert mod.main() == 0
    archived = sandbox.skill_library / "legacy" / "shared" / "old-local-skill" / "SKILL.md"
    assert archived.read_text(encoding="utf-8") == "legacy body\n"
    assert not old.exists()


def test_legacy_migration_keeps_declared_claude_lazy_view(sandbox, monkeypatch):
    mod = load_skills_sync_module(sandbox)
    monkeypatch.setattr(mod.sys, "argv", ["skills-sync.py", "--apply", "--migrate-legacy"])

    assert mod.main() == 0
    library = sandbox.skill_library / "fake-skill-a"
    claude_view = sandbox.home / ".claude" / "skills" / "fake-skill-a"
    assert claude_view.resolve() == library.resolve()


def test_diff_does_not_create_lazy_views(sandbox, monkeypatch):
    mod = load_skills_sync_module(sandbox)
    monkeypatch.setattr(mod.sys, "argv", ["skills-sync.py"])

    assert mod.main() == 0
    assert not sandbox.skill_library.exists()
    assert not sandbox.active_skills.exists()


def test_vault_copy_fallback_refreshes_stale_body_with_backup(sandbox, monkeypatch):
    mod = load_skills_sync_module(sandbox)
    source = sandbox.skills_dir / "fake-skill-a"
    destination = sandbox.skill_library / "fake-skill-a"

    def fail_symlink(self, target, target_is_directory=False):
        raise OSError("symlink privilege unavailable")

    monkeypatch.setattr(Path, "symlink_to", fail_symlink)
    mod.ensure_link(source, destination, apply=True, label="library/fake-skill-a")
    original = (destination / "SKILL.md").read_text(encoding="utf-8")
    (source / "SKILL.md").write_text("updated canonical body\n", encoding="utf-8")

    mod.ensure_link(source, destination, apply=True, label="library/fake-skill-a")

    assert (destination / "SKILL.md").read_text(encoding="utf-8") == "updated canonical body\n"
    backups = list(
        (sandbox.skill_library / "legacy" / "refreshed-copies" / "library").glob(
            "fake-skill-a.local-edit.bak-*/SKILL.md"
        )
    )
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == original


def test_missing_vault_source_fails_without_creating_a_library_link(sandbox, monkeypatch):
    (sandbox.skills_dir / "skills.manifest.yaml").write_text(
        "skills:\n"
        "  missing-source:\n"
        "    origin: vault\n"
        "    targets: []\n",
        encoding="utf-8",
    )
    mod = load_skills_sync_module(sandbox)
    monkeypatch.setattr(mod.sys, "argv", ["skills-sync.py", "--apply"])

    assert mod.main() == 1
    assert not (sandbox.skill_library / "missing-source").exists()


PINNED_COMMIT = hashlib.sha1(b"skills-sync test revision").hexdigest()


def test_github_fetch_disables_interactive_credentials_and_has_timeout(sandbox, monkeypatch):
    mod = load_skills_sync_module(sandbox)
    monkeypatch.setattr(mod.shutil, "which", lambda command: "/usr/bin/git")
    observed = {}

    def fake_run(command, **kwargs):
        observed.setdefault("commands", []).append(command)
        observed["kwargs"] = kwargs
        if "fetch" in command:
            return SimpleNamespace(returncode=1, stdout="", stderr="authentication required")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)

    assert not mod.install_github(
        "remote-skill", {"repo": "example/remote-skill", "commit": PINNED_COMMIT}, apply=True
    )
    fetch = next(command for command in observed["commands"] if "fetch" in command)
    assert fetch[-2:] == ["origin", PINNED_COMMIT]
    assert "credential.interactive=never" in fetch
    assert observed["kwargs"]["stdin"] is subprocess.DEVNULL
    assert observed["kwargs"]["timeout"] == mod.GIT_CLONE_TIMEOUT_SECONDS
    assert observed["kwargs"]["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert observed["kwargs"]["env"]["GCM_INTERACTIVE"] == "Never"


def test_github_clone_timeout_is_reported_without_crashing(sandbox, monkeypatch, capsys):
    mod = load_skills_sync_module(sandbox)
    monkeypatch.setattr(mod.shutil, "which", lambda command: "/usr/bin/git")

    def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(mod.subprocess, "run", fake_run)

    assert not mod.install_github(
        "remote-skill", {"repo": "example/remote-skill", "commit": PINNED_COMMIT}, apply=True
    )
    assert f"timed out after {mod.GIT_CLONE_TIMEOUT_SECONDS}s" in capsys.readouterr().out


def test_github_skill_fetches_and_records_the_pinned_commit(sandbox, monkeypatch):
    mod = load_skills_sync_module(sandbox)
    monkeypatch.setattr(mod.shutil, "which", lambda command: "/usr/bin/git")
    observed = []

    def fake_run(command, **kwargs):
        observed.append(command)
        if command[1] == "init":
            Path(command[-1]).mkdir(parents=True)
        if "rev-parse" in command:
            return SimpleNamespace(returncode=0, stdout=f"{PINNED_COMMIT}\n", stderr="")
        if "checkout" in command:
            repo_dir = Path(command[command.index("-C") + 1])
            (repo_dir / "SKILL.md").write_text("# pinned skill\n", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    spec = {"repo": "example/remote-skill", "commit": PINNED_COMMIT}

    assert mod.install_github("remote-skill", spec, apply=True)
    installed = sandbox.skill_library / "remote-skill"
    source = (installed / ".source").read_text(encoding="utf-8")
    assert f"commit: {PINNED_COMMIT}" in source
    fetch = next(command for command in observed if "fetch" in command)
    assert fetch[-1] == PINNED_COMMIT

    def fail_if_called(*args, **kwargs):
        raise AssertionError("a verified pinned skill must not be fetched again")

    monkeypatch.setattr(mod.subprocess, "run", fail_if_called)
    assert mod.install_github("remote-skill", spec, apply=True)


def test_github_skill_rejects_a_different_fetched_commit(sandbox, monkeypatch, capsys):
    mod = load_skills_sync_module(sandbox)
    monkeypatch.setattr(mod.shutil, "which", lambda command: "/usr/bin/git")

    def fake_run(command, **kwargs):
        if "rev-parse" in command:
            return SimpleNamespace(returncode=0, stdout="a" * 40 + "\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)

    assert not mod.install_github(
        "remote-skill", {"repo": "example/remote-skill", "commit": PINNED_COMMIT}, apply=True
    )
    assert "does not match" in capsys.readouterr().out
    assert not (sandbox.skill_library / "remote-skill").exists()


@pytest.mark.parametrize(
    ("manifest", "message"),
    [
        ("not-a-mapping\n", "root must be a mapping"),
        ("skills: []\n", "'skills' must be a mapping"),
        ("skills:\n  broken: nope\n", "skill 'broken' must be a mapping"),
        (
            "skills:\n  remote:\n    origin: github\n    repo: example/remote\n    targets: []\n",
            "needs a full 40-character commit SHA",
        ),
        (
            "skills:\n  remote:\n    origin: github\n    repo: example/remote\n    commit: short\n    targets: []\n",
            "needs a full 40-character commit SHA",
        ),
        (
            "skills:\n  bad:\n    origin: vault\n    targets: []\n    exposure: eager\n",
            "exposure must be 'manual' or 'core'",
        ),
    ],
)
def test_invalid_manifest_returns_a_readable_error_without_traceback(sandbox, monkeypatch, capsys, manifest, message):
    (sandbox.skills_dir / "skills.manifest.yaml").write_text(manifest, encoding="utf-8")
    mod = load_skills_sync_module(sandbox)
    monkeypatch.setattr(mod.sys, "argv", ["skills-sync.py"])

    assert mod.main() == 1
    output = capsys.readouterr().out
    assert message in output
    assert "AttributeError" not in output
