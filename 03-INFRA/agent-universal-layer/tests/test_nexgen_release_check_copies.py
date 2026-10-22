"""Which copy of a check a release waits for, when a commit carries it more than once."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from nexgen_core.release_checks import by_name, verdict  # noqa: E402

PUSH = "https://github.com/o/r/actions/runs/100/job/1"
PULL = "https://github.com/o/r/actions/runs/200/job/2"


def _copy(conclusion: str, started: str, url: str, status: str = "COMPLETED") -> dict:
    return {"name": "engine-tests", "status": status, "conclusion": conclusion,
            "startedAt": started, "detailsUrl": url}


def test_a_green_push_copy_started_later_does_not_hide_a_red_pull_request_copy():
    """PR #134: the push run's engine-tests started after the pull request's red one and was read instead."""
    red = _copy("FAILURE", "2026-10-21T19:20:00Z", PULL)
    green = _copy("SUCCESS", "2026-10-21T19:22:00Z", PUSH)
    for order in ([red, green], [green, red]):
        assert verdict(by_name(order), {"engine-tests"}) == ("failed", ["engine-tests"])


def test_a_skipped_copy_does_not_stand_in_for_a_red_one():
    """PR #134: engine-tests-windows was skipped on the push run and red on the pull request's."""
    red = _copy("FAILURE", "2026-10-21T19:20:00Z", PULL)
    skipped = _copy("SKIPPED", "2026-10-21T19:25:00Z", PUSH)
    for order in ([red, skipped], [skipped, red]):
        assert verdict(by_name(order), {"engine-tests"}) == ("failed", ["engine-tests"])


def test_a_rerun_inside_the_same_run_replaces_its_red_attempt():
    """Rerunning a failed job keeps its run id: the newer green attempt is the answer."""
    red = _copy("FAILURE", "2026-10-21T19:20:00Z", PULL)
    rerun = _copy("SUCCESS", "2026-10-21T19:40:00Z", PULL.replace("job/2", "job/3"))
    for order in ([red, rerun], [rerun, red]):
        assert verdict(by_name(order), {"engine-tests"}) == ("green", [])


def test_a_green_copy_wins_over_a_skipped_one_from_another_run():
    green = _copy("SUCCESS", "2026-10-21T19:20:00Z", PULL)
    skipped = _copy("SKIPPED", "2026-10-21T19:25:00Z", PUSH)
    for order in ([green, skipped], [skipped, green]):
        assert by_name(order)["engine-tests"]["conclusion"] == "SUCCESS"


def test_a_running_copy_of_another_run_still_wins():
    green = _copy("SUCCESS", "2026-10-21T19:20:00Z", PUSH)
    running = _copy("", "2026-10-21T19:10:00Z", PULL, status="IN_PROGRESS")
    for order in ([green, running], [running, green]):
        assert verdict(by_name(order), {"engine-tests"}) == ("missing", ["engine-tests"])