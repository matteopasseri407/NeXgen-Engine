"""Pinning dello split info/inventory di nexgen prima del refactor.

Fissa il comportamento attuale (non quello desiderato): get_engine_info
su env finto e stabile (due chiamate danno lo stesso esito),
cmd_info e cmd_inventory coerenti tra loro sullo stesso env finto,
firme pubbliche invariate. Solo tmp_path, solo env finti, nessuna rete,
nessuna home vera.
"""
from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.cli import engine as engine_cmds  # noqa: E402
from nexgen_core.tools import info as info_mod  # noqa: E402

pytestmark = pytest.mark.filterwarnings("ignore")


def _isolate_fake_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
    """Solo finti in tmp_path: home, vault ed engine isolati, PATH senza CLI reali."""
    fake_home = tmp_path / "fake-home"
    fake_vault = tmp_path / "fake-vault"
    fake_engine = tmp_path / "fake-engine"
    for d in (fake_home, fake_vault, fake_engine):
        d.mkdir(parents=True, exist_ok=True)
    (fake_vault / "03-INFRA" / "agent-universal-layer" / "instructions").mkdir(
        parents=True, exist_ok=True
    )
    (fake_vault / "03-INFRA" / "agent-universal-layer" / "instructions" / "AGENTS.md").write_text(
        "# canonical bootstrap\n", encoding="utf-8"
    )
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir(exist_ok=True)
    monkeypatch.setenv("NEXGEN_HOME", str(fake_home))
    monkeypatch.setenv("KNOWLEDGE_VAULT_PATH", str(fake_vault))
    monkeypatch.setenv("AGENT_ENGINE_ROOT", str(fake_engine))
    monkeypatch.setenv("NEXGEN_DISABLE_HOST_MUTATIONS", "1")
    monkeypatch.setenv("KNOWLEDGE_VAULT_REMOTE", "local")
    monkeypatch.setenv("PATH", str(empty_bin))
    for key in (
        "AGENT_VAULT_DATA",
        "AGENT_STATE_DIR",
        "XDG_CONFIG_HOME",
        "XDG_STATE_HOME",
        "XDG_DATA_HOME",
        "CODEX_HOME",
        "KNOWLEDGE_VAULT_MIRRORS",
    ):
        monkeypatch.delenv(key, raising=False)
    return fake_home, fake_vault, fake_engine


def _assert_all_inside_tmp(tmp_path: Path) -> None:
    from nexgen_core.paths import resolve_engine_root, resolve_home, resolve_vault_data

    assert str(resolve_home()).startswith(str(tmp_path))
    assert str(resolve_vault_data()).startswith(str(tmp_path))
    assert str(resolve_engine_root()).startswith(str(tmp_path))


def test_get_engine_info_su_env_finto_stabile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate_fake_env(tmp_path, monkeypatch)
    _assert_all_inside_tmp(tmp_path)
    first = info_mod.get_engine_info()
    second = info_mod.get_engine_info()
    assert first == second
    for key in (
        "version",
        "os",
        "host",
        "python",
        "vault_path",
        "engine_root",
        "runtimes",
        "modules",
        "secrets",
        "doctor",
        "notes_count",
        "extensions",
    ):
        assert key in first, f"chiave mancante: {key}"
    assert isinstance(first["runtimes"], list) and first["runtimes"]
    assert isinstance(first["notes_count"], int) and first["notes_count"] >= 0
    assert str(first["vault_path"]).startswith(str(tmp_path))
    assert str(first["engine_root"]).startswith(str(tmp_path))
    _assert_all_inside_tmp(tmp_path)


def test_render_info_json_coerente_con_get_engine_info(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate_fake_env(tmp_path, monkeypatch)
    info = info_mod.get_engine_info()
    data = json.loads(info_mod.render_info(as_json=True))
    assert data["vault_path"] == info["vault_path"]
    assert data["engine_root"] == info["engine_root"]
    assert data["version"] == info["version"]
    text = info_mod.render_info(as_json=False)
    assert "neXgen Engine" in text
    assert "PLANES & RUNTIMES" in text
    assert str(info["vault_path"]) in text
    _assert_all_inside_tmp(tmp_path)


def test_cmd_info_e_cmd_inventory_coerenti(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    _isolate_fake_env(tmp_path, monkeypatch)
    from nexgen_core.paths import resolve_vault_data

    info = info_mod.get_engine_info()
    rc_info = engine_cmds.cmd_info(SimpleNamespace(json=False, interactive=False, all=False))
    assert rc_info == 0
    out_info = capsys.readouterr().out
    assert "neXgen Engine" in out_info
    assert str(info["vault_path"]) in out_info

    rc_inv = engine_cmds.cmd_inventory(SimpleNamespace())
    assert rc_inv in (0, 2)
    out_inv = capsys.readouterr().out
    assert "MCP" in out_inv
    assert "claude:" in out_inv
    assert ("Read-only" in out_inv or "Sola lettura" in out_inv)
    # Stesso env finto per entrambi: il vault non si e' spostato.
    assert str(resolve_vault_data()) == info["vault_path"]
    assert str(resolve_vault_data()).startswith(str(tmp_path))
    _assert_all_inside_tmp(tmp_path)


def test_firme_pubbliche_invariate() -> None:
    assert list(inspect.signature(info_mod.get_engine_info).parameters) == ["vault_data"]
    assert list(inspect.signature(info_mod.render_info).parameters) == [
        "as_json",
        "vault_data",
        "show_all",
    ]
    assert list(inspect.signature(engine_cmds.cmd_info).parameters) == ["args"]
    assert list(inspect.signature(engine_cmds.cmd_inventory).parameters) == ["args"]
