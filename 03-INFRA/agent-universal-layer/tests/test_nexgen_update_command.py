"""Regression tests for the real cross-platform ``nexgen-update`` command."""
from __future__ import annotations

import importlib.util
import os
import subprocess
from pathlib import Path

import pytest
from conftest import REAL_VAULT, load_agent_sync_module

SCRIPT = REAL_VAULT / "03-INFRA" / "scripts" / "nexgen_core" / "updater.py"
POWERSHELL_LAUNCHER = REAL_VAULT / "03-INFRA" / "scripts" / "nexgen-update.ps1"


def _load_updater():
    spec = importlib.util.spec_from_file_location("nexgen_update_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "nexgen update test",
        "GIT_AUTHOR_EMAIL": "nexgen-update-test-identity",
        "GIT_COMMITTER_NAME": "nexgen update test",
        "GIT_COMMITTER_EMAIL": "nexgen-update-test-identity",
    }
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True, env=env
    )


def _write_release(repo: Path, version: str, previous: str | None = None) -> None:
    (repo / "03-INFRA").mkdir(exist_ok=True)
    (repo / "03-INFRA" / ".gitkeep").touch()
    (repo / "VERSION").write_text(f"{version}\n", encoding="utf-8")
    changelog = f"## [{version}] - 2026-08-01\n\n### Changed\n\n- Release {version}.\n"
    if previous:
        changelog += f"\n## [{previous}] - 2026-07-31\n\n### Added\n\n- Release {previous}.\n"
    (repo / "CHANGELOG.md").write_text(changelog, encoding="utf-8")
    _git(repo, "add", "VERSION", "CHANGELOG.md", "03-INFRA/.gitkeep")
    _git(repo, "commit", "-m", f"release v{version}")
    _git(repo, "tag", f"v{version}")


def _upgrade_fixture(tmp_path: Path) -> tuple[Path, Path]:
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-b", "main")
    _write_release(origin, "0.1.0")

    engine = tmp_path / "engine"
    _git(tmp_path, "clone", str(origin), str(engine))

    _write_release(origin, "0.2.0", previous="0.1.0")
    return origin, engine


def _env(engine: Path, data: Path | None = None) -> dict[str, str]:
    return {
        **os.environ,
        "AGENT_ENGINE_ROOT": str(engine / "03-INFRA"),
        "AGENT_VAULT_DATA": str(data or engine),
    }


def _bare_env(engine: Path, home: Path) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"AGENT_VAULT_DATA", "KNOWLEDGE_VAULT_PATH", "USERPROFILE"}
    }
    env.update({"AGENT_ENGINE_ROOT": str(engine / "03-INFRA"), "HOME": str(home)})
    return env


def test_bare_command_discovers_default_split_vault(tmp_path, capsys):
    updater = _load_updater()
    _origin, engine = _upgrade_fixture(tmp_path)
    home = tmp_path / "home"
    data = home / "KnowledgeVault"
    data.mkdir(parents=True)
    _git(data, "init", "-b", "main")
    (data / "note.md").write_text("private data\n", encoding="utf-8")
    _git(data, "add", "note.md")
    _git(data, "commit", "-m", "seed data")

    result = updater.main(
        ["--check"], environ=_bare_env(engine, home), which=lambda _name: None
    )

    assert result == 0
    assert f"Data:   {data.resolve()}" in capsys.readouterr().out


def test_bare_command_without_default_vault_keeps_single_clone(tmp_path):
    updater = _load_updater()
    _origin, engine = _upgrade_fixture(tmp_path)
    home = tmp_path / "empty-home"

    engine_repo, data_repo = updater.resolve_repositories(_bare_env(engine, home))

    assert engine_repo == engine.resolve()
    assert data_repo == engine.resolve()


