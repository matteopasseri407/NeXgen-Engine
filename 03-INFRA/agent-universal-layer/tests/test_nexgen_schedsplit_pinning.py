"""Pinning dello split scheduler su home finta in tmp_path.

Fissa il comportamento attuale, non quello desiderato: la separazione
della generazione del wrapper dalla sua installazione
(_render_wrapper / _install_wrapper) non deve cambiare esiti.
Nessuna rete, nessuna HOME reale.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import scheduler  # noqa: E402


def _fake_home(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    home = tmp_path / "home"
    engine_root = tmp_path / "engine"
    vault = tmp_path / "vault"
    vault_data = vault
    home.mkdir(parents=True, exist_ok=True)
    engine_root.mkdir(parents=True, exist_ok=True)
    vault.mkdir(parents=True, exist_ok=True)
    return home, engine_root, vault_data, vault


def _render_kwargs(home: Path, engine_root: Path, vault_data: Path, vault: Path, mode: str = "guard") -> dict:
    script_path = home / ".local" / "bin" / "agent-sync.cmd"
    return {
        "script_path": script_path,
        "engine_root": engine_root,
        "vault_data": vault_data,
        "vault": vault,
        "branch": "main",
        "mode": mode,
    }


def test_wrapper_renders_stably_for_same_input(tmp_path: Path):
    home, engine_root, vault_data, vault = _fake_home(tmp_path)
    kwargs = _render_kwargs(home, engine_root, vault_data, vault, mode="guard")
    first = scheduler._render_wrapper(**kwargs)
    second = scheduler._render_wrapper(**kwargs)
    assert first == second
    assert "guard" in first
    assert str(kwargs["script_path"]) in first or "agent-sync.cmd" in first


def test_wrapper_modes_differ_but_share_template(tmp_path: Path):
    home, engine_root, vault_data, vault = _fake_home(tmp_path)
    guard = scheduler._render_wrapper(**_render_kwargs(home, engine_root, vault_data, vault, mode="guard"))
    beat = scheduler._render_wrapper(**_render_kwargs(home, engine_root, vault_data, vault, mode="heartbeat"))
    assert guard != beat
    assert "guard" in guard
    assert "heartbeat" in beat


def test_install_refuses_without_shim_with_clear_message(tmp_path: Path, monkeypatch):
    home, engine_root, vault_data, vault = _fake_home(tmp_path)
    monkeypatch.setattr(scheduler, "_host_mutations_disabled", lambda: False)
    monkeypatch.setattr(scheduler, "host_mutations_disabled", lambda: False)
    messages: list[str] = []
    ok = scheduler.install_scheduled_task(
        home=home,
        engine_root=engine_root,
        vault_data=vault_data,
        vault=vault,
        branch="main",
        log=messages.append,
    )
    assert ok is False
    assert any("doesn't exist" in m and "not installed" in m for m in messages), messages
    assert list((tmp_path / "home").rglob("*.vbs")) == []


def test_install_refuses_unreadable_wrapper_with_clear_message(tmp_path: Path):
    home, _, _, _ = _fake_home(tmp_path)
    target = home / ".local" / "state" / "start-agent-sync-hidden.vbs"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"\xff\xfe invalid \x00 utf8")
    before = target.read_bytes()
    with pytest.raises(OSError, match="(?i)unreadable|refus"):
        scheduler._install_wrapper(target, "new content", log=lambda m: None, label="hidden wrapper updated")
    assert target.read_bytes() == before


def test_install_writes_only_under_tmp_home(tmp_path: Path, monkeypatch):
    home, engine_root, vault_data, vault = _fake_home(tmp_path)
    shim = home / ".local" / "bin" / "agent-sync.cmd"
    shim.parent.mkdir(parents=True, exist_ok=True)
    shim.write_text("synthetic launcher", encoding="utf-8")
    appdata = tmp_path / "appdata"
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setattr(scheduler, "_host_mutations_disabled", lambda: False)
    monkeypatch.setattr(scheduler, "host_mutations_disabled", lambda: False)
    monkeypatch.setattr(scheduler, "_scheduled_task_invokes_wrapper", lambda *a: True)
    monkeypatch.setattr(
        scheduler,
        "_run_external",
        lambda args, **k: subprocess.CompletedProcess(args, 0, "", ""),
    )
    written: list[Path] = []
    real_write = scheduler._write_if_different

    def recording_write(path: Path, content: str) -> bool:
        written.append(Path(path))
        return real_write(path, content)

    monkeypatch.setattr(scheduler, "_write_if_different", recording_write)
    messages: list[str] = []
    ok = scheduler.install_scheduled_task(
        home=home,
        engine_root=engine_root,
        vault_data=vault_data,
        vault=vault,
        branch="main",
        log=messages.append,
    )
    assert ok is True
    assert written, "install should write its wrappers"
    for p in written:
        assert tmp_path in p.resolve().parents or p.resolve() == tmp_path, f"write outside tmp_path: {p}"
    assert (home / ".local" / "state" / "start-agent-sync-hidden.vbs").is_file()
    assert (home / ".local" / "state" / "start-agent-heartbeat-hidden.vbs").is_file()
