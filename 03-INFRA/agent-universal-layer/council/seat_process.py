"""Spawn, stream, and time out one seat's vendor CLI process.

Builds the per-CLI argv/stdin/env for a seat invocation, isolates the
environment codex/agy/opencode run under (no application bearer tokens, and
for codex/opencode no on-disk MCP manifest), and streams the subprocess's
output with a deadline that distinguishes "never produced a line" (likely
quota exhaustion) from "started, then hung mid-response".
"""
from __future__ import annotations

import argparse
import json
import math
import os
import queue
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from routing import _windows_command_argv
from session import (
    _force_stop_process_tree,
    _private_mkdir,
    _set_active_proc,
    _set_private_mode,
    _write_private_text,
)

SUPPORTED_CLIS = ("opencode", "agy", "codex", "claude", "ollama")

# 2026-07-15: agy blocked from every Council mode as a PASSIVE seat, after a
# live relay incident. Reproduced 5 independent ways (council relay live,
# plus 4 direct reproductions: --sandbox on/off, prompt via stdin vs.
# positional argv, with/without --new-project, HOME overridden to an empty
# dir) -- every single one, 'agy --print' ignored BOTH --model (always
# answered as its own default model) AND the given prompt, running its own
# "Context Initialization" instead: reading real files from the operator's
# home (~/.gemini/antigravity-cli/{history.jsonl,conversation_summaries.db,
# knowledge/}), resolved independent of $HOME (an isolated HOME had zero
# effect -- checked live, not inferred). No override flag or env var exists
# for this (checked live in agy --help, agy models, and the installed
# binary's string table). One live relay run redacted a "possible secret"
# from agy's own output via the leak-scan guardrail. Every Council seat must
# be a stateless text-in/text-out oracle; this is the opposite, and it does
# not even execute the assigned task, so there's no privacy-vs-usefulness
# trade to make here -- what's blocked doesn't work at all today, for any
# task shape.
#
# This does NOT restrict agy as an ACTIVE caller of Council (a human working
# interactively in Antigravity who has it shell out to `council` itself, the
# same way any other CLI can) -- that's a structurally different code path
# (Council has no notion of "who spawned me") gated only by the same
# propose-before-auto-invoking policy that already applies to every CLI
# (AGENTS.md).
#
# Independently reviewed twice via `council challenge --seat codex-sol`
# (2026-07-15). Round 1 set the reactivation bar: re-enabling requires
# proving ALL THREE, not just isolation -- an isolated-but-prompt-ignoring
# seat is still useless as a Council oracle:
#   1. process/container-level isolation, with an access-log audit proving
#      no vault or persistent-state access;
#   2. functional conformance: a battery of nonce-based prompts, run on
#      fresh processes, answered correctly with zero "Context
#      Initialization";
#   3. a verifiable model identity, or drop the "Gemini 3.1 Pro (High)"
#      declaration if --model does not actually select anything.
# Round 2 confirmed the invoker/seat distinction above is sound, and pinned
# the enforcement requirement this comment's own guard exists to satisfy:
# the check must sit at the single point immediately preceding process
# spawn (see run_seat below), not only at the earlier fail-fast checkpoints
# -- those exist for a clean error message and relay fallback, not as the
# actual guarantee.
AGY_BLOCK_REASON = (
    "seat 'agy' blocked in every Council mode: verified live "
    "(5 independent reproductions) that 'agy --print' systematically ignores "
    "both --model and the given prompt, running its own initialization "
    "that reads real files from the operator's environment instead of "
    "answering the assigned task. Persistent state lives in fixed paths not "
    "isolable via HOME or any known environment variable (none found). Does "
    "not affect using agy as an interactive CALLER of council (unchanged). "
    "Re-enable only after proving ALL THREE: (1) process/container-level "
    "isolation verified with an access audit that excludes the vault and "
    "persistent state; (2) functional conformance on a battery of "
    "nonce-based prompts, on fresh processes, zero 'Context "
    "Initialization'; (3) verifiable model identity, or removal of the "
    "declaration if --model does not actually select anything. Details: "
    "docs/council.md, current limitations section."
)

DEFAULT_SEAT_TIMEOUT_SECONDS = 300.0
RETRYABLE_SEAT_ERROR_KINDS = frozenset({
    "empty_response",
    "invocation",
    "no_output_timeout",
    "partial_timeout",
    "process_error",
    "seat_error",
})

OPENCODE_ATTACHED_PROMPT = (
    "Read the attached file as the complete Council task and answer it exactly as instructed."
)


class SeatRunError(RuntimeError):
    def __init__(self, message: str, kind: str = "error") -> None:
        super().__init__(message)
        self.kind = kind


