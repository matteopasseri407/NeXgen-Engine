"""The browser OpenClaw drives: one it manages on a server, one already here on a node.

One job: decide the `browser` section each role needs, as a merge over the file
already in force, and say the words that justify it. A gateway with no screen
drives a Chromium it started itself, in OpenClaw's own empty profile, because an
always-on browser must never carry a person's logins: the engine finds that
binary and never installs it. A machine with a screen already runs the shared
Chrome the engine keeps on loopback port 9222, and OpenClaw attaches to that one
instead of starting a second.

`attachOnly: true` is not a preference. Without it OpenClaw reads a loopback
`cdpUrl` as a profile of its own, tries to manage a browser it did not start and
reports port ownership errors. OpenClaw's validator also refuses any key it does
not know, so the sections written here carry exactly the keys its schema names.

Nothing here writes a file, starts a browser or opens a socket: the paths are
read, and the rest is one decision the plan and the node setup share.
"""
from __future__ import annotations

import re
import shutil
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nexgen_core.i18n import t
from nexgen_core.paths import resolve_home
from nexgen_core.tools.chrome import profile_dir_for

__all__ = [
    "LOOPBACK_CDP",
    "PROFILE_NAME",
    "SERVER_PROFILE",
    "NodeBrowser",
    "attach_only_missing",
    "host_chrome",
    "is_shared_chrome",
    "is_snap",
    "newest_playwright_chrome",
    "plan_node_browser",
    "playwright_cache",
    "server_section",
    "shared_chrome_ready",
]

#: The profile the engine adds on a machine that has the shared Chrome.
PROFILE_NAME = "nexgen"

#: OpenClaw's own managed profile: empty, and never the person's.
SERVER_PROFILE = "openclaw"

#: The shared Chrome of this machine, exactly as `nexgen_core/tools/chrome.py` starts it.
LOOPBACK_CDP = "http://127.0.0.1:9222"

#: The command that starts that Chrome: the launcher, or its profile, is what "set up" means.
LAUNCHER = "agent-chrome"

#: The cache `npx playwright install chromium` writes into, under the user that ran it.
PLAYWRIGHT_CACHE = (".cache", "ms-playwright")

#: A Chromium a managed launch can use, beside Playwright's own build.
GOOGLE_CHROME = Path("/usr/bin/google-chrome-stable")

#: The snap tree: its AppArmor confinement breaks the launch OpenClaw performs.
SNAP_NAME = "snap"

#: Hosts a `cdpUrl` can name when the browser is on this machine.
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _text(value: Any) -> str:
    """A non-empty string, or nothing: a key holding only whitespace decides nothing."""
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _section(document: dict[str, Any], name: str) -> dict[str, Any]:
    """One section of a configuration document, or an empty one when it is absent."""
    value = document.get(name)
    return value if isinstance(value, dict) else {}


def is_snap(path: Path) -> bool:
    """Whether a binary lives in the snap tree, whose confinement breaks a managed launch."""
    return SNAP_NAME in Path(path).parts[:-1]


def is_shared_chrome(profile_dir: Path, launcher: str | None) -> bool:
    """Whether the shared Chrome is set up: a profile and a way to start it, not a running browser."""
    return profile_dir.is_dir() or bool(launcher)


def shared_chrome_ready(
    home: Path | None = None, *, which: Callable[[str], str | None] = shutil.which,
) -> bool:
    """Whether this machine has the shared Chrome the engine keeps, asked without starting it.

    The launcher counts because a person who installed the engine has it, and the
    profile directory counts because Chrome creates it the first time it runs.
    Chrome itself may be closed: OpenClaw attaches when it opens.
    """
    return is_shared_chrome(profile_dir_for(home), which(LAUNCHER))


def playwright_cache(home: Path | None = None) -> Path:
    """Where Playwright installs its browsers for the user that ran the install."""
    return resolve_home(home).joinpath(*PLAYWRIGHT_CACHE)


def _revision(directory: Path) -> int:
    """The revision number at the end of a Playwright directory name, or -1 without one."""
    found = re.search(r"(\d+)$", directory.name)
    return int(found.group(1)) if found else -1


def newest_playwright_chrome(cache_root: Path) -> Path | None:
    """The newest Playwright Chromium under a cache directory, or None.

    The revision in the directory name decides, not the modification time: two
    installs on one host leave both behind, and the one `npx playwright install`
    would start is the newest. `chromium-*` also skips the headless shell, which
    is a different binary and cannot serve a managed launch.
    """
    directories = [entry for entry in cache_root.glob("chromium-*") if entry.is_dir()]
    for directory in sorted(directories, key=_revision, reverse=True):
        for binary in sorted(directory.glob("chrome-linux*/chrome")):
            if binary.is_file() and not is_snap(binary):
                return binary
    return None


def host_chrome(home: Path | None = None) -> str | None:
    """A Chromium on this host that a managed launch can use, or None.

    Playwright's own build comes first: it installs for the service user without
    touching the system packages, and it is the one measured to work where the
    distro's snap package does not. Google's package is the fallback for a host
    that already has Chrome. The apt `chromium-browser` shim is a snap launcher
    and is never a candidate here.
    """
    playwright = newest_playwright_chrome(playwright_cache(home))
    if playwright is not None:
        return str(playwright)
    if GOOGLE_CHROME.is_file() and not is_snap(GOOGLE_CHROME):
        return str(GOOGLE_CHROME)
    return None


