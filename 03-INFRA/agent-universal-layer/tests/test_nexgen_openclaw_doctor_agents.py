"""Tests for unconfigured OpenClaw agent folder detection (D3).

nexgen: one-job reason="verify that doctor warns on agent folders not declared in configuration and stays silent otherwise"
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.openclaw.doctor_agents import check_unconfigured_agents  # noqa: E402
from nexgen_core.report import Severity  # noqa: E402


def test_no_agents_folder_is_silent(tmp_path: Path) -> None:
    config = {"agents": {"entries": {"main": {}}}}
    outcome = check_unconfigured_agents(config, tmp_path)
    assert outcome is None


def test_folder_with_entry_is_silent(tmp_path: Path) -> None:
    agents_dir = tmp_path / "agents"
    (agents_dir / "main").mkdir(parents=True)
    config = {"agents": {"entries": {"main": {}}}}
    outcome = check_unconfigured_agents(config, tmp_path)
    assert outcome is None


def test_folder_without_entry_is_named(tmp_path: Path) -> None:
    agents_dir = tmp_path / "agents"
    (agents_dir / "main").mkdir(parents=True)
    (agents_dir / "openclaw").mkdir(parents=True)
    (agents_dir / "siti").mkdir(parents=True)
    # Also ignore non-directories (e.g. stray files in agents dir)
    (agents_dir / "stray_file.txt").write_text("hello", encoding="utf-8")

    config = {"agents": {"entries": {"main": {}}}}
    outcome = check_unconfigured_agents(config, tmp_path)

    assert outcome is not None
    assert outcome.severity == Severity.WARN
    assert outcome.id == "openclaw.unconfigured_agents"
    assert "openclaw" in outcome.message
    assert "siti" in outcome.message
    assert "main" not in outcome.message.split(":")[-1]  # main should not be named in the unconfigured list
    assert outcome.action is not None
    assert len(outcome.action) > 0
    # Must never have an automated remedy that deletes or moves anything
    assert outcome.remedy is None