@dataclass
class SeatInvocation:
    """A vendor command plus the private transport it needs for the prompt."""

    argv: list[str]
    stdin_text: str | None
    output_file: Path | None
    input_file: Path | None
    # None => Popen inherits the operator's full os.environ, unchanged
    # (claude, ollama: see ISOLATED_SEAT_CLIS). A dict => the seat runs with
    # exactly that environment and nothing else (codex, agy, opencode: see
    # _isolated_seat_env).
    env: dict[str, str] | None = None


def _is_retryable_seat_error(error: SeatRunError) -> bool:
    return error.kind in RETRYABLE_SEAT_ERROR_KINDS


def _parse_timeout_seconds(value: object) -> float:
    """Validate one positive, finite timeout expressed in seconds."""
    if isinstance(value, bool):
        raise ValueError("must be a finite number greater than zero")
    try:
        seconds = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("must be a finite number greater than zero") from exc
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("must be a finite number greater than zero")
    return seconds


def _timeout_seconds_argument(value: str) -> float:
    try:
        return _parse_timeout_seconds(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _format_timeout_seconds(seconds: float) -> str:
    return f"{seconds:g}"


def _resolve_timeout_seconds(seat: dict, invocation_timeout: float | None) -> float:
    """Apply invocation override, then the seat policy, then the safe default."""
    if invocation_timeout is not None:
        return _parse_timeout_seconds(invocation_timeout)
    if "timeout_seconds" in seat:
        return _parse_timeout_seconds(seat["timeout_seconds"])
    return DEFAULT_SEAT_TIMEOUT_SECONDS


def _effort_forwarding(seat: dict) -> tuple[list[str], str]:
    """Single source for how a seat's reasoning_effort becomes both a real
    CLI flag and the human-facing label -- used by _build_seat_command (the
    actual argv) AND _effort_label (propose / static menu / routing-status).
    Before this existed the two had already drifted apart once
    (beta-readiness review, 2026-07-13): the label showed reasoning_effort
    identically for every CLI even though only claude/codex/ollama/opencode
    actually forwarded it to a real flag, and separately ollama's own
    downmapping/dropping logic lived only in _build_seat_command with
    nothing on the label side to say so. One mapping, both call sites.

    Per-CLI semantics:
    - claude: --effort <v> verbatim.
    - codex: -c model_reasoning_effort=<v> verbatim.
    - opencode: --variant <v> verbatim (provider-specific, no fixed enum to
      validate against here -- see the long comment in _build_seat_command).
    - agy: no reasoning-effort CLI flag exists at all (verified via
      `agy --help`): never a flag, always the caveat on the label.
    - ollama: --think only documents low/medium/high (`ollama run --help`).
      xhigh/max (valid claude/codex tiers) are downmapped to --think high
      rather than dropped, with the label saying so. Anything else ollama
      doesn't know is dropped with no flag, same as before, with the label
      saying so instead of silently looking identical to a seat that
      genuinely forwarded it.
    """
    effort = seat.get("reasoning_effort")
    if not effort or effort == "none":
        return [], ""
    cli = seat.get("cli")
    label = f", effort {effort}"
    if cli == "claude":
        return ["--effort", str(effort)], label
    if cli == "codex":
        return ["-c", f'model_reasoning_effort="{effort}"'], label
    if cli == "opencode":
        return ["--variant", str(effort)], label
    if cli == "agy":
        return [], f"{label} (not applied by this CLI)"
    if cli == "ollama":
        if effort in ("low", "medium", "high"):
            return ["--think", effort], label
        if effort in ("xhigh", "max"):
            return ["--think", "high"], f"{label} (mapped to high for ollama)"
        return [], f"{label} (not applied: value not supported by ollama)"
    return [], label


def _effort_label(seat: dict) -> str:
    """Shared by every place that renders a seat's reasoning_effort to the
    user (propose, the static menu, routing-status): a single source so a
    per-CLI caveat can't drift out of sync between them the way it already
    had (beta-readiness review, 2026-07-13). Delegates to _effort_forwarding
    so this label always reflects exactly what _build_seat_command does --
    no parallel hardcoded per-CLI list here."""
    return _effort_forwarding(seat)[1]


# Seats whose vendor CLI can reach an MCP server (see the long note below
# _build_seat_command): every seat here is launched with an *explicit*
# environment, never a full copy of the operator's os.environ. claude and
# ollama are deliberately absent -- claude because --tools "" already makes
# every tool, MCP included, uninvocable by construction, and ollama because
# it never gets the (opt-in, unused here) --experimental flag that would
# give it a tool-calling surface at all. Neither needs env-level isolation
# on top of a guarantee that already holds at the process-capability level.
ISOLATED_SEAT_CLIS = ("codex", "agy", "opencode")

# Explicit ALLOWLIST, not a denylist of today's known application tokens.
# A denylist only excludes names someone remembered to add to it; the next
# service this machine grows a bearer token for (n8n, the vault library and
# Firecrawl already exist -- see the audit finding) would leak into every
# isolated seat by default until someone noticed and patched the blocklist.
# An allowlist inverts that: anything not named here is absent by
# construction, including tokens that do not exist yet.
_ISOLATED_SEAT_ENV_ALLOWLIST = (
    # POSIX: process/user identity, locale, the CLI's own runtime plumbing.
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "TERM", "TMPDIR",
    "XDG_RUNTIME_DIR", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME",
    # Windows equivalents (council.ps1/Windows CI: same code path, not
    # separately re-verified live -- kept conservative rather than
    # excluded, since a missing USERPROFILE/APPDATA is the kind of gap
    # that silently breaks a seat rather than loudly failing it).
    "USERPROFILE", "APPDATA", "LOCALAPPDATA", "SystemRoot", "SystemDrive",
    "ComSpec", "PATHEXT", "windir", "TEMP", "TMP",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy",
)


def _isolated_seat_env(cli: str, session_dir: Path) -> dict[str, str]:
    """Minimal environment for a codex/agy/opencode seat (audit FINDING A,
    2026-07-12): the Popen that launches these three CLIs used to omit
    ``env=`` entirely, so the child inherited os.environ in full -- every
    applicative bearer token this machine holds (``N8N_MCP_TOKEN``,
    ``VAULT_LIBRARY_TOKEN``, ``FIRECRAWL_*``) plus the real, non-sandboxed
    vendor config was reachable by a seat, even though the seat prompt is
    told in words only (see the relay/no-tools prompts) never to use them.
    ``-s read-only``/``--sandbox`` scope the shell tool, not MCP servers
    (see the long note above ``_build_seat_command``), so the prompt was
    the only thing standing between a prompt-injected diff and a real MCP
    call with real credentials. This closes the env half of that gap.

    Two layers, applied per seat and verified live against the installed
    binaries on this machine on 2026-07-12 (not merely inferred from
    ``--help``):

    1. Replace, never extend, the child's environment with the short
       allowlist above: nothing not named there can be present, whether or
       not it exists yet.
    2. Where a config-isolation mechanism exists AND was confirmed live not
       to break the seat's own model resolution, point the CLI's config
       lookup at an empty, session-private directory so the MCP server
       list itself never loads (stronger than (1) alone, which only denies
       *credential* substitution into a server entry that may still be
       declared on disk):

       - codex: ``CODEX_HOME`` -> a fresh dir under this session containing
         only a copy of the real ``auth.json`` (no ``config.toml``, so no
         ``[mcp_servers.*]`` table is ever read). Verified with
         ``codex doctor`` under that isolated ``CODEX_HOME``: "0 MCP
         servers", "stored ChatGPT tokens: true", 0 warn / 0 fail. ``codex
         --ignore-user-config`` was considered instead (see the note above
         _build_seat_command) and rejected there for an unverified risk of
         breaking the seat; copying only ``auth.json`` into an isolated
         ``CODEX_HOME`` sidesteps that -- it was verified end to end, not
         merely inferred from a flag's documented scope.
       - opencode: ``XDG_CONFIG_HOME`` -> a fresh, empty dir under this
         session (no ``opencode.json``, so no ``"mcp"`` key is ever read).
         Auth lives under ``XDG_DATA_HOME``
         (``~/.local/share/opencode/auth.json``), which this function does
         not touch, so login survives. Verified live: ``opencode debug
         paths`` (only the config path moves), ``opencode providers list``
         (credentials still listed) and ``opencode models`` (the built-in
         ``opencode``/``opencode-go`` providers this project's seats use --
         see seats.yaml -- still resolve with no config file present, since
         they are bundled with the CLI, not declared in opencode.json).
       - agy: no config-isolation mechanism found. Antigravity's MCP
         manifest lives at a hardcoded ``~/.gemini/antigravity/
         mcp_config.json`` with no override flag in ``agy --help`` and no
         candidate env var found in the installed binary's string table
         (checked live, not just documentation). Layer (1) still helps
         concretely here: that manifest's own server entries interpolate
         their bearer token from the *child process's* environment at
         connect time (``"Authorization: Bearer ${N8N_MCP_TOKEN}"``,
         verified by reading the manifest), so even though the server
         entry is still listed, the credential it needs is absent from
         this seat's environment. Documented as a known residual gap, same
         posture as the CLI-sandbox-flag gap above ``_build_seat_command``:
         mitigated as far as verified, not claimed closed.

    The isolated directories live under ``session_dir`` (already private,
    mode 0700 -- see ``new_session_dir``) and share its lifecycle: removed
    with the rest of the session on the normal path, hardened to 0700/0600
    alongside it when ``--keep-session`` is used.
    """
    env = {name: os.environ[name] for name in _ISOLATED_SEAT_ENV_ALLOWLIST if name in os.environ}
    isolation_dir = Path(tempfile.mkdtemp(prefix=f"council-env-{cli}-", dir=session_dir))
    _set_private_mode(isolation_dir, 0o700)

    if cli == "codex":
        codex_home = isolation_dir / "codex-home"
        _private_mkdir(codex_home)
        _set_private_mode(codex_home, 0o700)
        real_codex_home = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))
        real_auth = real_codex_home / "auth.json"
        if real_auth.is_file():
            auth_copy = codex_home / "auth.json"
            auth_copy.write_bytes(real_auth.read_bytes())
            _set_private_mode(auth_copy, 0o600)
        env["CODEX_HOME"] = str(codex_home)
        if "OPENAI_API_KEY" in os.environ:
            # An alternative to the auth.json/ChatGPT-login flow above;
            # some installs authenticate codex this way instead. Not a
            # Council application token, so it is not excluded by the
            # allowlist rule above -- it is simply not on it by default.
            env["OPENAI_API_KEY"] = os.environ["OPENAI_API_KEY"]
    elif cli == "opencode":
        config_home = isolation_dir / "opencode-config"
        _private_mkdir(config_home)
        _set_private_mode(config_home, 0o700)
        env["XDG_CONFIG_HOME"] = str(config_home)
    # agy: base allowlist only -- see the docstring above for what was
    # checked and why no directory isolation was applied.

    return env


