"""`--json` on the two read-only `sup` verbs: the same lines, ready to forward.

The plugin and the supervisor's tools answer with the words the engine already
wrote, never with words of their own, so the shape has to be the lines and not a
second rendering of the data. These tests prove the JSON carries exactly the
terminal's text, on a temporary lane and with a fake harness.
"""
from __future__ import annotations

import json

import pytest

from nexgen_local import cli
from nexgen_local import supervision_costs as costs
from nexgen_local import supervision_state as state

EXPORT = {
    "info": {
        "id": "ses_json",
        "model": {"id": "deepseek-v4.1-flash", "providerID": "opencode-go", "variant": "max"},
        "cost": 0.25,
        "tokens": {"input": 1000, "output": 200, "reasoning": 30, "cache": {"read": 400, "write": 0}},
        "time": {"created": 1_000_000, "updated": 1_060_000},
    },
    "messages": [],
}


@pytest.fixture
def lane(tmp_path, monkeypatch):
    """A temporary lane and a fake harness: no real session is read."""
    monkeypatch.setattr(state, "lane_state_dir", lambda: tmp_path / "lane")
    monkeypatch.setattr(costs.shutil, "which", lambda name: r"C:\fake\opencode.cmd")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(
        costs.subprocess, "run",
        lambda argv, **kwargs: costs.subprocess.CompletedProcess(argv, 0, json.dumps(EXPORT), ""),
    )
    return tmp_path / "lane" / "supervision"


def _only_task(lane) -> str:
    return sorted(path.name for path in lane.iterdir() if path.is_dir())[0]


def test_the_task_list_in_json_is_the_text_the_terminal_prints(lane, capsys):
    cli.main(["sup", "start", "primo compito"])
    capsys.readouterr()

    assert cli.main(["sup", "status"]) == 0
    plain = capsys.readouterr().out
    assert cli.main(["sup", "status", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["lines"]
    assert "\n".join(payload["lines"]) + "\n" == plain


def test_one_task_in_json_names_that_task_and_its_units(lane, capsys):
    cli.main(["sup", "start", "prova fase 1"])
    task_id = _only_task(lane)
    cli.main(["sup", "attach", task_id, "prova", "--role", "esecutore", "--opencode-session", "ses_json"])
    capsys.readouterr()

    assert cli.main(["sup", "status", task_id, "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["lines"][0] == f"compito {task_id}"
    assert any(line.startswith("unità prova") for line in payload["lines"])


def test_an_unknown_task_is_still_an_error_in_json_mode(lane, capsys):
    """A failure is a failure: the JSON shape must not turn it into an empty answer."""
    assert cli.main(["sup", "status", "compito-che-non-esiste", "--json"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "nexgen-local:" in captured.err


def test_the_spend_in_json_says_where_the_numbers_come_from(lane, capsys):
    assert cli.main(["sup", "costi", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["lines"][0].startswith("spesa delle sessioni")
