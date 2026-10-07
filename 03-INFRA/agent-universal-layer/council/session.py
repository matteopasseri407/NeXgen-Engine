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
from contextlib import contextmanager, ExitStack
from datetime import UTC, datetime, timedelta
from pathlib import Path

from nexgen_core.files import write_private_text, secure_artifact
from nexgen_core.lock import HostLock, LockTimeoutError
from nexgen_core.processes import force_stop_process_tree

ENGINE_ROOT = Path(__file__).resolve().parent
LEAK_SCAN_DIR = ENGINE_ROOT.parent / "leak-scan"


def _load_leak_scan():
    """File-path-loaded (invisible to static imports by design): the egress
    gate fails closed with a named path when the scanner is absent, never
    with an importlib AttributeError agents can't attribute."""
    target = LEAK_SCAN_DIR / "leak_scan.py"
    if not target.is_file():
        raise RuntimeError(f"[council] leak-scan assente: {target}")
    spec = importlib.util.spec_from_file_location("leak_scan", target)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"[council] leak-scan non caricabile: {target}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def _local_state_root() -> Path:
    """Where Council sessions live: the engine's home (`NEXGEN_HOME` when set), else the real one.

    Sessions hold prompts, diffs and seat output. A checkout run in a sandbox home used to
    write them into the working installation's state, and clean them up from there.
    """
    from nexgen_core.paths import resolve_home

    sandboxed = bool(os.environ.get("NEXGEN_HOME"))
    home = resolve_home()
    if os.name == "nt":
        local = None if sandboxed else os.environ.get("LOCALAPPDATA")
        return Path(local) if local else home / "AppData" / "Local"
    return home / ".local" / "state"


_LOCAL_STATE_ROOT = _local_state_root()
SESSIONS_DIR = _LOCAL_STATE_ROOT / "council" / "sessions"
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
    except OSError as exc:
        print(f"[council] chmod failed on {path}: {exc}", file=sys.stderr)


def _write_private_text(path: Path, text: str) -> None:
    """Publish a complete private artefact through the Engine's file owner."""
    write_private_text(path, text)


def _secure_session_tree(session_dir: Path) -> None:
    """Tighten known session artefacts after a kept debug run."""
    if os.name == "nt" or not session_dir.exists():
        return
    for path in sorted(session_dir.rglob("*"), reverse=True):
        _set_private_mode(path, 0o700 if path.is_dir() else 0o600)
    _set_private_mode(session_dir, 0o700)


@contextmanager
def session_run_lock(session_dir: Path):
    """Keep one stable lock outside the tree that cleanup may remove.

    Also honor older runs' in-tree lock when present. If an OS refuses to
    remove that open legacy file, cleanup preserves the session.
    """
    lock_dir = session_dir.parent.parent / "session-locks"
    secure_artifact(lock_dir)
    lock = HostLock(lock_dir / f"{session_dir.name}.lock", timeout=0, command_name="council session")
    with ExitStack() as stack:
        lock.acquire()
        stack.callback(lock.release)
        secure_artifact(lock_dir, lock.lock_path)
        legacy_path = session_dir / "relay-run.lock"
        if legacy_path.exists():
            legacy = HostLock(legacy_path, timeout=0, command_name="council session")
            legacy.acquire()
            stack.callback(legacy.release)
        yield


def _cleanup_sessions(ttl_days: int, *, remove_all: bool = False, announce: bool = False) -> int:
    if not SESSIONS_DIR.is_dir():
        return 0
    cutoff = datetime.now(UTC) - timedelta(days=ttl_days)
    removed = 0
    for session_dir in sorted(SESSIONS_DIR.iterdir()):
        if not session_dir.is_dir():
            continue
        try:
            with session_run_lock(session_dir):
                if not remove_all:
                    mtime = datetime.fromtimestamp(session_dir.stat().st_mtime, tz=UTC)
                    if mtime >= cutoff:
                        continue
                shutil.rmtree(session_dir)
        except LockTimeoutError:
            if announce:
                print(f"[council] keeping {session_dir.name}: session is running")
            continue
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
    """Compatibility boundary; process cleanup has one Engine owner."""
    force_stop_process_tree(proc, process_group=getattr(proc, "_council_process_group", None))


