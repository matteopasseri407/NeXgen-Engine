"""Council session lifecycle: private on-disk artefacts, TTL cleanup, and the
best-effort shutdown handlers that stop an in-flight seat and remove an
ephemeral session directory on SIGTERM/SIGINT/interpreter exit.

Also owns the egress/output privacy gates (leak-scan on the outbound brief,
redaction of a seat's generated output) since both operate on the same
session-scoped, privacy-sensitive material this module already protects.
"""
from __future__ import annotations

import atexit
import importlib.util
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

ENGINE_ROOT = Path(__file__).resolve().parent
LEAK_SCAN_DIR = ENGINE_ROOT.parent / "leak-scan"

if os.name == "nt":
    _LOCAL_STATE_ROOT = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
else:
    _LOCAL_STATE_ROOT = Path.home() / ".local" / "state"
SESSIONS_DIR = _LOCAL_STATE_ROOT / "council" / "sessions"
COUNCIL_STATE_DIR = _LOCAL_STATE_ROOT / "council"
DEFAULT_TTL_DAYS = 7


def _private_mkdir(path: Path, *, parents: bool = False, exist_ok: bool = False) -> None:
    """Create a private directory without pretending mode bits secure NTFS."""
    kwargs = {} if os.name == "nt" else {"mode": 0o700}
    path.mkdir(parents=parents, exist_ok=exist_ok, **kwargs)


