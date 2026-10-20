"""Le finestre visibili: aperte, mai nascoste, e mai una vera dentro un test.

`os.name` non si cambia per tutto il processo (romperebbe pathlib e i worker
paralleli): si sostituisce solo il modulo `os` visto da `supervision_windows`,
come fa `test_nexgen_no_window.py`. `Popen` e `which` sono finti, quindi nessun
test apre una finestra: quello che si verifica è l'argv e la sua piattaforma.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from nexgen_local import supervision_windows as windows


@pytest.fixture
def spawned(monkeypatch):
    """Ogni avvio viene registrato; nessun processo vero parte."""
    calls: list[tuple[list[str], dict]] = []

    def fake_popen(argv, **kwargs):
        calls.append((list(argv), dict(kwargs)))
        return SimpleNamespace(pid=4242)

    monkeypatch.setattr(windows.subprocess, "Popen", fake_popen)
    return calls


def test_windows_with_windows_terminal_opens_a_dedicated_window(tmp_path, spawned, pretend_os, monkeypatch):
    pretend_os(windows, "nt")
    monkeypatch.setattr(
        windows.shutil, "which",
        lambda name: {"wt.exe": r"C:\wt.exe", "pwsh": r"C:\pwsh.exe"}.get(name),
    )

    handle = windows.open_window(
        "supervisore · prova", ["opencode", "cartella", "--prompt", "brief"], cwd=tmp_path,
    )

    argv, kwargs = spawned[0]
    assert argv[:7] == [r"C:\wt.exe", "-w", "-1", "new-tab", "--title", "supervisore · prova", "-d"]
    assert argv[7] == str(tmp_path)
    assert argv[8:10] == [r"C:\pwsh.exe", "-NoExit"]
    assert argv[10] == "-Command"
    assert argv[11] == "& 'opencode' 'cartella' '--prompt' 'brief'"
    assert kwargs == {}, "wt apre la finestra da sé: nessun flag di console nascosta"
    assert handle["launcher"] == r"C:\wt.exe"
    assert handle["pid"] == 4242


def test_windows_without_windows_terminal_gets_its_own_console(tmp_path, spawned, pretend_os, monkeypatch):
    pretend_os(windows, "nt")
    monkeypatch.setattr(
        windows.shutil, "which",
        lambda name: r"C:\pwsh.exe" if name == "pwsh" else None,
    )

    windows.open_window("supervisore · prova", ["opencode", "cartella"], cwd=tmp_path)

    argv, kwargs = spawned[0]
    assert argv[:2] == [r"C:\pwsh.exe", "-NoExit"]
    assert argv[2] == "-Command"
    script = argv[3]
    assert script.startswith("$Host.UI.RawUI.WindowTitle = 'supervisore · prova'")
    assert "& 'opencode' 'cartella'" in script
    assert kwargs["cwd"] == str(tmp_path)
    assert kwargs["creationflags"] == 0x00000010, "CREATE_NEW_CONSOLE: una console dedicata e visibile"


def test_windows_quotes_an_apostrophe_inside_the_title(tmp_path, spawned, pretend_os, monkeypatch):
    pretend_os(windows, "nt")
    monkeypatch.setattr(
        windows.shutil, "which",
        lambda name: r"C:\pwsh.exe" if name == "pwsh" else None,
    )

    windows.open_window("l'unità dell'esecutore", ["opencode"], cwd=tmp_path)

    script = spawned[0][0][3]
    assert "$Host.UI.RawUI.WindowTitle = 'l''unità dell''esecutore'" in script


def test_windows_without_powershell_refuses_and_starts_nothing(tmp_path, spawned, pretend_os, monkeypatch):
    pretend_os(windows, "nt")
    monkeypatch.setattr(windows.shutil, "which", lambda name: None)

    with pytest.raises(windows.WindowError, match="PowerShell"):
        windows.open_window("supervisore", ["opencode"], cwd=tmp_path)

    assert spawned == [], "un rifiuto non può lasciare un processo nascosto dietro di sé"


def test_linux_with_gnome_terminal_keeps_the_shell_open(tmp_path, spawned, pretend_os, monkeypatch):
    pretend_os(windows, "posix")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(
        windows.shutil, "which",
        lambda name: "/usr/bin/gnome-terminal" if name == "gnome-terminal" else None,
    )

    handle = windows.open_window(
        "supervisore · prova", ["opencode", "cartella", "--prompt", "brief"], cwd=tmp_path,
    )

    argv, kwargs = spawned[0]
    assert argv == [
        "/usr/bin/gnome-terminal", "--title", "supervisore · prova", "--",
        "bash", "-c", "opencode cartella --prompt brief; exec bash",
    ]
    assert kwargs["cwd"] == str(tmp_path)
    assert kwargs["start_new_session"] is True
    assert kwargs["stdin"] is windows.subprocess.DEVNULL
    assert handle["launcher"] == "/usr/bin/gnome-terminal"


def test_linux_with_xterm_uses_its_own_flags(tmp_path, spawned, pretend_os, monkeypatch):
    pretend_os(windows, "posix")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setattr(
        windows.shutil, "which",
        lambda name: "/usr/bin/xterm" if name == "xterm" else None,
    )

    windows.open_window("supervisore", ["opencode"], cwd=tmp_path)

    argv = spawned[0][0]
    assert argv[:5] == ["/usr/bin/xterm", "-T", "supervisore", "-e", "bash"]
    assert argv[5] == "-c"
    assert argv[6] == "opencode; exec bash"


def test_linux_without_a_graphical_session_refuses(tmp_path, spawned, pretend_os, monkeypatch):
    pretend_os(windows, "posix")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(windows.shutil, "which", lambda name: f"/usr/bin/{name}")

    with pytest.raises(windows.WindowError, match="DISPLAY"):
        windows.open_window("supervisore", ["opencode"], cwd=tmp_path)

    assert spawned == [], "senza schermo il motore si ferma, non ripiega in silenzio"


def test_linux_without_any_terminal_refuses(tmp_path, spawned, pretend_os, monkeypatch):
    pretend_os(windows, "posix")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(windows.shutil, "which", lambda name: None)

    with pytest.raises(windows.WindowError, match="terminale grafico"):
        windows.open_window("supervisore", ["opencode"], cwd=tmp_path)

    assert spawned == []


def test_a_missing_working_directory_is_refused(tmp_path, spawned, pretend_os, monkeypatch):
    pretend_os(windows, "nt")
    monkeypatch.setattr(windows.shutil, "which", lambda name: r"C:\pwsh.exe")

    with pytest.raises(windows.WindowError, match="cartella di lavoro inesistente"):
        windows.open_window("supervisore", ["opencode"], cwd=tmp_path / "mai-creata")

    assert spawned == []


def test_an_empty_command_is_refused(tmp_path, spawned):
    with pytest.raises(windows.WindowError, match="comando vuoto"):
        windows.open_window("supervisore", [], cwd=tmp_path)
    assert spawned == []
