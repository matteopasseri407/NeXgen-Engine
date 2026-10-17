"""Pinning dello split prompt/skills del notifier prima dell'unificazione.

Fissa il comportamento attuale (non quello desiderato): prompt su stato
finto stabile senza mai toccare la home reale, skills notice stabile
(peek ripetibile, consume una volta sola), throttle once-daily rispettato
in entrambe le lane, firme pubbliche invariate. Solo tmp_path, solo stati
finti, nessuna rete, nessun dialogo reale.
"""
from __future__ import annotations

import inspect
import json
import sys
import time
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.tools import notifier_prompt as prompt_mod  # noqa: E402
from nexgen_core.tools import notifier_skills as skills_mod  # noqa: E402
from nexgen_core.tools import notifier_state as state_mod  # noqa: E402

pytestmark = pytest.mark.filterwarnings("ignore")


@pytest.fixture
def fake_env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    monkeypatch.setenv("NEXGEN_HOME", str(home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("AGENT_STATE_DIR", str(state))
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    return home


def _write_cache(current="v2.2.0", latest="v2.3.0", age_hours=1, has_update=True):
    path = state_mod._state_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "current": current,
        "latest": latest,
        "checked_at": time.time() - age_hours * 3600,
        "has_update": has_update,
    }), encoding="utf-8")


def _state_dir(tmp_path) -> Path:
    return tmp_path / "state"


def test_prompt_su_stato_finto_stabile_chiede_una_volta(fake_env, tmp_path, monkeypatch, capsys):
    _write_cache()
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "n")
    monkeypatch.setattr(prompt_mod, "_spawn_background_refresh", lambda: None)
    launched = []

    def _no_exec(*args):
        launched.append(args)
        raise AssertionError("n non deve lanciare update")

    monkeypatch.setattr(prompt_mod.os, "execvp", _no_exec)
    assert prompt_mod.cmd_shell_check() == 0
    assert capsys.readouterr().out == ""
    assert state_mod._dismissed_today("v2.3.0"), "il no silenzioso deve comunque registrare il prompt"

    # Seconda shell lo stesso giorno: silenziosa, nessun secondo prompt.
    def _boom(_prompt=""):
        raise AssertionError("secondo prompt nello stesso giorno")

    monkeypatch.setattr("builtins.input", _boom)
    assert prompt_mod.cmd_shell_check() == 0
    assert capsys.readouterr().out == ""
    assert launched == []


def test_prompt_yes_lancia_update_una_volta(fake_env, tmp_path, monkeypatch, capsys):
    _write_cache()
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "s")
    monkeypatch.setattr(prompt_mod, "_spawn_background_refresh", lambda: None)
    launched = {}
    monkeypatch.setattr(prompt_mod.os, "execvp", lambda *a: launched.setdefault("argv", a))
    assert prompt_mod.cmd_shell_check() == 0
    assert launched["argv"] == ("nexgen", ["nexgen", "update", "--target", "2.3.0"])
    assert "2.3.0" in capsys.readouterr().out


def test_skills_notice_stabile_e_consuma_una_volta(fake_env, tmp_path, monkeypatch):
    _write_cache(has_update=False)
    nex = _state_dir(tmp_path) / "nexgen"
    nex.mkdir(parents=True, exist_ok=True)
    (nex / "third-party-applied.json").write_text(json.dumps({
        "applied": [{"what": "skill 'demo' (github o/r)", "new": "b" * 40, "at": time.time()}],
    }), encoding="utf-8")
    first = skills_mod._skills_notice(mark=False)
    second = skills_mod._skills_notice(mark=False)
    assert first == second
    assert first is not None and "Skill aggiornate: demo." in first
    consumed = skills_mod._skills_notice(mark=True)
    assert consumed == first
    assert skills_mod._skills_notice(mark=False) is None


