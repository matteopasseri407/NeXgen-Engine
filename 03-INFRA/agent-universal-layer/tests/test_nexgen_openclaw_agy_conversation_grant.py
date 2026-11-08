"""The Antigravity plugin gets the conversation grant when it is installed and enabled.

Its `before_prompt_build` hook gives `agy` back the turns it lost. OpenClaw withholds
conversation hooks from a plugin that is not bundled with it unless the grant is written,
so without this grant the hook was blocked in the gateway log. The grant is written only
when the plugin is there and enabled, and it never enables a plugin by itself.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(REPO_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(REPO_SCRIPTS))

from nexgen_core.openclaw.conversation_grant import with_engine_plugin  # noqa: E402
from nexgen_core.openclaw.terms import LOCAL_AGY_BACKEND  # noqa: E402
from nexgen_core.runtimes.openclaw import PLUGIN_ID  # noqa: E402


def _config(agy_entry=None) -> dict:
    entries = {} if agy_entry is None else {LOCAL_AGY_BACKEND: agy_entry}
    return {"plugins": {"allow": [LOCAL_AGY_BACKEND], "entries": entries}}


def test_an_installed_and_enabled_antigravity_plugin_is_granted_the_conversation():
    agy = {"enabled": True, "config": {"permissionMode": "sandbox"}}
    out = with_engine_plugin(_config(agy), deposited=False, agy_installed=True)
    entry = out["plugins"]["entries"][LOCAL_AGY_BACKEND]
    assert entry["hooks"] == {"allowConversationAccess": True}
    assert entry["config"] == {"permissionMode": "sandbox"}, "the plugin's own settings are kept"
    assert entry["enabled"] is True
    assert PLUGIN_ID not in out["plugins"]["entries"], "the engine plugin is not deposited, so it is not touched"


def test_an_antigravity_plugin_that_is_not_installed_gets_no_grant():
    out = with_engine_plugin(_config({"enabled": True}), deposited=False, agy_installed=False)
    assert "hooks" not in out["plugins"]["entries"][LOCAL_AGY_BACKEND]


def test_a_disabled_or_absent_antigravity_entry_gets_no_grant_even_when_installed():
    disabled = with_engine_plugin(_config({"enabled": False}), deposited=False, agy_installed=True)
    absent = with_engine_plugin(_config(None), deposited=False, agy_installed=True)
    assert "hooks" not in disabled["plugins"]["entries"][LOCAL_AGY_BACKEND]
    assert LOCAL_AGY_BACKEND not in absent["plugins"]["entries"], "the grant never creates or enables the plugin"


def test_a_hook_key_the_person_wrote_is_kept_when_the_grant_is_merged():
    agy = {"enabled": True, "hooks": {"someOtherKey": "kept"}}
    out = with_engine_plugin(_config(agy), deposited=False, agy_installed=True)
    assert out["plugins"]["entries"][LOCAL_AGY_BACKEND]["hooks"] == {
        "someOtherKey": "kept", "allowConversationAccess": True,
    }


def test_the_engine_plugin_keeps_its_grant_and_enablement_when_deposited():
    out = with_engine_plugin({"plugins": {}}, deposited=True)
    plugins = out["plugins"]
    assert PLUGIN_ID in plugins["allow"]
    assert plugins["entries"][PLUGIN_ID] == {"enabled": True, "hooks": {"allowConversationAccess": True}}
    assert LOCAL_AGY_BACKEND not in plugins["entries"]
