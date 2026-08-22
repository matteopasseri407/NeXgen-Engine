"""The interface to the LLM runners (claude/codex/agy) used by vault-groom.

Each CLI runner uses its own verified scoping mechanism (a tool allowlist
for claude, `-s` sandbox for codex, `--mode` for agy), not a shared set of
flags -- that dispatch ages fast, so it lives behind `Runner`: one object
per runner with `run_readonly(prompt)` and `run_write(prompt, workdir)`.
Adding a runner means adding a class here and registering it in
`_RUNNERS`, nothing more.

`opencode` is recognized but explicitly rejected: it has no
per-invocation scoping flag (its permission model lives in opencode.json,
checked once per project), so there's no way to guarantee the read-only
pass is actually read-only.
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

DEFAULT_TIMEOUT_SECONDS = 20 * 60


@dataclass(frozen=True)
class RunResult:
    text: str
    exit_code: int


class RunnerError(RuntimeError):
    """Base class for runner resolution/launch errors."""


class RunnerNotFoundError(RunnerError):
    """The chosen runner's command isn't on PATH."""


class RunnerUnsupportedError(RunnerError):
    """The runner is recognized but not supported today (opencode)."""


class RunnerUnknownError(RunnerError):
    """GROOM_RUNNER isn't one of the recognized names."""


def _kill_process_group(proc: subprocess.Popen) -> None:
    try:
        if sys.platform == "win32":
            proc.kill()
        elif hasattr(os, "killpg") and hasattr(signal, "SIGKILL"):
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
    except (ProcessLookupError, PermissionError, OSError):
        pass


