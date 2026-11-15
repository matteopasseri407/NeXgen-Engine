"""`gh pr checks --required --watch`: the official CLI waits, this engine reads its verdict.

The flags are checked here against what `gh pr checks --help` really offers on
this machine (gh 2.92.0): `--required` and `--watch`, with exit 0 for green, 1
for a failed check and 8 for checks still pending.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from nexgen_core import release_checks


def _runner(code: int, stdout: str = "", calls: list | None = None):
    def run(argv, cwd):
        if calls is not None:
            calls.append(tuple(str(a) for a in argv))
        return subprocess.CompletedProcess([str(a) for a in argv], code, stdout, "")

    return run


def test_it_asks_only_for_the_required_checks_and_waits_for_them():
    calls: list = []

    ok, detail = release_checks.pr_required_checks(Path("."), 114, _runner(0, "All checks passed", calls))

    assert ok is True
    assert detail == "the required checks are green on #114"
    assert calls == [("gh", "pr", "checks", "114", "--required", "--watch", "--fail-fast")]


def test_a_failed_required_check_stops_and_names_the_line(tmp_path: Path):
    out = "X  ci.yml/test  1m0s  some required check failed\n"

    ok, detail = release_checks.pr_required_checks(tmp_path, 114, _runner(1, out))

    assert ok is False
    assert detail == "failing on #114: X  ci.yml/test  1m0s  some required check failed"


def test_a_check_still_pending_is_not_read_as_green(tmp_path: Path):
    """`gh` exits 8 while checks are pending; only 0 means everything passed."""
    ok, detail = release_checks.pr_required_checks(tmp_path, 114, _runner(8, "pending"))

    assert ok is False
    assert detail == "not answered on #114 (gh exit 8): pending"


def test_a_green_run_with_nothing_to_show_still_says_which_pull_request(tmp_path: Path):
    ok, detail = release_checks.pr_required_checks(tmp_path, 7, _runner(0))

    assert (ok, detail) == (True, "the required checks are green on #7")
