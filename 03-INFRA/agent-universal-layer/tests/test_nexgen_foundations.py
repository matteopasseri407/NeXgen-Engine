"""Unit tests for foundation paths/files/config/report/jsonc hardening."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

pytestmark = pytest.mark.filterwarnings("ignore")


@pytest.mark.skipif(os.name == "nt", reason="symlinks require privileges")
def test_failed_launcher_publication_preserves_old_link(tmp_path, monkeypatch):
    from nexgen_core import files, shims
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    old = tmp_path / "old-launcher"
    old.write_text("old launcher")
    launcher = bin_dir / "nexgen"
    launcher.symlink_to(old)

    def fail(*args):
        raise OSError("synthetic publication failure")

    monkeypatch.setattr(files.os, "replace", fail)
    with pytest.raises(OSError):
        shims.install_shims(bin_dir=bin_dir, home=tmp_path)
    assert launcher.is_symlink()
    assert launcher.read_text() == "old launcher"
    assert old.read_text() == "old launcher"


@pytest.fixture
def startup_install(tmp_path, monkeypatch):
    import subprocess
    from types import SimpleNamespace
    from nexgen_core import scheduler

    home = tmp_path / "home"
    shim = home / ".local" / "bin" / "agent-sync.cmd"
    shim.parent.mkdir(parents=True)
    shim.write_text("synthetic launcher")
    appdata = tmp_path / "appdata"
    target = appdata / "Microsoft/Windows/Start Menu/Programs/Startup/KnowledgeVault Agent Sync.vbs"
    target.parent.mkdir(parents=True)
    target.write_text("foreign script")
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setattr(scheduler, "_host_mutations_disabled", lambda: False)
    monkeypatch.setattr(scheduler, "_scheduled_task_invokes_wrapper", lambda *a: False)
    monkeypatch.setattr(scheduler, "_run_external", lambda args, **k: subprocess.CompletedProcess(args, int("KnowledgeVault Agent Sync Logon" in args), "", ""))
    messages = []
    def run():
        return scheduler.install_scheduled_task(home=home, engine_root=tmp_path / "engine", vault_data=tmp_path / "vault", vault=tmp_path / "vault", branch="main", log=messages.append)
    return SimpleNamespace(run=run, target=target, messages=messages, scheduler=scheduler)


def test_unreadable_foreign_startup_is_never_overwritten(startup_install, monkeypatch):
    read = Path.read_bytes
    def fail_target(path):
        if path == startup_install.target:
            raise PermissionError("synthetic denial")
        return read(path)
    monkeypatch.setattr(Path, "read_bytes", fail_target)
    startup_install.run()
    assert startup_install.target.read_text() == "foreign script"


def test_failed_startup_replacement_preserves_foreign_content(startup_install, monkeypatch):
    from nexgen_core import files
    replace = files.os.replace
    def fail_target(source, target):
        if Path(target) == startup_install.target:
            raise OSError("synthetic publication failure")
        return replace(source, target)
    monkeypatch.setattr(files.os, "replace", fail_target)
    startup_install.run()
    assert startup_install.target.read_text() == "foreign script"


def test_each_foreign_startup_version_has_a_distinct_backup(startup_install):
    startup_install.run()
    startup_install.target.write_text("second foreign version")
    startup_install.run()
    backups = list(startup_install.target.parent.glob("*.bak"))
    assert {p.read_text() for p in backups} == {"foreign script", "second foreign version"}


def test_vault_env_expands_tilde(tmp_path: Path, monkeypatch) -> None:
    from nexgen_core.paths import resolve_vault_data

    monkeypatch.setenv("AGENT_VAULT_DATA", "~/KnowledgeVault")
    monkeypatch.delenv("KNOWLEDGE_VAULT_PATH", raising=False)
    resolved = resolve_vault_data()
    assert "~" not in str(resolved)
    assert resolved == Path.home() / "KnowledgeVault"


def test_relative_nexgen_home_fails_fast(tmp_path: Path, monkeypatch) -> None:
    from nexgen_core.paths import resolve_home

    monkeypatch.setenv("NEXGEN_HOME", "relative-checkout")
    with pytest.raises(ValueError, match="absolute"):
        resolve_home()
    monkeypatch.setenv("NEXGEN_HOME", str(tmp_path / "sandbox"))
    assert resolve_home() == tmp_path / "sandbox"


def test_opencode_candidates_honor_xdg_and_name_order(tmp_path: Path, monkeypatch) -> None:
    from nexgen_core.paths import opencode_config_candidates

    home = tmp_path / "home"
    xdg = tmp_path / "xdg"
    (xdg / "opencode").mkdir(parents=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    candidates = opencode_config_candidates(home)
    names = ["opencode.jsonc", "opencode.json", "config.json"]
    assert [p.name for p in candidates if p.parent == xdg / "opencode"] == names
    if sys.platform == "win32":
        assert [p.name for p in candidates] == ["opencode.jsonc", "opencode.jsonc", "opencode.json", "opencode.json", "config.json", "config.json"]
        assert candidates[1].parent == home / "AppData" / "Roaming" / "opencode"
    else:
        assert len(candidates) == 3


def test_strict_manifest_names_every_bad_entry(tmp_path: Path) -> None:
    from nexgen_core.config import ConfigError, load_mcp_manifest

    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        "schema_version: 1\nservers:\n"
        "  good:\n    command: run\n"
        "  typo:\n    comand: run\n"
        "  empty: {}\n",
        encoding="utf-8",
    )
    data = load_mcp_manifest(manifest)
    assert set(data["servers"]) == {"good"}
    with pytest.raises(ConfigError, match="typo"):
        load_mcp_manifest(manifest, strict=True)


def test_empty_and_null_configs_say_so(tmp_path: Path) -> None:
    from nexgen_core.config import ConfigError, load_council_config, load_mcp_manifest

    empty = tmp_path / "empty.yaml"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(ConfigError, match="empty"):
        load_mcp_manifest(empty)
    seats = tmp_path / "seats.yaml"
    seats.write_text("schema_version: 1\nseats:\n", encoding="utf-8")
    assert load_council_config(seats)["seats"] == {}


def test_remedy_none_keeps_broken_and_default_is_readonly() -> None:
    from nexgen_core.report import CheckOutcome, Report, Severity

    outcome = CheckOutcome(id="x", severity=Severity.BROKEN, message="m",
                           remedy=lambda: None)
    report = Report()
    report.add(outcome)
    assert outcome.severity == Severity.BROKEN
    assert outcome.remedied is False
    assert report.ok_count == 0


def test_atomic_write_unique_tmp_and_no_setuid_carry(tmp_path: Path) -> None:
    import threading

    from nexgen_core.files import atomic_write_text

    target = tmp_path / "cfg.json"
    target.write_text("old", encoding="utf-8")
    os.chmod(target, 0o600)
    errors: list[str] = []

    def write(i: int) -> None:
        try:
            for attempt in range(8):
                atomic_write_text(target, f"v{i}.{attempt}")
        except BaseException:  # noqa: BLE001
            import traceback

            errors.append(traceback.format_exc())

    threads = [threading.Thread(target=write, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    assert target.read_text(encoding="utf-8").startswith("v")
    leftovers = list(tmp_path.glob("cfg.json.*.tmp"))
    assert leftovers == []
    mode = target.stat().st_mode & 0o7777
    if os.name != "nt":
        assert mode & 0o777 == 0o600
    assert mode & 0o7000 == 0


def test_unreadable_file_is_never_overwritten(tmp_path: Path) -> None:
    from nexgen_core.files import write_text_if_changed

    target = tmp_path / "cfg.json"
    target.write_bytes(b"\xff\xfe invalid \x00 utf8")
    with pytest.raises(OSError, match="unreadable"):
        write_text_if_changed(target, "new")
    assert target.read_bytes().startswith(b"\xff\xfe")


def test_jsonc_comment_traps(tmp_path: Path) -> None:
    from nexgen_core.jsonc import (
        _skip_jsonc_trivia,
        parse_jsonc,
        remove_jsonc_top_level_value,
        set_jsonc_top_level_value,
    )

    assert parse_jsonc('{"a": 1, // keep\n}') == {"a": 1}
    assert parse_jsonc('{"a": 1, /* x */}') == {"a": 1}
    big = "// c\n" * 5000 + '{"a": 1}'
    assert parse_jsonc(big) == {"a": 1}
    assert _skip_jsonc_trivia("// x\n" * 3000 + "{}", 0) == 15000
    textured = '{"k": 1 // keep this\n, "j": 2}'
    out = set_jsonc_top_level_value(textured, "k", 42)
    assert "// keep this" in out and parse_jsonc(out)["k"] == 42
    dupes = '{"k": 1, "k": 2}'
    assert parse_jsonc(set_jsonc_top_level_value(dupes, "k", 9)) == {"k": 9}
    assert parse_jsonc(remove_jsonc_top_level_value(dupes, "k")) == {}
    with pytest.raises(ValueError, match="unterminated"):
        _skip_jsonc_trivia("/* xx", 0)
    out = set_jsonc_top_level_value('{"a": 1}', "k", ("x", "y"))
    assert parse_jsonc(out)["k"] == ["x", "y"]