def test_check_reports_release_without_moving_head(tmp_path, capsys):
    updater = _load_updater()
    _origin, engine = _upgrade_fixture(tmp_path)
    before = _git(engine, "rev-parse", "HEAD").stdout.strip()

    result = updater.main(["--check"], environ=_env(engine), which=lambda _name: None)

    assert result == 0
    assert _git(engine, "rev-parse", "HEAD").stdout.strip() == before
    assert (engine / "VERSION").read_text(encoding="utf-8").strip() == "0.1.0"
    output = capsys.readouterr()
    assert "Current: v0.1.0" in output.out
    assert "Latest released target: v0.2.0" in output.out
    assert "No installed files or branch were changed" in output.out


def test_yes_merges_release_without_detaching_head(tmp_path, capsys):
    updater = _load_updater()
    _origin, engine = _upgrade_fixture(tmp_path)

    result = updater.main(["--yes"], environ=_env(engine), which=lambda _name: None)

    assert result == 0
    assert (engine / "VERSION").read_text(encoding="utf-8").strip() == "0.2.0"
    assert _git(engine, "symbolic-ref", "--short", "HEAD").stdout.strip() == "main"
    output = capsys.readouterr().out
    assert "v0.2.0 installed in MINIMAL mode" in output
    assert "Automatic doctor verification was unavailable" in output


def test_declined_confirmation_moves_nothing(tmp_path, capsys):
    updater = _load_updater()
    _origin, engine = _upgrade_fixture(tmp_path)
    before = _git(engine, "rev-parse", "HEAD").stdout.strip()

    result = updater.main(
        [], environ=_env(engine), input_fn=lambda _prompt: "no", which=lambda _name: None
    )

    assert result == 0
    assert _git(engine, "rev-parse", "HEAD").stdout.strip() == before
    assert "Update cancelled" in capsys.readouterr().out


def test_dirty_data_repo_blocks_before_engine_ref_moves(tmp_path, capsys):
    updater = _load_updater()
    _origin, engine = _upgrade_fixture(tmp_path)
    before = _git(engine, "rev-parse", "HEAD").stdout.strip()

    data = tmp_path / "data"
    data.mkdir()
    _git(data, "init", "-b", "main")
    note = data / "note.md"
    note.write_text("committed\n", encoding="utf-8")
    _git(data, "add", "note.md")
    _git(data, "commit", "-m", "seed data")
    note.write_text("work in progress\n", encoding="utf-8")

    result = updater.main(["--yes"], environ=_env(engine, data), which=lambda _name: None)

    assert result == 1
    assert _git(engine, "rev-parse", "HEAD").stdout.strip() == before
    assert "data repository is dirty" in capsys.readouterr().err


def test_dirty_engine_repo_blocks_before_merge(tmp_path, capsys):
    updater = _load_updater()
    _origin, engine = _upgrade_fixture(tmp_path)
    before = _git(engine, "rev-parse", "HEAD").stdout.strip()
    (engine / "local-work.txt").write_text("do not overwrite\n", encoding="utf-8")

    result = updater.main(["--yes"], environ=_env(engine), which=lambda _name: None)

    assert result == 1
    assert _git(engine, "rev-parse", "HEAD").stdout.strip() == before
    assert "engine repository is dirty" in capsys.readouterr().err


def test_detached_engine_checkout_is_refused(tmp_path, capsys):
    updater = _load_updater()
    _origin, engine = _upgrade_fixture(tmp_path)
    before = _git(engine, "rev-parse", "HEAD").stdout.strip()
    _git(engine, "checkout", "--detach", before)

    result = updater.main(["--yes"], environ=_env(engine), which=lambda _name: None)

    assert result == 1
    assert _git(engine, "rev-parse", "HEAD").stdout.strip() == before
    assert "engine checkout is detached" in capsys.readouterr().err


def test_missing_noninteractive_confirmation_fails_without_moving(tmp_path, capsys):
    updater = _load_updater()
    _origin, engine = _upgrade_fixture(tmp_path)
    before = _git(engine, "rev-parse", "HEAD").stdout.strip()

    def eof(_prompt):
        raise EOFError

    result = updater.main([], environ=_env(engine), input_fn=eof, which=lambda _name: None)

    assert result == 1
    assert _git(engine, "rev-parse", "HEAD").stdout.strip() == before
    assert "confirmation required" in capsys.readouterr().err


