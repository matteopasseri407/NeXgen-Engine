"""The two agent-chrome launchers must implement the same contract.

`03-INFRA/agent-browser-cdp.md` states one hard rule: agents attach to ONE
shared, visible Chrome and reuse its window. The POSIX launcher honoured it
by probing `127.0.0.1:9222` first and exiting cleanly when the shared browser
was already up; the PowerShell one, added later in #46, never had that probe.
On Windows a bare `agent-chrome` therefore started a second Chrome process
every time and blocked its caller until the user closed the window, which an
end user reported against v0.96.0.

These are source-level invariants on purpose: launching a real browser is not
something CI can do, and the defect was a missing branch, not a runtime bug.
"""
from __future__ import annotations

from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[3]
SH = REPO / "03-INFRA" / "scripts" / "agent-chrome.sh"
PS1 = REPO / "03-INFRA" / "scripts" / "agent-chrome.ps1"

CDP_PORT = "9222"


@pytest.fixture(scope="module")
def sources() -> dict[str, str]:
    return {"sh": SH.read_text(encoding="utf-8"), "ps1": PS1.read_text(encoding="utf-8")}


def test_both_launchers_probe_the_shared_cdp_endpoint_before_starting_one(sources):
    for dialect, source in sources.items():
        assert f"127.0.0.1:{CDP_PORT}/json/version" in source, (
            f"agent-chrome.{dialect}: no probe of the shared CDP endpoint, so it "
            "cannot know the shared browser is already running"
        )


def test_both_launchers_exit_without_starting_chrome_when_it_is_already_up(sources):
    # The reuse branch has to come before the launch, and it has to be a clean
    # exit: a non-zero code would make every caller treat "already running"
    # as a failure.
    sh = sources["sh"]
    probe = sh.index("/json/version")
    assert "exit 0" in sh[probe : probe + 200], "agent-chrome.sh: reuse branch no longer exits 0"
    assert probe < sh.index('exec "$chrome"'), "agent-chrome.sh: probe must precede the launch"

    ps1 = sources["ps1"]
    probe = ps1.index("/json/version")
    assert "exit 0" in ps1[probe : probe + 600], "agent-chrome.ps1: reuse branch no longer exits 0"
    assert probe < ps1.index("& $Chrome"), "agent-chrome.ps1: probe must precede the launch"


def test_the_reuse_branch_is_scoped_to_an_argument_free_call(sources):
    """With a URL to open, launching is correct: Chrome's own single-instance
    handoff forwards it to the running window, which IS the shared window.
    Exiting early there would silently swallow the URL."""
    assert '[ "$#" -eq 0 ]' in sources["sh"]
    assert "(-not $ChromeArgs)" in sources["ps1"]


@pytest.mark.parametrize(
    "flag",
    [
        "--remote-debugging-address=127.0.0.1",
        f"--remote-debugging-port={CDP_PORT}",
        "--no-first-run",
    ],
)
def test_both_launchers_pass_the_same_chrome_flags(sources, flag):
    """--no-first-run in particular: the dedicated CDP profile is created
    empty, and Chrome's first-run flow on an empty profile steals focus and
    asks to become the default browser -- in front of an agent that was told
    to start the browser without making the user ask."""
    for dialect, source in sources.items():
        assert flag in source, f"agent-chrome.{dialect}: lost {flag}"


def test_neither_launcher_writes_into_the_standard_chrome_profile(sources):
    """Chrome 136+ refuses --remote-debugging-port on the default profile
    directory, so both launchers refuse to invent a second daily profile when
    they find an unmigrated standard one."""
    assert "standard_profile" in sources["sh"]
    assert "$StandardProfile" in sources["ps1"]
    for dialect, source in sources.items():
        assert "AGENT_CHROME_PROFILE" in source, (
            f"agent-chrome.{dialect}: lost the profile override env var"
        )
