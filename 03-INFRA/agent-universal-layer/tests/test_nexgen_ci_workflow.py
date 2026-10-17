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


def test_the_windows_leg_answers_nightly_on_demand_and_on_a_release_pull_request():
    """Daily pushes do not wait for Windows; a pull request into main does.

    The workflow still has the push/pull_request triggers every Linux job uses;
    the Windows job opts out of a plain push itself, which is the thing asserted
    here: if the `if` line is dropped, this test goes red on the next push.
    """
    doc = workflow()
    # The nightly door exists, at the hour the job's own budget was planned for.
    schedule = triggers(doc)["schedule"]
    assert schedule == [{"cron": "0 3 * * *"}]
    assert "workflow_dispatch" in triggers(doc)

    job = doc["jobs"]["engine-tests-windows"]
    gate = job["if"]
    assert "workflow_dispatch" in gate
    # The nightly door exists, and this leg is not the one that walks through it:
    # nightly-windows does, on `developer` (see the test below).
    assert "schedule" not in gate
    # Positive form only: an expression that merely excludes push would silently
    # start running a Windows leg on a future event.
    assert "push" not in gate
    assert "github.event_name == 'pull_request' && github.base_ref == 'main'" in gate
    assert "-n auto" in job["steps"][-1]["run"]


def test_the_nightly_runs_on_developer_and_publishes_a_status_on_the_commit():
    """GitHub runs `schedule` only on the default branch (2026-10-17).

    A nightly that never checked out `developer` proved nothing about the branch
    the moving channels follow, and a beta that read the run's event would look
    for a schedule run on a branch that can never have one. Both halves are
    pinned here: the checkout, and the `full-suite` status the commit carries.
    """
    doc = workflow()
    jobs = doc["jobs"]
    legs = ["full-suite-linux", "full-suite-windows"]
    for name in legs:
        job = jobs[name]
        gate = job["if"]
        assert "schedule" in gate and "workflow_dispatch" in gate
        checkout = next(s for s in job["steps"] if "actions/checkout" in s.get("uses", ""))
        assert checkout["with"]["ref"] == "developer", name

    status = jobs["full-suite-status"]
    assert set(status["needs"]) == set(legs)
    assert status["permissions"]["statuses"] == "write"
    assert "always()" in status["if"]
    step = next(s for s in status["steps"] if "statuses/" in s.get("run", ""))
    # The status goes on the commit the legs actually ran on, in the context
    # beta reads - never on wherever `developer` points by then.
    assert "gh api -X POST" in step["run"] and "context=full-suite" in step["run"]
    assert "statuses/${LINUX_SHA}" in step["run"]
    assert '"$LINUX_SHA" = "$WINDOWS_SHA"' in step["run"]
    assert '"$LINUX" = success' in step["run"] and '"$WINDOWS" = success' in step["run"]
    for name in legs:
        recorded = next(s for s in jobs[name]["steps"] if s.get("id") == "tested")
        assert "git rev-parse HEAD" in recorded["run"], name
        assert jobs[name]["outputs"]["sha"] == "${{ steps.tested.outputs.sha }}"


def test_the_fast_windows_gate_is_what_nightly_needs():
    """nightly's Windows proof: a job that runs on every push of developer, in minutes.

    It is not a release gate and it must not become one: the push is never
    blocked by it (CI is asynchronous), only the propagation of `nightly`.
    """
    doc = workflow()
    job = doc["jobs"]["quick-windows"]
    assert job["runs-on"] == "windows-latest"
    assert job["timeout-minutes"] == 8
    assert job["if"] == "github.ref == 'refs/heads/developer'"
    runs = [step.get("run", "") for step in job["steps"]]
    assert any(run.strip() == "python 03-INFRA/scripts/smoke.py" for run in runs)
    quick = next(run for run in runs if "python -m pytest" in run)
    for name in ("test_nexgen_command_surface.py", "test_nexgen_text_encoding.py",
                 "test_nexgen_pathssplit_pinning.py"):
        assert name in quick


def test_the_windows_leg_no_longer_duplicates_the_scheduled_run():
    """The nightly has its own Windows leg on developer; this one is for releases and dispatch."""
    doc = workflow()
    gate = doc["jobs"]["engine-tests-windows"]["if"]
    assert "schedule" not in gate
    assert "workflow_dispatch" in gate
    assert "github.event_name == 'pull_request' && github.base_ref == 'main'" in gate


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


def test_the_timing_sensitive_modules_are_pinned_to_one_worker():
    """`--dist loadgroup` meets the `xdist_group` marker; both must exist, or the
    pinning is a comment nobody reads and these tests fail under load again."""
    config = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    markers = config["tool"]["pytest"]["ini_options"]["markers"]
    assert any("xdist_group" in marker for marker in markers)

    tests = REPO / "03-INFRA" / "agent-universal-layer" / "tests"
    for module in ("test_nexgen_mcp_transport.py", "test_nexgen_update_command.py"):
        source = (tests / module).read_text(encoding="utf-8")
        assert 'pytest.mark.xdist_group("serial")' in source, module


def test_pytest_xdist_is_a_development_dependency_only():
    """Parallelism is a test-runner detail: it must never reach a user's install."""
    text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    project = tomllib.loads(text)["project"]
    assert not any("xdist" in dep for dep in project["dependencies"])
    assert any("xdist" in dep for dep in project["optional-dependencies"]["dev"])


def test_the_release_workflow_may_read_the_checks_it_judges():
    """v2.16.1: `release.py ci-passed` reads the tagged commit's check runs, and a token
    without `checks: read` got nothing back, so every required check read as not reported."""
    release = yaml.safe_load((REPO / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8"))
    assert release["permissions"].get("checks") in ("read", "write")