def _no_chromium_note() -> str:
    """What a person installs when the gateway has no browser to drive, and what not to use."""
    return t("No Chromium that a managed launch can use was found on this server: as the gateway's user run "
             "'npx playwright@latest install chromium', then as root "
             "'npx playwright@latest install-deps chromium', and run this setup again. Do not use the "
             "'chromium' snap package: its AppArmor confinement breaks the managed launch.")


def server_section(
    document: dict[str, Any], home: Path | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """The `browser` block a gateway with no screen needs, and the note when it cannot have one.

    A path the person already wrote is kept untouched, unless it is a snap
    package: that one is the documented failure, not a choice, so a usable binary
    replaces it and the plan says why. A `defaultProfile` the person set stays
    theirs, and every other key in the section stays as written; OpenClaw's own
    empty profile is proposed only where the file names none.
    """
    browser = _section(document, "browser")
    declared = _text(browser.get("executablePath"))
    if declared and not is_snap(Path(declared)):
        return None, None
    found = host_chrome(home)
    if found is None:
        return None, _no_chromium_note()
    block: dict[str, Any] = {
        **browser,
        "enabled": True,
        "headless": True,
        "noSandbox": True,
        "executablePath": found,
    }
    if not _text(browser.get("defaultProfile")):
        block["defaultProfile"] = SERVER_PROFILE
    note = None
    if declared:
        note = t("The browser path in the file was '{declared}', a snap package whose confinement breaks the "
                 "managed launch: the Chromium at {found} is used instead.", declared=declared, found=found)
    return block, note


@dataclass(frozen=True)
class NodeBrowser:
    """The `browser` section to write on a node, and the lines that explain it."""

    #: The whole section, merged over the keys the person wrote, or None to change nothing.
    section: dict[str, Any] | None = None
    notes: tuple[str, ...] = ()


def _hint_without_shared_chrome() -> str:
    """The one line a machine without the shared Chrome gets, and the command that fixes it."""
    return t("Hint: this PC's shared Chrome is not set up, so no browser profile was added here. Run "
             "'agent-chrome --ensure', then 'nexgen openclaw node --apply' again.")


def _want_default(browser: dict[str, Any], section: dict[str, Any]) -> str | None:
    """Point `defaultProfile` at the profile when the person named none, and say what happens.

    A default somebody typed is theirs: the profile is added beside it and the line
    says so. Returns None when the default already names this profile.
    """
    current = _text(browser.get("defaultProfile"))
    if not current:
        section["defaultProfile"] = PROFILE_NAME
        return t("browser.defaultProfile: '{name}', so an agent's browser tool uses that profile.",
                 name=PROFILE_NAME)
    if current != PROFILE_NAME:
        return t("browser.defaultProfile stays '{current}': the '{name}' profile is added beside it.",
                 current=current, name=PROFILE_NAME)
    return None


def plan_node_browser(
    document: dict[str, Any], *, ready: bool, cdp_url: str = LOOPBACK_CDP,
) -> NodeBrowser:
    """The `browser` section a node needs, merged over what the person already wrote.

    Only two things are ever added: the profile that attaches to this machine's
    shared Chrome, and the default that points the agents at it. An existing
    `defaultProfile` is the person's and stays, and a profile named `nexgen` that
    already holds other values is left alone with a line saying so: a value
    somebody typed is never overwritten to match the engine's. A file that already
    holds both is not rewritten, so a second apply changes nothing.
    """
    if not ready:
        return NodeBrowser(None, (_hint_without_shared_chrome(),))
    browser = _section(document, "browser")
    profiles = _section(browser, "profiles")
    wanted = {"cdpUrl": cdp_url, "attachOnly": True}
    declared = profiles.get(PROFILE_NAME)
    if declared is not None and declared != wanted:
        return NodeBrowser(None, (
            t("The browser profile '{name}' is already in the file with other values: left as it is.",
              name=PROFILE_NAME),
        ))
    section = dict(browser)
    notes: list[str] = []
    if declared is None:
        section["profiles"] = {**profiles, PROFILE_NAME: dict(wanted)}
        notes.append(t("Browser profile '{name}': OpenClaw attaches to this PC's shared Chrome on {url} and "
                       "starts none of its own.", name=PROFILE_NAME, url=cdp_url))
    default_note = _want_default(browser, section)
    if default_note is not None:
        notes.append(default_note)
    if section == browser:
        return NodeBrowser(None, ())
    return NodeBrowser(section, tuple(notes))


def _is_loopback(url: str) -> bool:
    """Whether a `cdpUrl` names a browser on this machine rather than one elsewhere."""
    host = (urllib.parse.urlparse(url).hostname or "").strip().lower()
    return host in LOOPBACK_HOSTS or host.startswith("127.")


def attach_only_missing(document: dict[str, Any]) -> tuple[str, ...]:
    """The profiles that attach to a loopback port without saying they only attach.

    OpenClaw reads such a profile as one it manages, so it tries to own a browser
    it did not start and reports port ownership errors. The fix is one key.
    """
    profiles = _section(_section(document, "browser"), "profiles")
    missing: list[str] = []
    for name, entry in profiles.items():
        if not isinstance(entry, dict):
            continue
        url = _text(entry.get("cdpUrl"))
        if url and _is_loopback(url) and entry.get("attachOnly") is not True:
            missing.append(str(name))
    return tuple(sorted(missing))
