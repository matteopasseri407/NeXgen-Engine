"""Chrome's generated web-app launchers must not win the first-process race.

Shadowing `google-chrome.desktop` only covers the browser icon. Chrome also
writes one `chrome-<app-id>-<profile>.desktop` per installed web app, and those
call the Chrome binary directly.

That gap is not cosmetic. The FIRST Chrome process to open the shared profile
decides whether :9222 exists for the rest of the session. Starting a PWA from
the dock therefore starts Chrome with no debugging port, and every later launch
-- `agent-chrome` included -- is reduced to an IPC handoff, which cannot add a
port to a browser that is already running. The whole shared-browser lane then
stays dead until Chrome is restarted, with no error anywhere, because opening
the web app itself worked perfectly.

Reproduced on Ubuntu 26.04 / Chrome 151 on 2026-08-14: launching the n8n web
app first left `curl 127.0.0.1:9222/json/version` refused, and a subsequent
`agent-chrome` printed "Apertura nella sessione del browser esistente" and
changed nothing.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path, PurePosixPath

import pytest

REPO = Path(__file__).resolve().parents[3]
SCRIPTS = REPO / "03-INFRA" / "scripts"


@pytest.fixture(scope="module")
def agent_sync():
    spec = importlib.util.spec_from_file_location("agent_sync_webapp_launchers", SCRIPTS / "agent_sync.py")
    module = importlib.util.module_from_spec(spec)
    # Same trap conftest.load_agent_sync_module() documents: dataclasses with
    # postponed annotations resolve their module through sys.modules while the
    # decorator runs, so a manual import has to register it first.
    sys.modules[spec.name] = module
    sys.path.insert(0, str(SCRIPTS))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(SCRIPTS))
    return module


# The repair is Linux-only (its caller returns early elsewhere), so the input
# is a POSIX path on every runner. A plain Path() would become a backslash path
# under the Windows job and assert against a different string than the code
# under test ever sees.
LAUNCHER = PurePosixPath("/home/user/.local/bin/agent-chrome")


def test_a_web_app_exec_line_is_routed_through_the_shared_launcher(agent_sync):
    routed = agent_sync._route_exec_line(
        "/opt/google/chrome/google-chrome --profile-directory=Default --app-id=abc",
        LAUNCHER,
    )
    assert routed == f"{LAUNCHER} --profile-directory=Default --app-id=abc"


def test_routing_drops_a_hardcoded_user_data_dir(agent_sync):
    """The launcher owns the profile choice. A generated entry that pins
    --user-data-dir keeps working but silently freezes the app onto whatever
    path Chrome recorded when the shortcut was created."""
    routed = agent_sync._route_exec_line(
        "/opt/google/chrome/google-chrome --user-data-dir=/old/profile --app-id=abc",
        LAUNCHER,
    )
    assert "--user-data-dir" not in routed
    assert routed == f"{LAUNCHER} --app-id=abc"


def test_routing_preserves_desktop_field_codes(agent_sync):
    """Outlook's entry declares a mailto handler and receives %U. Dropping it
    would silently stop passing the URL the desktop was asked to open."""
    routed = agent_sync._route_exec_line(
        "/opt/google/chrome/google-chrome --app-id=abc %U", LAUNCHER
    )
    assert routed.endswith("--app-id=abc %U")


def test_a_path_needing_quotes_uses_desktop_entry_quoting_not_posix(agent_sync):
    """The Desktop Entry Specification quotes with DOUBLE quotes and escapes
    `"`, backtick, `$` and `\\` inside them. shlex.quote emits POSIX single
    quotes instead, which the spec does not accept -- invisible on a path
    without reserved characters, silently broken on a home directory with a
    space in it."""
    launcher = PurePosixPath("/opt/agent tools/bin/agent-chrome")
    routed = agent_sync._route_exec_line(
        "/opt/google/chrome/google-chrome --app-id=abc", launcher
    )
    assert routed == f'"{launcher}" --app-id=abc'
    assert "'" not in routed


def test_field_codes_are_never_quoted(agent_sync):
    """%U is a desktop field code, not an argument: quoting it would stop the
    desktop from substituting the URL it was asked to open."""
    routed = agent_sync._route_exec_line(
        "/opt/google/chrome/google-chrome --app-id=abc %U", LAUNCHER
    )
    assert routed.endswith(" %U")


def test_routing_is_idempotent(agent_sync):
    """agent-sync guard re-runs this every 30 minutes; a non-idempotent rewrite
    would rewrite every entry on every cycle."""
    assert agent_sync._route_exec_line(f"{LAUNCHER} --app-id=abc", LAUNCHER) is None


@pytest.mark.parametrize(
    "exec_value",
    [
        "/usr/bin/code --new-window",
        "/usr/bin/firefox %u",
        "",
        'sh -c "unbalanced',
    ],
)
def test_routing_leaves_everything_that_is_not_a_chrome_launch_alone(agent_sync, exec_value):
    assert agent_sync._route_exec_line(exec_value, LAUNCHER) is None


def test_the_whole_entry_is_rewritten_including_desktop_actions(agent_sync, tmp_path):
    """Outlook ships three [Desktop Action] blocks, each with its own Exec=.
    Routing only the first one would leave three unguarded entry points."""
    applications = tmp_path / "applications"
    applications.mkdir()
    entry = applications / "chrome-abc-Default.desktop"
    entry.write_text(
        "[Desktop Entry]\n"
        "Name=Outlook (PWA)\n"
        "Exec=/opt/google/chrome/google-chrome --app-id=abc %U\n"
        "StartupWMClass=crx_abc\n"
        "\n"
        "[Desktop Action Nuovo-messaggio]\n"
        "Name=Nuovo messaggio\n"
        "Exec=/opt/google/chrome/google-chrome --app-id=abc --app-launch-url-for-shortcuts-menu-item=https://x\n",
        encoding="utf-8",
    )
    entry.chmod(0o600)

    logged: list[str] = []
    env = type("Env", (), {"log": lambda self, message: logged.append(message)})()
    agent_sync._route_linux_chrome_app_launchers(env, applications, LAUNCHER)

    rewritten = entry.read_text(encoding="utf-8")
    assert rewritten.count(f"Exec={LAUNCHER}") == 2
    assert "/opt/google/chrome/google-chrome" not in rewritten
    # Everything that is not an Exec= line survives untouched.
    assert "StartupWMClass=crx_abc" in rewritten
    assert "[Desktop Action Nuovo-messaggio]" in rewritten
    assert entry.stat().st_mode & 0o777 == 0o600
    assert len(logged) == 1

    logged.clear()
    agent_sync._route_linux_chrome_app_launchers(env, applications, LAUNCHER)
    assert logged == [], "second pass rewrote an already-routed entry"


def test_launchers_that_are_not_chrome_web_apps_are_not_touched(agent_sync, tmp_path):
    applications = tmp_path / "applications"
    applications.mkdir()
    other = applications / "vocalinux.desktop"
    other.write_text("[Desktop Entry]\nExec=/usr/bin/google-chrome\n", encoding="utf-8")
    original = other.read_text(encoding="utf-8")

    env = type("Env", (), {"log": lambda self, message: None})()
    agent_sync._route_linux_chrome_app_launchers(env, applications, LAUNCHER)

    assert other.read_text(encoding="utf-8") == original, (
        "the repair is scoped to Chrome's generated chrome-*.desktop entries"
    )
