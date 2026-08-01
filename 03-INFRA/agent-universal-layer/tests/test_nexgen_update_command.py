"""Regression tests for the real cross-platform ``nexgen-update`` command."""
from __future__ import annotations

import importlib.util
import os
import subprocess
from pathlib import Path

import pytest

from conftest import REAL_VAULT, load_agent_sync_module


SCRIPT = REAL_VAULT / "03-INFRA" / "scripts" / "nexgen_update.py"
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


def test_diverged_engine_branch_is_rejected_before_merge(tmp_path, capsys):
    updater = _load_updater()
    _origin, engine = _upgrade_fixture(tmp_path)
    local = engine / "local-commit.txt"
    local.write_text("keep this history\n", encoding="utf-8")
    _git(engine, "add", "local-commit.txt")
    _git(engine, "commit", "-m", "local engine history")
    before = _git(engine, "rev-parse", "HEAD").stdout.strip()

    result = updater.main(["--yes"], environ=_env(engine), which=lambda _name: None)

    assert result == 1
    assert _git(engine, "rev-parse", "HEAD").stdout.strip() == before
    assert "is not a fast-forward" in capsys.readouterr().err


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
    assert (scripts / "nexgen_update.py").is_file()


def test_powershell_launcher_has_one_python_backend_and_forwards_arguments():
    source = POWERSHELL_LAUNCHER.read_text(encoding="utf-8")
    assert 'Join-Path $PSScriptRoot "nexgen_update.py"' in source
    assert 'sys.version_info >= (3, 10)' in source
    assert "& $runtimeCommand @runtimePrefix $script @args" in source


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
    assert "usage: nexgen-update" in result.stdout
