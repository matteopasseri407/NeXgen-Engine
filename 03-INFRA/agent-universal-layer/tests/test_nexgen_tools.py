"""Unit test per i tool unificati di nexgen_core/tools/: now, open_folder, chrome, firecrawl."""
from __future__ import annotations

import sys
from pathlib import Path

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

