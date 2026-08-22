"""Unit test per lo sblocco e la conformita del seggio agy nel Council."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pytest

COUNCIL_DIR = Path(__file__).resolve().parents[2] / "agent-universal-layer" / "council"
if str(COUNCIL_DIR) not in sys.path:
    sys.path.insert(0, str(COUNCIL_DIR))

from proposal import _check_seat_allowed
from seat_process import _build_seat_command, _effort_forwarding, _effort_label


def test_agy_build_seat_command(tmp_path: Path) -> None:
    seat = {
        "cli": "agy",
        "model": "gemini-3.7-flash-high",
        "reasoning_effort": "high",
    }
    invocation = _build_seat_command(seat, "Test prompt text", tmp_path)
    assert invocation.argv == [
        "agy",
        "--model",
        "gemini-3.7-flash-high",
        "--disable-slash-commands",
        "--new-project",
        "--sandbox",
        "--effort",
        "high",
        "-p",
        "Test prompt text",
    ]
    assert invocation.stdin_text is None
    assert invocation.output_file is None
    assert "PATH" in invocation.env


def test_agy_effort_forwarding() -> None:
    seat_high = {"cli": "agy", "reasoning_effort": "high"}
    args, label = _effort_forwarding(seat_high)
    assert args == ["--effort", "high"]
    assert label == ", effort high"

    seat_invalid = {"cli": "agy", "reasoning_effort": "xhigh"}
    args_inv, label_inv = _effort_forwarding(seat_invalid)
    assert args_inv == []
    assert "not applied" in label_inv


def test_agy_seat_allowed_in_proposal(capsys) -> None:
    seat = {"cli": "agy", "model": "gemini-3.7-flash-high", "zero_retention": False}
    args = argparse.Namespace()
    # Must not raise or sys.exit
    _check_seat_allowed("gemini", seat, args)
    captured = capsys.readouterr()
    assert "WARNING" in captured.err
