"""How a parity probe asks each harness for one call, and how it reads the answer.

One job: turn a lane, a model and the probe message into the argv of the native
command or of OpenClaw's own agent turn, run it under a deadline with no console
window on Windows, and read back the TOTAL input tokens of the one model call it
made. Nothing here decides anything -- not a lane, not a record, not a release:
`parity.py` owns those, and the split is why a harness that renames a usage field
costs one reader here instead of the gate that blocks a release.

Why the total, and not `input_tokens`. A warm prompt is billed as a cache read and
a cold one as a cache write, so the first field alone says the second harness is
cheap when it is only cached. Every reader below adds what that client keeps
apart, on both sides of the comparison, and `docs/openclaw.md` says which fields
each client reports.

The four commands, each read from the tool's own `--help` on 2026-11-15, not from
memory: `agy --help` (`-p`, `--output-format json`, `--model`), `claude --help`
(`-p`, `--output-format json`, `--model`), `codex exec --help` (`--json`,
`-m/--model`) and `openclaw agent --help` (`--agent`, `--session-key`,
`--message`, `--json`). The version flags are `--version` on all four, and each
prints its number somewhere in the first line.
"""
from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nexgen_core import processes

#: The three CLI-backend lanes this engine measures. `google-antigravity-cli` and
#: `claude-cli` are OpenClaw's own runtime ids; Codex's is `codex` and the lane is
#: named after the binary it runs.
AGY = "google-antigravity-cli"
CLAUDE = "claude-cli"
CODEX = "codex-cli"
LANES: tuple[str, ...] = (AGY, CLAUDE, CODEX)

#: The executable each lane runs natively.
BINARIES: dict[str, str] = {AGY: "agy", CLAUDE: "claude", CODEX: "codex"}

#: What OpenClaw removes before it starts `claude` (`CLAUDE_CLI_CLEAR_ENV` in OpenClaw
#: 2026.11.2): every variable that chooses the account, the endpoint or the token.
#: The native call has to run on the same login the agent's turn runs on, the flat
#: plan's, or the two sides bill different accounts; on the gateway an
#: `ANTHROPIC_API_KEY` sits in the service's environment, and a native `claude` that
#: inherited it would call the paid API instead of the plan.
CLAUDE_CLEARED_ENV: frozenset[str] = frozenset({
    "ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY_OLD", "ANTHROPIC_API_TOKEN", "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL", "ANTHROPIC_CUSTOM_HEADERS", "ANTHROPIC_OAUTH_TOKEN", "ANTHROPIC_UNIX_SOCKET",
    "CLAUDE_CODE_API_KEY_FILE_DESCRIPTOR", "CLAUDE_CODE_OAUTH_REFRESH_TOKEN", "CLAUDE_CODE_OAUTH_SCOPES",
    "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR", "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_FOUNDRY", "CLAUDE_CODE_USE_VERTEX",
})


#: The words a run that produced no answer carries instead of an exit code.
MISSING = "missing"
TIMEOUT = "timeout"
FOLDER = "folder"

#: How long one native call may take. A model turn on a cold session reads the
#: workspace and answers: the owner's measured runs took seconds, and this ceiling
#: only exists so a hung CLI cannot hold a release check forever.
NATIVE_TIMEOUT_SECONDS = 300.0

#: How long one OpenClaw turn may take: a gateway round trip, a session and the
#: same model call, so it gets twice the native budget.
OPENCLAW_TIMEOUT_SECONDS = 600.0

#: How long a `--version` may take.
VERSION_TIMEOUT_SECONDS = 60.0


class FormatError(ValueError):
    """The answer is not the shape this module was written against."""


@dataclass(frozen=True)
class Ran:
    """One finished (or never started) command: its exit code and its two streams.

    `error` is empty when the command ran at all. A missing binary and a deadline
    are answers of their own, never exceptions: a release check reports them as
    one line instead of a traceback.
    """

    argv: tuple[str, ...] = ()
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""
    error: str = ""
    detail: str = ""


