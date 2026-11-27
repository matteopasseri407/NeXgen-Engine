"""Build and read one DeepSeek Harness (``dsh``) Council seat.

One job: the Council's own frame around a dsh run -- the error this seat raises with the kind
``SeatRunError`` mirrors, the headless argv the seat passes, the private overlay it writes, and
how its NDJSON answer becomes an opinion -- while everything the launcher itself dictates is in
``nexgen_core.dsh_cli``, shared with the supervisor.

``seat_process`` is already past its size ceiling; rule 3 says the next change splits instead of
growing it, so the dsh-specific half lives here.

The harness facts themselves (launcher location, the patch, the stream) and the live
verification behind them are documented in ``dsh_cli``.
"""
from __future__ import annotations

import os
import shutil  # noqa: F401 - the seat's own suite fences the launcher probe through seat_dsh.shutil
from pathlib import Path

from nexgen_core import deposit_env, dsh_cli
from session import _private_mkdir, _write_private_text

#: The profile the shipped app materializes for a one-shot run (``resources/cli`` boots it).
HEADLESS_PROFILE = dsh_cli.HEADLESS_PROFILE
#: The single variable the harness reads for both OpenCode routes.
HARNESS_KEY_ENV = dsh_cli.HARNESS_KEY_ENV
#: Harness route -> the name this machine stores that plan's key under (see ``dsh_cli``).
KEY_SOURCES = dsh_cli.KEY_SOURCES
#: Where the Windows installer puts the launcher, relative to ``%LOCALAPPDATA%``.
WINDOWS_LAUNCHER = dsh_cli.WINDOWS_LAUNCHER


class DshSeatError(RuntimeError):
    """A dsh seat that cannot be built or read. ``kind`` mirrors ``SeatRunError.kind``."""

    def __init__(self, message: str, kind: str) -> None:
        super().__init__(message)
        self.kind = kind


def dsh_executable() -> str | None:
    """The launcher on this host, or None when the harness is not installed here."""
    return dsh_cli.executable()


def dsh_route(seat: dict) -> tuple[str, str]:
    """The seat's model as the harness wants it: (provider route, provider-owned model id).

    The two halves travel in the patch instead of in argv, because the harness has no
    ``--model`` flag, so an unusable route is refused here before any file or process exists.
    """
    try:
        return dsh_cli.split_route(str(seat.get("model", "")))
    except dsh_cli.DshError as error:
        raise DshSeatError(
            f"[council] a dsh seat's model must be <provider>/<model>, got {seat.get('model')!r}.",
            "invocation",
        ) from error


def _deposit_values() -> dict[str, str]:
    """Every value in the secrets deposit, read now so this seat's own suite can replace it."""
    return deposit_env.read_deposit()


def _credential_names(provider: str) -> tuple[str, ...]:
    """Where to look for this route's key, in order: the plan it names, then the harness's name."""
    return dsh_cli.credential_names(provider)


def _first_value(names: tuple[str, ...], values: dict[str, str]) -> str:
    """The first of these names that carries a value, or an empty string."""
    return dsh_cli._first_value(names, values)


def dsh_credential_env(seat: dict) -> dict[str, str]:
    """Bridge a plan key onto the one variable the harness reads.

    The seat's route names the plan, so that plan's key is the first answer; the harness's own
    variable is the second; the secrets deposit is the third, because nothing loads the deposit
    into a graphical session and a seat launched from one would otherwise start without a key
    it already has on disk. Only a name that carries a value is bridged: a route with no
    credential fails the harness's own request with ``MISSING_CREDENTIAL`` and its own
    explanation, which is a better diagnostic than this module could write before spending the
    same time.
    """
    provider, _model = dsh_route(seat)
    names = _credential_names(provider)
    value = _first_value(names, os.environ) or _first_value(names, _deposit_values())
    return {HARNESS_KEY_ENV: value} if value else {}


