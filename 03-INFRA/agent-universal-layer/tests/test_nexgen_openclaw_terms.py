"""Which Google ids the doctor reads as a plan, and which one as Google's own client.

Almost every test here is about similarity, because that is where this rule breaks:
an approved backend whose id begins with a forbidden one, and a lookalike that must
not be taken for the approved backend just because it reads like it.
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.openclaw.terms import (  # noqa: E402 - sys.path shim for cloned checkout
    LOCAL_AGY_BACKEND,
    google_subscriptions,
    local_agy_backend,
)


def test_a_clean_file_names_no_google_plan():
    assert google_subscriptions({"plugins": {"allow": ["browser", "ollama"]}}) == []


def test_the_gemini_cli_runtime_is_still_a_subscription():
    config = {"agents": {"defaults": {"models": {"google/gemini-3-pro": {"agentRuntime": {"id": "google-gemini-cli"}}}}}}

    assert google_subscriptions(config) == ["google/gemini-3-pro"]


def test_the_antigravity_plugin_is_still_a_subscription():
    assert google_subscriptions({"plugins": {"allow": ["browser", "google-antigravity"]}}) == ["google-antigravity"]


def test_the_antigravity_model_route_is_still_a_subscription():
    """A standalone route selects the backend by its prefix, with no runtime field."""
    config = {"agents": {"defaults": {"models": {"google-antigravity/gemini-pro": {}}}}}

    assert google_subscriptions(config) == ["google-antigravity/gemini-pro"]


def test_the_gemini_cli_model_route_is_still_a_subscription():
    config = {"agents": {"defaults": {"models": {"google-gemini-cli/gemini-3-pro": {}}}}}

    assert google_subscriptions(config) == ["google-gemini-cli/gemini-3-pro"]


def test_an_antigravity_oauth_profile_is_still_a_subscription():
    config = {"auth": {"profiles": {"google-antigravity:me": {"provider": "google-antigravity", "mode": "oauth"}}}}

    assert google_subscriptions(config) == ["google-antigravity:me"]


def test_an_oauth_profile_under_another_google_name_is_still_a_subscription():
    """The terms do not care which id the profile wears."""
    config = {"auth": {"profiles": {"gcp:me": {"provider": "google-vertex", "mode": "oauth"}}}}

    assert google_subscriptions(config) == ["gcp:me"]


def test_the_local_agy_backend_plugin_is_not_a_subscription():
    config = {
        "plugins": {
            "allow": ["browser", LOCAL_AGY_BACKEND],
            "entries": {LOCAL_AGY_BACKEND: {"config": {"permissionMode": "settings"}}},
        },
    }

    assert google_subscriptions(config) == []
    assert local_agy_backend(config) == LOCAL_AGY_BACKEND


def test_a_model_on_the_local_agy_backend_is_not_a_subscription():
    """Both shapes OpenClaw accepts: the route's prefix, and the runtime field."""
    config = {"agents": {"defaults": {"models": {
        f"{LOCAL_AGY_BACKEND}/agy-model": {},
        "google/gemini-3-pro": {"agentRuntime": {"id": LOCAL_AGY_BACKEND}},
    }}}}

    assert google_subscriptions(config) == []
    assert local_agy_backend(config) == LOCAL_AGY_BACKEND


def test_the_local_agy_auth_profile_is_not_a_subscription():
    """The plugin's profile holds the synthetic marker, so its mode is not oauth."""
    config = {"auth": {"profiles": {f"{LOCAL_AGY_BACKEND}:local": {
        "provider": LOCAL_AGY_BACKEND, "mode": "api_key",
    }}}}

    assert google_subscriptions(config) == []
    assert local_agy_backend(config) == LOCAL_AGY_BACKEND


def test_an_id_that_only_begins_like_the_approved_backend_is_not_taken_for_it():
    config = {"plugins": {"allow": [f"{LOCAL_AGY_BACKEND}-x"]}}

    assert local_agy_backend(config) is None
    assert google_subscriptions(config) == []


def test_an_id_that_only_ends_like_a_forbidden_one_is_not_tagged():
    config = {"plugins": {"allow": ["my-google-antigravity"]}}

    assert google_subscriptions(config) == []


def test_the_approved_backend_is_not_read_as_the_forbidden_one():
    """The whole point of the exact comparison: one starts with the other."""
    assert LOCAL_AGY_BACKEND.startswith("google-antigravity")
    assert google_subscriptions({"plugins": {"allow": [LOCAL_AGY_BACKEND]}}) == []


def test_a_disabled_plugin_entry_does_not_count_as_the_backend_being_here():
    config = {"plugins": {"entries": {LOCAL_AGY_BACKEND: {"enabled": False}}}}

    assert local_agy_backend(config) is None
