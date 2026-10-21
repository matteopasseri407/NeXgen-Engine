"""Le finestre visibili: aperte, mai nascoste, e mai una vera dentro un test.

`os.name` non si cambia per tutto il processo (romperebbe pathlib e i worker
paralleli): si sostituisce solo il modulo `os` visto da `supervision_windows`,
come fa `test_nexgen_no_window.py`. `Popen` e `which` sono finti, quindi nessun
test apre una finestra: quello che si verifica è l'argv e la sua piattaforma.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from nexgen_local import supervision_state as state
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


def test_windows_with_windows_terminal_opens_a_tab_of_the_one_window(tmp_path, spawned, pretend_os, monkeypatch):
    """Una sola finestra di Windows Terminal, chiamata per nome: ogni sessione è una scheda."""
    pretend_os(windows, "nt")
    monkeypatch.setattr(
        windows.shutil, "which",
        lambda name: {"wt.exe": r"C:\wt.exe", "pwsh": r"C:\pwsh.exe"}.get(name),
    )

    handle = windows.open_window(
        "supervisore · prova", ["opencode", "cartella", "--prompt", "brief"], cwd=tmp_path,
    )

    argv, kwargs = spawned[0]
    assert argv[:7] == [r"C:\wt.exe", "-w", windows.WINDOW_NAME, "new-tab", "--title", "supervisore · prova", "-d"]
    assert argv[2] == "nexgen-sup", "il nome della finestra è fisso: le schede si accumulano lì"
    assert "-1" not in argv, "niente finestre nuove a ogni unità"
    assert argv[7] == str(tmp_path)
    assert argv[8:10] == [r"C:\pwsh.exe", "-NoExit"]
    assert argv[10] == "-Command"
    assert argv[11] == "& 'opencode' 'cartella' '--prompt' 'brief'"
    assert kwargs == {}, "wt apre la finestra da sé: nessun flag di console nascosta"
    assert handle["launcher"] == r"C:\wt.exe"
    assert handle["pid"] == 4242


def test_windows_terminal_keeps_a_semicolon_inside_its_arguments(tmp_path, spawned, pretend_os, monkeypatch):
    """wt tratta `;` come separatore dei suoi sottocomandi: nei valori va scritto `\\;`.

    Senza escape la riga viene spezzata, il resto parte come comando a sé e la
    finestra non esegue quello che le è stato chiesto.
    """
    pretend_os(windows, "nt")
    folder = tmp_path / "compito; uno"
    folder.mkdir()
    monkeypatch.setattr(
        windows.shutil, "which",
        lambda name: {"wt.exe": r"C:\wt.exe", "pwsh": r"C:\pwsh.exe"}.get(name),
    )

    windows.open_window("prova; finestra", ["opencode", "--prompt", "prima; seconda"], cwd=folder)

    argv = spawned[0][0]
    assert argv[4:6] == ["--title", "prova\\; finestra"]
    assert argv[6] == "-d"
    assert argv[7].count(";") == argv[7].count("\\;"), "una cartella con un punto e virgola va protetta come il resto"
    assert argv[7].replace("\\;", ";") == str(folder)
    assert argv[-1] == "& 'opencode' '--prompt' 'prima\\; seconda'"


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


# --- la scheda che si chiude quando il lavoro è finito ----------------------


@pytest.fixture
def lane(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "lane_state_dir", lambda: tmp_path / "lane")
    return tmp_path / "lane" / "supervision"


@pytest.fixture
def task(lane):
    return state.start("prova schede")["id"]


def _tab_rows(marker: str, *, serve: bool = False, python: bool = False) -> list[dict]:
    """The tree of one tab: shell, the npm shim, the harness, and what it started."""
    rows = [
        {
            "pid": 100, "ppid": 1, "name": "pwsh.exe",
            "command_line": f"pwsh.exe -NoExit -Command \"$env:NEXGEN_SUP_TAB = '{marker}'\"",
        },
        {"pid": 200, "ppid": 100, "name": "cmd.exe", "command_line": "opencode.CMD"},
        {"pid": 300, "ppid": 200, "name": "opencode.exe", "command_line": "opencode.exe C:\\copia --prompt ..."},
    ]
    if serve:
        rows.append({"pid": 400, "ppid": 300, "name": "opencode.exe", "command_line": "opencode.exe serve --service"})
    if python:
        rows.append({"pid": os.getpid(), "ppid": 300, "name": "python.exe", "command_line": "python -c ..."})
    return rows


def test_a_finished_tab_is_closed_from_the_harness_up_to_its_shell(task, pretend_os):
    """Prima i figli, poi la shell: uccisa la shell la scheda sparisce, e i suoi figli no."""
    pretend_os(windows, "nt")
    killed: list = []

    result = windows.close_tab(
        task, "esecutore", lister=lambda: _tab_rows(windows.tab_marker(task, "esecutore")),
        killer=killed.extend,
    )

    assert killed == [300, 200, 100]
    assert result["closed"] == [300, 200, 100]
    assert "scheda esecutore chiusa" in result["line"]
    events = [row for row in state.read_events(task) if row.get("event") == "tab_closed"]
    assert events and events[0]["role"] == "esecutore"
    assert events[0]["pids"] == [300, 200, 100]


def test_a_tab_holding_the_shared_server_is_left_open_and_said(task, pretend_os):
    """La prima scheda del giorno tiene in piedi il servizio di tutti: chiuderla li spegnerebbe."""
    pretend_os(windows, "nt")
    killed: list = []

    result = windows.close_tab(
        task, "esecutore",
        lister=lambda: _tab_rows(windows.tab_marker(task, "esecutore"), serve=True),
        killer=killed.extend,
    )

    assert killed == [], "nessun processo toccato quando c'è di mezzo il servizio condiviso"
    assert "servizio condiviso" in result["reason"] and "400" in result["reason"]
    assert "lasciata aperta" in result["line"]
    left = [row for row in state.read_events(task) if row.get("event") == "tab_left_open"]
    assert left and left[0]["pids"] == [400, 300, 200, 100]


def test_a_tab_that_is_not_there_is_not_an_error(task, pretend_os):
    pretend_os(windows, "nt")
    killed: list = []

    result = windows.close_tab(task, "esecutore", lister=lambda: [], killer=killed.extend)

    assert killed == []
    assert "nessuna scheda" in result["line"]
    assert not [row for row in state.read_events(task) if row.get("event") in ("tab_closed", "tab_left_open")]


def test_the_process_asking_to_close_is_never_killed(task, pretend_os):
    """La scheda dello scout chiude se stessa: il processo che chiede resta in vita per finire."""
    pretend_os(windows, "nt")
    killed: list = []

    windows.close_tab(
        task, "scout",
        lister=lambda: _tab_rows(windows.tab_marker(task, "scout"), python=True),
        killer=killed.extend,
    )

    assert os.getpid() not in killed
    assert killed == [300, 200, 100]


def test_a_process_list_that_cannot_be_read_kills_nothing(task, pretend_os):
    pretend_os(windows, "nt")
    killed: list = []

    def broken() -> list:
        raise windows.WindowError("PowerShell non ha risposto (TimeoutExpired)")

    result = windows.close_tab(task, "esecutore", lister=broken, killer=killed.extend)

    assert killed == []
    assert "lasciata aperta" in result["line"] and "non leggibili" in result["line"]


def test_a_command_line_that_only_quotes_the_marker_is_not_a_shell(task, pretend_os):
    """Un `grep`, un editor o un `cmd` che citano il marcatore non sono la scheda."""
    pretend_os(windows, "nt")
    marker = windows.tab_marker(task, "esecutore")
    rows = [
        {"pid": 500, "ppid": 1, "name": "grep.exe", "command_line": f"grep -r {marker} C:/copia"},
        {"pid": 501, "ppid": 1, "name": "pwsh.exe", "command_line": f"pwsh -Command \"Write-Output '{marker}'\""},
        {"pid": 502, "ppid": 1, "name": "cmd.exe", "command_line": f"cmd /c echo \"$env:NEXGEN_SUP_TAB = '{marker}'\""},
    ]
    killed: list = []

    result = windows.close_tab(task, "esecutore", lister=lambda: rows, killer=killed.extend)

    assert killed == []
    assert "nessuna scheda" in result["line"]
    assert not [row for row in state.read_events(task) if row.get("event") in ("tab_closed", "tab_left_open")]


def test_the_tab_carries_the_marker_the_closer_looks_for(tmp_path, spawned, pretend_os, monkeypatch):
    pretend_os(windows, "nt")
    monkeypatch.setattr(
        windows.shutil, "which",
        lambda name: {"wt.exe": r"C:\wt.exe", "pwsh": r"C:\pwsh.exe"}.get(name),
    )

    handle = windows.open_window(
        "esecutore · prova", ["opencode"], cwd=tmp_path,
        marker=windows.tab_marker("20260101-000000-prova", "esecutore"),
    )

    script = spawned[0][0][11]
    assert script.startswith("$env:NEXGEN_SUP_TAB = 'nexgen-sup:20260101-000000-prova:esecutore'")
    assert "& 'opencode'" in script
    assert handle["marker"] == "nexgen-sup:20260101-000000-prova:esecutore"
