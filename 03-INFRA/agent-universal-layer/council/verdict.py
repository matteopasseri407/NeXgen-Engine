"""Brief construction, per-round seat orchestration, and VERDICT parsing.

Owns the shared VERDICT: APPROVE|REVISE|REJECT contract every seat prompt
promises to close with (``extract_verdict``), the brainstorm/challenge/
code-review round runner that applies it (``run_rounds``), and the initial
brief text every mode hands to its first prompt (``build_brief``).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from seat_process import SeatRunError, run_seat
from session import _write_private_text, redact_generated_output

VERDICT_RE = re.compile(r"(?i)verdict\s*:\s*(APPROVE|REVISE|REJECT)\b")

MAX_CONTEXT_FILE_BYTES = 2_000_000  # ~2MB: generoso per un brief/diff di testo, non per un binario


def _read_or_exit(path_str: str, label: str) -> str:
    path = Path(path_str)
    if not path.is_file():
        sys.exit(f"[council] {label} file not found: {path_str}")
    size = path.stat().st_size
    if size > MAX_CONTEXT_FILE_BYTES:
        sys.exit(
            f"[council] {label} file too large ({size} bytes, limit {MAX_CONTEXT_FILE_BYTES}): "
            "reduce the context before attaching it."
        )
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        sys.exit(f"[council] {label} file is not valid UTF-8 text (binary?): {path_str}")


def build_brief(question: str | None, context_path: str | None, diff_path: str | None = None) -> str:
    parts = []
    if question:
        parts.append(f"Question: {question}")
    if diff_path:
        diff_text = _read_or_exit(diff_path, "diff")
        parts.append(f"\nDiff to review:\n```diff\n{diff_text}\n```")
    if context_path:
        context_text = _read_or_exit(context_path, "context")
        parts.append(f"\nContext:\n{context_text}")
    if not parts:
        sys.exit("[council] empty brief: at least a question, a diff, or a context file is required.")
    return "\n".join(parts)


def extract_verdict(text: str) -> str:
    """Positional, not textual search: only the LAST non-blank line can carry
    the verdict. A seat prompt promises 'chiudi SEMPRE con una riga a se'
    stante' (see prompts/*.md and build_relay_prompt) precisely so a verdict
    quoted mid-response -- e.g. a later stage citing a prior stage's
    'VERDICT: REJECT' while itself approving at the end -- is never picked up
    as if it were this response's own conclusion. fullmatch on the trimmed
    last line also rejects a verdict buried in trailing prose on that same
    line: the contract is a standalone line, not merely 'appears last'."""
    last_line = ""
    for line in reversed(text.splitlines()):
        if line.strip():
            last_line = line.strip()
            break
    # Tolerate the near-universal LLM closing tics (markdown emphasis,
    # terminal punctuation), then anchor at the START of the line: a verdict
    # with a trailing caveat ("VERDICT: REJECT because ...") is still that
    # seat's own final verdict -- treating it as "(absent)" would fail open
    # and silently defeat the relay's REJECT-stop. A QUOTED verdict as the
    # last line ("> VERDICT: REJECT") keeps its quote prefix after the strip
    # and still reads as absent, which is the spoof this parser exists for.
    last_line = last_line.strip("*_` ").rstrip(".!").rstrip("*_` ")
    match = VERDICT_RE.match(last_line)
    return match.group(1).upper() if match else "(absent)"


def run_rounds(
    seat_name: str, seat: dict, session_dir: Path, mode_label: str, brief: str,
    role_prompt_initial: str, role_prompt_continue: str | None, rounds: int,
    timeout_seconds: float,
) -> tuple[list[str], list[str]]:
    responses: list[str] = []
    verdicts: list[str] = []
    prompt = role_prompt_initial.replace("{brief}", brief)
    for r in range(1, rounds + 1):
        print(f"[council] round {r}/{rounds} — seat: {seat_name} ({seat['model']})")
        try:
            response, _usage = run_seat(seat, prompt, session_dir, timeout_seconds)
        except SeatRunError as e:
            sys.exit(str(e))
        # Audit FINDING B (2026-07-12): this gate used to be wired only into
        # _run_relay_stage. brainstorm/challenge/code-review wrote the raw
        # seat response straight to disk and to the continuation prompt,
        # so a hallucinated secret in a seat's own output could reach the
        # kept-session file, the printed verdict, and (in brainstorm) the
        # next round's prompt unredacted. Same gate, same place in the
        # pipeline as relay: right after run_seat, before anything else
        # sees the response.
        response, generated_output_redacted = redact_generated_output(response)
        if generated_output_redacted:
            print(
                "[council] seat output with a possible secret: the fragment was redacted, "
                "the session continues."
            )
        seat_file = session_dir / f"{r:02d}-{seat_name}-{mode_label}-r{r}.md"
        _write_private_text(seat_file, response)
        verdict = extract_verdict(response)
        if verdict == "(absent)":
            print(f"[council] WARNING: no VERDICT line found in round {r}'s response.")
        responses.append(response)
        verdicts.append(verdict)
        print(f"[council] round {r} verdict: {verdict}")
        if r < rounds:
            if role_prompt_continue is None:
                break
            prompt = role_prompt_continue.replace("{brief}", brief).replace("{previous}", response)
    return responses, verdicts


def write_verdict(session_dir: Path, seat_name: str, seat: dict, mode: str, verdicts: list[str], final_response: str) -> None:
    lines = [
        "# Verdict", "",
        f"Seat: {seat_name} ({seat['model']})",
        f"Mode: {mode}",
        f"Rounds run: {len(verdicts)}",
    ]
    for i, v in enumerate(verdicts, 1):
        lines.append(f"Verdict round {i}: {v}")
    lines.append("")
    lines.append(f"## Final response (round {len(verdicts)})")
    lines.append("")
    lines.append(final_response)
    _write_private_text(session_dir / "verdict.md", "\n".join(lines) + "\n")
