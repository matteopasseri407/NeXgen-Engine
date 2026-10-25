"""The pin owns the OpenClaw version, and the gateway does not update itself.

Two updaters on one file is a tug of war: OpenClaw installs a release the engine
never tested against its plugin, and the next setup puts the pin back. The
generated configuration turns OpenClaw's own updater and its start-up check off,
so the engine's pin is the only hand on that wheel.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import i18n  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw.detect import Detection, Installation, Login, LocalModels  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw.profile import build_config  # noqa: E402 - sys.path shim for cloned checkout


@pytest.fixture(autouse=True)
def _english():
    i18n.set_language("en")
    yield
    i18n.set_language(None)


def machine() -> Detection:
    """An installed gateway with no login behind it: the shape is what this test reads."""
    return Detection(
        openclaw=Installation(installed=True, version="2026.10.6", config_path=str(Path("/state/openclaw.json"))),
        claude=Login(binary=False),
        codex=Login(binary=False),
        opencode_go=False,
        local=LocalModels(ollama=()),
        n8n=False,
    )


def test_the_generated_config_leaves_the_version_to_the_pin():
    config = build_config(machine(), current=None)

    assert config["update"]["auto"]["enabled"] is False
    assert config["update"]["checkOnStart"] is False


def test_a_person_who_turned_it_on_does_not_win_either():
    """The section is the engine's: an older file cannot keep an updater alive beside the pin."""
    config = build_config(machine(), current={"update": {"auto": {"enabled": True}, "checkOnStart": True}})

    assert config["update"]["auto"]["enabled"] is False