def _write_transport_file(session_dir: Path, prompt: str) -> Path:
    """Create a short-lived private file for a CLI that accepts attachments.

    The session directory is already private.  ``mkstemp`` also gives the file
    mode 0600 before it is populated, so there is no permissive creation window.
    The caller always unlinks this transport file, including after a failed seat.
    """
    fd, tmp_name = tempfile.mkstemp(prefix="council-prompt-", suffix=".md", dir=session_dir)
    os.close(fd)
    path = Path(tmp_name)
    try:
        _write_private_text(path, prompt)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return path


def _feed_stdin(stream, prompt: str) -> None:
    """Write a potentially large prompt without blocking the output watchdog."""
    try:
        stream.write(prompt)
        stream.flush()
    except (BrokenPipeError, OSError, ValueError):
        # The child process will return its own diagnostic if it exits before
        # consuming stdin.  Do not mask that with a writer-thread traceback.
        pass
    finally:
        try:
            stream.close()
        except (OSError, ValueError):
            pass


def _drain_lines(stream, line_queue: queue.Queue[str | None]) -> None:
    for line in stream:
        line_queue.put(line)
    line_queue.put(None)


def _drain_text(stream, sink: list[str]) -> None:
    for line in stream:
        sink.append(line)


def _build_seat_command(seat: dict, prompt: str, session_dir: Path) -> SeatInvocation:
    """Build a vendor command without putting the user prompt in ``argv``.

    Codex documents ``-`` as stdin.  Antigravity's print mode consumes stdin
    when no positional prompt is supplied.  OpenCode exposes file attachments,
    so it receives a static instruction plus a protected prompt file instead.
    Codex keeps its final response in a separate protected output file because
    its stdout also carries progress and warnings.

    CLI-level enforcement of "no tools" is NOT equivalent across the five
    CLIs below, even though every seat prompt (``council/prompts/*.md``)
    carries the same textual "non hai strumenti e non devi usarne" line as a
    baseline.  Verified 2026-07-12 against the real installed binaries
    (``--help`` output plus, where the flag's actual scope was ambiguous
    from ``--help`` alone, live invocations inspected via the CLI's own
    JSONL session logs). NOTE: the per-CLI gaps documented below are about
    *argv flags* (sandbox/tool scoping) only. ``codex``/``agy``/``opencode``
    additionally run under the env-level isolation built by
    ``_isolated_seat_env`` (no application tokens, and for codex/opencode
    also no on-disk MCP manifest) -- read that function's docstring first,
    this one is about the residual, still-open gap on top of it:

    - ``claude``: ``--tools ""`` is a comprehensive, documented, CLI-enforced
      block — no tool is invocable by construction, independent of the
      prompt. This is the only seat where "no tools" is a guarantee, not a
      request.
    - ``codex``: ``-s read-only`` is documented by ``codex exec --help`` as
      scoping "the sandbox policy... when executing model-generated shell
      commands" — i.e. the shell/exec tool only. It says nothing about MCP
      servers, and live testing (comparing ``-s read-only`` against
      ``-s danger-full-access`` via the rollout JSONL, and testing
      ``--ignore-user-config`` to see if it drops MCP servers from the
      session) found no flag that closes that gap without an unacceptable
      risk of silently breaking the seat's output. Codex relies on the
      textual prompt instruction, same as opencode below.
    - ``agy``: ``--sandbox`` is described by Antigravity's own embedded
      product docs (extracted from the installed binary) as scoping the
      "run_command" tool specifically ("no network access... unless added
      explicitly by the user") — again shell/terminal-command scoped, not a
      documented MCP block. No ``--no-mcp``/``--tools`` equivalent flag
      exists in ``agy --help``. Live verification of whether this also
      blocks the CLI's own MCP tool calls was not possible during this
      review (Antigravity subscription quota was exhausted on every model
      tried); treat this CLI as prompt-only until someone confirms
      otherwise live.
    - ``opencode``: no CLI-level tool/MCP block exists at all. ``--pure``
      disables external *plugins*, a different subsystem from MCP servers.
      ``OPENCODE_CONFIG``/``OPENCODE_CONFIG_CONTENT`` were tested and found
      to merge with (not replace) the user's global ``opencode.json``, so
      they cannot isolate a run from already-configured MCP servers. A
      working per-server ``"mcp": {"<name>": {"enabled": false}}`` override
      does exist (confirmed via ``opencode debug config``), but applying it
      here would require shelling out to an undocumented ``debug``
      subcommand to enumerate the user's server names before every seat
      invocation — fragile, version-unstable, and not worth the added
      failure surface for an unverified gain. Prompt-only, like codex.
    - ``ollama``: ``ollama run <model>`` has no tool-calling/agent-loop
      surface at all unless the (undocumented-by-default) ``--experimental``
      flag is passed — confirmed via ``ollama run --help`` against the
      installed Ollama build (re-verify after an Ollama upgrade). This seat
      never passes it, so there is nothing for an MCP server to be
      reachable through: verified safe by construction, not just by prompt.

    See the "Council CLI-level enforcement is asymmetric" note in
    ``instructions/AGENTS.md`` (next to the "Council exception" paragraph)
    for the user-facing version of this same finding.
    """
    cli = seat["cli"]
    model = seat["model"]
    if cli == "opencode":
        input_file = _write_transport_file(session_dir, prompt)
        argv = [
            "opencode", "run", OPENCODE_ATTACHED_PROMPT,
            "-m", model, "--format", "json", "--file", str(input_file),
            "--dir", str(session_dir),
        ]
        # --variant is opencode's real reasoning-effort control (verified via
        # `opencode run --help`: "model variant (provider-specific reasoning
        # effort, e.g., high, max, minimal)"), same concept as claude's
        # --effort above. Forwarded as-is: unlike ollama's --think, opencode
        # documents this as provider-specific with no fixed enum this script
        # could validate against, so an unrecognized value is the target
        # provider's problem to reject, not something to filter here. See
        # _effort_forwarding: single source shared with _effort_label.
        extra_argv, _label = _effort_forwarding(seat)
        argv.extend(extra_argv)
        return SeatInvocation(
            argv,
            None,
            None,
            input_file,
            env=_isolated_seat_env(cli, session_dir),
        )
    if cli == "agy":
        # Print mode reads stdin when no positional prompt is supplied.  Keeping
        # the brief out of argv avoids both the Windows command-line cap and the
        # POSIX single-argument cap.
        # --sandbox = restrizioni sul tool run_command (niente rete/filesystem
        # fuori workspace per i comandi shell), mai --dangerously-skip-permissions.
        # Non e' un blocco MCP documentato: vedi la nota estesa sopra
        # _build_seat_command per cosa e' verificato e cosa no per questa CLI.
        return SeatInvocation(
            ["agy", "--print", "--model", model, "--sandbox"],
            prompt,
            None,
            None,
            env=_isolated_seat_env(cli, session_dir),
        )
    if cli == "claude":
        # --tools "" already makes every tool, MCP included, uninvocable by
        # construction (see the note above): no env-level isolation needed
        # or applied here, unlike codex/agy/opencode. Full os.environ, same
        # as before this fix.
        argv = [
            "claude", "--print", "--model", model,
            "--permission-mode", "plan", "--tools", "", "--no-session-persistence",
            "--output-format", "json",
        ]
        # See _effort_forwarding: single source shared with _effort_label.
        extra_argv, _label = _effort_forwarding(seat)
        argv.extend(extra_argv)
        return SeatInvocation(argv, prompt, None, None)
    if cli == "codex":
        # ``codex exec -`` reads the initial prompt from stdin.  Without -o,
        # stdout includes banner/warning/progress beyond the final answer.
        # -s read-only is the same sandbox validated in A0, with no write access
        # for the consultant seat. It scopes the shell/exec tool only, not MCP
        # servers: see the extended note above _build_seat_command.
        # dir=session_dir: the session dir is already private (0700, created by
        # new_session_dir()) -- without this, mkstemp() falls back to the shared
        # system temp dir, where the codex seat's raw response briefly lives
        # outside any of the access controls the rest of the session gets.
        fd, tmp_name = tempfile.mkstemp(prefix="council-codex-", suffix=".txt", dir=session_dir)
        os.close(fd)
        output_file = Path(tmp_name)
        # --skip-git-repo-check: codex exec refuses to start when its CWD is
        # not a git repo / trusted directory, and the seat subprocess inherits
        # whatever directory the user happened to run council from -- often
        # not one (found on the first real multi-vendor run, 2026-07-13). The
        # flag makes startup deterministic regardless of caller CWD. Safe here
        # because the seat is read-only sandboxed and consumes only the piped
        # prompt, never the surrounding directory.
        argv = ["codex", "exec", "-", "-m", model, "--skip-git-repo-check"]
        # See _effort_forwarding: single source shared with _effort_label.
        extra_argv, _label = _effort_forwarding(seat)
        argv.extend(extra_argv)
        argv.extend(["-s", "read-only", "-o", str(output_file)])
        return SeatInvocation(
            argv,
            prompt,
            output_file,
            None,
            env=_isolated_seat_env(cli, session_dir),
        )
    if cli == "ollama":
        # --think <low|medium|high> is ollama's real reasoning-effort control
        # (verified via `ollama run --help`), same concept as claude's
        # --effort / codex's model_reasoning_effort above. See
        # _effort_forwarding for the low/medium/high passthrough, the
        # xhigh/max downmapping to --think high, and the drop-with-no-flag
        # fallback for anything else ollama doesn't document -- single
        # source shared with _effort_label.
        argv = ["ollama", "run", model]
        extra_argv, _label = _effort_forwarding(seat)
        argv.extend(extra_argv)
        return SeatInvocation(argv, prompt, None, None)
    raise SeatRunError(
        f"[council] cli '{cli}' not supported (expected: {', '.join(SUPPORTED_CLIS)}).", "unsupported_cli"
    )


