"""Unit test per i tool unificati di nexgen_core/tools/: now, open_folder, chrome, firecrawl."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.tools.chrome import get_profile_dir
from nexgen_core.tools.firecrawl import FirecrawlClient
from nexgen_core.tools.now import format_human, format_shell, get_agent_now_data
from nexgen_core.tools.open_folder import open_folder


def test_agent_now_13_fields():
    data = get_agent_now_data()
    expected_keys = {
        "source", "local_time", "utc_time", "timezone", "epoch_seconds",
        "date", "time", "year", "weekday", "ntp_synchronized",
        "ntp_enabled", "can_ntp", "local_rtc"
    }
    assert set(data.keys()) == expected_keys
    assert data["source"] == "system_clock"
    assert data["year"] >= 2026
    assert isinstance(data["epoch_seconds"], int)

    # Formattazione human e shell
    human = format_human(data)
    assert "Local:" in human
    assert "NTP:" in human

    shell = format_shell(data)
    assert "AGENT_NOW_LOCAL_ISO=" in shell
    assert "AGENT_NOW_YEAR=" in shell


def test_open_folder_validation(tmp_path: Path):
    # Percorso relativo deve fallire con codice 2
    assert open_folder("relative/path") == 2
    # Percorso inesistente deve fallire con codice 2
    assert open_folder(str(tmp_path / "non_existent")) == 2


def test_chrome_profile_resolution():
    profile = get_profile_dir()
    assert "chrome-agent-debug" in str(profile)


def test_firecrawl_client_structure():
    client = FirecrawlClient(api_url="http://127.0.0.1:33002", api_key="test-key")
    assert client.api_url == "http://127.0.0.1:33002"
    assert client.api_key == "test-key"


def test_shims_installation(tmp_path: Path):
    from nexgen_core.shims import install_shims
    bin_dir = tmp_path / "bin"
    home = tmp_path / "home"
    installed = install_shims(scripts_dir=SCRIPTS_DIR, bin_dir=bin_dir, home=home)
    assert len(installed) >= 7
    assert (bin_dir / "agent-sync").exists() or (bin_dir / "agent-sync.cmd").exists()
    assert (bin_dir / "agent-doctor").exists() or (bin_dir / "agent-doctor.cmd").exists()


def test_vault_map_default_path(tmp_path: Path, monkeypatch):
    from nexgen_core.tools.vault_map import main as vault_map_main
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    (vault_dir / "00-START-HERE.md").write_text("# Start\n\n[[note-a]]\n", encoding="utf-8")
    (vault_dir / "note-a.md").write_text("# Note A\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_VAULT_DATA", str(vault_dir))
    exit_code = vault_map_main(["--check"])
    assert exit_code == 0


# --- Una macchina nuova, che non ha ancora niente --------------------------
#
# Questi girano in un processo separato con l'ambiente ripulito, e non è un
# dettaglio: chiamare le stesse funzioni in-process le trova già importate, e
# il difetto che cercano è proprio l'import che non riesce. `install.sh
# --check` andava in crash su qualsiasi clone pulito, e in-process passava.

REPO_ROOT = SCRIPTS_DIR.parents[1]

#: Ogni file che qualcuno esegue per percorso invece che come comando
#: installato: l'installer, i launcher storici, i punti d'ingresso che altri
#: componenti invocano. Nessuno di loro può contare su un sys.path preparato.
ENTRY_POINTS_RUN_BY_PATH = (
    "03-INFRA/scripts/nexgen_core/bootstrap.py",
    "03-INFRA/scripts/nexgen_core/cli/__init__.py",
    "03-INFRA/scripts/nexgen_core/legacy_launchers.py",
    "03-INFRA/scripts/agent_sync.py",
    "03-INFRA/scripts/agent-skill.py",
    "03-INFRA/scripts/skills-sync.py",
    "03-INFRA/scripts/vault-map.py",
    "03-INFRA/scripts/firecrawl-search-health.py",
    "03-INFRA/scripts/check_required_rules.py",
    "03-INFRA/agent-universal-layer/mcp/render.py",
)


def _clean_env(home: Path) -> dict:
    """L'ambiente di chi ha appena clonato: niente PYTHONPATH, niente scorciatoie."""
    env = {
        k: v for k, v in os.environ.items()
        if k not in ("PYTHONPATH", "AGENT_ENGINE_ROOT", "AGENT_STATE_DIR",
                     "AGENT_VAULT_DATA", "KNOWLEDGE_VAULT_PATH", "XDG_STATE_HOME")
    }
    env["NEXGEN_HOME"] = str(home)  # niente di questo tocca la macchina vera
    return env


