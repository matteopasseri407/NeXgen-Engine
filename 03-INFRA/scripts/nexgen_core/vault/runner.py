"""L'interfaccia verso i runner LLM (claude/codex/agy) usati da vault-groom.

Ogni runner CLI usa il proprio meccanismo di scoping verificato (allowlist
di strumenti per claude, sandbox `-s` per codex, `--mode` per agy), non un
set di flag condiviso -- quel dispatch invecchia in fretta, quindi vive
dietro `Runner`: un oggetto per runner con `run_readonly(prompt)` e
`run_write(prompt, workdir)`. Aggiungere un runner significa aggiungere una
classe qui e registrarla in `_RUNNERS`, nient'altro.

`opencode` è riconosciuto ma rifiutato esplicitamente: non ha un flag di
scoping per-invocazione (il suo modello di permessi vive in opencode.json,
controllato una volta per progetto), quindi non c'è modo di garantire che
la passata di sola lettura sia davvero read-only.
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

DEFAULT_TIMEOUT_SECONDS = 20 * 60


@dataclass(frozen=True)
class RunResult:
    text: str
    exit_code: int


class RunnerError(RuntimeError):
    """Base per gli errori di risoluzione/avvio di un runner."""


class RunnerNotFoundError(RunnerError):
    """Il comando del runner scelto non è sul PATH."""


class RunnerUnsupportedError(RunnerError):
    """Il runner è riconosciuto ma non supportato oggi (opencode)."""


class RunnerUnknownError(RunnerError):
    """GROOM_RUNNER non è uno dei nomi riconosciuti."""


def _kill_process_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def _run_streaming(
    cmd: list[str], *, cwd: Path | None, input_text: str | None, timeout: int
) -> RunResult:
    """Lancia `cmd`, cattura stdout+stderr uniti, applica un timeout reale.

    Popen + `start_new_session=True`, non `subprocess.run(timeout=...)`: un
    runner LLM piantato può avere lanciato dei figli propri, e
    `subprocess.run`'s timeout uccide solo il processo diretto, lasciando i
    nipoti orfani. Qui il processo entra nel proprio process group, e allo
    scadere del timeout si uccide l'intero gruppo con SIGKILL.
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
    """Contratto comune a tutti i runner LLM."""

    name = "runner"

    def __init__(self, *, model: str, vault: Path, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> None:
        self.model = model
        self.vault = vault
        self.timeout = timeout

    def run_readonly(self, prompt: str) -> RunResult:
        """La passata di sola lettura: gira contro il vault reale, senza scrivere."""
        raise NotImplementedError

    def run_write(self, prompt: str, workdir: Path) -> RunResult:
        """Il write pass: gira SOLO dentro `workdir` (il clone senza origin)."""
        raise NotImplementedError


class ClaudeRunner(Runner):
    name = "claude"

    READ_TOOLS: ClassVar[list[str]] = [
        "Read", "Grep", "Glob", "Bash(python3:*)",
        "mcp__vault-library__semantic_search", "mcp__vault-library__search_notes",
        "mcp__vault-library__read_note", "mcp__vault-library__recent_activity",
        "mcp__vault-library__list_related", "mcp__vault-library__get_start_here",
    ]
    # Push non è in questa lista (vedi anche --disallowedTools sotto): la
    # VERA garanzia resta il clone senza `origin`, questo è solo un secondo
    # strato.
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
        # `-s read-only` è una vera sandbox policy di Codex: la promessa di
        # non-mutazione è una garanzia runtime, non solo un allowlist.
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
    """Risolve il nome di `GROOM_RUNNER` in un `Runner` pronto all'uso.

    Fallisce forte QUI, prima che qualunque passata parta: `opencode` con
    la spiegazione del perché, un nome sconosciuto con l'elenco di quelli
    supportati, un comando riconosciuto ma assente dal PATH con
    l'istruzione per risolverlo -- mai un generico "command not found".
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
