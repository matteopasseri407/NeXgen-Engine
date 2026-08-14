"""The two agent-chrome launchers must implement the same contract.

`03-INFRA/agent-browser-cdp.md` states one hard rule: agents attach to ONE
shared, visible Chrome and reuse its window.

The launchers used to serve that rule with a single behaviour: a bare
`agent-chrome` probed `127.0.0.1:9222` and exited 0 when the shared browser was
already up, so an agent asking for the browser never started a second one.
(#46 added the POSIX probe to PowerShell after an end user reported v0.96.0
blocking its caller until the window was closed.)

That conflated two different callers. A human clicking the Chrome icon runs the
same bare command, and when a windowless Chrome still held :9222 the launcher
exited 0 and the desktop treated the launch as successful -- so clicking Chrome
did nothing at all, repeatedly. The contract is now split by intent:

  agent-chrome            someone wants a WINDOW -- always hand off to Chrome.
  agent-chrome --ensure   an agent wants the browser to EXIST -- exit 0 if up.

These are source-level invariants on purpose: launching a real browser is not
something CI can do, and both defects were a missing branch, not a runtime bug.
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


def test_both_launchers_expose_the_ensure_mode(sources):
    for dialect, source in sources.items():
        assert "--ensure" in source, (
            f"agent-chrome.{dialect}: lost the --ensure mode, so an agent has no way "
            "to require the shared browser without opening a window at the human"
        )


def test_the_reuse_branch_exits_cleanly_and_only_under_ensure(sources):
    """The reuse branch has to precede the launch and exit 0: a non-zero code
    would make every caller treat "already running" as a failure. It must also
    be reachable ONLY in --ensure mode, because that is the whole fix -- a bare
    call is a request for a window and may never silently do nothing."""
    for dialect, source in sources.items():
        probe = source.index("/json/version")
        launch = source.index('exec "$chrome"' if dialect == "sh" else "& $Chrome")
        assert probe < launch, f"agent-chrome.{dialect}: probe must precede the launch"

    sh = sources["sh"]
    reuse = sh.index('if [ "$mode" = "ensure" ] && cdp_is_up; then')
    assert "exit 0" in sh[reuse : reuse + 120], "agent-chrome.sh: reuse branch no longer exits 0"

    ps1 = sources["ps1"]
    reuse = ps1.index('if ($Mode -eq "ensure" -and (Test-Cdp))')
    assert "exit 0" in ps1[reuse : reuse + 120], "agent-chrome.ps1: reuse branch no longer exits 0"


def test_an_argument_free_call_still_reaches_chrome(sources):
    """Regression guard for the reported "clicking Chrome does nothing": the
    old launchers exited early on `$# -eq 0` / `-not $ChromeArgs`, which is the
    exact shape of a desktop icon activation."""
    assert '[ "$#" -eq 0 ]' not in sources["sh"], (
        "agent-chrome.sh: an argument-free call is a desktop activation, not an "
        "--ensure call, and must not exit before launching Chrome"
    )
    assert "(-not $ChromeArgs)" not in sources["ps1"], (
        "agent-chrome.ps1: an argument-free call is a desktop activation, not an "
        "--ensure call, and must not exit before launching Chrome"
    )


def test_both_launchers_detect_a_chrome_that_holds_the_profile_without_cdp(sources):
    """The first Chrome process to open the shared profile decides whether
    :9222 exists for the whole session. A launcher that loses that race cannot
    repair it by handing off, so the state has to be reported rather than left
    to look like a broken agent, and --heal has to exist to recover it."""
    for dialect, source in sources.items():
        assert "--heal" in source, f"agent-chrome.{dialect}: lost the --heal recovery mode"
        assert "without the CDP port" in source, (
            f"agent-chrome.{dialect}: no report when another Chrome owns the shared "
            "profile without the debugging port"
        )


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


def test_the_posix_launcher_does_not_force_the_browser_class_on_web_apps():
    """--class=Google-chrome keeps the dock from splitting the pinned Chrome
    icon when a custom user-data-dir changes the WM_CLASS. Applying it to a PWA
    launch would do the opposite -- collapsing every installed web app into the
    Chrome icon -- now that agent-sync routes those launchers here too."""
    source = SH.read_text(encoding="utf-8")
    guard = source.index("class_args=(--class=Google-chrome)")
    assert "--app-id=" in source[guard : guard + 400], (
        "agent-chrome.sh: --class is no longer suppressed for web-app windows"
    )


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
