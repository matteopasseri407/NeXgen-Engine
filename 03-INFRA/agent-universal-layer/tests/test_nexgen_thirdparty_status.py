"""`nexgen thirdparty status`: what updates by itself, what waits for a yes, and what to type.

The owner's complaint was that nothing said how an item gets updated when it does not happen by
itself. The command prints one line per third-party item with the policy that governs it and the
exact command (or file) that moves it; the doctor says how many are waiting and the one command that
applies them. Expected values are the literal strings a person reads.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

INFRA = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(INFRA / "scripts"))

from nexgen_core import i18n, thirdparty_status  # noqa: E402
from nexgen_core.checks.thirdparty_checks import check_thirdparty_updates  # noqa: E402
from nexgen_core.cli import build_parser  # noqa: E402
from nexgen_core.report import Severity  # noqa: E402


@pytest.fixture(autouse=True)
def _english():
    i18n.set_language("en")
    yield
    i18n.set_language(None)


# --- the rule itself -----------------------------------------------------------------------------

@pytest.mark.parametrize("kind, origin, pin_state, update, pin, expected", [
    # the guardian raises these alone, in the hourly beat: nothing to type
    ("mcp", "", "pinned", {"state": "stale", "tier": "auto"}, "", ("auto", "")),
    # cleared by the guardian, one human yes for the batch
    ("mcp", "", "pinned", {"state": "stale", "tier": "batch"}, "", ("yes", "nexgen skills bump")),
    ("skill", "github", "pinned", {"state": "stale", "tier": "batch"}, "", ("yes", "nexgen skills bump")),
    # held: no command raises it, so the answer names the file that holds the pin
    ("mcp", "", "pinned", {"state": "stale", "tier": "hold"}, "", ("look", "edit mcp/manifest.yaml")),
    ("skill", "github", "pinned", {"state": "stale", "tier": "hold"}, "", ("look", "edit skills.manifest.yaml")),
    # a skill its vendor installs: the pin is only what the watch compares, and the spec in the
    # manifest is what names the package the vendor's own updater takes
    ("skill", "upstream", "pinned", {"state": "stale", "tier": "hold", "pinned": "4.8.0"},
     "impeccable@4.8.0", ("look", "npx impeccable update")),
    # a newer version the judge has not covered yet: the bump command recomputes the verdicts
    ("mcp", "", "pinned", {"state": "stale"}, "", ("unchecked", "nexgen skills bump")),
    # nothing moved
    ("mcp", "", "pinned", {"state": "current", "upstream": "3.25.3"}, "", ("pinned", "")),
    # no version at all in the launcher: it changes without anyone here deciding
    ("mcp", "", "unpinned", {"state": "n/a"}, "firecrawl-mcp", ("floating", "edit mcp/manifest.yaml")),
    # an endpoint somebody else serves: there is no pin to write
    ("mcp", "", "none", {"state": "n/a"}, "", ("floating", "")),
    # the publisher withdrew the pin, and the numbers may compare "lower": a person decides
    ("mcp", "", "pinned", {"state": "current", "deprecated": "publisher says so"}, "",
     ("look", "edit mcp/manifest.yaml")),
    # a module's component is replaced where it runs; the guardian gives it no target
    ("module", "", "pinned", {"state": "stale", "tier": "hold"}, "", ("by-hand", "")),
    ("module", "", "pinned", {"state": "current"}, "", ("pinned", "")),
])
def test_which_policy_governs_one_item(kind, origin, pin_state, update, pin, expected):
    assert thirdparty_status.policy_and_command(kind, origin, pin_state, update, pin) == expected


@pytest.mark.parametrize("pin, expected", [
    ("impeccable@4.8.0", "npx impeccable update"),
    ("@scope/thing@1.2.3", "npx @scope/thing update"),
    ("impeccable", "npx impeccable update"),
    ("@scope/thing", "npx @scope/thing update"),
    ("", ""),
])
def test_the_installer_command_names_the_package_not_the_version(pin, expected):
    assert thirdparty_status._installer_command(pin) == expected


def test_a_commit_is_shortened_but_a_version_is_never_touched():
    assert thirdparty_status._short("0ef9fe5bc3572734fd3a" "e6c448fe00a30cd5dcf0") == "0ef9fe5bc"
    assert thirdparty_status._short("firecrawl-mcp@3.25.3") == "firecrawl-mcp@3.25.3"
    assert thirdparty_status._short("2026.8.31") == "2026.8.31"


# --- a whole machine -----------------------------------------------------------------------------

#: One of each update path: held (a minor jump), cleared (a patch jump), no pin at all, and the
#: engine's own connector, which must never appear as third-party.
_SERVERS = {
    "firecrawl": {"command": "npx", "args": ["-y", "firecrawl-mcp@3.25.3"], "targets": ["claude"]},
    "supabase": {"command": "npx", "args": ["-y", "@supabase/mcp-server-supabase@0.13.0"],
                 "targets": ["claude"]},
    "vercel": {"url": "https://mcp.vercel.com/", "targets": ["claude"]},
    "playwright": {"command": "node", "args": ["${AGENT_ENGINE_ROOT}/mcp/x.mjs"], "targets": ["claude"]},
}

#: A pinned repository, a skill its vendor installs, and one that is simply the owner's.
_SKILLS = {
    "humanizer": {"origin": "github", "repo": "blader/humanizer",
                  "commit": "0ef9fe5bc3572734fd3a" "e6c448fe00a30cd5dcf0",
                  "exposure": "manual", "targets": ["claude"]},
    "impeccable": {"origin": "upstream", "exposure": "manual", "targets": ["claude"],
                   "deps": {"kind": "npx", "spec": "impeccable@4.8.0"}},
    "mine": {"origin": "vault", "exposure": "manual", "targets": ["claude"]},
}

#: What the dependency watch left behind: every pin with the newest version it could reach.
_STATUS = {
    "checked_at": 1_000.0,
    "pins": [
        {"what": "MCP server 'firecrawl' (npm firecrawl-mcp)", "kind": "npm-version",
         "pinned": "3.25.3", "upstream": "3.40.0", "stale": True},
        {"what": "MCP server 'supabase' (npm @supabase/mcp-server-supabase)", "kind": "npm-version",
         "pinned": "0.13.0", "upstream": "0.13.1", "stale": True},
        {"what": "skill 'humanizer' (github blader/humanizer)", "kind": "git-commit",
         "pinned": "0ef9fe5bc3572734fd3a" "e6c448fe00a30cd5dcf0",
         "upstream": "b48c832f0f809c1b5658" "edbb838c2521b72f2413", "stale": True},
        {"what": "skill 'impeccable' (npm impeccable)", "kind": "npm-version",
         "pinned": "4.8.0", "upstream": "4.9.1", "stale": True},
    ],
}

#: What the guardian decided about them: one cleared for a single yes, three held.
_GUARD = {
    "checked_at": 1_000.0,
    "auto": [],
    "batch": [{"what": "MCP server 'supabase' (npm @supabase/mcp-server-supabase)",
               "pinned": "0.13.0", "upstream": "0.13.1",
               "plain": "Piccolo ritocco (0.13.0 -> 0.13.1), stessa linea: niente funzioni nuove."}],
    "hold": [
        {"what": "MCP server 'firecrawl' (npm firecrawl-mcp)", "pinned": "3.25.3", "upstream": "3.40.0",
         "plain": "Ci sono novità dentro (3.25.3 -> 3.40.0): da guardare prima."},
        {"what": "skill 'humanizer' (github blader/humanizer)",
         "pinned": "0ef9fe5bc3572734fd3a" "e6c448fe00a30cd5dcf0",
         "upstream": "b48c832f0f809c1b5658" "edbb838c2521b72f2413",
         "plain": "Tocca i collegamenti automatici (plugin.json): da guardare prima."},
        {"what": "skill 'impeccable' (npm impeccable)", "pinned": "4.8.0", "upstream": "4.9.1",
         "plain": "Ci sono novità dentro (4.8.0 -> 4.9.1): da guardare prima."},
    ],
}


@pytest.fixture
def machine(tmp_path, monkeypatch):
    vault, engine, state, home = tmp_path / "vault", tmp_path / "engine", tmp_path / "state", tmp_path / "home"
    layer = vault / "03-INFRA" / "agent-universal-layer"
    (layer / "mcp").mkdir(parents=True)
    (layer / "skills").mkdir(parents=True)
    (engine / "agent-universal-layer" / "skills").mkdir(parents=True)
    (state / "nexgen").mkdir(parents=True)
    home.mkdir()
    monkeypatch.setenv("NEXGEN_HOME", str(home))
    monkeypatch.setenv("AGENT_STATE_DIR", str(state))
    monkeypatch.setenv("AGENT_VAULT_DATA", str(vault))
    monkeypatch.setenv("AGENT_ENGINE_ROOT", str(engine))
    (layer / "mcp" / "manifest.yaml").write_text(yaml.safe_dump({"servers": _SERVERS}), encoding="utf-8")
    (layer / "skills" / "skills.manifest.yaml").write_text(
        yaml.safe_dump({"skills": _SKILLS}), encoding="utf-8")
    (home / ".agents" / "skill-library").mkdir(parents=True)
    (state / "nexgen" / "third-party-status.json").write_text(json.dumps(_STATUS), encoding="utf-8")
    (state / "nexgen" / "third-party-guard.json").write_text(json.dumps(_GUARD), encoding="utf-8")
    return {"vault": vault, "engine": engine, "state": state, "home": home}


def _report(m):
    return thirdparty_status.report(home=m["home"], vault_data=m["vault"],
                                    engine_root=m["engine"], state_dir=m["state"], now=1_000.0)


def _by_name(items):
    return {item.name: item for item in items}


def test_every_third_party_item_gets_its_own_policy_and_command(machine):
    text, items = _report(machine)
    rows = _by_name(items)
    assert (rows["supabase"].policy, rows["supabase"].command) == ("yes", "nexgen skills bump")
    assert (rows["firecrawl"].policy, rows["firecrawl"].command) == ("look", "edit mcp/manifest.yaml")
    assert (rows["humanizer"].policy, rows["humanizer"].command) == ("look", "edit skills.manifest.yaml")
    # a skill its vendor installs is never rewritten from here: the installer is the answer
    assert (rows["impeccable"].policy, rows["impeccable"].command) == ("look", "npx impeccable update")
    assert (rows["vercel"].policy, rows["vercel"].command) == ("floating", "")
    # the engine's own connector is not a third-party item at all
    assert "playwright" not in rows and "mine" not in rows
    assert "supabase" in text and "nexgen skills bump" in text


def test_the_line_carries_the_pin_the_newest_and_the_policy(machine):
    _text, items = _report(machine)
    rows = _by_name(items)
    assert rows["supabase"].pinned == "@supabase/mcp-server-supabase@0.13.0"
    assert rows["supabase"].newest == "0.13.1"
    assert rows["humanizer"].pinned == "0ef9fe5bc"
    assert rows["humanizer"].newest == "b48c832f0"
    assert rows["impeccable"].pinned == "impeccable@4.8.0" and rows["impeccable"].newest == "4.9.1"
    assert rows["firecrawl"].note == "Ci sono novità dentro (3.25.3 -> 3.40.0): da guardare prima."


def test_the_summary_counts_what_moves_and_what_waits(machine):
    text, _items = _report(machine)
    assert "5 third-party items: 1 ready to move, 3 need a look, 0 updated where they run, 1 with no pin." in text
    assert "One command moves them: nexgen skills bump" in text


def test_the_legend_only_explains_the_policies_that_are_here(machine):
    text, _items = _report(machine)
    assert "How to read this" in text
    # present: look, yes, floating
    assert "one command for all of them: nexgen skills bump" in text
    assert "no command raises it" in text
    # absent: nothing here moves by itself, nothing is by hand, nothing was left unchecked
    assert "the hourly watch raises it by itself" not in text
    assert "a component of a module" not in text


def test_a_machine_never_checked_says_so_instead_of_reading_as_up_to_date(machine):
    for name in ("third-party-status.json", "third-party-guard.json"):
        (machine["state"] / "nexgen" / name).unlink()
    text, items = _report(machine)
    assert "never checked on this machine yet" in text
    # Nothing may read as "up to date": the two honest answers are "unchecked" and "no pin at all".
    assert {item.policy for item in items} == {"unchecked", "floating"}


def test_a_machine_with_nothing_third_party_lists_nothing(machine):
    (machine["vault"] / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml").write_text(
        yaml.safe_dump({"servers": {}}), encoding="utf-8")
    (machine["vault"] / "03-INFRA" / "agent-universal-layer" / "skills" / "skills.manifest.yaml").write_text(
        yaml.safe_dump({"skills": {}}), encoding="utf-8")
    text, items = _report(machine)
    assert items == []
    assert "No third-party skill or MCP server is declared on this machine." in text


# --- the doctor line -------------------------------------------------------------------------------

def _check(m):
    return check_thirdparty_updates(m["vault"], m["home"], m["engine"], m["state"])


def test_the_doctor_names_the_one_command_for_what_is_cleared(machine):
    (machine["state"] / "nexgen" / "third-party-guard.json").write_text(json.dumps({
        "checked_at": 1_000.0, "auto": [],
        "batch": [{"what": "MCP server 'supabase' (npm @supabase/mcp-server-supabase)",
                   "pinned": "0.13.0", "upstream": "0.13.1"}],
        "hold": [],
    }), encoding="utf-8")
    outcome = _check(machine)
    assert outcome.severity == Severity.WARN
    assert "waiting for one yes" in outcome.message and "supabase" in outcome.message
    assert outcome.action == "Run 'nexgen skills bump' and answer yes: it raises every cleared pin at once."


def test_the_doctor_names_the_command_that_explains_a_held_item(machine):
    (machine["state"] / "nexgen" / "third-party-guard.json").write_text(json.dumps({
        "checked_at": 1_000.0, "auto": [], "batch": [],
        "hold": [{"what": "MCP server 'firecrawl' (npm firecrawl-mcp)",
                  "pinned": "3.25.3", "upstream": "3.40.0"}],
    }), encoding="utf-8")
    outcome = _check(machine)
    assert outcome.severity == Severity.WARN
    # The command a person can act on is the one that explains each item, never the one that
    # silently applies nothing: that mismatch is what this line exists to stop.
    assert outcome.action == "Run 'nexgen thirdparty status': one line per item, with the exact thing that moves it."


def test_the_doctor_is_green_when_nothing_is_waiting(machine):
    for name in ("third-party-status.json", "third-party-guard.json"):
        (machine["state"] / "nexgen" / name).unlink()
    (machine["state"] / "nexgen" / "third-party-status.json").write_text(json.dumps({
        "checked_at": 1_000.0,
        "pins": [{"what": "MCP server 'firecrawl' (npm firecrawl-mcp)", "kind": "npm-version",
                  "pinned": "3.25.3", "upstream": "3.25.3", "stale": False}],
    }), encoding="utf-8")
    outcome = _check(machine)
    assert outcome.severity == Severity.OK
    assert outcome.message == "No third-party update is waiting (5 items watched)."


def test_the_doctor_says_undetermined_when_the_watch_never_ran(machine):
    for name in ("third-party-status.json", "third-party-guard.json"):
        (machine["state"] / "nexgen" / name).unlink()
    outcome = _check(machine)
    assert outcome.severity == Severity.UNDETERMINED
    assert outcome.message == "Upstream versions have not been checked on this machine yet."


def test_a_pending_update_never_turns_the_doctor_red(machine):
    """A queued update is not a broken machine: the exit code must not move because of this line."""
    assert _check(machine).severity in (Severity.OK, Severity.WARN)


# --- the command exists, and it reads what the watch wrote -----------------------------------------

def test_the_verb_exists():
    parser = build_parser()
    args = parser.parse_args(["thirdparty", "status"])
    assert args.thirdparty_command == "status"
    assert args.func.__name__ == "cmd_thirdparty_status"
    # the help for the group itself is not an error
    assert parser.parse_args(["thirdparty"]).func is not None


def test_what_the_watch_and_the_guardian_write_is_what_the_command_reads(machine):
    """Producer and reader agree: run the watch and the judge with faked upstream, then ask."""
    from nexgen_core.depwatch import run_depwatch
    from nexgen_core.thirdparty_guard import run_guardian

    result = run_depwatch(
        vault_data=machine["vault"], state_dir=machine["state"],
        git_ls_remote=lambda repo: "0ef9fe5bc3572734fd3a" "e6c448fe00a30cd5dcf0",
        npm_latest_version=lambda package: {"firecrawl-mcp": "3.25.4",
                                           "@supabase/mcp-server-supabase": "0.13.0",
                                           "impeccable": "4.8.0"}[package],
    )
    assert result.wrote
    run_guardian(result.findings, machine["state"])
    text, items = _report(machine)
    rows = _by_name(items)
    # a patch jump on a package that runs is cleared for one yes, not applied alone
    assert (rows["firecrawl"].policy, rows["firecrawl"].pinned, rows["firecrawl"].newest) == (
        "yes", "firecrawl-mcp@3.25.3", "3.25.4")
    assert rows["humanizer"].policy == "pinned"
    assert "firecrawl" in text
