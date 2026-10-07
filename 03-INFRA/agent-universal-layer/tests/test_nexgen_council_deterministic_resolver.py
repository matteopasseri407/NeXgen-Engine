"""Tests for the pure, deterministic Council resolver and host snapshotting."""
from __future__ import annotations

import sys
from pathlib import Path
import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "scripts"
COUNCIL_DIR = Path(__file__).resolve().parents[1] / "council"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
if str(COUNCIL_DIR) not in sys.path:
    sys.path.insert(0, str(COUNCIL_DIR))

from routing import (  # noqa: E402
    HostSnapshot,
    ResolvePolicy,
    RoutingCandidate,
    RoutingPlan,
    SeatCapability,
    resolve,
)


@pytest.fixture
def sample_seats():
    return {
        "claude-seat": {
            "vendor": "anthropic",
            "cli": "claude",
            "model": "claude-opus-5-5",
            "routing_label": "Claude Opus 5.5",
            "zero_retention": False,
        },
        "gemini-seat": {
            "vendor": "google",
            "cli": "agy",
            "model": "gemini-3.8-flash-high",
            "routing_label": "Gemini 3.8 Flash",
            "zero_retention": True,
        },
        "codex-seat": {
            "vendor": "openai",
            "cli": "codex",
            "model": "gpt-6-luna",
            "routing_label": "GPT-6 Luna",
            "zero_retention": False,
        },
    }


@pytest.fixture
def sample_plan():
    return RoutingPlan(
        source="test-plan",
        roles={
            "L-Arch": (
                RoutingCandidate("Claude Opus 5.5", cli="claude", cost="forfait"),
                RoutingCandidate("Gemini 3.8 Flash", cli="agy", cost="forfait"),
            ),
            "L-Code": (
                RoutingCandidate("GPT-6 Luna", cli="codex", cost="forfait"),
            ),
            "Empty-Role": (),
        },
    )


@pytest.fixture
def all_available_snapshot(sample_seats):
    return HostSnapshot(
        host_name="test-host",
        capabilities={name: SeatCapability(True, "available") for name in sample_seats},
        timestamp=1000.0,
        unhealthy_seats={},
    )


def test_pure_resolver_determinism(sample_plan, all_available_snapshot, sample_seats):
    """Identical inputs must produce identical outputs byte-for-byte."""
    policy = ResolvePolicy()
    res1 = resolve(sample_plan, all_available_snapshot, sample_seats, "L-Arch", policy)
    res2 = resolve(sample_plan, all_available_snapshot, sample_seats, "L-Arch", policy)
    assert res1.seat_name == "claude-seat"
    assert res1.degraded is False
    assert res1.refusal_reason is None
    assert res1 == res2


def test_zero_retention_fail_closed(sample_plan, all_available_snapshot, sample_seats):
    """When zero-retention is required and primary is not ZDR, skip to ZDR candidate."""
    policy = ResolvePolicy(zero_retention_required=True)
    res = resolve(sample_plan, all_available_snapshot, sample_seats, "L-Arch", policy)
    # Primary (claude) has zero_retention=False, so gemini (zero_retention=True) must be chosen
    assert res.seat_name == "gemini-seat"
    assert res.seat["zero_retention"] is True


def test_zero_retention_refusal_when_no_zdr_available(sample_plan, all_available_snapshot, sample_seats):
    """When zero-retention is required and NO candidate is ZDR, fail-closed refusal."""
    policy = ResolvePolicy(zero_retention_required=True, allow_role_fallback=False)
    # L-Code has only GPT-6 Luna which has zero_retention=False
    res = resolve(sample_plan, all_available_snapshot, sample_seats, "L-Code", policy)
    assert res.seat_name is None
    assert "satisfying policy constraints" in res.refusal_reason


def test_author_vendor_exclusion_and_diversity(sample_plan, all_available_snapshot, sample_seats):
    """Reviewing author_vendor must exclude candidates from that vendor."""
    policy = ResolvePolicy(author_vendor="anthropic")
    res = resolve(sample_plan, all_available_snapshot, sample_seats, "L-Arch", policy)
    # Primary anthropic is excluded; gemini (google) is selected
    assert res.seat_name == "gemini-seat"
    assert res.seat["vendor"] == "google"


def test_author_vendor_exhaustion_does_not_reintroduce_author(sample_plan, all_available_snapshot, sample_seats):
    """If all role candidates match author_vendor, do not select author vendor even in fallback."""
    policy = ResolvePolicy(author_vendor="openai", allow_role_fallback=True)
    res = resolve(sample_plan, all_available_snapshot, sample_seats, "L-Code", policy)
    # L-Code has openai. Fallback must NOT pick openai!
    assert res.seat_name != "codex-seat"
    assert res.seat["vendor"] != "openai"
    assert res.degraded is True