def test_throttle_once_daily_rispettato_gui_e_shell(fake_env, tmp_path, monkeypatch):
    import nexgen_core.updater as updater_module

    state_mod._write_state({"dismissed": {"version": "v2.3.0", "day": state_mod._today()}})
    monkeypatch.setattr(
        updater_module.EngineUpdater, "check_updates",
        staticmethod(lambda: (True, "v2.2.0", "v2.3.0")),
    )

    def _boom(*a, **k):
        raise AssertionError("throttle: secondo prompt nello stesso giorno")

    monkeypatch.setattr(prompt_mod, "_call_prompt_user", _boom)
    assert prompt_mod.cmd_check() == 0

    # Il record odierno sopprime anche la lane shell senza chiedere.
    _write_cache()
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", _boom)
    monkeypatch.setattr(prompt_mod, "_spawn_background_refresh", lambda: None)
    assert prompt_mod.cmd_shell_check() == 0


def test_batch_hold_once_daily_ognuno_una_volta(fake_env, tmp_path):
    _write_cache(has_update=False)
    nex = _state_dir(tmp_path) / "nexgen"
    nex.mkdir(parents=True, exist_ok=True)
    (nex / "third-party-guard.json").write_text(json.dumps({
        "checked_at": time.time(),
        "auto": [],
        "hold": [{"what": "skill 'h' (github o/r)", "pinned": "a" * 40,
                  "upstream": "b" * 40, "reasons": ["x"], "plain": "fermo", "target": None}],
        "batch": [{"what": "MCP server 'srv' (npm pkg)", "pinned": "1.0.0",
                   "upstream": "1.0.1", "reasons": ["patch"], "plain": "piano",
                   "target": {"kind": "npm-version", "manifest": "mcp",
                              "server": "srv", "package": "pkg"}}],
    }), encoding="utf-8")
    first = skills_mod._skills_notice(mark=True)
    assert first is not None
    assert "nexgen skill bump" in first and "in attesa" in first
    assert skills_mod._skills_notice(mark=False) is None
    # Stesso giorno, stesso set: resta silenzioso.
    assert skills_mod._skills_notice(mark=True) is None


def test_firme_pubbliche_invariate():
    assert list(inspect.signature(prompt_mod.cmd_check).parameters) == ["force"]
    assert list(inspect.signature(prompt_mod.cmd_shell_check).parameters) == []
    assert list(inspect.signature(prompt_mod._shell_check).parameters) == []
    assert list(inspect.signature(prompt_mod._shell_check_engine).parameters) == ["state"]
    assert list(inspect.signature(prompt_mod._shell_check_skills).parameters) == []
    assert list(inspect.signature(prompt_mod._check_skills_gui).parameters) == ["force"]
    assert list(inspect.signature(prompt_mod._prompt_user).parameters) == ["current", "latest", "notes_hint"]
    assert list(inspect.signature(prompt_mod._logo_path).parameters) == []
    assert list(inspect.signature(prompt_mod._notes_hint).parameters) == []
    assert prompt_mod._notes_hint() == "Note di rilascio: `nexgen update --check`."
    assert list(inspect.signature(skills_mod._skills_notice).parameters) == ["mark"]
    assert list(inspect.signature(skills_mod._take_fresh_applied).parameters) == ["mark"]
    assert list(inspect.signature(skills_mod._confirm_skills_shown).parameters) == []
    assert list(inspect.signature(skills_mod._held_once_daily).parameters) == ["mark"]
    assert list(inspect.signature(skills_mod._batch_once_daily).parameters) == ["mark"]
    assert list(inspect.signature(skills_mod._applied_file).parameters) == []
    assert list(inspect.signature(skills_mod._short_skill_name).parameters) == ["what"]


def test_usa_helper_stato_condivisi_nessun_duplicato_locale():
    # Le due lane riusano gli helper di notifier_state, non copie locali.
    assert prompt_mod._read_state is state_mod._read_state
    assert prompt_mod._dismissed_today is state_mod._dismissed_today
    assert prompt_mod._is_throttled is state_mod._is_throttled
    assert prompt_mod._record_prompt_time is state_mod._record_prompt_time
    assert prompt_mod._cache_fresh is state_mod._cache_fresh
    assert prompt_mod._cache_usable is state_mod._cache_usable
    assert skills_mod._read_state is state_mod._read_state
    assert skills_mod._write_state is state_mod._write_state
    assert skills_mod._skills_dismissed is state_mod._skills_dismissed
    assert skills_mod._record_skills_dismissal is state_mod._record_skills_dismissal
    for mod in (prompt_mod, skills_mod):
        src = inspect.getsource(mod)
        assert "last_update_check.json" not in src
        assert "update-status.json" not in src
