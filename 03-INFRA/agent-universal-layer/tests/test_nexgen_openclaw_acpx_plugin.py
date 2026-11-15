"""The acpx plugin line: what is installed, and when it no longer matches the lanes.

The plugin is OpenClaw's, not the engine's, so the only things worth checking are
the ones whose change breaks a lane the configuration already allows: a version
older than the one the lanes were verified against, a version nobody can read,
and an adapter that disappeared from the plugin's own dependencies.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import i18n  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw.acpx_plugin import (  # noqa: E402 - sys.path shim for cloned checkout
    ADAPTERS,
    MIN_VERIFIED_VERSION,
    Plugin,
    check,
    read_plugin,
)
from nexgen_core.report import Severity  # noqa: E402 - sys.path shim for cloned checkout


@pytest.fixture(autouse=True)
def _english():
    """The source language, whatever the machine reads: these lines are English by construction."""
    i18n.set_language("en")
    yield
    i18n.set_language(None)


def plugin_folder(state: Path) -> Path:
    folder = state / "extensions" / "acpx"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def write_plugin(
    state: Path,
    *,
    version: str = MIN_VERIFIED_VERSION,
    dependencies: dict[str, str] | None = None,
    harnesses: list[str] | None = None,
) -> Path:
    folder = plugin_folder(state)
    declared = dependencies if dependencies is not None else {
        ADAPTERS["claude"]: "0.85.1", ADAPTERS["codex"]: "2.1.1", "acpx": "0.19.4",
    }
    (folder / "package.json").write_text(
        json.dumps({"name": "@openclaw/acpx", "version": version, "dependencies": declared}),
        encoding="utf-8",
    )
    (folder / "openclaw.plugin.json").write_text(
        json.dumps({"id": "acpx", "activation": {"onAgentHarnesses": harnesses or [
            "acp-opencode", "acp-qwen", "acp-pi", "acp-kilocode", "acp-copilot",
        ]}}),
        encoding="utf-8",
    )
    return folder


def lanes(*allowed: str) -> dict:
    """The configuration the engine writes when an ACP lane exists."""
    return {
        "plugins": {"allow": ["browser", "acpx"], "entries": {"acpx": {"enabled": True}}},
        "acp": {"enabled": True, "backend": "acpx", "allowedAgents": list(allowed)},
    }


def test_a_plugin_that_carries_the_adapters_passes(tmp_path):
    write_plugin(tmp_path / "state")

    outcome = check(lanes("claude", "opencode"), tmp_path / "state")

    assert outcome.severity == Severity.OK
    assert outcome.id == "openclaw.acp.acpx"
    assert MIN_VERIFIED_VERSION in outcome.message
    assert "acp-opencode" in outcome.message


def test_an_opencode_lane_needs_no_adapter_package(tmp_path):
    """The engine pins the OpenCode command itself, so only claude and codex need a package."""
    write_plugin(tmp_path / "state", dependencies={"acpx": "0.19.4"})

    outcome = check(lanes("opencode"), tmp_path / "state")

    assert outcome.severity == Severity.OK


def test_an_adapter_the_plugin_no_longer_declares_is_named(tmp_path):
    write_plugin(tmp_path / "state", dependencies={"acpx": "0.19.4"})

    outcome = check(lanes("claude", "opencode"), tmp_path / "state")

    assert outcome.severity == Severity.WARN
    assert "no adapter for 'claude'" in outcome.message
    assert "openclaw plugins update acpx" in (outcome.action or "")


def test_a_version_older_than_the_verified_one_warns(tmp_path):
    write_plugin(tmp_path / "state", version="2026.9.1")

    outcome = check(lanes("claude"), tmp_path / "state")

    assert outcome.severity == Severity.WARN
    assert "older than the 2026.10.6" in outcome.message


def test_a_newer_catalogue_plugin_is_never_a_finding(tmp_path):
    """A normal `openclaw update` leaves a newer plugin behind: that is not drift."""
    write_plugin(tmp_path / "state", version="2027.1.4")

    outcome = check(lanes("claude", "codex"), tmp_path / "state")

    assert outcome.severity == Severity.OK


def test_a_lane_without_its_plugin_is_named_with_the_install_command(tmp_path):
    outcome = check(lanes("claude"), tmp_path / "state")

    assert outcome.severity == Severity.WARN
    assert "no acpx plugin is installed" in outcome.message
    assert "openclaw plugins install @openclaw/acpx" in (outcome.action or "")


def test_an_unreadable_package_is_reported_and_never_guessed(tmp_path):
    folder = plugin_folder(tmp_path / "state")
    (folder / "package.json").write_text("{not json", encoding="utf-8")

    outcome = check(lanes("claude"), tmp_path / "state")

    assert outcome.severity == Severity.WARN
    assert "cannot be read" in outcome.message


def test_a_plugin_standing_by_with_no_lane_says_nothing(tmp_path):
    write_plugin(tmp_path / "state")

    assert check({"plugins": {"allow": ["browser"]}}, tmp_path / "state") is None


def test_a_plugin_that_declares_no_version_is_reported_that_way(tmp_path):
    write_plugin(tmp_path / "state", version="")

    outcome = check(lanes("claude"), tmp_path / "state")

    assert outcome.severity == Severity.WARN
    assert "declares no version" in outcome.message


def test_read_plugin_reports_the_version_the_adapters_and_the_harness_list(tmp_path):
    write_plugin(tmp_path / "state", version="2026.10.6", harnesses=["acp-opencode"])

    plugin = read_plugin(tmp_path / "state")

    assert plugin == Plugin(
        version="2026.10.6",
        dependencies=(ADAPTERS["claude"], ADAPTERS["codex"], "acpx"),
        harnesses=("acp-opencode",),
    )


def test_read_plugin_says_none_when_there_is_no_plugin(tmp_path):
    assert read_plugin(tmp_path / "state") is None
