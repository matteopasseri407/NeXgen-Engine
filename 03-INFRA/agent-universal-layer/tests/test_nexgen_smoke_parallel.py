"""The smoke runs its cases together: the hook waits for the slowest, not the sum.

The pre-push hook runs this on every push, on a machine that is often busy with
something else. Eight starts in a row cost eight times a start on a loaded
machine, and the budget is a wall clock, not a piece of work. Every case is
read-only and independent by contract — that is what makes it a smoke and not a
test suite — so they can be asked at the same time without changing what is
proven. The report keeps the declared order, so the same run reads the same way.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SMOKE = REPO / "03-INFRA" / "scripts" / "smoke.py"


def _smoke_module():
    spec = importlib.util.spec_from_file_location("smoke_under_test", SMOKE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_commands_are_asked_together_and_reported_in_order(monkeypatch, capsys):
    smoke = _smoke_module()

    def slow(argv):
        time.sleep(0.2)
        expected = dict((tuple(a), e) for a, e in smoke.CASES)[tuple(argv[2:])]
        return subprocess.CompletedProcess(argv, sorted(expected)[0], "answered\n", "")

    monkeypatch.setattr(smoke, "run", slow)

    started = time.monotonic()
    problems = smoke.check_commands()
    spent = time.monotonic() - started

    assert problems == []
    assert spent < len(smoke.CASES) * 0.2 / 2, spent
    printed = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert len(printed) == len(smoke.CASES)
    for line, (args, _expected) in zip(printed, smoke.CASES):
        assert line.startswith(("OK  ", "FAIL"))
        assert "nexgen " + " ".join(args) in line


def test_a_failing_command_still_reports_its_own_reason(monkeypatch, capsys):
    smoke = _smoke_module()

    def refuse(argv):
        if "plan" in argv:
            return subprocess.CompletedProcess(argv, 7, "", "boom")
        time.sleep(0.05)
        expected = dict((tuple(a), e) for a, e in smoke.CASES)[tuple(argv[2:])]
        return subprocess.CompletedProcess(argv, sorted(expected)[0], "fine", "")

    monkeypatch.setattr(smoke, "run", refuse)

    problems = smoke.check_commands()

    assert len(problems) == 1
    assert "nexgen plan" in problems[0] and "exit 7" in problems[0]
    assert "nexgen plan" in capsys.readouterr().out


def test_a_silent_command_says_it_did_not_answer(monkeypatch, capsys):
    smoke = _smoke_module()

    def time_out(argv):
        raise subprocess.TimeoutExpired(argv, smoke.COMMAND_TIMEOUT)

    monkeypatch.setattr(smoke, "run", time_out)

    problems = smoke.check_commands()

    assert len(problems) == len(smoke.CASES)
    assert all("no answer" in problem for problem in problems)
    assert sum(1 for line in capsys.readouterr().out.splitlines() if "no answer" in line) == len(smoke.CASES)
