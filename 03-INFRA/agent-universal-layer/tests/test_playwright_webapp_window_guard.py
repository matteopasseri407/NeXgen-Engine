"""An installed web app is not a spare browser tab.

A shared Chrome exposes every installed web app (WhatsApp, n8n, ChatGPT,
Gmail...) over CDP as an ordinary `page` target, indistinguishable from a tab.
Upstream `@playwright/mcp` therefore adopts whichever target it enumerates
first as the current tab, and a later `browser_navigate` replaces whatever the
human had open in that application window.

Observed on Ubuntu 26.04 / Chrome 151 on 2026-08-14: with the n8n and ChatGPT
web apps open, `/json/list` returned the ChatGPT app window first, so the agent
started every session pointed at it.

Hiding those windows would be the wrong fix: an open WhatsApp window IS the
right place to send a WhatsApp message. The distinction is between USING an app
window for its own app and ADOPTING it as a general browser.

The wrapper's fifth patch classifies each page by `display-mode` -- the
platform's own answer to "tab or installed app window", so no per-application
URL list has to be maintained. An app window stays listed and selectable, is
never adopted implicitly as the current tab, and cannot be navigated off its
own origin.

These are source-level invariants: the patch targets a third-party bundle that
CI does not download, and the wrapper already refuses to start when the bundle
no longer matches, so what has to be protected here is the intent.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
WRAPPER = REPO / "03-INFRA" / "agent-universal-layer" / "mcp" / "playwright-human-safe.mjs"

MARKER = "agent-webapp-window-guard-patch-v1"


@pytest.fixture(scope="module")
def source() -> str:
    return WRAPPER.read_text(encoding="utf-8")


def test_the_guard_is_a_marked_patch_like_the_other_four(source):
    assert f"const APP_WINDOW_MARKER = '{MARKER}'" in source
    assert "function patchAppWindows(" in source


def test_classification_uses_display_mode_and_not_a_url_list(source):
    """A per-application URL list would misclassify a normal tab that happens
    to be open on the same site, and would need updating for every web app the
    user installs."""
    assert 'matchMedia("(display-mode: browser)").matches' in source


def test_an_app_window_is_never_adopted_as_the_current_tab_implicitly(source):
    assert "await this.__agentProtectAppWindows();" in source
    guard = source.index("async __agentProtectAppWindows()")
    body = source[guard : guard + 1200]
    assert "if (!current || state.explicit.has(current))" in body, (
        "only an ACCIDENTAL current tab may be dropped"
    )
    assert "this._currentTab = void 0;" in body


def test_an_app_window_stays_listed_so_it_can_be_used_for_its_own_app(source):
    """Asking for a WhatsApp message should use the WhatsApp window. Removing
    app windows from the tab list would make that impossible, which is why the
    guard drops an accidental selection instead of the window itself."""
    protect = source.index("async __agentProtectAppWindows()")
    body = source[protect : protect + 1200]
    assert "_tabs.splice" not in body, "an app window must not be removed from the tab list"


def test_a_deliberate_selection_of_an_app_window_survives(source):
    """Once the agent has chosen an app window on purpose, the next tool call
    must not silently move it back to an ordinary tab."""
    assert "this.__agentWindowState().explicit.add(tab2);" in source


def test_an_app_window_cannot_be_navigated_off_its_own_origin(source):
    assert "__agentCheckAppWindowNavigation" in source
    assert "Refusing to navigate the" in source


def test_an_unreachable_page_is_never_quarantined(source):
    """A page that cannot be evaluated (still loading, crashed, chrome://) must
    fall back to "ordinary tab". Quarantining on failure would let a transient
    error silently hide the user's real tabs from the agent."""
    guard = source.index("async __agentIsAppWindow(page)")
    body = source[guard : guard + 900]
    assert "return false; /* Unreachable page: never quarantine it, never cache. */" in body


def test_the_patch_is_validated_fail_closed_before_launching(source):
    """Consistent with the other four patches: refuse to start on an
    unrecognised upstream bundle instead of applying a partial replacement."""
    assert "Refusing an unsafe partial patch" in source
    validation = source.index("Human-safe patch validation failed in memory")
    preceding = source[max(0, validation - 900) : validation]
    for expected in ("APP_WINDOW_MARKER", "guardedEnsureTab", "guardedSelectTab", "guardedNavigateCheck"):
        assert expected in preceding, f"final validation does not assert {expected}"


def test_the_wrapper_still_parses(source):
    """Cheap guard against a template-literal or escaping mistake in the
    injected JavaScript, which is written as a string inside this file."""
    import shutil
    import subprocess

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not available")
    result = subprocess.run([node, "--check", str(WRAPPER)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