def test_unknown_target_fails_closed(tmp_path, capsys):
    updater = _load_updater()
    _origin, engine = _upgrade_fixture(tmp_path)

    result = updater.main(
        ["--check", "--target", "v9.9.9"], environ=_env(engine), which=lambda _name: None
    )

    assert result == 1
    assert "not a released tag merged into origin/main" in capsys.readouterr().err


def test_cryptographically_bad_release_signature_is_rejected(tmp_path, capsys, monkeypatch):
    updater = _load_updater()
    _origin, engine = _upgrade_fixture(tmp_path)
    before = _git(engine, "rev-parse", "HEAD").stdout.strip()
    real_git = updater._git

    def bad_signature(repo, *args, **kwargs):
        if args == ("log", "-1", "--format=%G?", "v0.2.0"):
            return subprocess.CompletedProcess(args, 0, "B\n", "")
        return real_git(repo, *args, **kwargs)

    monkeypatch.setattr(updater, "_git", bad_signature)

    result = updater.main(["--yes"], environ=_env(engine), which=lambda _name: None)

    assert result == 1
    assert _git(engine, "rev-parse", "HEAD").stdout.strip() == before
    assert "invalid or expired signature" in capsys.readouterr().err


def test_missing_local_public_key_warns_without_mislabeling_release(tmp_path, capsys, monkeypatch):
    updater = _load_updater()
    _origin, engine = _upgrade_fixture(tmp_path)
    real_git = updater._git

    def unverifiable_signature(repo, *args, **kwargs):
        if args == ("log", "-1", "--format=%G?", "v0.2.0"):
            return subprocess.CompletedProcess(args, 0, "E\n", "")
        return real_git(repo, *args, **kwargs)

    monkeypatch.setattr(updater, "_git", unverifiable_signature)

    result = updater.main(["--check"], environ=_env(engine), which=lambda _name: None)

    assert result == 0
    error = capsys.readouterr().err
    assert "could not verify the release commit signature" in error
    assert "unsigned commit" not in error


def test_local_only_semver_tag_is_not_treated_as_a_release(tmp_path, capsys):
    updater = _load_updater()
    _origin, engine = _upgrade_fixture(tmp_path)
    _git(engine, "tag", "v9.9.9")

    result = updater.main(["--check"], environ=_env(engine), which=lambda _name: None)

    assert result == 0
    output = capsys.readouterr().out
    assert "Latest released target: v0.2.0" in output
    assert "v9.9.9" not in output


def test_release_tag_with_mismatched_version_is_rejected_before_merge(tmp_path, capsys):
    updater = _load_updater()
    origin, engine = _upgrade_fixture(tmp_path)
    before = _git(engine, "rev-parse", "HEAD").stdout.strip()
    _write_release(origin, "0.3.1", previous="0.2.0")
    _git(origin, "tag", "-d", "v0.3.1")
    _git(origin, "tag", "v0.3.0")

    result = updater.main(["--yes"], environ=_env(engine), which=lambda _name: None)

    assert result == 1
    assert _git(engine, "rev-parse", "HEAD").stdout.strip() == before
    assert "contains VERSION='0.3.1'; expected '0.3.0'" in capsys.readouterr().err


def test_single_clone_preserves_local_data_commit_with_a_merge(tmp_path, capsys):
    updater = _load_updater()
    _origin, engine = _upgrade_fixture(tmp_path)
    local = engine / "local-commit.txt"
    local.write_text("keep this history\n", encoding="utf-8")
    _git(engine, "add", "local-commit.txt")
    _git(engine, "commit", "-m", "local engine history")
    _git(engine, "config", "user.name", "nexgen merge test")
    _git(engine, "config", "user.email", "nexgen-merge-test@localhost")
    local_commit = _git(engine, "rev-parse", "HEAD").stdout.strip()

    result = updater.main(["--yes"], environ=_env(engine), which=lambda _name: None)

    assert result == 0
    assert local.read_text(encoding="utf-8") == "keep this history\n"
    assert (engine / "VERSION").read_text(encoding="utf-8").strip() == "0.2.0"
    parents = _git(engine, "rev-list", "--parents", "-n", "1", "HEAD").stdout.split()
    assert len(parents) == 3
    assert local_commit in parents[1:]
    assert "git merge --no-edit v0.2.0" in capsys.readouterr().out