def test_error_driven_cooldown(sample_plan, sample_seats):
    """Seats in active error cooldown are skipped in favor of the next candidate."""
    snapshot = HostSnapshot(
        host_name="test-host",
        capabilities={name: SeatCapability(True, "available") for name in sample_seats},
        timestamp=1000.0,
        unhealthy_seats={"claude-seat": 1200.0},  # active cooldown
    )
    policy = ResolvePolicy()
    res = resolve(sample_plan, snapshot, sample_seats, "L-Arch", policy)
    assert res.seat_name == "gemini-seat"


def test_role_fallback_marks_degraded(sample_plan, sample_seats):
    """When role has no candidates on host, fallback activates with degraded=True."""
    # Host where claude and agy are unavailable
    snapshot = HostSnapshot(
        host_name="test-host",
        capabilities={
            "claude-seat": SeatCapability(False, "missing CLI"),
            "gemini-seat": SeatCapability(False, "missing CLI"),
            "codex-seat": SeatCapability(True, "available"),
        },
        timestamp=1000.0,
        unhealthy_seats={},
    )
    policy = ResolvePolicy(allow_role_fallback=True)
    res = resolve(sample_plan, snapshot, sample_seats, "L-Arch", policy)
    assert res.seat_name == "codex-seat"
    assert res.degraded is True
    assert "Role 'L-Arch' had no directly approved candidates" in res.degraded_reason

def test_pure_resolve_zero_io(sample_plan, all_available_snapshot, sample_seats, monkeypatch):
    """resolve() must never touch time.time, open, or os.environ: 100% pure."""
    import builtins
    import os
    import time

    def forbidden(*args, **kwargs):
        raise RuntimeError("I/O forbidden inside pure resolve function")

    monkeypatch.setattr(time, "time", forbidden)
    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(os, "getenv", forbidden)

    policy = ResolvePolicy()
    res = resolve(sample_plan, all_available_snapshot, sample_seats, "L-Arch", policy)
    assert res.seat_name == "claude-seat"
    assert res.degraded is False


def test_cli_error_classification_real_strings():
    """Strict precedence and regex word boundary error classification."""
    from routing import classify_error

    # Real message from Anthropic CLI when model requires credits
    fable_err = (
        "Fable 5.1 requires usage credits. Switch to another model, "
        "or manage usage credits at claude.ai/settings/usage?from=cc_cli_limit_message, to continue."
    )
    kind, ttl = classify_error(fable_err)
    assert kind == "billing_required"
    assert ttl == 86400 * 30

    # 429 quota exhaustion
    kind, ttl = classify_error("Error 429: rate limit exceeded, quota exhausted")
    assert kind == "quota_exhausted"
    assert ttl == 300

    # 401 auth
    kind, ttl = classify_error("Error 401: unauthorized, invalid api key")
    assert kind == "auth_failed"
    assert ttl == 3600

    # 404 model not found
    kind, ttl = classify_error("Error 404: model does not exist or unrecognized model")
    assert kind == "model_not_found"
    assert ttl == 86400

    # generic crash
    kind, ttl = classify_error("Process killed by SIGTERM")
    assert kind == "execution_error"
    assert ttl == 60


def test_allow_role_fallback_false_refusal(sample_plan, sample_seats):
    """When allow_role_fallback=False and no approved candidate is available, fail closed."""
    snapshot = HostSnapshot(
        host_name="test-host",
        capabilities={
            "claude-seat": SeatCapability(False, "missing CLI"),
            "gemini-seat": SeatCapability(False, "missing CLI"),
            "codex-seat": SeatCapability(True, "available"),
        },
        timestamp=1000.0,
        unhealthy_seats={},
    )
    policy = ResolvePolicy(allow_role_fallback=False)
    res = resolve(sample_plan, snapshot, sample_seats, "L-Arch", policy)
    assert res.seat_name is None
    assert res.degraded is False
    assert "No candidate for role 'L-Arch' is available" in res.refusal_reason


def test_end_to_end_cooldown_integration(sample_plan, sample_seats, tmp_path):
    """record_seat_failure records cooldown TTL which probe_host/resolve honors."""
    from routing import record_seat_failure
    health_file = tmp_path / "council_seat_health.json"

    # Record failure for claude-seat
    kind, ttl = record_seat_failure(
        "claude-seat",
        "requires usage credits to continue",
        now=1000.0,
        unhealthy_path=health_file,
    )
    assert kind == "billing_required"
    assert ttl == 86400 * 30

    # Snapshot built with this health data
    import json
    unhealthy = json.loads(health_file.read_text(encoding="utf-8"))
    snapshot = HostSnapshot(
        host_name="test-host",
        capabilities={name: SeatCapability(True, "available") for name in sample_seats},
        timestamp=1000.0,
        unhealthy_seats=unhealthy,
    )

    policy = ResolvePolicy()
    res = resolve(sample_plan, snapshot, sample_seats, "L-Arch", policy)
    # Claude is in cooldown; Gemini is selected seamlessly
    assert res.seat_name == "gemini-seat"
