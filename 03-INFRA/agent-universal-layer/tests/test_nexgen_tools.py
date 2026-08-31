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
    assert "chrome-agent-debug" in str(profile) or "ChromeDebugProfile" in str(profile)


def test_chrome_profile_env_override(monkeypatch):
    monkeypatch.setenv("AGENT_CHROME_PROFILE", "/custom/chrome/profile")
    assert get_profile_dir() == Path("/custom/chrome/profile")


def test_chrome_profile_windows_canonical(monkeypatch, tmp_path: Path):
    from nexgen_core.tools import chrome

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    canonical = fake_home / "ChromeDebugProfile"
    canonical.mkdir()

    monkeypatch.delenv("AGENT_CHROME_PROFILE", raising=False)
    monkeypatch.setattr(chrome.sys, "platform", "win32")
    monkeypatch.setattr(chrome, "resolve_home", lambda: fake_home)

    assert chrome.get_profile_dir() == canonical


def test_chrome_profile_windows_fallback(monkeypatch, tmp_path: Path):
    from nexgen_core.tools import chrome

    fake_home = tmp_path / "home"
    fake_home.mkdir()

    monkeypatch.delenv("AGENT_CHROME_PROFILE", raising=False)
    monkeypatch.setattr(chrome.sys, "platform", "win32")
    monkeypatch.setattr(chrome, "resolve_home", lambda: fake_home)
    monkeypatch.setenv("LOCALAPPDATA", str(fake_home / "AppData" / "Local"))

    assert chrome.get_profile_dir() == fake_home / "AppData" / "Local" / "Google" / "Chrome" / "User Data" / "chrome-agent-debug"


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
    """`install` `--check` e' la prima cosa che un utente nuovo esegue."""
    if sys.platform == "win32":
        cmd = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(REPO_ROOT / "install.ps1"), "--check"]
    else:
        cmd = ["sh", str(REPO_ROOT / "install.sh"), "--check"]
    result = subprocess.run(
        cmd,
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
    if sys.platform == "win32":
        cmd = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(REPO_ROOT / "install.ps1"), "--check"]
    else:
        cmd = ["sh", str(REPO_ROOT / "install.sh"), "--check"]
    subprocess.run(
        cmd,
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



# --- vault-map --context: il quartiere di una nota, read-only ---------------

def _context_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "00-START-HERE.md").write_text("# Start\n\n[[hub]]\n", encoding="utf-8")
    (vault / "hub.md").write_text("# Hub\n\n[[vicino-a]]\n[[vicino-b]]\n", encoding="utf-8")
    (vault / "vicino-a.md").write_text("# Vicino A\n\n[[fondolina]]\n", encoding="utf-8")
    (vault / "vicino-b.md").write_text("# Vicino B\n", encoding="utf-8")
    (vault / "fondolina.md").write_text("# Fondolina\n", encoding="utf-8")
    (vault / "isolata.md").write_text("# Isolata\n", encoding="utf-8")
    return vault


def test_context_lists_the_neighbourhood_by_hops(tmp_path: Path):
    from nexgen_core.tools.vault_map import build_context

    result = build_context(_context_vault(tmp_path), "hub", hops=1, max_nodes=10)

    paths = [n["path"] for n in result["nodes"]]
    assert paths[0] == "hub.md"
    assert "vicino-a.md" in paths and "vicino-b.md" in paths
    assert "00-START-HERE.md" in paths  # inbound conta quanto outbound
    assert "fondolina.md" not in paths, "a 1 hop non si arriva al secondo anello"
    assert "isolata.md" not in paths


def test_context_at_two_hops_reaches_the_second_ring(tmp_path: Path):
    from nexgen_core.tools.vault_map import build_context

    result = build_context(_context_vault(tmp_path), "hub", hops=2, max_nodes=10)

    paths = [n["path"] for n in result["nodes"]]
    assert "fondolina.md" in paths
    deep = next(n for n in result["nodes"] if n["path"] == "fondolina.md")
    assert deep["depth"] == 2
    assert deep["via"] == "vicino-a.md"


def test_context_resolves_stem_and_title_and_caps_nodes(tmp_path: Path):
    from nexgen_core.tools.vault_map import build_context

    vault = _context_vault(tmp_path)

    by_stem = build_context(vault, "vicino-b", hops=1, max_nodes=10)
    assert by_stem["nodes"][0]["path"] == "vicino-b.md"
    by_title = build_context(vault, "Fondolina", hops=1, max_nodes=10)
    assert by_title["nodes"][0]["path"] == "fondolina.md"

    capped = build_context(vault, "hub", hops=2, max_nodes=2)
    assert len(capped["nodes"]) <= 3  # il nodo di contesto + 2 vicini


def test_context_read_only_and_missing_note(tmp_path: Path, capsys):
    import json as jsonlib

    from nexgen_core.tools.vault_map import main as map_main

    vault = _context_vault(tmp_path)
    before = {p.name: p.read_text(encoding="utf-8") for p in vault.glob("*.md")}

    assert map_main(["--vault", str(vault), "--context", "hub", "--hops", "2"]) == 0
    after = {p.name: p.read_text(encoding="utf-8") for p in vault.glob("*.md")}
    assert before == after
    capsys.readouterr()  # scarica l'output umano: il JSON deve restare puro

    assert map_main(["--vault", str(vault), "--context", "hub", "--json"]) == 0
    payload = jsonlib.loads(capsys.readouterr().out)
    assert payload["context"] == "hub.md"
    assert {"path", "depth", "direction", "via"} <= set(payload["nodes"][0].keys())

    assert map_main(["--vault", str(vault), "--context", "inesistente"]) == 3


def test_context_rejects_negative_hops(tmp_path: Path):
    """Un valore assurdo è un errore, non un quartiere vuoto."""
    import pytest

    from nexgen_core.tools.vault_map import build_context

    with pytest.raises(ValueError):
        build_context(_context_vault(tmp_path), "hub", hops=-1)


def test_update_notifier_check(monkeypatch, tmp_path: Path):
    from nexgen_core.tools import update_notifier

    monkeypatch.setattr(update_notifier, "HOME", tmp_path)
    monkeypatch.setattr(update_notifier, "THROTTLE_FILE", tmp_path / "throttle.json")

    # Mock no updates
    monkeypatch.setattr("nexgen_core.updater.EngineUpdater.check_updates", lambda: (False, "v2.1.4", "v2.1.4"))
    assert update_notifier.cmd_check(force=True) == 0

    # Mock update available with prompt declined
    monkeypatch.setattr("nexgen_core.updater.EngineUpdater.check_updates", lambda: (True, "v2.1.4", "v2.1.5"))
    monkeypatch.setattr(update_notifier, "_prompt_user", lambda curr, lat: False)
    assert update_notifier.cmd_check(force=True) == 0
    assert (tmp_path / "throttle.json").is_file()