def test_split_engine_history_must_fast_forward(tmp_path, capsys):
    updater = _load_updater()
    _origin, engine = _upgrade_fixture(tmp_path)
    local = engine / "local-engine-change.txt"
    local.write_text("keep this engine history\n", encoding="utf-8")
    _git(engine, "add", "local-engine-change.txt")
    _git(engine, "commit", "-m", "local engine history")
    before = _git(engine, "rev-parse", "HEAD").stdout.strip()
    data = tmp_path / "data"
    data.mkdir()
    _git(data, "init", "-b", "main")
    (data / "note.md").write_text("clean\n", encoding="utf-8")
    _git(data, "add", "note.md")
    _git(data, "commit", "-m", "seed data")

    result = updater.main(
        ["--yes"],
        environ=_env(engine, data),
        which=lambda name: "fake-sync" if name == "agent-sync" else None,
    )

    assert result == 1
    assert _git(engine, "rev-parse", "HEAD").stdout.strip() == before
    assert "is not a fast-forward" in capsys.readouterr().err


def test_missing_single_clone_merge_identity_has_actionable_error(tmp_path, monkeypatch):
    updater = _load_updater()

    def missing_identity(_repo, *args, **_kwargs):
        assert args == ("var", "GIT_COMMITTER_IDENT")
        return subprocess.CompletedProcess(args, 128, "", "identity missing")

    monkeypatch.setattr(updater, "_git", missing_identity)
    with pytest.raises(updater.UpdateError, match="Git user.name and user.email"):
        updater._assert_merge_identity(tmp_path)


def test_explicit_downgrade_is_refused(tmp_path, capsys):
    updater = _load_updater()
    _origin, engine = _upgrade_fixture(tmp_path)
    assert updater.main(["--yes"], environ=_env(engine), which=lambda _name: None) == 0
    capsys.readouterr()
    before = _git(engine, "rev-parse", "HEAD").stdout.strip()

    result = updater.main(
        ["--check", "--target", "v0.1.0"],
        environ=_env(engine),
        which=lambda _name: None,
    )

    assert result == 1
    assert _git(engine, "rev-parse", "HEAD").stdout.strip() == before
    assert "refusing to downgrade" in capsys.readouterr().err


def test_split_topology_without_provisioner_is_refused_before_merge(tmp_path, capsys):
    updater = _load_updater()
    _origin, engine = _upgrade_fixture(tmp_path)
    before = _git(engine, "rev-parse", "HEAD").stdout.strip()
    data = tmp_path / "data"
    data.mkdir()
    _git(data, "init", "-b", "main")
    (data / "note.md").write_text("clean\n", encoding="utf-8")
    _git(data, "add", "note.md")
    _git(data, "commit", "-m", "seed data")

    result = updater.main(["--yes"], environ=_env(engine, data), which=lambda _name: None)

    assert result == 1
    assert _git(engine, "rev-parse", "HEAD").stdout.strip() == before
    assert "split engine/data topology requires agent-sync" in capsys.readouterr().err


def test_existing_split_pin_requires_vault_push_before_merge(tmp_path, capsys):
    updater = _load_updater()
    _origin, engine = _upgrade_fixture(tmp_path)
    before = _git(engine, "rev-parse", "HEAD").stdout.strip()
    data = tmp_path / "data"
    pin = data / "99-INDEX" / "ENGINE-PIN.txt"
    pin.parent.mkdir(parents=True)
    _git(data, "init", "-b", "main")
    pin.write_text(f"{before}\n", encoding="utf-8")
    _git(data, "add", "99-INDEX/ENGINE-PIN.txt")
    _git(data, "commit", "-m", "seed engine pin")

    result = updater.main(
        ["--yes"],
        environ=_env(engine, data),
        which=lambda name: "fake-sync" if name == "agent-sync" else None,
    )

    assert result == 1
    assert _git(engine, "rev-parse", "HEAD").stdout.strip() == before
    assert "split engine pin requires vault-push" in capsys.readouterr().err


