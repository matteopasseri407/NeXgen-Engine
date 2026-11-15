"""The Council verdict read from each CLI's own JSON Schema, not from prose."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

COUNCIL_DIR = Path(__file__).resolve().parents[1] / "council"
if str(COUNCIL_DIR) not in sys.path:
    sys.path.insert(0, str(COUNCIL_DIR))

import seat_process  # noqa: E402
import seat_verdict  # noqa: E402
from seat_process import SeatInvocation, _build_command, _parse_claude_result  # noqa: E402
from verdict import extract_verdict  # noqa: E402

EXPECTED_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {
            "type": "string",
            "enum": ["APPROVE", "REVISE", "REJECT"],
            "description": "APPROVE, REVISE or REJECT.",
        },
        "text": {
            "type": "string",
            "description": "The complete review, written as you would have written the reply.",
        },
    },
    "required": ["verdict", "text"],
    "additionalProperties": False,
}


def _invocation(cli: str, tmp_path: Path, **extra) -> SeatInvocation:
    seat = {"cli": cli, "model": "offline-test", **extra}
    return _build_command(seat, "brief", tmp_path).invocation


def test_claude_seat_gets_the_schema_inline(tmp_path):
    argv = _invocation("claude", tmp_path).argv

    assert json.loads(argv[argv.index("--json-schema") + 1]) == EXPECTED_SCHEMA


def test_codex_seat_points_the_output_schema_flag_at_a_private_file(tmp_path):
    invocation = _invocation("codex", tmp_path)
    argv = invocation.argv

    schema_path = Path(argv[argv.index("--output-schema") + 1])
    assert schema_path.is_file()
    assert schema_path.parent == invocation.scratch
    assert json.loads(schema_path.read_text(encoding="utf-8")) == EXPECTED_SCHEMA


def test_agy_seat_switches_print_mode_to_json_before_asking_for_the_schema(tmp_path):
    argv = _invocation("agy", tmp_path).argv

    assert argv[argv.index("--output-format") + 1] == "json"
    assert json.loads(Path(argv[argv.index("--json-schema") + 1]).read_text(encoding="utf-8")) == EXPECTED_SCHEMA


@pytest.mark.parametrize("cli", ["opencode", "dsh", "ollama"])
def test_seats_without_a_schema_flag_keep_their_arguments(cli, tmp_path):
    invocation = SeatInvocation(["vendor", "--print"], None, None, None)

    assert seat_verdict.apply_verdict_schema({"cli": cli}, invocation, tmp_path).argv == ["vendor", "--print"]


def test_a_codex_schema_reply_becomes_the_review_then_its_verdict():
    answer = '{"verdict":"REVISE","text":"Il piano regge, ma manca il rollback."}'

    unwrapped = seat_verdict.unwrap_structured_answer("codex", answer)

    assert unwrapped == "Il piano regge, ma manca il rollback.\n\nVERDICT: REVISE"
    assert extract_verdict(unwrapped) == "REVISE"


def test_the_validated_field_outvotes_a_verdict_line_the_review_ends_with():
    review = "Ho letto il piano.\nVERDICT: REJECT"
    answer = json.dumps({"verdict": "APPROVE", "text": review})

    assert extract_verdict(review) == "REJECT"
    assert extract_verdict(seat_verdict.unwrap_structured_answer("codex", answer)) == "APPROVE"


def test_a_quoted_verdict_inside_the_review_still_reads_as_absent_without_the_schema():
    review = "Ho letto il piano.\n> VERDICT: REJECT"
    answer = json.dumps({"verdict": "APPROVE", "text": review})

    assert extract_verdict(review) == "(absent)"
    assert extract_verdict(seat_verdict.unwrap_structured_answer("claude", answer)) == "APPROVE"


def test_an_agy_envelope_is_read_from_its_structured_output():
    envelope = json.dumps({
        "status": "SUCCESS",
        "response": '{"verdict":"APPROVE","text":"ok"}\n',
        "structured_output": {"verdict": "APPROVE", "text": "ok"},
    })

    assert seat_verdict.unwrap_structured_answer("agy", envelope) == "ok\n\nVERDICT: APPROVE"


def test_an_agy_envelope_carrying_only_the_response_text_still_reads():
    envelope = json.dumps({"status": "SUCCESS", "response": '{"verdict":"REJECT","text":"No."}'})

    assert seat_verdict.unwrap_structured_answer("agy", envelope) == "No.\n\nVERDICT: REJECT"


def test_an_agy_error_envelope_is_not_read_as_a_review():
    envelope = json.dumps({"status": "ERROR", "response": "", "error": "empty prompt"})

    assert seat_verdict.unwrap_structured_answer("agy", envelope) == envelope
    assert extract_verdict(envelope) == "(absent)"


@pytest.mark.parametrize("answer,verdict", [
    ("Reasoning.\nVERDICT: APPROVE", "APPROVE"),
    ('{"verdict":"MAYBE","text":"x"}', "(absent)"),
    ('{"verdict":"APPROVE"}', "(absent)"),
    ('```json\n{"verdict":"APPROVE","text":"x"}\n```', "(absent)"),
    ('{"status":"SUCCESS","response":"prose only"}', "(absent)"),
])
def test_an_answer_that_is_not_the_schema_reply_falls_back_to_the_positional_parser(answer, verdict):
    assert seat_verdict.unwrap_structured_answer("ollama", answer) == answer
    assert extract_verdict(answer) == verdict


def test_run_seat_hands_back_the_review_closed_by_its_verdict(monkeypatch, tmp_path):
    script = tmp_path / "provider.py"
    script.write_text('print(\'{"verdict":"REJECT","text":"No: manca il rollback."}\')\n', encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP"}}
    env["PYTHONUTF8"] = "1"
    invocation = SeatInvocation([sys.executable, "-u", str(script)], None, None, None, env, tmp_path, tmp_path)
    monkeypatch.setattr(seat_process, "_build_seat_command", lambda *args: invocation)

    answer, _usage = seat_process.run_seat({"cli": "codex", "model": "offline-test"}, "", tmp_path, 10)

    assert answer == "No: manca il rollback.\n\nVERDICT: REJECT"


def test_claude_prefers_the_object_its_cli_validated_over_the_prose():
    payload = json.dumps({
        "is_error": False,
        "modelUsage": {"claude-opus-5-5": {"canonicalModel": "claude-opus-5-5"}},
        "result": "Ho letto il piano e lo approvo.",
        "structured_output": {"verdict": "APPROVE", "text": "ok"},
    })

    answer, _usage = _parse_claude_result(payload, "claude-opus-5-5")

    assert answer == '{"verdict": "APPROVE", "text": "ok"}'
    assert extract_verdict(seat_verdict.unwrap_structured_answer("claude", answer)) == "APPROVE"


def test_claude_without_a_schema_keeps_its_result_text():
    payload = json.dumps({
        "is_error": False,
        "modelUsage": {"claude-opus-5-5": {"canonicalModel": "claude-opus-5-5"}},
        "result": "Reasoning.\nVERDICT: APPROVE",
    })

    answer, _usage = _parse_claude_result(payload, "claude-opus-5-5")

    assert answer == "Reasoning.\nVERDICT: APPROVE"
