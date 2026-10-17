"""The CI workflow's own contract, in the one place a YAML file cannot hold it.

A push that supersedes another must cancel the older run of the same ref instead
of letting both finish: the old one spends runners (and the owner's minutes) on
a verdict nobody will read.
"""
from __future__ import annotations

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[3]
CI = REPO / ".github" / "workflows" / "ci.yml"


def workflow() -> dict:
    return yaml.safe_load(CI.read_text(encoding="utf-8"))


def test_a_new_push_cancels_the_run_it_supersedes():
    concurrency = workflow()["concurrency"]
    assert concurrency["group"] == "ci-${{ github.ref }}"
    assert concurrency["cancel-in-progress"] is True