def test_split_pin_is_committed_before_provisioning(tmp_path, capsys, monkeypatch):
    updater = _load_updater()
    origin, engine = _upgrade_fixture(tmp_path)
    current_head = _git(engine, "rev-parse", "HEAD").stdout.strip()
    target_head = _git(origin, "rev-parse", "v0.2.0^{commit}").stdout.strip()
    data = tmp_path / "data"
    pin = data / "99-INDEX" / "ENGINE-PIN.txt"
    pin.parent.mkdir(parents=True)
    _git(data, "init", "-b", "main")
    pin.write_text(f"{current_head}\n", encoding="utf-8")
    _git(data, "add", "99-INDEX/ENGINE-PIN.txt")
    _git(data, "commit", "-m", "seed engine pin")
    events = []
    real_run = updater._run

    def fake_commands(args, **kwargs):
        if args[0] == "fake-vault-push":
            assert pin.read_text(encoding="utf-8").strip() == target_head
            assert args[-2:] == ["--", "99-INDEX/ENGINE-PIN.txt"]
            _git(data, "add", "--", args[-1])
            _git(data, "commit", "-m", "publish engine pin")
            events.append("pin")
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[0] == "fake-sync":
            assert pin.read_text(encoding="utf-8").strip() == target_head
            events.append("sync")
            return subprocess.CompletedProcess(args, 0, "", "")
        return real_run(args, **kwargs)

    monkeypatch.setattr(updater, "_run", fake_commands)
    monkeypatch.setattr(updater, "_doctor", lambda *_args, **_kwargs: (0, 0))
    commands = {
        "agent-sync": "fake-sync",
        "agent-doctor": "fake-doctor",
        "vault-push": "fake-vault-push",
    }

    result = updater.main(
        ["--yes"],
        environ=_env(engine, data),
        which=commands.get,
    )

    assert result == 0
    assert events == ["pin", "sync"]
    assert pin.read_text(encoding="utf-8").strip() == target_head
    assert _git(data, "status", "--porcelain").stdout == ""
    output = capsys.readouterr().out
    assert "update 99-INDEX/ENGINE-PIN.txt through vault-push" in output
    assert "installed and verified" in output


def test_failed_split_pin_publish_stops_before_provisioning(tmp_path, capsys, monkeypatch):
    updater = _load_updater()
    _origin, engine = _upgrade_fixture(tmp_path)
    current_head = _git(engine, "rev-parse", "HEAD").stdout.strip()
    data = tmp_path / "data"
    pin = data / "99-INDEX" / "ENGINE-PIN.txt"
    pin.parent.mkdir(parents=True)
    _git(data, "init", "-b", "main")
    pin.write_text(f"{current_head}\n", encoding="utf-8")
    _git(data, "add", "99-INDEX/ENGINE-PIN.txt")
    _git(data, "commit", "-m", "seed engine pin")
    sync_called = False
    real_run = updater._run

    def fail_pin_publish(args, **kwargs):
        nonlocal sync_called
        if args[0] == "fake-vault-push":
            return subprocess.CompletedProcess(args, 1, "", "simulated push failure")
        if args[0] == "fake-sync":
            sync_called = True
            return subprocess.CompletedProcess(args, 0, "", "")
        return real_run(args, **kwargs)

    monkeypatch.setattr(updater, "_run", fail_pin_publish)
    result = updater.main(
        ["--yes"],
        environ=_env(engine, data),
        which=lambda name: {
            "agent-sync": "fake-sync",
            "vault-push": "fake-vault-push",
        }.get(name),
    )

    assert result == 1
    assert sync_called is False
    assert (engine / "VERSION").read_text(encoding="utf-8").strip() == "0.2.0"
    assert _git(data, "status", "--porcelain").stdout.strip()
    error = capsys.readouterr().err
    assert "private engine pin was not published" in error
    assert "If 99-INDEX/ENGINE-PIN.txt moved" in error


