"""The CI workflow's own contract, in the one place a YAML file cannot hold it.

A push that supersedes another must cancel the older run of the same ref instead
of letting both finish: the old one spends runners (and the owner's minutes) on
a verdict nobody will read.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[3]
CI = REPO / ".github" / "workflows" / "ci.yml"


def workflow() -> dict:
    return yaml.safe_load(CI.read_text(encoding="utf-8"))


def triggers(doc: dict) -> dict:
    # PyYAML reads the bare key `on` as boolean True (YAML 1.1); keep the test
    # working whichever the parser decides.
    return doc.get(True) or doc["on"]


def test_a_new_push_cancels_the_run_it_supersedes():
    concurrency = workflow()["concurrency"]
    assert concurrency["group"] == "ci-${{ github.ref }}"
    assert concurrency["cancel-in-progress"] is True


def test_the_windows_leg_answers_only_on_the_nightly_run_and_on_demand():
    """A push decides nothing about Windows, so it must not spend a Windows runner.

    The workflow still has the push/pull_request triggers every Linux job uses;
    the Windows job opts out of them itself, which is the thing asserted here:
    if the `if` line is dropped, this test goes red on the next push rather than
    after a wasted windows-latest slot.
    """
    doc = workflow()
    # The nightly door exists, at the hour the job's own budget was planned for.
    schedule = triggers(doc)["schedule"]
    assert schedule == [{"cron": "0 3 * * *"}]
    assert "workflow_dispatch" in triggers(doc)

    job = doc["jobs"]["engine-tests-windows"]
    gate = job["if"]
    assert "workflow_dispatch" in gate
    assert "schedule" in gate
    # Positive form only: an expression that merely excludes push/pull_request
    # would silently start running a Windows leg on a future event.
    assert "push" not in gate
    assert "pull_request" not in gate


def test_smoke_linux_runs_the_real_smoke_on_every_push():
    """The checks of this workflow live in YAML, and this one is wired, not documented."""
    doc = workflow()
    job = doc["jobs"]["smoke-linux"]
    assert job["runs-on"] == "ubuntu-latest"
    assert job["timeout-minutes"] == 3
    # Every event that starts this workflow runs it: no `if` gate at all.
    assert "if" not in job

    steps = job["steps"]
    python_step = next(s for s in steps if "setup-python" in s.get("uses", ""))
    assert python_step["with"]["python-version"] == "3.12"
    # The engine's own dependencies, from THIS checkout - not a pinned list
    # that can drift away from pyproject.toml.
    assert any(s.get("run", "").strip() == "pip install ." for s in steps)
    assert any(s.get("run", "").strip() == "python 03-INFRA/scripts/smoke.py" for s in steps)


def test_the_suite_runs_in_parallel_and_the_floor_leg_gets_the_same_command():
    doc = workflow()
    job = doc["jobs"]["engine-tests"]

    install = next(s for s in job["steps"] if s.get("name") == "Install test dependencies")
    assert "pytest-xdist" in install["run"]

    suite = next(s for s in job["steps"] if s.get("name") == "Engine regression suite")
    assert "-n auto" in suite["run"]
    # loadgroup, not the default: the tests pinned to one worker still run in
    # one job, together, while everything else spreads.
    assert "--dist loadgroup" in suite["run"]

    # Both legs (newest Python and the 3.11 floor) run that one command, so the
    # parallelism claim holds for the floor too.
    labels = [leg["label"] for leg in job["strategy"]["matrix"]["include"]]
    assert any("3.11" in leg["python"] for leg in job["strategy"]["matrix"]["include"])
    assert any(leg["label"] == "engine-tests" for leg in job["strategy"]["matrix"]["include"])
    assert len(labels) == 2


def test_pytest_xdist_is_a_development_dependency_only():
    """Parallelism is a test-runner detail: it must never reach a user's install."""
    text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    project = tomllib.loads(text)["project"]
    assert not any("xdist" in dep for dep in project["dependencies"])
    assert any("xdist" in dep for dep in project["optional-dependencies"]["dev"])