@pytest.mark.parametrize("relative", ENTRY_POINTS_RUN_BY_PATH)
def test_it_starts_on_a_machine_that_has_nothing(relative: str, tmp_path: Path):
    """Eseguito per percorso, senza nessun sys.path preparato, deve partire.

    Il difetto reale: bootstrap.py importava nexgen_core prima di mettere
    03-INFRA/scripts in sys.path. Sulle macchine dove qualcuno aveva fatto
    `pip install -e .` funzionava lo stesso, e nascondeva il guasto a tutti
    gli altri — cioè a chiunque installasse per la prima volta.
    """
    target = REPO_ROOT / relative
    assert target.is_file(), f"{relative} non esiste piu'"

    result = subprocess.run(
        [sys.executable, "-E", str(target), "--help"],
        capture_output=True, text=True, timeout=90, check=False,
        cwd=tmp_path,  # lontano dal repo: la cwd non deve salvarlo
        env=_clean_env(tmp_path / "home"),
    )
    assert "ModuleNotFoundError" not in result.stderr, (
        f"{relative} non si importa da solo su una macchina pulita:\n{result.stderr}"
    )
    assert "Traceback" not in result.stderr, f"{relative} muore all'avvio:\n{result.stderr}"


def test_the_installer_gets_all_the_way_through_its_checks(tmp_path: Path):
    """`sh install.sh --check` e' la prima cosa che un utente nuovo esegue."""
    result = subprocess.run(
        ["sh", str(REPO_ROOT / "install.sh"), "--check"],
        capture_output=True, text=True, timeout=180, check=False,
        cwd=tmp_path,
        env=_clean_env(tmp_path / "home"),
    )
    assert "Traceback" not in result.stderr, f"l'installer muore:\n{result.stderr}"
    assert result.stdout.strip(), "l'installer non ha detto niente"


def test_checking_writes_nothing_at_all(tmp_path: Path):
    """`--check` promette di non scrivere, e deve mantenerlo.

    Non e' teoria: una versione precedente installava i launcher lo stesso e
    ha sovrascritto i comandi veri di questa macchina.
    """
    home = tmp_path / "home"
    home.mkdir()
    subprocess.run(
        ["sh", str(REPO_ROOT / "install.sh"), "--check"],
        capture_output=True, text=True, timeout=180, check=False,
        cwd=tmp_path, env=_clean_env(home),
    )
    written = [str(p.relative_to(home)) for p in home.rglob("*")]
    assert not written, f"--check ha scritto: {written}"


def test_heal_chrome_powershell_escaping(monkeypatch, tmp_path: Path):
    from nexgen_core.tools import chrome

    captured_cmds = []

    def mock_run(cmd, **kwargs):
        captured_cmds.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(chrome, "is_cdp_up", lambda: False)
    monkeypatch.setattr(chrome, "singleton_owner_pid", lambda p: 1234)
    monkeypatch.setattr(chrome, "launch_chrome", lambda extra: 0)
    monkeypatch.setattr(chrome.subprocess, "run", mock_run)
    monkeypatch.setattr(chrome.sys, "platform", "win32")

    # Set profile with single quotes
    mock_profile = tmp_path / "Mario's Profile"
    monkeypatch.setattr(chrome, "get_profile_dir", lambda: mock_profile)

    exit_code = chrome.heal_chrome()
    assert exit_code == 0
    assert len(captured_cmds) == 1
    cmd_str = captured_cmds[0][3]  # The -Command argument
    assert "Mario''s Profile" in cmd_str