#: Runs one command in `cwd` under `timeout` and answers with what it printed.
Runner = Callable[[Sequence[str], Path, float], Ran]


def child_env(argv: Sequence[str], environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """The environment one probe command starts with: this one's, minus what OpenClaw clears.

    Only `claude` is narrowed, by `CLAUDE_CLEARED_ENV`: the other harnesses read their
    login from their own files, and `openclaw agent` must see what the gateway sees.
    """
    source = dict(os.environ if environ is None else environ)
    if argv and Path(str(argv[0])).stem.lower() == BINARIES[CLAUDE]:
        return {key: value for key, value in source.items() if key not in CLAUDE_CLEARED_ENV}
    return source


def default_runner(argv: Sequence[str], cwd: Path, timeout: float) -> Ran:
    """Run one probe command, windowless, with a deadline and no inherited stdin.

    The child is owned rather than handed to `subprocess.run`, for the reason the
    engine's other runtimes give: an npm shim whose `node` grandchild outlives it
    keeps the pipes open, so the bounded tree cleanup is what ends the run. Both
    streams are captured because a harness that fails says why on stderr. A `cwd`
    that is not there is its own answer: an agent whose workspace was never created
    is not a machine where the harness is missing.
    """
    folder = Path(cwd)
    if not folder.is_dir():
        return Ran(tuple(argv), error=FOLDER, detail=str(folder))
    try:
        proc = subprocess.Popen(
            processes.windows_command_argv(list(argv)), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8",
            errors="replace", cwd=str(cwd), env=child_env(argv), **processes.no_window_kwargs(),
        )
    except (OSError, processes.ShimEntryError):  # nexgen: allow reason="a binary that cannot be started is the missing one as far as a probe is concerned, and the lane reports it in one line"
        return Ran(tuple(argv), error=MISSING, detail=str(argv[0]))
    answered = False
    try:
        out, err = proc.communicate(timeout=timeout)
        answered = True
    except subprocess.TimeoutExpired:
        return Ran(tuple(argv), error=TIMEOUT, detail=str(int(timeout)))
    finally:
        if not answered:
            processes.force_stop_process_tree(proc)
    return Ran(tuple(argv), returncode=proc.returncode, stdout=out, stderr=err)


def native_argv(lane: str, model: str, message: str) -> tuple[str, ...]:
    """The command that runs one call of `lane` directly, as the owner measured it.

    The model is the one the agent runs, without its provider prefix: every one of
    these three binaries names its models bare. An empty model leaves the flag out
    and lets the harness use whatever it is configured with.
    """
    binary = BINARIES[lane]
    if lane == CODEX:
        argv = [binary, "exec", "--json", *(["--model", model] if model else []), message]
        return tuple(argv)
    argv = [binary, "-p", message, "--output-format", "json", *(["--model", model] if model else [])]
    return tuple(argv)


def openclaw_argv(agent: str, session_key: str, message: str) -> tuple[str, ...]:
    """The command that sends the same message to `agent` through OpenClaw.

    A fresh `--session-key` is what makes the two sides comparable: the same
    message in a cold session, never a resumed chat whose prompt is already warm.
    The reply is never delivered to a channel: `--deliver` is left out on purpose.
    """
    return ("openclaw", "agent", "--agent", agent, "--session-key", session_key,
            "--message", message, "--json")


def version_argv(binary: str) -> tuple[str, ...]:
    """The command that prints one harness's version, or OpenClaw's own."""
    return (binary, "--version")


def version_of(text: str) -> str:
    """The version number in a `--version` line, as every comparison here reads it.

    `OpenClaw 2026.11.2 (b556fdf)`, `1.5.9`, `2.1.323 (Claude Code)` and
    `codex-cli 0.155.1` all answer here: the first token that carries a digit, so
    a build hash or a product name cannot be mistaken for the version.
    """
    for token in str(text or "").split():
        cleaned = token.strip("(),;")
        if any(character.isdigit() for character in cleaned):
            return cleaned
    return ""


def documents(text: str) -> list[dict[str, Any]]:
    """Every JSON object the command printed: one document, or one per line.

    `agy` and `claude` print a single object; `codex exec --json` prints JSONL. A
    line of chatter folded into either shape is a shape this module does not
    understand, which the caller reports as one sentence -- never as a zero.
    """
    stripped = str(text or "").strip()
    if not stripped:
        return []
    try:
        whole = json.loads(stripped)
    except ValueError:
        return _json_lines(stripped)
    return [whole] if isinstance(whole, dict) else []


def _json_lines(text: str) -> list[dict[str, Any]]:
    """The objects in a JSONL answer, or a `FormatError` naming the odd line."""
    found: list[dict[str, Any]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
        except ValueError:
            raise FormatError("a line of the answer is not JSON") from None
        if isinstance(payload, dict):
            found.append(payload)
    return found


def _usage(document: dict[str, Any]) -> dict[str, Any]:
    """The usage object of one document, or a `FormatError`.

    Both shapes this module reads put the counts under `usage`, at the top level of
    the document or of the event that carries them.
    """
    usage = document.get("usage")
    if not isinstance(usage, dict):
        raise FormatError("there is no usage object")
    return usage


def _last_usage(docs: list[dict[str, Any]]) -> dict[str, Any]:
    """The usage of the last document that carries one: one turn, its own counts."""
    for document in reversed(docs):
        usage = document.get("usage")
        if isinstance(usage, dict):
            return usage
    raise FormatError("no document carries a usage object")


def _required(usage: dict[str, Any], key: str) -> int:
    """A token count this client always reports, refused when it is not a count."""
    value = usage.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise FormatError(f"usage carries no numeric {key}")
    return value


def _optional(usage: dict[str, Any], *keys: str) -> int:
    """The first of `keys` this client reports, or zero when it reports none."""
    for key in keys:
        if key in usage:
            return _required(usage, key)
    return 0


def native_input(lane: str, text: str) -> int:
    """The TOTAL input tokens of the one call the native command made.

    Anthropic-shaped usage keeps the cache out of `input_tokens`, so a Claude turn
    adds the read and the write; `agy` prints the same three names. Codex prints
    OpenAI's shape, where `cached_input_tokens` is a subset of `input_tokens`, and
    adding it again would report a number that is not a bill.
    """
    usage = _last_usage(documents(text))
    if lane == CODEX:
        return _required(usage, "input_tokens")
    if lane == CLAUDE:
        return (
            _required(usage, "input_tokens")
            + _optional(usage, "cache_creation_input_tokens")
            + _optional(usage, "cache_read_input_tokens")
        )
    if lane == AGY:
        return (
            _required(usage, "input_tokens")
            + _optional(usage, "cache_read_tokens")
            + _optional(usage, "cache_creation_input_tokens", "cache_creation_tokens", "cache_write_tokens")
        )
    raise FormatError(f"no usage reader for lane {lane!r}")


def openclaw_input(text: str) -> int:
    """The TOTAL input tokens OpenClaw recorded for the one call of that turn.

    `result.meta.agentMeta.lastCallUsage` is the last model call of the turn -- the
    only one a one-word probe makes -- and it keeps the cache apart from `input`,
    so both are added here exactly as on the native side.
    """
    docs = documents(text)
    if not docs:
        raise FormatError("the answer carried no JSON document")
    result = docs[-1].get("result")
    meta = result.get("meta") if isinstance(result, dict) else None
    agent_meta = meta.get("agentMeta") if isinstance(meta, dict) else None
    usage = agent_meta.get("lastCallUsage") if isinstance(agent_meta, dict) else None
    if not isinstance(usage, dict):
        raise FormatError("no result.meta.agentMeta.lastCallUsage in the answer")
    return _required(usage, "input") + _optional(usage, "cacheRead") + _optional(usage, "cacheWrite")
