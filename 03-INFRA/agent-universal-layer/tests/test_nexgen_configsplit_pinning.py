"""Pinning dello split config prima del refactor.

Fissa il comportamento attuale (non quello desiderato) con config finte in
tmp_path: load_mcp_manifest su manifest finto ritorna le voci attese,
load_skills_manifest stabile, config mancante con errore chiaro.
Solo tmp_path, solo finte, nessuna config vera.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.config import (  # noqa: E402
    ConfigError,
    load_council_config,
    load_mcp_manifest,
    load_skills_manifest,
)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_load_mcp_manifest_fake_returns_expected(tmp_path: Path) -> None:
    manifest = _write(
        tmp_path / "manifest.yaml",
        "schema_version: 1\n"
        "future_top_level_field: ignored_gracefully\n"
        "servers:\n"
        "  fake-cmd:\n"
        "    command: run-fake\n"
        "  fake-url:\n"
        "    url: http://127.0.0.1:9/fake\n"
        "  retired-one:\n"
        "    command: run-retired\n"
        "retired_servers:\n"
        "  - retired-one\n",
    )
    data = load_mcp_manifest(manifest)
    assert set(data["servers"]) == {"fake-cmd", "fake-url"}
    assert data["servers"]["fake-cmd"]["command"] == "run-fake"
    assert data["retired_servers"] == {"retired-one"}
    assert data["schema_version"] == 1


def test_load_mcp_manifest_strict_names_bad_entries(tmp_path: Path) -> None:
    manifest = _write(
        tmp_path / "manifest.yaml",
        "schema_version: 1\nservers:\n"
        "  good:\n    command: run\n"
        "  typo:\n    comand: run\n"
        "  empty: {}\n",
    )
    data = load_mcp_manifest(manifest)
    assert set(data["servers"]) == {"good"}
    try:
        load_mcp_manifest(manifest, strict=True)
    except ConfigError as exc:
        assert "typo" in str(exc)
    else:
        raise AssertionError("strict doveva sollevare ConfigError")


def test_load_skills_manifest_stable(tmp_path: Path) -> None:
    manifest = _write(
        tmp_path / "skills.manifest.yaml",
        "schema_version: 1\nskills:\n"
        "  fake-skill:\n    origin: vault\n"
        "    targets: [claude]\n"
        "  future-skill:\n    origin: vault\n"
        "    future_field: ignored\n",
    )
    data = load_skills_manifest(manifest)
    assert set(data["skills"]) == {"fake-skill", "future-skill"}
    assert data["skills"]["fake-skill"]["origin"] == "vault"
    assert data["schema_version"] == 1


def test_missing_config_clear_error(tmp_path: Path) -> None:
    missing = tmp_path / "nope.yaml"
    try:
        load_mcp_manifest(missing)
    except ConfigError as exc:
        assert "not found" in str(exc)
    else:
        raise AssertionError("mancante doveva sollevare ConfigError")
    try:
        load_skills_manifest(missing)
    except ConfigError as exc:
        assert "not found" in str(exc)
    else:
        raise AssertionError("mancante doveva sollevare ConfigError")
    try:
        load_council_config(missing)
    except ConfigError as exc:
        assert "not found" in str(exc)
    else:
        raise AssertionError("mancante doveva sollevare ConfigError")


def test_empty_config_clear_error(tmp_path: Path) -> None:
    empty = _write(tmp_path / "empty.yaml", "")
    try:
        load_mcp_manifest(empty)
    except ConfigError as exc:
        assert "empty" in str(exc)
    else:
        raise AssertionError("vuoto doveva sollevare ConfigError")


def test_firme_pubbliche_invariate() -> None:
    assert list(inspect.signature(load_mcp_manifest).parameters) == ["path", "strict"]
    assert list(inspect.signature(load_skills_manifest).parameters) == ["path", "strict"]
    assert list(inspect.signature(load_council_config).parameters) == ["path"]