def dsh_patch_text(seat: dict, data_root: Path) -> str:
    """The per-call overlay this seat runs under: pinned route, private root, three uploads off.

    The harness facts behind every entry are in ``dsh_cli.patch_document``. This seat is the
    caller that passes its own private directory, so the brief's own record and the projection
    store land in the isolation directory it already owns and removes, and never in the
    person's ``~/.dsh`` -- which also keeps their ``.credentials.yaml`` and any MCP server
    declared in their own profile out of the seat.
    """
    provider, model = dsh_route(seat)
    effort = str(seat.get("reasoning_effort") or "")
    document = dsh_cli.patch_document(provider, model, effort=effort, data_root=data_root)
    return dsh_cli.patch_text(document)


def write_dsh_patch(seat: dict, directory: Path) -> Path:
    """Write the overlay into the seat's private isolation directory and return it.

    The directory is one the seat already owns and that ``run_seat`` removes when the seat ends,
    so the patch and the session it redirects never outlive the call and need no cleanup of
    their own.
    """
    path = directory / "council-seat.yml"
    data_root = directory / "dsh-data"
    _private_mkdir(data_root)
    _write_private_text(path, dsh_patch_text(seat, data_root))
    return path


def dsh_launcher(scratch: Path) -> str:
    """The launcher to start on this host: the vendor's own, or a space-free wrapper for it.

    The Windows rule and the generated wrapper live in ``dsh_cli.launcher``; this seat only
    says which private directory the wrapper may be written into.
    """
    return dsh_cli.launcher(scratch, _write_private_text)


def dsh_argv(patch_path: Path, scratch: Path) -> list[str]:
    """The headless one-shot command, with the brief left off argv.

    ``-`` is the documented "read the task from stdin", so the brief never reaches the process
    table. ``--json`` is the NDJSON stream this module reads. The permission preset is the
    ``read-only`` sandbox the base profile already ships, and ``--approval-policy never`` turns
    its ``ask`` into a denial instead of a prompt: nobody is at a terminal to answer one.
    """
    return [
        dsh_launcher(scratch),
        "--profile", HEADLESS_PROFILE,
        "--patch", str(patch_path),
        "--permission-mode", "read-only",
        "--approval-policy", "never",
        "--json", "-",
    ]


def dsh_probe_argv(scratch: Path) -> list[str]:
    """The headless profile's own help: the flags a seat passes are listed there, not at the top."""
    return [dsh_launcher(scratch), "--profile", HEADLESS_PROFILE, "--help"]


def _step_tokens(event: dict) -> int:
    """One step's reported total, or 0 when the harness omitted a partial sum."""
    return dsh_cli._step_tokens(event)


def parse_dsh_result(raw: str, model: str, usage: dict) -> tuple[str, dict]:
    """The seat's answer and token usage from dsh's NDJSON stream.

    The answer is the ``final`` event's text. ``step_end.usage`` reports tokens per step and the
    harness records no cost anywhere, so the recap can print tokens and must not invent a price.
    A stream with no ``final`` event was cut short (a killed seat) and is refused rather than
    reported as an empty opinion.
    """
    events = dsh_cli.read_events(raw)
    if not events:
        raise DshSeatError(
            f"[council] seat '{model}' answered in a shape dsh does not write; no verdict for this round.",
            "invalid_output",
        )
    final = dsh_cli.final_event(events)
    if final is None:
        raise DshSeatError(
            f"[council] seat '{model}' stopped before dsh reported a final answer; no verdict for this round.",
            "invalid_output",
        )
    reason = final.get("reason") if isinstance(final.get("reason"), dict) else {}
    if reason.get("kind") != "completed":
        error = reason.get("error") if isinstance(reason.get("error"), dict) else {}
        detail = str(error.get("message") or reason.get("kind") or "unknown")
        raise DshSeatError(f"[council] error from seat: {detail}", "seat_error")
    text = final.get("text")
    if not isinstance(text, str) or not text.strip():
        raise DshSeatError("[council] the seat responded but with no usable text (empty output).", "empty_response")
    reported = dict(usage or {})
    tokens = sum(
        _step_tokens(event)
        for event in events
        if event.get("type") == "status" and event.get("phase") == "step_end"
    )
    if tokens:
        reported["tokens"] = tokens
    return text, reported