def _set_private_mode(path: Path, mode: int) -> None:
    """Apply POSIX privacy modes where the platform supports them."""
    if os.name == "nt":
        return
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def _write_private_text(path: Path, text: str) -> None:
    """Write a session artefact without first exposing it to the umask."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        if os.name != "nt":
            os.fchmod(fd, 0o600)
    except Exception:
        os.close(fd)
        raise
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)


def _secure_session_tree(session_dir: Path) -> None:
    """Tighten known session artefacts after a kept debug run."""
    if os.name == "nt" or not session_dir.exists():
        return
    for path in sorted(session_dir.rglob("*"), reverse=True):
        _set_private_mode(path, 0o700 if path.is_dir() else 0o600)
    _set_private_mode(session_dir, 0o700)


def _cleanup_sessions(ttl_days: int, *, remove_all: bool = False, announce: bool = False) -> int:
    if not SESSIONS_DIR.is_dir():
        return 0
    cutoff = datetime.now(UTC) - timedelta(days=ttl_days)
    removed = 0
    for session_dir in sorted(SESSIONS_DIR.iterdir()):
        if not session_dir.is_dir():
            continue
        if not remove_all:
            try:
                mtime = datetime.fromtimestamp(session_dir.stat().st_mtime, tz=UTC)
            except OSError:
                continue
            if mtime >= cutoff:
                continue
        try:
            shutil.rmtree(session_dir)
        except OSError as exc:
            if announce:
                print(f"[council] cannot remove {session_dir.name}: {exc}")
            continue
        removed += 1
        if announce:
            print(f"[council] removed: {session_dir.name}")
    return removed


def _remove_session_tree(session_dir: Path) -> OSError | None:
    """Remove an ephemeral session, tolerating short NTFS handle-release lag."""
    retry_delays = (0.05, 0.1, 0.2, 0.4, 0.8) if os.name == "nt" else ()
    for attempt in range(len(retry_delays) + 1):
        try:
            shutil.rmtree(session_dir)
            return None
        except OSError as exc:
            if attempt >= len(retry_delays):
                return exc
            time.sleep(retry_delays[attempt])
    return None


def _finalize_session(session_dir: Path, keep_session: bool) -> None:
    if keep_session:
        _secure_session_tree(session_dir)
        return
    exc = _remove_session_tree(session_dir)
    if exc is not None:
        print(f"[council] WARNING: session cleanup failed ({exc}).")


def slugify(text: str) -> str:
    keep = [c.lower() if c.isalnum() else "-" for c in text[:40]]
    slug = "".join(keep).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug or "session"


def new_session_dir(label: str) -> Path:
    """mkdir WITHOUT exist_ok: two invocations with the same label in the same
    second (timestamp resolution is one second) must never silently share a
    folder and overwrite each other's files -- on collision, retry with a
    random suffix until a free one is found (verified live: without this,
    two sessions launched close together with the same label end up in the
    same directory)."""
    _cleanup_sessions(DEFAULT_TTL_DAYS)
    _private_mkdir(SESSIONS_DIR, parents=True, exist_ok=True)
    _set_private_mode(SESSIONS_DIR, 0o700)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    base_name = f"council-{slugify(label)}-{timestamp}"
    session_dir = SESSIONS_DIR / base_name
    while True:
        try:
            _private_mkdir(session_dir, parents=True, exist_ok=False)
            _set_private_mode(session_dir, 0o700)
            return session_dir
        except FileExistsError:
            session_dir = SESSIONS_DIR / f"{base_name}-{os.urandom(3).hex()}"


def _force_stop_process_tree(proc: subprocess.Popen) -> None:
    """Force-stop a seat and reap its launcher.

    On Windows an npm ``.cmd`` shim is launched through ``cmd.exe``. Killing
    only that parent can leave the Node/Codex child alive with SQLite handles
    open inside the Council session directory. ``taskkill /T`` terminates the
    exact descendant tree rooted at the launcher PID; other platforms keep
    the existing single-process kill behavior.
    """
    used_windows_tree_kill = False
    pid = getattr(proc, "pid", None)
    if os.name == "nt" and pid is not None:
        try:
            result = subprocess.run(
                ["taskkill.exe", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
            used_windows_tree_kill = result.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            pass

    if not used_windows_tree_kill:
        try:
            proc.kill()
        except OSError:
            pass

    try:
        proc.wait(timeout=5)
    except TypeError:  # lightweight test doubles may not accept timeout
        proc.wait()
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
            proc.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            pass


_STATE_LOCK = threading.Lock()
_ACTIVE_PROC: subprocess.Popen | None = None
_ACTIVE_SESSION_DIR: Path | None = None
_ACTIVE_SESSION_KEEP = False
_CLEANUP_RAN = False


def _set_active_proc(proc: subprocess.Popen | None) -> None:
    """Track the seat subprocess currently running, if any, so a SIGTERM or
    interpreter-exit cleanup can try to stop it. Only one seat runs at a
    time (brainstorm/challenge/relay invoke seats sequentially), so a single
    slot is enough."""
    global _ACTIVE_PROC
    with _STATE_LOCK:
        _ACTIVE_PROC = proc


def _set_active_session(session_dir: Path | None, keep: bool = False) -> None:
    """Track the ephemeral session dir currently in use, so an interrupted
    run can still be cleaned up like the happy path's ``_finalize_session``
    would (unless the user asked to keep it with ``--keep-session``)."""
    global _ACTIVE_SESSION_DIR, _ACTIVE_SESSION_KEEP
    with _STATE_LOCK:
        _ACTIVE_SESSION_DIR = session_dir
        _ACTIVE_SESSION_KEEP = keep


def _best_effort_cleanup(*_args) -> None:
    """Best-effort cleanup for SIGTERM and interpreter exit: try to stop the
    currently running seat subprocess and remove the in-progress ephemeral
    session directory (unless it was explicitly kept).

    This is deliberately best-effort and never raises: it must not turn a
    clean shutdown into a traceback. It also cannot do anything about
    SIGKILL -- no userspace handler, Python or otherwise, ever runs for
    that signal; this only covers SIGTERM and normal interpreter exit
    (uncaught exception, sys.exit, ...), which is the gap the rest of the
    codebase already leaves uncovered outside the try/finally in
    ``_run_mode``/``cmd_relay``.
    """
    global _CLEANUP_RAN
    with _STATE_LOCK:
        if _CLEANUP_RAN:
            return
        _CLEANUP_RAN = True
        proc, session_dir, keep = _ACTIVE_PROC, _ACTIVE_SESSION_DIR, _ACTIVE_SESSION_KEEP
    if proc is not None:
        try:
            if proc.poll() is None:
                if os.name == "nt" and getattr(proc, "pid", None) is not None:
                    _force_stop_process_tree(proc)
                else:
                    proc.terminate()
                    try:
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        _force_stop_process_tree(proc)
        except Exception:
            pass
    if session_dir is not None and not keep:
        _remove_session_tree(session_dir)


def _handle_sigterm(signum, frame) -> None:  # pragma: no cover - exercised via _best_effort_cleanup
    _best_effort_cleanup()
    # Restore the default disposition and re-deliver the signal to self so
    # the process still terminates the conventional way (correct exit code,
    # no swallowed SIGTERM) instead of silently surviving it.
    signal.signal(signum, signal.SIG_DFL)
    os.kill(os.getpid(), signum)


# Same handler body as _handle_sigterm, registered separately for SIGINT.
# An interactive Ctrl+C is NOT the gap this closes: the kernel delivers
# SIGINT to the whole foreground process group, so the vendor CLI child
# already receives it directly and exits on its own. The gap is a SIGINT
# sent only to council.py's own pid -- a supervisor, a timeout manager, or
# another agent interrupting just this process, all realistic in agentic
# use -- which would otherwise leave the child orphaned: run_seat's finally
# clears _ACTIVE_PROC without killing it, so atexit later finds nothing to
# stop.
_handle_sigint = _handle_sigterm


def _install_shutdown_handlers() -> None:
    """Wire the best-effort cleanup into SIGTERM, SIGINT and interpreter
    exit. Only called from main() (real CLI invocation), never at import
    time, so importing council.py as a library (tests) never mutates the
    importing process's signal disposition."""
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, _handle_sigterm)
        signal.signal(signal.SIGINT, _handle_sigint)
    atexit.register(_best_effort_cleanup)


