"""The relay (F4 v0): one bounded hand-off to another installed CLI.

The lane can ask another CLI a question and bring the answer back. The
invocation mirrors the Council seat posture that was verified live there:
env allowlist, isolated config directories for codex/opencode, read-only
sandbox flags, no MCP credentials, hard timeouts, capped output, one audit
receipt per call. The answer is displayed to the user; it is never fed back
into a mutating chain automatically, and the relayed CLI never gets write
access.

v0 supports ``claude``, ``codex`` and ``opencode``. ``agy`` is deliberately
absent: its isolation is prompt-only (documented in the Council findings),
which is not enough for an unattended relay.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from .config import LaneConfig
from .tools import audit_event

RELAY_CLIS = ("claude", "codex", "opencode")
DEFAULT_TIMEOUT = 600
MAX_OUTPUT = 20_000
MAX_ATTACH = 60_000

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

RELAY_PREFIX = (
    "Sei un consulente in sandbox di sola lettura. Rispondi in italiano, conciso e concreto. "
    "Non hai strumenti e non devi usarne: non toccare file, non eseguire comandi. "
)
OPENCODE_ATTACHED = "Read the attached file as the complete task and answer it exactly as instructed."

#: Same allowlist as the Council seats: nothing not named here reaches a
#: sandboxed child (no application bearer tokens), except the two deliberate
#: per-CLI exceptions documented in _isolated_env.
ENV_ALLOWLIST = (
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "TERM", "TMPDIR",
    "XDG_RUNTIME_DIR", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME",
    "USERPROFILE", "APPDATA", "LOCALAPPDATA", "SystemRoot", "SystemDrive",
    "ComSpec", "PATHEXT", "windir", "TEMP", "TMP",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy",
)


class RelayError(RuntimeError):
    """The relay was refused or the target CLI failed."""


@dataclass
class RelayResult:
    cli: str
    model: str
    answer: str
    elapsed_s: float
    returncode: int
    truncated: bool = False


def available_clis() -> list[str]:
    return [cli for cli in RELAY_CLIS if shutil.which(cli)]


def _isolated_env(cli: str, workdir: Path) -> dict[str, str]:
    if cli == "claude":
        # --tools "" already makes every tool, MCP included, uninvocable by
        # construction (verified in the Council), so no env isolation is
        # needed or applied here.
        return dict(os.environ)
    env = {name: os.environ[name] for name in ENV_ALLOWLIST if name in os.environ}
    if cli == "codex":
        home = workdir / "codex-home"
        home.mkdir(parents=True, exist_ok=True)
        os.chmod(home, 0o700)
        real_home = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))
        real_auth = real_home / "auth.json"
        if real_auth.is_file():
            auth_copy = home / "auth.json"
            auth_copy.write_bytes(real_auth.read_bytes())
            os.chmod(auth_copy, 0o600)
        env["CODEX_HOME"] = str(home)
        if "OPENAI_API_KEY" in os.environ:
            env["OPENAI_API_KEY"] = os.environ["OPENAI_API_KEY"]
    elif cli == "opencode":
        config_home = workdir / "opencode-config"
        config_home.mkdir(parents=True, exist_ok=True)
        os.chmod(config_home, 0o700)
        env["XDG_CONFIG_HOME"] = str(config_home)
    return env


def _argv(cli: str, model: str, prompt_file: Path, output_file: Path) -> tuple[list[str], str | None]:
    """Return (argv, stdin_text); opencode takes the prompt as an attachment."""
    if cli == "claude":
        argv = [
            "claude", "--print", "--model", model,
            "--permission-mode", "plan", "--tools", "", "--no-session-persistence",
            "--output-format", "json",
        ]
        return argv, prompt_file.read_text(encoding="utf-8")
    if cli == "codex":
        argv = [
            "codex", "exec", "-", "-m", model, "--skip-git-repo-check",
            "-s", "read-only", "-o", str(output_file),
        ]
        return argv, prompt_file.read_text(encoding="utf-8")
    if cli == "opencode":
        argv = ["opencode", "run", OPENCODE_ATTACHED, "-m", model, "--file", str(prompt_file)]
        return argv, None
    raise RelayError(f"CLI non supportato in v0: {cli} (supportati: {', '.join(RELAY_CLIS)})")


def _extract(cli: str, stdout: str, output_file: Path) -> str:
    if cli == "claude":
        try:
            payload = json.loads(stdout)
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            result = payload.get("result") or payload.get("content") or ""
            if isinstance(result, str) and result.strip():
                return result
        return stdout
    if cli == "codex":
        if output_file.is_file():
            text = output_file.read_text(errors="replace").strip()
            if text:
                return text
        return stdout
    # opencode: plain text with progress lines and ANSI escapes
    lines = [line for line in ANSI_RE.sub("", stdout).splitlines() if not line.startswith("> ")]
    return "\n".join(lines).strip()


def run_relay(
    cfg: LaneConfig,
    cli: str,
    model: str,
    prompt: str,
    *,
    attach: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> RelayResult:
    """Run one read-only, isolated hand-off and return the answer."""
    if cli not in RELAY_CLIS:
        raise RelayError(f"CLI non supportato in v0: {cli} (supportati: {', '.join(RELAY_CLIS)})")
    if not shutil.which(cli):
        raise RelayError(f"{cli} non installato su questa macchina")
    if not prompt.strip():
        raise RelayError("prompt vuoto")

    full_prompt = RELAY_PREFIX + prompt.strip()
    truncated = False
    if attach:
        try:
            text = Path(attach).read_text(errors="replace")
        except OSError as exc:
            raise RelayError(f"allegato non leggibile: {exc}") from exc
        if len(text) > MAX_ATTACH:
            text = text[:MAX_ATTACH] + "\n[...allegato troncato]"
            truncated = True
        full_prompt += f"\n\n--- contenuto allegato ({attach}) ---\n{text}"

    workdir = Path(tempfile.mkdtemp(prefix="nexgen-relay-"))
    prompt_file = workdir / "prompt.txt"
    prompt_file.write_text(full_prompt, encoding="utf-8")
    output_file = workdir / "answer.txt"
    argv, stdin_text = _argv(cli, model, prompt_file, output_file)

    started = time.time()
    try:
        proc = subprocess.run(
            argv,
            input=stdin_text,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_isolated_env(cli, workdir),
            cwd=workdir,
        )
    except subprocess.TimeoutExpired as exc:
        audit_event(cfg, "relay", {"cli": cli, "model": model, "timeout": timeout}, ok=False, chars=0)
        raise RelayError(f"{cli} non ha risposto entro {timeout}s") from exc
    except OSError as exc:
        raise RelayError(f"{cli} non eseguibile: {exc}") from exc

    answer = _extract(cli, proc.stdout, output_file)
    if len(answer) > MAX_OUTPUT:
        answer = answer[:MAX_OUTPUT] + "\n[...troncato]"
        truncated = True
    audit_event(
        cfg,
        "relay",
        {"cli": cli, "model": model, "rc": proc.returncode},
        ok=proc.returncode == 0,
        chars=len(answer),
    )
    if proc.returncode != 0 and not answer:
        detail = (proc.stderr or "").strip()[-500:]
        raise RelayError(f"{cli} e' uscito con codice {proc.returncode}: {detail}")
    return RelayResult(
        cli=cli,
        model=model,
        answer=answer,
        elapsed_s=round(time.time() - started, 1),
        returncode=proc.returncode,
        truncated=truncated,
    )
