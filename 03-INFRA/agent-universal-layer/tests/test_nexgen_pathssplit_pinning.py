"""Pinning dello split paths prima del refactor.

Fissa il comportamento attuale (non quello desiderato): resolve_home su
env finto stabile, resolve_state_dir stabile, opencode_config_candidates
ritorna attesi, firme pubbliche invariate. Solo home finte in tmp_path
con env finte, MAI home vera.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import paths as paths_mod  # noqa: E402

pytestmark = pytest.mark.filterwarnings("ignore")


def _fake_home(tmp_path: Path, name: str = "home") -> Path:
    home = tmp_path / name
    home.mkdir(parents=True, exist_ok=True)
    return home


def test_resolve_home_su_env_finto_stabile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = _fake_home(tmp_path, "home")
    other = _fake_home(tmp_path, "other")
    assert paths_mod.resolve_home(home) == home
    assert paths_mod.resolve_home(str(other)) == other
    monkeypatch.setenv("NEXGEN_HOME", str(home))
    assert paths_mod.resolve_home() == home
    assert paths_mod.resolve_home() == paths_mod.resolve_home()
    monkeypatch.setenv("NEXGEN_HOME", "relative-checkout")
    with pytest.raises(ValueError, match="absolute"):
        paths_mod.resolve_home()


def test_resolve_vault_data_default_e_override_finti(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = _fake_home(tmp_path, "home")
    for key in ("AGENT_VAULT_DATA", "KNOWLEDGE_VAULT_PATH", "NEXGEN_HOME"):
        monkeypatch.delenv(key, raising=False)
    assert paths_mod.resolve_vault_data(home) == home / "KnowledgeVault"
    assert paths_mod.resolve_vault_data(home) == paths_mod.resolve_vault_data(home)
    override = tmp_path / "vault-override"
    assert paths_mod.resolve_vault_data(home, override) == override
    monkeypatch.setenv("AGENT_VAULT_DATA", str(tmp_path / "env-vault"))
    assert paths_mod.resolve_vault_data(home) == tmp_path / "env-vault"


def test_resolve_state_dir_stabile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = _fake_home(tmp_path, "home")
    for key in ("AGENT_STATE_DIR", "XDG_STATE_HOME", "NEXGEN_HOME"):
        monkeypatch.delenv(key, raising=False)
    assert paths_mod.resolve_state_dir(home) == home / ".local" / "state"
    assert paths_mod.resolve_state_dir(home) == paths_mod.resolve_state_dir(home)
    custom = tmp_path / "custom-state"
    assert paths_mod.resolve_state_dir(home, custom) == custom
    monkeypatch.setenv("AGENT_STATE_DIR", str(tmp_path / "env-state"))
    assert paths_mod.resolve_state_dir(home) == tmp_path / "env-state"


def test_resolve_state_dir_sandbox_ignora_xdg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = _fake_home(tmp_path, "home")
    xdg = tmp_path / "xdg-state"
    xdg.mkdir()
    monkeypatch.setenv("NEXGEN_HOME", str(home))
    monkeypatch.setenv("XDG_STATE_HOME", str(xdg))
    monkeypatch.delenv("AGENT_STATE_DIR", raising=False)
    assert paths_mod.resolve_state_dir(home) == home / ".local" / "state"
    assert paths_mod.resolve_state_dir() == home / ".local" / "state"


def test_opencode_config_candidates_ritorna_attesi(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = _fake_home(tmp_path, "home")
    xdg = tmp_path / "xdg"
    (xdg / "opencode").mkdir(parents=True)
    appdata = tmp_path / "appdata"
    (appdata / "opencode").mkdir(parents=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.delenv("NEXGEN_HOME", raising=False)
    assert paths_mod.OPENCODE_CONFIG_NAMES == ("opencode.jsonc", "opencode.json", "config.json")
    first = paths_mod.opencode_config_candidates(home)
    second = paths_mod.opencode_config_candidates(home)
    assert first == second
    expected_dirs = [xdg / "opencode"]
    if sys.platform == "win32":
        expected_dirs.append(appdata / "opencode")
    else:
        assert len(first) == len(paths_mod.OPENCODE_CONFIG_NAMES)
    assert first == [d / name for name in paths_mod.OPENCODE_CONFIG_NAMES for d in expected_dirs]


def test_opencode_config_dir_default_sotto_home_finta(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = _fake_home(tmp_path, "home")
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("NEXGEN_HOME", raising=False)
    assert paths_mod.opencode_config_dir(home) == home / ".config" / "opencode"
    assert paths_mod.opencode_config_path(home) == home / ".config" / "opencode" / "opencode.jsonc"


def test_firme_pubbliche_invariate() -> None:
    expected = {
        "resolve_home": ["home"],
        "resolve_vault_data": ["home", "override"],
        "resolve_engine_root": ["home", "override"],
        "bundled_engine_root": [],
        "installed_as_package": [],
        "resolve_runtime_dir": ["home"],
        "resolve_state_dir": ["home", "override"],
        "canonical_instructions": ["vault_data"],
        "mcp_manifest": ["vault_data"],
        "skills_manifest": ["vault_data"],
        "remotes_config": ["vault_data"],
        "opencode_config_dir": ["home"],
        "opencode_config_candidates": ["home"],
        "opencode_config_path": ["home"],
        "opencode_agents_file": ["home"],
        "opencode_skills_dir": ["home"],
        "claude_config": ["home"],
        "claude_settings": ["home"],
        "claude_desktop_config": ["home"],
        "codex_home": ["home"],
        "codex_config": ["home"],
        "antigravity_config": ["home"],
        "antigravity_configs": ["home"],
        "antigravity_settings": ["home"],
        "antigravity_hooks": ["home"],
    }
    actual = {name: list(inspect.signature(getattr(paths_mod, name)).parameters) for name in expected}
    assert actual == expected