def test_unreadable_pre_upgrade_doctor_blocks_before_merge(tmp_path, capsys, monkeypatch):
    updater = _load_updater()
    _origin, engine = _upgrade_fixture(tmp_path)
    before = _git(engine, "rev-parse", "HEAD").stdout.strip()
    monkeypatch.setattr(updater, "_doctor", lambda *_args, **_kwargs: (None, 1))

    result = updater.main(
        ["--yes"],
        environ=_env(engine),
        which=lambda name: "fake-doctor" if name == "agent-doctor" else None,
    )

    assert result == 1
    assert _git(engine, "rev-parse", "HEAD").stdout.strip() == before
    assert "pre-upgrade doctor did not return a readable" in capsys.readouterr().err


def test_failed_provisioning_does_not_auto_rollback(tmp_path, capsys, monkeypatch):
    updater = _load_updater()
    _origin, engine = _upgrade_fixture(tmp_path)
    previous = _git(engine, "rev-parse", "HEAD").stdout.strip()
    real_run = updater._run

    def fake_sync(args, **kwargs):
        if args[0] == "fake-sync":
            return subprocess.CompletedProcess(args, 1, "", "simulated sync failure")
        return real_run(args, **kwargs)

    monkeypatch.setattr(updater, "_run", fake_sync)
    result = updater.main(
        ["--yes"],
        environ=_env(engine),
        which=lambda name: "fake-sync" if name == "agent-sync" else None,
    )

    assert result == 1
    assert (engine / "VERSION").read_text(encoding="utf-8").strip() == "0.2.0"
    error = capsys.readouterr().err
    assert "agent-sync apply failed" in error
    assert f"git -C {engine} reset --hard {previous}" in error


def test_new_doctor_failure_is_reported_without_auto_rollback(tmp_path, capsys, monkeypatch):
    updater = _load_updater()
    _origin, engine = _upgrade_fixture(tmp_path)
    previous = _git(engine, "rev-parse", "HEAD").stdout.strip()
    doctor_results = iter(((0, 0), (1, 1)))
    monkeypatch.setattr(updater, "_doctor", lambda *_args, **_kwargs: next(doctor_results))
    real_run = updater._run

    def fake_sync(args, **kwargs):
        if args[0] == "fake-sync":
            return subprocess.CompletedProcess(args, 0, "", "")
        return real_run(args, **kwargs)

    monkeypatch.setattr(updater, "_run", fake_sync)
    result = updater.main(
        ["--yes"],
        environ=_env(engine),
        which=lambda name: "fake-sync" if name == "agent-sync" else "fake-doctor",
    )

    assert result == 1
    assert (engine / "VERSION").read_text(encoding="utf-8").strip() == "0.2.0"
    error = capsys.readouterr().err
    assert "introduced new failures: before=0, after=1" in error
    assert f"git -C {engine} reset --hard {previous}" in error


def test_provisioner_installs_real_command_on_both_platforms(sandbox):
    module = load_agent_sync_module(sandbox)
    command = module.LINKED_COMMANDS["nexgen-update"]
    assert command == {"source": "engine", "posix": True, "windows": True}
    scripts = REAL_VAULT / "03-INFRA" / "scripts"
    assert (scripts / "nexgen-update.sh").is_file()
    assert (scripts / "nexgen-update.ps1").is_file()
    assert (scripts / "nexgen_core" / "updater.py").is_file()


