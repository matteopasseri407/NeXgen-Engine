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
    names = [p.name for p in opencode_config_candidates(home)]
    assert names == ["opencode.jsonc", "opencode.json", "config.json"]
    assert all(str(p).startswith(str(xdg)) for p in opencode_config_candidates(home))


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
    errors: list[BaseException] = []

    def write(i: int) -> None:
        try:
            atomic_write_text(target, f"v{i}")
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

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
