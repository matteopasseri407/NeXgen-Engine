"""The update notice you actually see: shell hook, cache, once-a-day prompt."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.tools import update_notifier as notifier  # noqa: E402


def _isolate(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("NEXGEN_HOME", str(home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("AGENT_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    return home


def _write_cache(current="v2.2.0", latest="v2.3.0", age_hours=1, has_update=True):
    path = notifier._state_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "current": current,
        "latest": latest,
        "checked_at": time.time() - age_hours * 3600,
        "has_update": has_update,
    }), encoding="utf-8")


def test_shell_check_asks_once_per_day_and_launches_on_yes(tmp_path, monkeypatch, capsys):
    _isolate(tmp_path, monkeypatch)
    _write_cache()
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "s")
    launched = {}
    monkeypatch.setattr(notifier.os, "execvp", lambda *args: launched.setdefault("argv", args))

    assert notifier.cmd_shell_check() == 0
    assert launched["argv"] == ("nexgen", ["nexgen", "update", "--target", "2.3.0"])
    assert "2.3.0" in capsys.readouterr().out

    # Second shell the same day: silent, no second prompt.
    monkeypatch.setattr("builtins.input", lambda _prompt="": (_ for _ in ()).throw(AssertionError("asked twice")))
    assert notifier.cmd_shell_check() == 0


def test_shell_check_no_means_no_launch_but_still_records(tmp_path, monkeypatch, capsys):
    _isolate(tmp_path, monkeypatch)
    _write_cache()
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "n")
    monkeypatch.setattr(notifier.os, "execvp", lambda *args: (_ for _ in ()).throw(AssertionError("launched")))

    assert notifier.cmd_shell_check() == 0
    assert capsys.readouterr().out == ""  # "no" is silent: the nag already happened
    assert notifier._dismissed_today("v2.3.0")


def test_shell_check_silent_without_tty_or_without_update(tmp_path, monkeypatch, capsys):
    _isolate(tmp_path, monkeypatch)
    _write_cache()
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr("builtins.input", lambda _prompt="": (_ for _ in ()).throw(AssertionError("prompted headless")))
    assert notifier.cmd_shell_check() == 0
    assert capsys.readouterr().out == ""

    _write_cache(has_update=False)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    assert notifier.cmd_shell_check() == 0
    assert capsys.readouterr().out == ""


def test_shell_check_refreshes_stale_cache_in_background(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _write_cache(age_hours=30)  # stale, but usable: nag on last-known + refresh
    spawned = {}
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "n")

    def fake_popen(*args, **kwargs):
        spawned["args"] = args
        raise RuntimeError("nope")  # _spawn swallows everything

    # Hermetic: CI runners have no `nexgen` on PATH, dev machines do. The
    # spawn decision must not depend on the real environment either way.
    monkeypatch.setattr(notifier.shutil, "which", lambda _name: "/fake/bin/nexgen")
    monkeypatch.setattr(notifier.subprocess, "Popen", fake_popen)
    assert notifier.cmd_shell_check() == 0
    assert spawned["args"][0][:3] == [spawned["args"][0][0], "tool", "update-notifier"]


def test_shell_check_missing_cache_bootstraps_silently(tmp_path, monkeypatch, capsys):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt="": (_ for _ in ()).throw(AssertionError("prompted without data")))
    monkeypatch.setattr(notifier.subprocess, "Popen", lambda *a, **k: None)
    assert notifier.cmd_shell_check() == 0
    assert capsys.readouterr().out == ""


def test_refresh_cache_writes_state_and_never_raises(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    repo = tmp_path / "engine"
    (repo / "VERSION").parent.mkdir(parents=True, exist_ok=True)
    (repo / "VERSION").write_text("2.2.0\n", encoding="utf-8")
    monkeypatch.setattr(notifier, "_newest_tag_ls_remote", lambda _repo, timeout=20: "v2.3.0")

    result = notifier.refresh_update_cache(str(repo))
    assert result["current"] == "v2.2.0"
    assert result["latest"] == "v2.3.0"
    assert result["has_update"] is True
    stored = json.loads(notifier._state_file().read_text(encoding="utf-8"))
    assert stored["latest"] == "v2.3.0"

    # Offline (no tag): keeps what it knew, raises nothing.
    monkeypatch.setattr(notifier, "_newest_tag_ls_remote", lambda _repo, timeout=20: None)
    assert notifier.refresh_update_cache(str(repo)) is not None
    assert notifier.refresh_update_cache("/nonexistent") is not None


def test_install_and_remove_shell_hook_are_idempotent(tmp_path, monkeypatch, capsys):
    home = _isolate(tmp_path, monkeypatch)
    assert notifier.cmd_install_shell_hook(shell="bash") == 0
    bashrc = home / ".bashrc"
    assert "nexgen tool update-notifier --shell-check" in bashrc.read_text(encoding="utf-8")
    assert notifier.cmd_install_shell_hook(shell="bash") == 0
    assert bashrc.read_text(encoding="utf-8").count("NeXgen Engine update notice") == 1
    assert notifier.cmd_install_shell_hook(remove=True, shell="bash") == 0
    assert "update-notifier" not in bashrc.read_text(encoding="utf-8")
    capsys.readouterr()


def test_gui_check_honors_shared_dismissal(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    notifier._write_state({"dismissed": {"version": "v2.3.0", "day": notifier._today()}})

    import nexgen_core.updater as updater_module

    monkeypatch.setattr(
        updater_module.EngineUpdater, "check_updates",
        staticmethod(lambda: (True, "v2.2.0", "v2.3.0")),
    )
    monkeypatch.setattr(notifier, "_prompt_user", lambda *a, **k: (_ for _ in ()).throw(AssertionError("nagged twice")))

    assert notifier.cmd_check() == 0


def test_shell_check_announces_applied_updates_once_and_asks_nothing(tmp_path, monkeypatch, capsys):
    _isolate(tmp_path, monkeypatch)
    _write_cache(has_update=False)
    state_dir = tmp_path / "state"
    (state_dir / "nexgen").mkdir(parents=True, exist_ok=True)
    (state_dir / "nexgen" / "third-party-applied.json").write_text(json.dumps({
        "applied": [{"what": "skill 'demo' (github o/r)", "new": "b" * 40, "at": time.time()}],
    }), encoding="utf-8")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt="": (_ for _ in ()).throw(AssertionError("asked")))

    assert notifier.cmd_shell_check() == 0
    assert "Skill aggiornate: demo." in capsys.readouterr().out

    # Second shell: already said, stays silent.
    assert notifier.cmd_shell_check() == 0
    assert capsys.readouterr().out == ""


def test_shell_check_held_line_appears_once_per_day(tmp_path, monkeypatch, capsys):
    _isolate(tmp_path, monkeypatch)
    _write_cache(has_update=False)
    state_dir = tmp_path / "state"
    (state_dir / "nexgen").mkdir(parents=True, exist_ok=True)
    (state_dir / "nexgen" / "third-party-guard.json").write_text(json.dumps({
        "checked_at": time.time(),
        "auto": [], "batch": [],
        "hold": [{"what": "skill 'demo' (github o/r)", "pinned": "a" * 40,
                  "upstream": "b" * 40, "reasons": ["x"], "plain": "fermo", "target": None}],
    }), encoding="utf-8")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt="": (_ for _ in ()).throw(AssertionError("asked")))

    assert notifier.cmd_shell_check() == 0
    assert "in attesa" in capsys.readouterr().out
    assert notifier.cmd_shell_check() == 0
    assert capsys.readouterr().out == ""


def test_shell_check_announces_batch_ready_once(tmp_path, monkeypatch, capsys):
    _isolate(tmp_path, monkeypatch)
    _write_cache(has_update=False)
    state_dir = tmp_path / "state"
    (state_dir / "nexgen").mkdir(parents=True, exist_ok=True)
    (state_dir / "nexgen" / "third-party-guard.json").write_text(json.dumps({
        "checked_at": time.time(),
        "auto": [], "hold": [],
        "batch": [{"what": "MCP server 'srv' (npm pkg)", "pinned": "1.0.0",
                   "upstream": "1.0.1", "reasons": ["patch"], "plain": "piano",
                   "target": {"kind": "npm-version", "manifest": "mcp",
                              "server": "srv", "package": "pkg"}}],
    }), encoding="utf-8")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt="": (_ for _ in ()).throw(AssertionError("asked")))

    assert notifier.cmd_shell_check() == 0
    assert "nexgen skill bump" in capsys.readouterr().out
    assert notifier.cmd_shell_check() == 0
    assert capsys.readouterr().out == ""


def test_shell_check_batch_and_hold_each_appear_once(tmp_path, monkeypatch, capsys):
    _isolate(tmp_path, monkeypatch)
    _write_cache(has_update=False)
    state_dir = tmp_path / "state"
    (state_dir / "nexgen").mkdir(parents=True, exist_ok=True)
    (state_dir / "nexgen" / "third-party-guard.json").write_text(json.dumps({
        "checked_at": time.time(),
        "auto": [], "hold": [
            {"what": "skill 'h' (github o/r)", "pinned": "a" * 40, "upstream": "b" * 40,
             "reasons": ["x"], "plain": "fermo", "target": None}],
        "batch": [{"what": "MCP server 'srv' (npm pkg)", "pinned": "1.0.0",
                   "upstream": "1.0.1", "reasons": ["patch"], "plain": "piano",
                   "target": {"kind": "npm-version", "manifest": "mcp",
                              "server": "srv", "package": "pkg"}}],
    }), encoding="utf-8")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt="": (_ for _ in ()).throw(AssertionError("asked")))

    assert notifier.cmd_shell_check() == 0
    out = capsys.readouterr().out
    assert "nexgen skill bump" in out and "in attesa" in out
    assert notifier.cmd_shell_check() == 0
    assert capsys.readouterr().out == ""


def test_crashed_dialog_does_not_consume_announcement(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    state_dir = tmp_path / "state"
    (state_dir / "nexgen").mkdir(parents=True, exist_ok=True)
    (state_dir / "nexgen" / "third-party-applied.json").write_text(json.dumps({
        "applied": [{"what": "skill 'demo' (github o/r)", "new": "b" * 40, "at": time.time()}],
    }), encoding="utf-8")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(notifier.shutil, "which", lambda _name: "/usr/bin/zenity")

    crashed = {"rc": 1}

    def _run(*args, **kwargs):
        class _Proc:
            returncode = crashed["rc"]
        return _Proc()

    monkeypatch.setattr(notifier.subprocess, "run", _run)
    notifier._check_skills_gui()
    # Still pending: the user saw nothing.
    assert notifier._skills_notice(mark=False) is not None
    assert "Skill aggiornate: demo." in (notifier._skills_notice(mark=False) or "")

    crashed["rc"] = 0
    notifier._check_skills_gui()
    assert notifier._skills_notice(mark=False) is None


def test_ensure_boot_check_writes_files_and_is_idempotent(tmp_path, monkeypatch, capsys):
    home = _isolate(tmp_path, monkeypatch)
    first = notifier.ensure_boot_check(home)
    assert (home / ".config" / "autostart" / "nexgen-update-check.desktop").is_file()
    assert (home / ".config" / "systemd" / "user" / "nexgen-update-check.timer").is_file()
    assert any("boot" in note.lower() or "autostart" in note.lower() for note in first)
    capsys.readouterr()


def test_ensure_shell_hook_installs_once(tmp_path, monkeypatch):
    home = _isolate(tmp_path, monkeypatch)
    assert notifier.ensure_shell_hook(home) != ["[shell-hook] already present"]
    assert (home / ".bashrc").is_file()
    assert notifier.ensure_shell_hook(home) == ["[shell-hook] already present"]


def test_ensure_boot_check_windows_uses_schtasks(tmp_path, monkeypatch):
    import os as _os
    if _os.name != "nt":
        pytest.skip("Windows-only lane")
    home = _isolate(tmp_path, monkeypatch)
    calls = []

    def _fake_run(argv, **kwargs):
        calls.append(argv)
        class _Proc:
            returncode = 0
            stdout = "wscript.exe fake"
            stderr = ""
        return _Proc()

    monkeypatch.setattr(notifier.subprocess, "run", _fake_run)
    notes = notifier.ensure_boot_check(home)
    assert any("schtasks" in n for n in notes)
    assert any(argv[:2] == ["schtasks.exe", "/Query"] for argv in calls)
    assert any("/Create" in argv for argv in calls)
    assert (home / ".local" / "state" / "nexgen-update-check-hidden.vbs").is_file()


def test_windows_task_probe_matches_notifier_command(monkeypatch):
    def _fake_run(argv, **kwargs):
        class _Proc:
            returncode = 0
            stdout = 'wscript.exe "C:\\x\\nexgen-update-check-hidden.vbs"'
            stderr = ""
        return _Proc()

    monkeypatch.setattr(notifier.subprocess, "run", _fake_run)
    assert notifier._windows_task_runs_notifier("other") is False