def _parse_claude_result(raw: str, expected_model: str) -> tuple[str, dict]:
    """Extract Claude's answer and prove the explicitly requested model ran.

    Claude has no free-standing model inventory command, but non-interactive
    JSON results expose the actual canonical model in ``modelUsage``. Council
    already passes ``--model``; checking the result closes the remaining gap
    without making a proposal itself spend subscription quota.
    """
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SeatRunError(
            "[council] Claude returned unreadable JSON; the selected model cannot be verified.",
            "claude_json",
        ) from exc
    if not isinstance(payload, dict) or payload.get("is_error"):
        raise SeatRunError("[council] Claude returned an error result.", "seat_error")

    model_usage = payload.get("modelUsage")
    if not isinstance(model_usage, dict) or not model_usage:
        raise SeatRunError(
            "[council] Claude returned no modelUsage; the selected model cannot be verified.",
            "model_unverified",
        )
    reported: set[str] = set()
    for key, details in model_usage.items():
        if isinstance(key, str) and key.strip():
            reported.add(key.strip().casefold())
        if isinstance(details, dict):
            canonical = details.get("canonicalModel")
            if isinstance(canonical, str) and canonical.strip():
                reported.add(canonical.strip().casefold())
    expected = expected_model.strip().casefold()
    if reported != {expected}:
        shown = ", ".join(sorted(reported)) or "(none)"
        raise SeatRunError(
            f"[council] Claude ran {shown}, not the declared model {expected_model}.",
            "model_mismatch",
        )

    result = payload.get("result")
    if not isinstance(result, str) or not result.strip():
        raise SeatRunError(
            "[council] Claude responded but returned no usable result text.",
            "empty_response",
        )
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    usage = {**usage, "cost": payload.get("total_cost_usd"), "model": expected_model}
    return result, usage


