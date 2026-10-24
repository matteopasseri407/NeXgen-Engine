"""How the gateway is exposed: one line, and a failure for a served gateway that is wide.

The line exists so that "the gateway is running" never has to be read as "only
I can reach it". The cases that matter are the two ends: loopback is always
safe, and a gateway published through Tailscale Serve while also listening on a
network address is the exact hole Serve was chosen to avoid.
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
from nexgen_core.openclaw.companion import Companion  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw.detect import Detection, Installation, LocalModels, Login  # noqa: E402
from nexgen_core.openclaw.doctor import collect  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw.server_gateway import exposure  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.report import Severity  # noqa: E402 - sys.path shim for cloned checkout


@pytest.fixture(autouse=True)
def _english():
    i18n.set_language("en")
    yield
    i18n.set_language(None)


def gateway(bind: str | None = "loopback", mode: str = "off") -> dict:
    block: dict = {"mode": "local", "port": 18789, "auth": {"mode": "token", "token": "a-real-token"}}
    if bind is not None:
        block["bind"] = bind
    block["tailscale"] = {"mode": mode}
    return {"gateway": block}


def test_loopback_alone_says_nothing_outside_this_machine_can_reach_it():
    outcome = exposure(gateway())

    assert outcome.id == "openclaw.gateway.exposure"
    assert outcome.severity == Severity.OK
    assert "loopback only" in outcome.message


def test_a_served_gateway_says_tailscale_publishes_it_over_https():
    outcome = exposure(gateway(mode="serve"))

    assert outcome.severity == Severity.OK
    assert "Tailscale Serve" in outcome.message


def test_a_served_gateway_that_also_listens_on_the_network_is_a_failure():
    """The port Serve avoids is open again: this is the misconfiguration, not a style."""
    outcome = exposure(gateway(bind="lan", mode="serve"))

    assert outcome.severity == Severity.BROKEN
    assert "loopback" in outcome.message
    assert "gateway.bind" in (outcome.action or "")


def test_a_served_gateway_on_a_wildcard_bind_is_the_same_failure():
    outcome = exposure(gateway(bind="auto", mode="serve"))

    assert outcome.severity == Severity.BROKEN


def test_the_home_network_without_serve_is_reported_as_such():
    outcome = exposure(gateway(bind="lan"))

    assert outcome.severity == Severity.OK
    assert "home network" in outcome.message


def test_every_interface_is_a_warning_because_the_network_decides_who_reaches_it():
    outcome = exposure(gateway(bind="0.0.0.0"))

    assert outcome.severity == Severity.WARN
    assert "every interface" in outcome.message


def test_a_direct_tailnet_bind_is_a_warning_not_a_failure():
    outcome = exposure(gateway(bind="tailnet"))

    assert outcome.severity == Severity.WARN
    assert "gateway.tailscale.mode" in (outcome.action or "")


def test_funnel_is_named_as_the_public_internet():
    outcome = exposure(gateway(mode="funnel"))

    assert outcome.severity == Severity.WARN
    assert "public internet" in outcome.message


def test_a_missing_bind_is_named_instead_of_guessed():
    outcome = exposure({"gateway": {"auth": {"mode": "token", "token": "t"}}})

    assert outcome.severity == Severity.WARN
    assert "(not set)" in outcome.message


def test_the_doctor_report_carries_the_exposure_line(tmp_path):
    """The check is not just a function: it reaches the report a person reads."""
    path = tmp_path / "home" / ".openclaw" / "openclaw.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "gateway": {
            "mode": "local", "port": 18789, "bind": "lan",
            "auth": {"mode": "token", "token": "a-real-token"},
            "tailscale": {"mode": "serve"},
        },
        "agents": {"defaults": {"heartbeat": {"every": "0m"}}, "entries": {}},
    }), encoding="utf-8")
    snapshot = Detection(
        openclaw=Installation(installed=True, version="2026.10.6", config_path=str(path), config_present=True),
        claude=Login(binary=False), codex=Login(binary=False),
        opencode_go=True, local=LocalModels(), n8n=False, companion=Companion(),
    )

    outcomes = collect(
        detected=snapshot, home=tmp_path / "home", vault_data=tmp_path / "vault",
        engine_root=tmp_path / "engine", env={}, platform="linux",
        connect=lambda host, port, timeout: False,
        wsl_distributions=lambda: [],
        node_version=lambda: "v24.22.1",
    )

    found = next(outcome for outcome in outcomes if outcome.id == "openclaw.gateway.exposure")
    assert found.severity == Severity.BROKEN