def _load_leak_scan():
    spec = importlib.util.spec_from_file_location("leak_scan", LEAK_SCAN_DIR / "leak_scan.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def egress_gate(text: str) -> None:
    leak_scan = _load_leak_scan()
    patterns, allow = leak_scan.load_patterns(LEAK_SCAN_DIR / "leak_patterns.yaml")
    units = [
        leak_scan.Unit("brief", i, line)
        for i, line in enumerate(text.splitlines(), 1)
    ]
    findings = leak_scan.scan_units(units, patterns, allow, [])
    blocking = [f for f in findings if f.blocking]
    soft = [f for f in findings if not f.blocking]
    if soft:
        print("[council] warning (non-blocking): possible identifying data in the brief.")
        for f in soft:
            print(f"  ? {f.label}:{f.lineno}  [{f.kind}]  match={f.redacted}")
    if blocking:
        print("[council] STOP: the brief contains possible secrets, send blocked.")
        for f in blocking:
            print(f"  ! {f.label}:{f.lineno}  [{f.kind}]  match={f.redacted}")
        sys.exit(1)


def redact_generated_output(text: str) -> tuple[str, bool]:
    """Redact suspicious model output before it reaches another seat or disk.

    The original brief is a hard gate and must never leave the process with a
    possible secret. A model can still hallucinate something that resembles a
    secret. That output is not a reason to discard an otherwise useful relay:
    remove the affected lines and keep the remaining analysis moving.
    """
    leak_scan = _load_leak_scan()
    patterns, allow = leak_scan.load_patterns(LEAK_SCAN_DIR / "leak_patterns.yaml")
    lines = text.splitlines(keepends=True)
    units = [
        leak_scan.Unit("generated output", index, line.rstrip("\r\n"))
        for index, line in enumerate(lines, 1)
    ]
    findings = leak_scan.scan_units(units, patterns, allow, [])
    blocked_lines = {finding.lineno for finding in findings if finding.blocking}
    if not blocked_lines:
        return text, False

    redacted: list[str] = []
    for index, line in enumerate(lines, 1):
        if index not in blocked_lines:
            redacted.append(line)
            continue
        newline = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
        redacted.append(f"[REDACTED POSSIBLE SECRET]{newline}")
    return "".join(redacted), True