def run_seat(
    seat: dict,
    prompt: str,
    session_dir: Path,
    timeout_seconds: float | None = None,
) -> tuple[str, dict]:
    """Legge stdout in streaming (non subprocess.run in blocco): un timeout senza
    aver mai ricevuto una riga e' un segnale diagnostico diverso da un timeout a
    meta' risposta (es. quota abbonamento esaurita o blocco lato provider senza
    errore visibile lato client, verificato dal vivo su un seat a quota esaurita:
    TimeoutExpired non porta output parziale, va letto mentre arriva). Il parsing
    dell'output varia per CLI: opencode emette eventi JSONL (`--format json`),
    Claude restituisce un singolo oggetto JSON verificabile, le altre CLI
    supportate stampano testo semplice."""
    model = seat["model"]
    cli = seat["cli"]
    # AUTHORITATIVE enforcement point (2026-07-15, see AGY_BLOCK_REASON above
    # SUPPORTED_CLIS): the single spot immediately before a seat's process is
    # ever built or spawned, independent of and not merely backed up by the
    # earlier fail-fast checks in _check_seat_allowed / _run_relay_stage.
    # Every call path (run_rounds, _run_relay_stage) funnels through here --
    # `cli` is schema-validated to a canonical SUPPORTED_CLIS string with no
    # aliasing (config_schema.py), so this equality check cannot be bypassed
    # by an alternate spelling, wrapper, or path.
    if cli == "agy":
        raise SeatRunError(f"[council] {AGY_BLOCK_REASON}", "agy_blocked")
    try:
        resolved_timeout_seconds = _resolve_timeout_seconds(seat, timeout_seconds)
    except ValueError as exc:
        raise SeatRunError(f"[council] invalid timeout for seat '{model}': {exc}.", "invalid_timeout") from exc
    timeout_label = _format_timeout_seconds(resolved_timeout_seconds)
    invocation = _build_seat_command(seat, prompt, session_dir)
    stdin_writer: threading.Thread | None = None
    try:
        try:
            proc = subprocess.Popen(
                _windows_command_argv(invocation.argv),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.PIPE if invocation.stdin_text is not None else subprocess.DEVNULL,
                text=True,
                # Windows otherwise uses the active ANSI code page (commonly
                # cp1252) for text pipes. Codex requires UTF-8 on
                # ``codex exec -`` stdin, and every other Council seat accepts
                # UTF-8, so keep the shared transport deterministic.
                encoding="utf-8",
                # None => inherit os.environ (claude, ollama, unchanged).
                # dict => exactly that environment, nothing else (codex,
                # agy, opencode). See _isolated_seat_env.
                env=invocation.env,
            )
        except OSError as e:
            raise SeatRunError(f"[council] unable to invoke the seat: {e}", "invocation")
        _set_active_proc(proc)

        if invocation.stdin_text is not None:
            stdin_writer = threading.Thread(
                target=_feed_stdin,
                args=(proc.stdin, invocation.stdin_text),
                daemon=True,
            )
            stdin_writer.start()

        line_queue: queue.Queue[str | None] = queue.Queue()
        stderr_lines: list[str] = []
        stdout_reader = threading.Thread(target=_drain_lines, args=(proc.stdout, line_queue), daemon=True)
        stderr_reader = threading.Thread(target=_drain_text, args=(proc.stderr, stderr_lines), daemon=True)
        stdout_reader.start()
        stderr_reader.start()

        text_chunks = []
        usage = {}
        got_any_line = False
        deadline = time.monotonic() + resolved_timeout_seconds

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _force_stop_process_tree(proc)
                if not got_any_line:
                    raise SeatRunError(
                        f"[council] seat '{model}' did not respond within {timeout_label}s "
                        "without producing any output: likely subscription quota exhausted or a "
                        "provider-side block (no diagnosable error from the client). Verify manually "
                        "before retrying.",
                        "no_output_timeout",
                    )
                raise SeatRunError(
                    f"[council] seat '{model}' started responding but did not finish within "
                    f"{timeout_label}s: timeout mid-response, no verdict for this round.",
                    "partial_timeout",
                )
            try:
                line = line_queue.get(timeout=remaining)
            except queue.Empty:
                continue
            if line is None:
                break
            got_any_line = True
            if cli == "opencode":
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    event = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                if event.get("type") == "error":
                    _force_stop_process_tree(proc)
                    raise SeatRunError(f"[council] error from seat: {event.get('error')}", "seat_error")
                part = event.get("part") or {}
                if event.get("type") == "text" and "text" in part:
                    text_chunks.append(part["text"])
                if event.get("type") == "step_finish":
                    usage = {"tokens": part.get("tokens"), "cost": part.get("cost")}
            else:
                # Claude emits one JSON object; agy/codex/ollama emit plain
                # text. For codex the authoritative answer arrives later from
                # output_file; stdout is used only for liveness diagnostics.
                text_chunks.append(line)

        stdout_reader.join(timeout=5)
        stderr_reader.join(timeout=5)
        # The streaming loop above is bounded by the seat timeout, but
        # proc.wait() here is not: a seat that closed its stdout (EOF) while
        # still running -- explicit fd close, or a child that inherited the
        # pipe and lingers -- used to block this call forever, with no
        # deadline and no kill. Give the wait the same remaining budget and
        # the same kill fallback as the streaming phase (2026-08-15
        # review, council of Opus 5).
        #
        # hung is raised BEFORE the kill and drives the classification:
        # deducing the timeout from the exit code would misfire in the
        # normal case (the post-kill wait returns -9/-15, a real POSIX
        # signal exit, while the cause was our own timeout) and -2 would
        # collide with SIGINT (2026-08-15 council-2, Opus 5).
        hung = False
        # EOF does not mean the verdict is lost: the seat may have flushed
        # stdout and still be writing its output file (codex) or finishing
        # up, so the post-EOF wait keeps the seat's own remaining deadline
        # -- NOT a short fixed grace, which would kill a healthy slow seat
        # mid-write (2026-08-15 council-4, Opus 5) -- with a realistic
        # minimum so an already-expired deadline still gives the process a
        # moment to finish instead of killing it instantly. A 1s floor was
        # too tight: codex that streams until the last 0.5s and then needs
        # ~3s to write its output file got killed mid-write, leaving a
        # truncated file on disk (2026-08-15 council-7, Opus 5).
        remaining = deadline - time.monotonic()
        _POST_EOF_GRACE = 10.0
        try:
            returncode = proc.wait(timeout=max(remaining, _POST_EOF_GRACE))
        except subprocess.TimeoutExpired:
            hung = True
            _force_stop_process_tree(proc)
            # Re-join after the kill: the fd is now at EOF, so the readers
            # finish -- joining before building the diagnostic avoids reading
            # stderr_lines while a live thread may still be appending to it
            # (2026-08-15 council, Opus 5).
            stdout_reader.join(timeout=5)
            stderr_reader.join(timeout=5)
            try:
                returncode = proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                returncode = None  # unkillable; sentinel outside the int space
        if hung:
            # The seat was killed, but its complete stdout answer may have
            # arrived before it hung (it closed stdout and lingered in
            # teardown): throw a usable verdict away only when there is
            # none (2026-08-15 council-7, Opus 5). read_text is guarded:
            # the kill can leave a file truncated mid-multibyte-sequence or
            # removed, which must degrade to the diagnostic, not explode as
            # an unclassified UnicodeDecodeError (2026-08-15 council-8,
            # Opus 5).
            if invocation.output_file is not None and invocation.output_file.is_file():
                try:
                    output_text = invocation.output_file.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    output_text = ""
                if output_text.strip():
                    return output_text, usage
            if text_chunks and invocation.output_file is None:
                # stdout IS the answer only for CLIs without an output file
                # (opencode/agy/ollama). codex writes the answer to
                # output_file; its stdout is liveness noise ("codex
                # started"), so returning it as a verdict would hand the
                # round a fake response instead of the diagnostic
                # (2026-08-15 council-8, Opus 5).
                if cli == "claude":
                    try:
                        return _parse_claude_result("".join(text_chunks), model)
                    except SeatRunError:
                        # Output that does not parse as a claude verdict is
                        # not a usable answer from a hung seat: fall through
                        # to the partial_timeout diagnostic (2026-08-15
                        # council-7, Opus 5).
                        pass
                else:
                    return "".join(text_chunks), usage
            if returncode is None:
                raise SeatRunError(
                    f"[council] seat '{model}' hung after closing its output and could not be "
                    f"terminated within {timeout_label}s: no verdict for this round.",
                    "partial_timeout",
                )
            raise SeatRunError(
                f"[council] seat '{model}' hung after closing its output and was killed after "
                f"{timeout_label}s with no usable verdict: no response for this round.",
                "partial_timeout",
            )
        if returncode != 0:
            raise SeatRunError(f"[council] the seat did not respond (exit {returncode}):\n{''.join(stderr_lines)}", "process_error")

        if invocation.output_file is not None:
            output_text = invocation.output_file.read_text(encoding="utf-8") if invocation.output_file.is_file() else ""
            if not output_text.strip():
                raise SeatRunError("[council] the seat responded but with no usable text (empty output).", "empty_response")
            return output_text, usage

        if not text_chunks:
            raise SeatRunError("[council] the seat responded but with no usable text (empty output).", "empty_response")
        if cli == "claude":
            return _parse_claude_result("".join(text_chunks), model)
        return "".join(text_chunks), usage
    finally:
        _set_active_proc(None)
        if stdin_writer is not None:
            stdin_writer.join(timeout=5)
        if invocation.output_file is not None:
            invocation.output_file.unlink(missing_ok=True)
        if invocation.input_file is not None:
            invocation.input_file.unlink(missing_ok=True)
