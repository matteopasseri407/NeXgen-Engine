"""Build and read one DeepSeek Harness (``dsh``) Council seat.

One job: everything that is specific to the Harness -- where its launcher is, the per-call
patch that pins the model route and turns its privacy defaults off, the headless argv, and the
NDJSON stream it answers in. ``seat_process`` keeps spawning, environment isolation and the
timeout, and calls in here, so a change in the harness touches this file only.

``seat_process`` is already past its size ceiling; rule 3 says the next change splits instead of
growing it, so the dsh-specific half lives here.

Verified live on 2026-11-14 against DeepSeek Harness 0.2.2-rc.3 on Windows:
``dsh --profile headless --patch <file> --permission-mode read-only --approval-policy never
--json -`` with the brief on stdin exits 0 and prints
``session``, ``status`` (turn_start/step_start/step_end/turn_end), ``text``, ``final`` events.
An unanswered turn exits 1 with ``dsh: <CODE>: <message>`` on stderr and a ``final`` event whose
reason is an error, which is why a non-zero exit is classified before any of this is read.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import yaml

from session import _private_mkdir, _write_private_text

#: The profile the shipped app materializes for a one-shot run (``resources/cli`` boots it).
HEADLESS_PROFILE = "headless"
#: The single variable the harness reads for both OpenCode routes.
HARNESS_KEY_ENV = "OPENCODE_API_KEY"
#: Harness route -> the name this machine stores that plan's key under. A dsh seat keeps the
#: engine's own `provider/model` vocabulary, and the harness has one variable for both routes,
#: so the seat's route decides which plan's key is bridged over.
KEY_SOURCES = {"opencode-go": "OPENCODE_GO_API_KEY", "opencode": "OPENCODE_ZEN_API_KEY"}
#: Where the Windows installer puts the launcher, relative to ``%LOCALAPPDATA%``: the app ships
#: dsh inside ``resources`` and never puts it on PATH.
WINDOWS_LAUNCHER = (
    Path("Programs") / "DeepSeek Harness" / "resources" / "runtime" / "cli" / "bin" / "dsh.cmd"
)


class DshSeatError(RuntimeError):
    """A dsh seat that cannot be built or read. ``kind`` mirrors ``SeatRunError.kind``."""

    def __init__(self, message: str, kind: str) -> None:
        super().__init__(message)
        self.kind = kind


def dsh_executable() -> str | None:
    """The launcher on this host, or None when the harness is not installed here.

    PATH first (an npm install exposes ``dsh``), then the installer's own location, which is
    where the app keeps it and the only place it exists on a normal Windows install.
    """
    found = shutil.which("dsh")
    if found:
        return found
    if os.name != "nt":
        return None
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if not local_app_data:
        return None
    candidate = Path(local_app_data) / WINDOWS_LAUNCHER
    return str(candidate) if candidate.is_file() else None


def dsh_route(seat: dict) -> tuple[str, str]:
    """The seat's model as the harness wants it: (provider route, provider-owned model id).

    The harness addresses a model by route and id separately and has no ``--model`` flag, so a
    dsh seat declares ``provider/model`` like the OpenCode seats do and the two halves travel in
    the patch instead of in argv.
    """
    provider, _, model = str(seat.get("model", "")).partition("/")
    if not provider.strip() or not model.strip():
        raise DshSeatError(
            f"[council] a dsh seat's model must be <provider>/<model>, got {seat.get('model')!r}.",
            "invocation",
        )
    return provider.strip(), model.strip()


def _deposit_values() -> dict[str, str]:
    """Every value in the secrets deposit, or {} where the engine is not on this path.

    Council also runs standalone (``seat_process._no_window_kwargs`` says the same): without
    the engine there is no deposit to read, and the environment is the only source.
    """
    try:
        from nexgen_core.deposit_env import read_deposit
    except ImportError:  # nexgen: allow reason="council runs standalone too, where the engine is not importable; the environment is then the only place a key can be"
        return {}
    return read_deposit()


def _credential_names(provider: str) -> tuple[str, ...]:
    """Where to look for this route's key, in order: the plan it names, then the harness's name.

    The harness documents one variable for both OpenCode routes, and a launcher that starts a
    seat from a desktop app may hold only that one. A route this module does not know still
    gets the harness's own name rather than an empty bridge.
    """
    plan = KEY_SOURCES.get(provider)
    return (plan, HARNESS_KEY_ENV) if plan else (HARNESS_KEY_ENV,)


def _first_value(names: tuple[str, ...], values: dict[str, str]) -> str:
    """The first of these names that carries a value, or an empty string."""
    for name in names:
        value = str(values.get(name) or "").strip()
        if value:
            return value
    return ""


def dsh_credential_env(seat: dict) -> dict[str, str]:
    """Bridge a plan key onto the one variable the harness reads.

    The seat's route names the plan, so that plan's key is the first answer; the harness's own
    variable is the second; the secrets deposit is the third, because nothing loads the deposit
    into a graphical session (see ``deposit_env``) and a seat launched from one would otherwise
    start without a key it already has on disk. Only a name that carries a value is bridged: a
    route with no credential fails the harness's own request with ``MISSING_CREDENTIAL`` and its
    own explanation, which is a better diagnostic than this module could write before spending
    the same time.
    """
    provider, _model = dsh_route(seat)
    names = _credential_names(provider)
    value = _first_value(names, os.environ) or _first_value(names, _deposit_values())
    return {HARNESS_KEY_ENV: value} if value else {}


def dsh_patch_text(seat: dict, data_root: Path) -> str:
    """The per-call overlay: pinned route, a private data root, and the three uploads off.

    Three harness defaults would be wrong for a seat that receives a private brief. The
    session-log plugin appends up to 8 MiB of the session to requests made to the official
    DeepSeek API, the session-telemetry exporter ships session history to DeepSeek's collector,
    and the package inventory sends the active plugin list with the same requests. The brief
    leaves this machine only to the route the seat declares, so all three are disabled here.
    They are config keys, not flags, so this file is the only place they can be set.

    ``data_root`` is the seat's private directory. The launcher resolves its own home from
    ``DSH_HOME`` and materializes a copy of its shipped ``headless`` profile there (verified
    live on 2026-11-15: a run with ``DSH_HOME`` set to a temp dir created only
    ``profiles/headless``, ``storages`` and ``.anonymous-user-id`` under it, and the person's
    ``~/.dsh`` gained nothing), so the seat composes that fresh profile and never the person's
    ``~/.dsh`` — which also keeps their ``.credentials.yaml`` and any MCP server declared in
    their own profile out of the seat. The session log and the projection store are pointed
    here explicitly as well, so the brief's own record lands in the same private directory and
    not in the harness's default one.

    The reasoning effort rides here too: ``agent-default-model`` is the harness's only model
    control, and a value the route does not know is its own to reject, exactly as the OpenCode
    variant is.
    """
    provider, model = dsh_route(seat)
    selection: dict[str, str] = {"provider": provider, "model": model}
    effort = str(seat.get("reasoning_effort") or "").strip()
    if effort and effort != "none":
        selection["reasoningEffort"] = effort
    document = [
        # A bare pi-ai mount registers nothing until a route is declared: this is that route.
        {"id": "llm-pi-ai", "config": {"providers": {provider: {"apiKeyEnv": HARNESS_KEY_ENV}}}},
        {"id": "agent-default-model", "config": selection},
        {"id": "session-persistence-jsonl", "config": {"root": str(data_root / "sessions")}},
        {"id": "storage-json", "config": {"root": str(data_root / "storages")}},
        {"id": "session-log-deepseek", "config": {"enabled": False}},
        {"id": "session-telemetry-otel", "config": {"mode": "DISABLED"}},
        {"id": "plugin-package-inventory-deepseek", "config": {"enabled": False}},
    ]
    return yaml.safe_dump(document, sort_keys=False)


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

    Windows starts a batch file only through ``cmd.exe``, and the engine's shared launcher
    helper hands ``cmd.exe`` the path as a separate argument: a batch path with a space in it
    (``...\\Programs\\DeepSeek Harness\\...\\dsh.cmd``) is split at the space and never runs.
    Inside a batch file a quoted path needs no escaping, so one generated line keeps the
    vendor's launcher authoritative and works. Only a launcher under a path with a space gets
    the wrapper, so the ordinary road is untouched; ``scratch`` is the private directory the
    wrapper lives in (removed with the seat).
    """
    executable = dsh_executable() or "dsh"
    if os.name != "nt" or " " not in executable or not executable.casefold().endswith((".cmd", ".bat")):
        return executable
    path = scratch / "dsh-launch.cmd"
    # The private writer opens in text mode, so "\n" lands as CRLF on Windows, which is what a
    # batch file wants.
    _write_private_text(path, f'@call "{executable}" %*\n')
    return str(path)


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
    usage = event.get("usage")
    if not isinstance(usage, dict):
        return 0
    total = usage.get("totalTokens")
    return total if isinstance(total, int) and not isinstance(total, bool) else 0


def parse_dsh_result(raw: str, model: str, usage: dict) -> tuple[str, dict]:
    """The seat's answer and token usage from dsh's NDJSON stream.

    The answer is the ``final`` event's text. ``step_end.usage`` reports tokens per step and the
    harness records no cost anywhere, so the recap can print tokens and must not invent a price.
    A stream with no ``final`` event was cut short (a killed seat) and is refused rather than
    reported as an empty opinion.
    """
    events: list[dict] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            event = json.loads(stripped)
        except json.JSONDecodeError:  # nexgen: allow reason="a seat killed mid-line leaves a partial tail, and the missing final event below is what refuses the answer"
            continue  # A tail cut mid-line, not an event.
        if isinstance(event, dict):
            events.append(event)
    if not events:
        raise DshSeatError(
            f"[council] seat '{model}' answered in a shape dsh does not write; no verdict for this round.",
            "invalid_output",
        )
    finals = [event for event in events if event.get("type") == "final"]
    if not finals:
        raise DshSeatError(
            f"[council] seat '{model}' stopped before dsh reported a final answer; no verdict for this round.",
            "invalid_output",
        )
    final = finals[-1]
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