def test_powershell_launcher_has_one_python_backend_and_forwards_arguments():
    """Il launcher trova un Python, uno solo, e passa tutto quello che riceve.

    Prima questo test fissava tre righe esatte del file. Il file ora è
    generato da una tabella sola insieme a tutti gli altri launcher, e ogni
    riorganizzazione legittima lo rompeva: si asserisce la regola.
    """
    source = POWERSHELL_LAUNCHER.read_text(encoding="utf-8")

    # Un solo backend: si prova una catena di candidati e alla prima
    # occorrenza buona si esce. Due invocazioni indipendenti significherebbe
    # due comportamenti diversi a seconda di cosa è installato.
    assert source.count("& $exe") <= 1, "il launcher invoca Python in più punti"
    assert "foreach" in source, "il launcher deve provare più candidati in ordine"
    assert "$args" in source, "il launcher deve inoltrare gli argomenti ricevuti"
    assert "exit $LASTEXITCODE" in source, "il codice di uscita del comando deve arrivare fino a chi lo ha lanciato"

    # E deve arrivare al motore, non a un file qualsiasi.
    assert "nexgen_core" in source


@pytest.mark.skipif(os.name != "nt", reason="PowerShell execution requires Windows.")
def test_powershell_launcher_executes_the_real_updater_help():
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(POWERSHELL_LAUNCHER),
            "--help",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert ("usage: nexgen update" in result.stdout) or ("usage: nexgen-update" in result.stdout)


def test_conflicted_merge_is_rolled_back_instead_of_leaving_markers(tmp_path, capsys):
    """The merge ran with _git's default check=True, so a conflict raised
    straight out of the call and no cleanup ever executed. The updater printed
    an error and returned 1 while leaving the repository mid-merge, with
    <<<<<<< markers inside AGENTS.md and the generated CLI configs -- the files
    the CLIs read as instructions, and in the single-clone topology that tree
    IS the user's live install. A failed update has to leave nothing behind.
    """
    updater = _load_updater()
    _origin, engine = _upgrade_fixture(tmp_path)
    _git(engine, "config", "user.name", "nexgen merge test")
    _git(engine, "config", "user.email", "nexgen-merge-test@localhost")
    changelog = engine / "CHANGELOG.md"
    changelog.write_text(
        "## [0.1.0] - 2026-07-30\n\n### Changed\n\n- Local divergent edit.\n",
        encoding="utf-8",
    )
    _git(engine, "add", "CHANGELOG.md")
    _git(engine, "commit", "-m", "local changelog edit")
    head_before = _git(engine, "rev-parse", "HEAD").stdout.strip()

    result = updater.main(["--yes"], environ=_env(engine), which=lambda _name: None)

    assert result == 1
    assert _git(engine, "rev-parse", "HEAD").stdout.strip() == head_before
    assert "<<<<<<<" not in changelog.read_text(encoding="utf-8"), "conflict markers left in the live tree"
    assert not (engine / ".git" / "MERGE_HEAD").exists(), "repository left mid-merge"
    status = _git(engine, "status", "--porcelain").stdout.strip()
    assert status == "", f"working tree not clean after rollback: {status!r}"
    assert (engine / "VERSION").read_text(encoding="utf-8").strip() == "0.1.0"


def test_being_ahead_of_the_newest_release_is_not_a_fault(tmp_path, capsys):
    """Chi gira su una pre-release è avanti, non rotto.

    Sulla macchina di sviluppo il battito orario stampava «refusing to
    downgrade ... use the manual recovery runbook» a ogni giro: un allarme
    per una situazione normale. Chiedere esplicitamente una versione più
    vecchia resta un rifiuto, ed è il test qui sopra.
    """
    updater = _load_updater()
    _origin, engine = _upgrade_fixture(tmp_path)
    assert updater.main(["--yes"], environ=_env(engine), which=lambda _name: None) == 0
    capsys.readouterr()
    (engine / "VERSION").write_text("99.0.0\n", encoding="utf-8")

    result = updater.main(["--check"], environ=_env(engine), which=lambda _name: None)

    captured = capsys.readouterr()
    assert result == 0, "essere avanti non è un guasto"
    assert "refusing" not in captured.err.lower()
    assert "ahead of the newest release" in captured.out