def _run_streaming(
    cmd: list[str], *, cwd: Path | None, input_text: str | None, timeout: int
) -> RunResult:
    """Launches `cmd`, captures merged stdout+stderr, applies a real timeout.

    Popen + `start_new_session=True`, not `subprocess.run(timeout=...)`: a
    stuck LLM runner may have spawned children of its own, and
    `subprocess.run`'s timeout kills only the direct process, leaving the
    grandchildren orphaned. Here the process enters its own process group,
    and when the timeout expires the whole group gets killed with SIGKILL.
    """
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd) if cwd else None,
        stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    try:
        out, _ = proc.communicate(input=input_text, timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_process_group(proc)
        out, _ = proc.communicate()
        out += f"\n[vault-groom: runner timed out after {timeout}s, killed]\n"
        return RunResult(text=out, exit_code=124)
    return RunResult(text=out, exit_code=proc.returncode)


class Runner:
    """Common contract for all LLM runners."""

    name = "runner"

    def __init__(self, *, model: str, vault: Path, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> None:
        self.model = model
        self.vault = vault
        self.timeout = timeout

    def run_readonly(self, prompt: str) -> RunResult:
        """The read-only pass: runs against the real vault, without writing."""
        raise NotImplementedError

    def run_write(self, prompt: str, workdir: Path) -> RunResult:
        """The write pass: runs ONLY inside `workdir` (the origin-less clone)."""
        raise NotImplementedError


class ClaudeRunner(Runner):
    name = "claude"

    READ_TOOLS: ClassVar[list[str]] = [
        "Read", "Grep", "Glob", "Bash(python3:*)",
        "mcp__vault-library__semantic_search", "mcp__vault-library__search_notes",
        "mcp__vault-library__read_note", "mcp__vault-library__recent_activity",
        "mcp__vault-library__list_related", "mcp__vault-library__get_start_here",
    ]
    # Push isn't in this list (see also --disallowedTools below): the REAL
    # guarantee remains the `origin`-less clone, this is only a second
    # layer.
    WRITE_TOOLS: ClassVar[list[str]] = [
        "Read", "Edit", "Write", "Grep", "Glob",
        "Bash(python3:*)", "Bash(git:*)", "Bash(mkdir:*)", "Bash(mv:*)",
        "mcp__vault-library__semantic_search", "mcp__vault-library__search_notes",
        "mcp__vault-library__read_note", "mcp__vault-library__list_related",
        "mcp__vault-library__update_note", "mcp__vault-library__create_note",
        "mcp__vault-library__append_note",
    ]

    def run_readonly(self, prompt: str) -> RunResult:
        cmd = ["claude", "-p", prompt, "--model", self.model, "--allowedTools", *self.READ_TOOLS]
        return _run_streaming(cmd, cwd=self.vault, input_text=None, timeout=self.timeout)

    def run_write(self, prompt: str, workdir: Path) -> RunResult:
        cmd = [
            "claude", "-p", prompt, "--model", self.model,
            "--allowedTools", *self.WRITE_TOOLS,
            "--disallowedTools", "Bash(git push:*)",
        ]
        return _run_streaming(cmd, cwd=workdir, input_text=None, timeout=self.timeout)


class CodexRunner(Runner):
    name = "codex"

    def run_readonly(self, prompt: str) -> RunResult:
        # `-s read-only` is a real Codex sandbox policy: the no-mutation
        # promise is a runtime guarantee, not just an allowlist.
        cmd = ["codex", "exec", "-s", "read-only", "-m", self.model, "-C", str(self.vault), "-"]
        return _run_streaming(cmd, cwd=None, input_text=prompt, timeout=self.timeout)

    def run_write(self, prompt: str, workdir: Path) -> RunResult:
        cmd = ["codex", "exec", "-s", "workspace-write", "-m", self.model, "-C", str(workdir), "-"]
        return _run_streaming(cmd, cwd=None, input_text=prompt, timeout=self.timeout)


class AgyRunner(Runner):
    name = "agy"

    def run_readonly(self, prompt: str) -> RunResult:
        cmd = ["agy", "--print", "--model", self.model, "--mode", "plan", "--sandbox", "--prompt", prompt]
        return _run_streaming(cmd, cwd=self.vault, input_text=None, timeout=self.timeout)

    def run_write(self, prompt: str, workdir: Path) -> RunResult:
        cmd = ["agy", "--print", "--model", self.model, "--mode", "accept-edits", "--prompt", prompt]
        return _run_streaming(cmd, cwd=workdir, input_text=None, timeout=self.timeout)


_RUNNERS: dict[str, type[Runner]] = {
    "claude": ClaudeRunner,
    "codex": CodexRunner,
    "agy": AgyRunner,
}

OPENCODE_EXPLANATION = (
    "vault-groom: GROOM_RUNNER=opencode is not supported today.\n"
    "  opencode has no per-invocation permission-scoping flag (its "
    "permission\n"
    "  model lives in opencode.json's own config, checked once per "
    "project,\n"
    "  not something this tool can safely toggle per run): there is no way "
    "to\n"
    "  guarantee the read-only pass is actually read-only, or that the "
    "write\n"
    "  pass doesn't silently inherit broader access than intended.\n"
    "  Use claude, codex, or agy, or define a dedicated restricted "
    "opencode\n"
    "  agent profile yourself and extend this runner's opencode branch."
)


def get_runner(name: str, model: str, vault: Path, *, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> Runner:
    """Resolves the `GROOM_RUNNER` name into a ready-to-use `Runner`.

    Fails loudly HERE, before any pass starts: `opencode` with the
    explanation why, an unknown name with the list of supported ones, a
    recognized command missing from PATH with the instruction to fix it --
    never a generic "command not found".
    """
    if name == "opencode":
        raise RunnerUnsupportedError(OPENCODE_EXPLANATION)
    cls = _RUNNERS.get(name)
    if cls is None:
        raise RunnerUnknownError(
            f"vault-groom: unknown GROOM_RUNNER '{name}' (supported: claude, codex, agy)"
        )
    if shutil.which(name) is None:
        raise RunnerNotFoundError(
            f"vault-groom: GROOM_RUNNER={name} but '{name}' was not found on PATH. "
            "Install it, or set GROOM_RUNNER=claude|codex|agy to one you already have."
        )
    return cls(model=model, vault=vault, timeout=timeout)
