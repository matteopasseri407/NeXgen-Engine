"""The Council verdict as a JSON Schema, and the CLI flags that enforce it.

Owns three things and nothing else: the one schema a schema-capable seat is
asked for (``VERDICT_SCHEMA``), the per-CLI argument that turns the vendor's
own validation on (``apply_verdict_schema``), and the unwrapping that turns the
validated reply back into the text-plus-closing-line every Council consumer
already reads (``unwrap_structured_answer``).

It exists because a seat's verdict used to be whichever line the model wrote
last. ``claude``, ``codex`` and ``agy`` all ship a flag that makes the CLI
itself validate a ``verdict`` field against an enum, so the value stops being a
guess about prose: a review that quotes ``> VERDICT: REJECT`` cannot outvote
the field, and a reply without the field is the CLI's schema violation rather
than a silent ``(absent)``. Seats whose CLI has no such flag (``opencode``,
``dsh``, ``ollama``) keep the positional contract in ``verdict.py`` unchanged.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from session import _write_private_text

if TYPE_CHECKING:
    from seat_process import SeatInvocation

VERDICT_VALUES = ("APPROVE", "REVISE", "REJECT")

# The CLIs whose own --help advertises schema-validated output, verified on the
# binaries installed here on 2026-11-15: claude 2.1.323 (`--json-schema
# <schema>`, inline), codex-cli 0.155.1 (`exec --output-schema <FILE>`) and agy
# 1.5.9 (`--json-schema`, which it refuses unless `--output-format json`).
# opencode 2 `run --help`, `dsh` and `ollama run --help` have no equivalent, so
# their seats are not in this tuple.
STRUCTURED_VERDICT_CLIS = ("agy", "claude", "codex")

VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {
            "type": "string",
            "enum": list(VERDICT_VALUES),
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

SCHEMA_FILE_NAME = "verdict-schema.json"


def _schema_file(directory: Path) -> Path:
    """The schema as a file, inside a private directory the seat already owns.

    codex and agy take a path instead of an inline string. The seat's own
    scratch directory is preferred, because `run_seat` removes it when the seat
    ends; a seat built without one (a test double, or a future CLI that is not
    in ``ISOLATED_SEAT_CLIS``) falls back to the session directory, which is
    private too and already removed with the session.
    """
    path = Path(directory) / SCHEMA_FILE_NAME
    _write_private_text(path, json.dumps(VERDICT_SCHEMA, indent=2))
    return path


def apply_verdict_schema(seat: dict, invocation: "SeatInvocation", session_dir: Path) -> "SeatInvocation":
    """Ask the CLIs that validate schemas for the structured verdict.

    claude takes the schema inline, codex and agy take a path. agy also needs
    ``--output-format json``: without it its own argument check refuses
    ``--json-schema`` outright (verified on 1.5.9). A seat whose CLI has no
    schema flag gets its invocation back untouched.
    """
    cli = seat.get("cli")
    if cli not in STRUCTURED_VERDICT_CLIS:
        return invocation
    if cli == "claude":
        invocation.argv.extend(["--json-schema", json.dumps(VERDICT_SCHEMA)])
        return invocation
    schema_path = _schema_file(invocation.scratch or session_dir)
    if cli == "codex":
        invocation.argv.extend(["--output-schema", str(schema_path)])
    else:
        invocation.argv.extend(["--output-format", "json", "--json-schema", str(schema_path)])
    return invocation


def _json_object(text: str) -> dict | None:
    """The whole answer as one JSON object, or None when it is not one."""
    stripped = text.strip()
    if not stripped.startswith("{"):
        return None
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:  # nexgen: allow reason="an answer that is not JSON is the normal shape of a seat without the schema, so it falls back to the positional parser instead of failing the round"
        return None
    return payload if isinstance(payload, dict) else None


def _verdict_and_text(cli: str, payload: dict) -> tuple[str, str] | None:
    """(verdict, review) when the object is a schema reply, otherwise None."""
    if cli == "agy":
        if str(payload.get("status") or "SUCCESS").upper() != "SUCCESS":
            # an agy error carries status ERROR and exits non-zero, which
            # _collect_result has already turned into a process_error; this
            # only stops the envelope itself being read as a review.
            return None
        structured = payload.get("structured_output")
        if isinstance(structured, dict):
            payload = structured
        else:
            nested = payload.get("response")
            payload = _json_object(nested) if isinstance(nested, str) else None
            if payload is None:
                return None
    verdict = payload.get("verdict")
    review = payload.get("text")
    if verdict not in VERDICT_VALUES or not isinstance(review, str):
        return None
    return verdict, review


def unwrap_structured_answer(cli: str, answer: str) -> str:
    """The seat's review as text, closed by the verdict its CLI validated.

    Each CLI wraps the schema reply differently: codex writes the bare object
    to its output file, claude's result text is that same JSON, and agy prints
    an envelope with the parsed object under ``structured_output``. All three
    end here so the relay hand-off, the session file and verdict.md keep one
    shape: the review, then the canonical closing line the positional parser
    and every prompt already promise. An answer that is not a schema reply is
    returned unchanged, which is what leaves the seats without a schema flag on
    their existing contract.
    """
    payload = _json_object(answer)
    if payload is None:
        return answer
    entry = _verdict_and_text(cli, payload)
    if entry is None:
        return answer
    verdict, review = entry
    review = review.strip()
    return f"{review}\n\nVERDICT: {verdict}" if review else f"VERDICT: {verdict}"


def claude_answer_text(payload: dict) -> object:
    """The answer claude actually validated, from its ``--output-format json`` envelope.

    Verified on 2.1.323 that ``result`` already holds that JSON as text and
    ``structured_output`` holds it parsed. Preferring ``structured_output``
    keeps the verdict readable on a build that puts prose, or a fenced copy, in
    ``result`` instead; the caller's own "no usable result text" check still
    guards the object.
    """
    structured = payload.get("structured_output")
    if isinstance(structured, dict) and structured:
        return json.dumps(structured)
    return payload.get("result")
