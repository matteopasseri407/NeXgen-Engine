"""The update notice you actually see: shell hook, cache, once-a-day prompt."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

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
