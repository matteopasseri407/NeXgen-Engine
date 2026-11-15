"""Already-published leak-scan findings are pinned one by one, never by value.

A release re-scans every commit since the last release, so a value in a commit that is already
on GitHub blocked every later release. gitleaks' baseline is what exempts it now: a finding
whose rule, file, line, columns, commit and author all match an entry there is old news, and
anything else still blocks. This file holds that list to its own promise, because an entry that
no rule could produce would silently stop exempting anything while looking like it did.
"""
from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

SCAN_DIR = Path(__file__).resolve().parents[1] / "leak-scan"
BASELINE = SCAN_DIR / "gitleaks-baseline.json"
CONFIG = SCAN_DIR / "gitleaks.toml"


def _entries() -> list[dict]:
    return json.loads(BASELINE.read_text(encoding="utf-8"))


def _rules() -> dict[str, dict]:
    return {rule["id"]: rule for rule in tomllib.loads(CONFIG.read_text(encoding="utf-8"))["rules"]}


def test_every_pinned_finding_names_a_full_commit():
    """A short SHA would match a different commit the day the prefix is reused."""
    assert [entry["Commit"] for entry in _entries() if len(entry["Commit"]) != 40] == []


def test_every_pinned_finding_says_why_it_is_harmless():
    """The list is read by a person a year later: a bare value is not reviewable."""
    assert [entry["File"] for entry in _entries() if not str(entry.get("NexgenReason", "")).strip()] == []


def test_every_pinned_value_is_what_its_rule_actually_matches():
    """The entry must be the finding the scanner would report for that rule, or it never applies."""
    rules = _rules()
    problems = []
    for entry in _entries():
        rule = rules.get(entry["RuleID"])
        group = int((rule or {}).get("secretGroup") or 0)
        match = re.search((rule or {}).get("regex", ""), entry["Match"])
        if match is None or match.group(group) != entry["Secret"]:
            problems.append(f"{entry['File']}:{entry['StartLine']} {entry['RuleID']}")
    assert problems == []


def test_no_commit_is_pinned_twice_for_the_same_position():
    """Two entries for one position can be pruned independently and drift apart.

    A position is a line and a column: two numbers on one line are two findings, and gitleaks
    needs one entry for each.
    """
    keys = [(entry["Commit"], entry["File"], entry["StartLine"], entry["StartColumn"])
            for entry in _entries()]
    assert len(keys) == len(set(keys))


#: The two commits published on developer before gitleaks was the gate, whose plain numbers in
#: production code and in a proposal are already public. Nothing newer may join this list:
#: a value in production code is rewritten before it is pushed, not pinned after.
PUBLISHED_BEFORE_THE_GATE = {
    "8cf09bd1f05307891d2cc6860cb39dc0d8975fb6",
    "dc001e16cfd0168fdee09e580e66147ad9f6d385",
}


def test_the_pinned_findings_live_in_tests_and_one_release_helper():
    """A pin outside those two places would be exempting production code from the gate."""
    outside = [entry["File"] for entry in _entries()
               if "/tests/" not in entry["File"] and not entry["File"].endswith("release_trust.py")
               and entry["Commit"] not in PUBLISHED_BEFORE_THE_GATE]
    assert outside == []


def test_a_pin_outside_the_tests_is_only_ever_a_plain_number():
    """The historical exemption covers numbers, never a key, a path or an address."""
    kinds = {entry["RuleID"] for entry in _entries()
             if entry["Commit"] in PUBLISHED_BEFORE_THE_GATE and "/tests/" not in entry["File"]}
    assert kinds <= {"nexgen-long-digits"}