_STATE_LOCK = threading.Lock()
_ACTIVE_PROC: subprocess.Popen | None = None
_ACTIVE_TOKEN = ""
_ACTIVE_SESSION_DIR: Path | None = None
_ACTIVE_SESSION_KEEP = False
_CLEANUP_RAN = False
#: Every seat subprocess currently running, sequential or parallel: the
#: single slot above stays the compatibility view, this registry is what
#: shutdown and cancellation actually iterate. Keys are opaque tokens.
_LIVE_PROCS: dict[str, subprocess.Popen] = {}
_CANCEL_GRACE_SECONDS = 5.0
_CANCEL_EPOCH = 0


def _proc_registry_epoch() -> int:
    with _STATE_LOCK:
        return _CANCEL_EPOCH


def _register_proc(proc: subprocess.Popen, expected_epoch: int | None = None) -> str:
    """Register or stop a process spawned across a cancellation boundary."""
    token = f"proc-{time.time_ns():x}-{id(proc):x}"
    with _STATE_LOCK:
        cancelled = expected_epoch is not None and expected_epoch != _CANCEL_EPOCH
        if not cancelled:
            _LIVE_PROCS[token] = proc
    if cancelled:
        _stop_one_proc(proc)
        return ""
    return token


def _release_proc(token: str) -> None:
    with _STATE_LOCK:
        _LIVE_PROCS.pop(token, None)


def _live_procs_snapshot() -> list[subprocess.Popen]:
    with _STATE_LOCK:
        return list(_LIVE_PROCS.values())


def _stop_one_proc(proc: subprocess.Popen) -> None:
    """Terminate and reap one seat process; never raises."""
    try:
        if getattr(proc, "_council_process_group", None) is not None:
            _force_stop_process_tree(proc)
            return
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


def _cancel_all_procs() -> int:
    """Request every stop concurrently, with one shared shutdown deadline."""
    stopped = 0
    global _CANCEL_EPOCH
    with _STATE_LOCK:
        _CANCEL_EPOCH += 1
        tracked = list(_LIVE_PROCS.items())
    workers = []
    for _, proc in tracked:
        try:
            alive = proc.poll() is None
        except Exception:
            alive = False
        if alive:
            stopped += 1
        worker = threading.Thread(target=_stop_one_proc, args=(proc,), daemon=True)
        workers.append(worker)
        worker.start()
    deadline = time.monotonic() + _CANCEL_GRACE_SECONDS
    for worker in workers:
        worker.join(timeout=max(0.0, deadline - time.monotonic()))
    with _STATE_LOCK:
        for token, _ in tracked:
            _LIVE_PROCS.pop(token, None)
    return stopped


def _set_active_proc(proc: subprocess.Popen | None) -> None:
    """Legacy single-slot tracker, kept for compatibility only.

    ``run_seat`` no longer uses this: parallel seats overlap, so every
    invocation registers its own token (``_register_proc``) and releases
    exactly that token. New code must use the registry, never this slot.
    """
    global _ACTIVE_PROC, _ACTIVE_TOKEN
    with _STATE_LOCK:
        if _ACTIVE_TOKEN:
            _LIVE_PROCS.pop(_ACTIVE_TOKEN, None)
            _ACTIVE_TOKEN = ""
        _ACTIVE_PROC = proc
        if proc is not None:
            _ACTIVE_TOKEN = f"proc-{time.time_ns():x}-{id(proc):x}"
            _LIVE_PROCS[_ACTIVE_TOKEN] = proc


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
    global _CLEANUP_RAN, _ACTIVE_PROC, _ACTIVE_TOKEN
    with _STATE_LOCK:
        if _CLEANUP_RAN:
            return
        _CLEANUP_RAN = True
        _, session_dir, keep = _ACTIVE_PROC, _ACTIVE_SESSION_DIR, _ACTIVE_SESSION_KEEP
    # Every tracked seat, sequential or parallel: one mechanism, no orphans.
    _cancel_all_procs()
    with _STATE_LOCK:
        _ACTIVE_PROC = None
        _ACTIVE_TOKEN = ""
    if session_dir is not None and not keep:
        _remove_session_tree(session_dir)


def _handle_sigterm(signum, frame) -> None:  # pragma: no cover - exercised via _best_effort_cleanup
    _best_effort_cleanup()
    # Restore the default disposition and re-deliver the signal to self so
    # the process still terminates the conventional way (correct exit code,
    # no swallowed SIGTERM) instead of silently surviving it.
    signal.signal(signum, signal.SIG_DFL)
    os.kill(os.getpid(), signum)


# Seats have their own process groups, so both interactive Ctrl+C and a
# signal addressed only to Council must explicitly stop all tracked trees.
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
