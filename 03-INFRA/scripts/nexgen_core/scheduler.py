"""Self-alignment scheduler for NeXgen Engine v2.

Ports into the package the startup self-alignment cycle that the release
installed via agent_sync.py:
- Linux: systemd user units (agent-sync.service/.timer, agent-heartbeat,
  agent-alert@) enabled with `systemctl --user enable --now`.
- Windows: scheduled task via schtasks.exe with a hidden VBS wrapper.

Rules:
1. Respects NEXGEN_DISABLE_HOST_MUTATIONS: sandbox tests and dry runs never
   touch system state (Task Scheduler, systemd).
2. Never arms a timer against a missing command (~/.local/bin/agent-sync).
3. The generated content depends on the real paths (engine_root, vault_data):
   each unit file is rewritten only if it changes (write_if_different).
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from nexgen_core.scheduler_startup import _install_startup_fallback

IS_WINDOWS = sys.platform == "win32"
HOST_MUTATIONS_DISABLED_ENV = "NEXGEN_DISABLE_HOST_MUTATIONS"

# systemd turns the start timeout off for Type=oneshot unless it is set, so a
# wedged run was never killed: it kept the host lock, and every later cycle
# found the lock busy and exited 0 without doing anything. The guard's limit
# stays under its 30-minute interval so two cycles can never overlap; the
# heartbeat is longer because it also carries the unattended upgrade.
GUARD_TIMEOUT_START = "25min"
HEARTBEAT_TIMEOUT_START = "40min"

_SYSTEMD_TIMER = """[Unit]
Description=agent-sync guard every 30 minutes and shortly after login

[Timer]
OnStartupSec=3min
OnUnitActiveSec=30min
Persistent=true

[Install]
WantedBy=timers.target
"""

_SYSTEMD_HEARTBEAT_TIMER = """[Unit]
Description=Hourly check that the agent sync is still completing

[Timer]
OnStartupSec=5min
OnUnitActiveSec=1h
Persistent=true

[Install]
WantedBy=timers.target
"""

_SYSTEMD_ALERT_TEMPLATE = """[Unit]
Description=Tell the user that %i could not run

[Service]
Type=oneshot
ExecStart=%h/.local/bin/nexgen notify-failure %i
"""

_VBS_TEMPLATE = (
    'Set shell = CreateObject("WScript.Shell")\r\n'
    'Set processEnv = shell.Environment("PROCESS")\r\n'
    'processEnv("AGENT_ENGINE_ROOT") = "{engine_root}"\r\n'
    'processEnv("AGENT_VAULT_DATA") = "{vault_data}"\r\n'
    'processEnv("KNOWLEDGE_VAULT_PATH") = "{vault}"\r\n'
    'processEnv("KNOWLEDGE_VAULT_BRANCH") = "{branch}"\r\n'
    'script = "{script}"\r\n'
    'shell.Run "cmd.exe /c " & Chr(34) & script & Chr(34) '
    '& " {mode}", 0, True\r\n'
)


def _systemd_env_line(key: str, value: str) -> str:
    """Quotes the whole assignment per systemd.syntax(7): a path with a space
    (e.g. '/opt/agents/nexgen engine') must not silently truncate the var."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'Environment="{key}={escaped}"'


_STANDARD_SERVICE_DIRS = (
    "/usr/local/sbin",
    "/usr/local/bin",
    "/usr/sbin",
    "/usr/bin",
    "/sbin",
    "/bin",
    "/snap/bin",
)

#: What the guard and its children launch by name. Their directories join the
#: unit's PATH even when they live outside the standard ones (nvm, volta,
#: ~/.opencode, a Homebrew prefix).
_GUARD_TOOLS = ("git", "node", "npx", "uv", "gh", "claude", "codex", "agy", "opencode")


def _tool_dirs() -> list[str]:
    return [str(Path(found).parent) for tool in _GUARD_TOOLS if (found := shutil.which(tool))]


def _unit_path_entries(home: Path) -> list[str]:
    """The PATH directories the installed units already declare, in order."""
    entries: list[str] = []
    for name in ("agent-sync.service", "agent-heartbeat.service"):
        try:
            text = (home / ".config" / "systemd" / "user" / name).read_text(encoding="utf-8")
        except OSError:
            continue
        match = re.search(r'^Environment="PATH=((?:[^"\\]|\\.)*)"', text, re.M)
        if match:
            # Undo _systemd_env_line's escaping, or every read would double
            # the backslashes of a path that has them and the unit would
            # never stop changing.
            value = re.sub(r"\\(.)", r"\1", match.group(1))
            entries.extend(value.split(os.pathsep))
    return entries


def _scheduler_path(home: Path) -> str:
    """The PATH a unit declares: the home launchers, the directory of the
    interpreter running the engine, then the standard system directories.

    The caller's PATH is not copied wholesale. Every shell has its own PATH,
    so copying it made each shell that ran the install rewrite the unit and
    reload the timers. The interpreter directory is there
    because the shim starts whatever `python3` is first on PATH, and that must
    be the interpreter that installed the engine. Each directory appears once.

    The guard runs with the PATH its own unit declares, so without the
    deduplication every cycle prepended the same two directories to a value it
    had itself written: the unit grew 51 bytes per cycle and was rewritten and
    reloaded every time. Now the unit is a fixed point of its own environment,
    and a unit that already grew collapses on its next write.
    """
    entries = [
        str(home / ".local" / "bin"),
        str(home / ".opencode" / "bin"),
        str(Path(sys.executable).parent) if sys.executable else "",
        # What the installed units already declare stays, in its order, and
        # the directories of the tools this shell can see are only ever added.
        # A shell that cannot see nvm therefore never takes `node` away from
        # the guard, and two shells converge on one unit instead of
        # rewriting it against each other.
        *_unit_path_entries(home),
        *_tool_dirs(),
        *_STANDARD_SERVICE_DIRS,
    ]
    seen: set[str] = set()
    unique: list[str] = []
    for entry in entries:
        key = os.path.normcase(entry)
        if not entry or key in seen:
            continue
        seen.add(key)
        unique.append(entry)
    return os.pathsep.join(unique)


def _guard_shim(home: Path) -> str:
    """The command the timers invoke: whichever launcher actually exists.

    `agent-sync` is the historical name, `nexgen` the current one. Hardcoding
    the legacy name arms timers against a command a cleanup may delete.
    """
    if (home / ".local" / "bin" / "nexgen").exists():
        return "%h/.local/bin/nexgen sync guard"
    return "%h/.local/bin/agent-sync guard"


def _heartbeat_shim(home: Path) -> str:
    if (home / ".local" / "bin" / "nexgen").exists():
        return "%h/.local/bin/nexgen heartbeat"
    return "%h/.local/bin/agent-sync heartbeat"


def _environment_file_lines(home: Path) -> list[str]:
    """The connector environment the guard must load, with the leading dash.

    The gateway `.env` holds the connector keys in systemd's own syntax, which is
    what a headless server has. The shell deposit (`secrets.env`) is left out on
    purpose: systemd skips its `export` lines and would load only part of it.
    Optional: a machine without a gateway still runs the guard, with the keys
    its `environment.d` gives the user manager.
    """
    return [f"EnvironmentFile=-{home / '.openclaw' / '.env'}"]


def _systemd_service_content(home: Path, engine_root: Path, vault_data: Path, vault: Path) -> str:
    lines = [
        "[Unit]",
        "Description=KnowledgeVault agent sync guard (pull + apply + healthcheck, no publish)",
        "OnFailure=agent-alert@%n.service",
        "",
        "[Service]",
        "Type=oneshot",
        f"TimeoutStartSec={GUARD_TIMEOUT_START}",
    ]
    # The root the engine resolved for this machine, always: leaving it out let
    # the guard fall back to a default that named another directory, and every
    # cycle then rewrote configuration with paths that do not exist.
    lines.append(_systemd_env_line("AGENT_ENGINE_ROOT", str(engine_root)))
    if vault_data.resolve() != vault.resolve():
        lines.append(_systemd_env_line("AGENT_VAULT_DATA", str(vault_data)))
    lines.extend(_environment_file_lines(home))
    lines.append(_systemd_env_line("PATH", _scheduler_path(home)))
    lines.append(f"ExecStart={_guard_shim(home)}")
    return "\n".join(lines) + "\n"


def _systemd_heartbeat_content(home: Path, engine_root: Path, vault_data: Path, vault: Path) -> str:
    lines = [
        "[Unit]",
        "Description=Liveness beat, third-party pin scan and released-upgrade uptake",
        "",
        "[Service]",
        "Type=oneshot",
        f"TimeoutStartSec={HEARTBEAT_TIMEOUT_START}",
    ]
    lines.append(_systemd_env_line("AGENT_ENGINE_ROOT", str(engine_root)))
    if vault_data.resolve() != vault.resolve():
        lines.append(_systemd_env_line("AGENT_VAULT_DATA", str(vault_data)))
    lines.extend(_environment_file_lines(home))
    lines.append(_systemd_env_line("PATH", _scheduler_path(home)))
    lines.append(f"ExecStart={_heartbeat_shim(home)}")
    return "\n".join(lines) + "\n"


def _run_external(args: Sequence[str], *, timeout: int = 30) -> subprocess.CompletedProcess:
    from nexgen_core.processes import no_window_kwargs

    try:
        return subprocess.run(list(args), timeout=timeout, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False, **no_window_kwargs())
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else (exc.stdout or b"").decode("utf-8", "replace")
        return subprocess.CompletedProcess(list(args), 1, stdout, f"timed out after {timeout}s")


def _atomic_write_text(path: Path, content: str) -> None:
    from nexgen_core.files import atomic_write_text

    atomic_write_text(path, content)


def _write_if_different(path: Path, content: str) -> bool:
    from nexgen_core.files import write_text_if_changed

    if path.is_symlink():
        try:
            target = path.resolve(strict=True)
        except OSError as exc:
            raise OSError(f"refusing to write through a broken symlink at {path}: {exc}") from exc
        # A managed symlink farm points elsewhere on purpose: write through
        # the link instead of replacing it with a regular file.
        path = target
    # Units are rewritten whenever their content changes; without a rotation
    # every change leaves another backup behind. Keep the last three, the
    # same policy the MCP renderer uses.
    return write_text_if_changed(path, content, keep=3)


def _resolve_cmd(name: str) -> str | None:
    candidates = {"python3": ["python3", "py"], "npx": ["npx", "npx.cmd"]}.get(name, [name])
    for c in candidates:
        p = shutil.which(c)
        if p:
            return p
    return None


def host_mutations_disabled() -> bool:
    """True under NEXGEN_DISABLE_HOST_MUTATIONS: sandboxes, tests and dry runs."""
    value = os.environ.get(HOST_MUTATIONS_DISABLED_ENV, "").strip().lower()
    return value in {"1", "true", "yes", "on"}


_host_mutations_disabled = host_mutations_disabled  # the name this module's callers used


def install_systemd_units(
    *,
    home: Path,
    engine_root: Path,
    vault_data: Path,
    vault: Path,
    log: Callable[[str], None] = print,
) -> bool:
    """Writes and enables the guard cycle's systemd user units."""
    if _host_mutations_disabled():
        return True
    if not (home / ".local" / "bin" / "agent-sync").exists() and not (home / ".local" / "bin" / "nexgen").exists():
        warning = (
            "systemd: neither ~/.local/bin/agent-sync nor ~/.local/bin/nexgen exists yet; timer not armed "
            "(will be retried on a future run once a shim is created)"
        )
        log(warning)
        print(warning, file=sys.stderr)
        return False

    unit_dir = home / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    changed = False
    healthy = True
    for path, content, label in (
        (unit_dir / "agent-sync.service", _systemd_service_content(home, engine_root, vault_data, vault), "agent-sync.service set"),
        (unit_dir / "agent-sync.timer", _SYSTEMD_TIMER, "agent-sync.timer updated"),
        (unit_dir / "agent-alert@.service", _SYSTEMD_ALERT_TEMPLATE, "agent-alert@.service installed"),
        (unit_dir / "agent-heartbeat.service", _systemd_heartbeat_content(home, engine_root, vault_data, vault), "agent-heartbeat.service installed"),
        (unit_dir / "agent-heartbeat.timer", _SYSTEMD_HEARTBEAT_TIMER, "agent-heartbeat.timer installed"),
    ):
        if _write_if_different(path, content):
            changed = True
            log(f"systemd: {label}")

    systemctl = _resolve_cmd("systemctl")
    if not systemctl:
        log("systemd: systemctl not found — files written but the timers are NOT enabled")
        return False
    if changed:
        r = _run_external([systemctl, "--user", "daemon-reload"], timeout=30)
        if r.returncode != 0:
            log(f"systemd: user daemon-reload failed: {(r.stderr or r.stdout).strip()}")
            healthy = False
    r = _run_external([systemctl, "--user", "enable", "--now", "agent-sync.timer", "agent-heartbeat.timer"], timeout=30)
    if r.returncode != 0:
        log(
            "systemd: could not enable agent-sync.timer "
            f"({(r.stderr or r.stdout).strip()}) — the recurring guard won't start. "
            "On a headless machine this is often missing `loginctl enable-linger $USER`."
        )
        healthy = False
    return healthy


def _scheduled_task_invokes_wrapper(task_name: str, wrapper_path: Path) -> bool:
    r = _run_external(["schtasks.exe", "/Query", "/TN", task_name, "/XML"], timeout=30)
    if r.returncode != 0:
        return False
    return str(wrapper_path) in r.stdout


def _render_wrapper(
    *,
    script_path: Path,
    engine_root: Path,
    vault_data: Path,
    vault: Path,
    branch: str,
    mode: str,
) -> str:
    """Renders the hidden VBS wrapper without touching the filesystem."""

    def vbs_string(value: Path | str) -> str:
        return str(value).replace('"', '""')

    return _VBS_TEMPLATE.format(
        script=vbs_string(script_path),
        engine_root=vbs_string(engine_root),
        vault_data=vbs_string(vault_data),
        vault=vbs_string(vault),
        branch=vbs_string(branch),
        mode=mode,
    )


def _install_wrapper(
    path: Path,
    content: str,
    *,
    log: Callable[[str], None] = print,
    label: str = "hidden wrapper updated",
) -> bool:
    """Writes one wrapper file, logging only when it changed."""
    if _write_if_different(path, content):
        log(f"scheduled-task: {label}")
        return True
    return False


def install_scheduled_task(
    *,
    home: Path,
    engine_root: Path,
    vault_data: Path,
    vault: Path,
    branch: str,
    log: Callable[[str], None] = print,
) -> bool:
    """Creates/updates the Windows scheduled tasks (guard every 30 min + hourly heartbeat)."""
    if _host_mutations_disabled():
        return True
    task_name = "KnowledgeVault Agent Sync"
    # The shim `install_shims` wrote earlier in the cycle: current launcher
    # name first, the historical alias as fallback.
    script_path = home / ".local" / "bin" / "nexgen.cmd"
    if not script_path.is_file():
        script_path = home / ".local" / "bin" / "agent-sync.cmd"
    if not script_path.is_file():
        warning = (
            f"scheduled-task: WARNING — {script_path} doesn't exist yet; "
            "task not installed (will be retried on a future run)"
        )
        log(warning)
        print(warning, file=sys.stderr)
        return False

    log_dir = home / ".local" / "state"
    log_dir.mkdir(parents=True, exist_ok=True)
    wrapper_path = log_dir / "start-agent-sync-hidden.vbs"

    content = _render_wrapper(
        script_path=script_path,
        engine_root=engine_root,
        vault_data=vault_data,
        vault=vault,
        branch=branch,
        mode="guard",
    )
    _install_wrapper(wrapper_path, content, log=log, label="hidden wrapper updated")

    beat_path = log_dir / "start-agent-heartbeat-hidden.vbs"
    _install_wrapper(
        beat_path,
        _render_wrapper(
            script_path=script_path,
            engine_root=engine_root,
            vault_data=vault_data,
            vault=vault,
            branch=branch,
            mode="heartbeat",
        ),
        log=log,
        label="heartbeat wrapper updated",
    )
    beat_task = "KnowledgeVault Agent Heartbeat"
    heartbeat_ok = True
    if not _scheduled_task_invokes_wrapper(beat_task, beat_path):
        r = _run_external(
            ["schtasks.exe", "/Create", "/TN", beat_task, "/SC", "HOURLY", "/TR", f'wscript.exe "{beat_path}"', "/F"],
            timeout=60,
        )
        if r.returncode == 0:
            log(f"scheduled-task: '{beat_task}' installed via schtasks.exe")
        else:
            log(f"scheduled-task: heartbeat task failed ({r.stdout}{r.stderr}) — hourly beat will not run")
            heartbeat_ok = False

    run_cmd = f'wscript.exe "{wrapper_path}"'
    every30 = ["schtasks.exe", "/Create", "/TN", task_name, "/SC", "MINUTE", "/MO", "30", "/TR", run_cmd, "/F"]
    logon = ["schtasks.exe", "/Create", "/TN", f"{task_name} Logon", "/SC", "ONLOGON", "/TR", run_cmd, "/F"]
    logon_marker = log_dir / "scheduled-task-logon-attempt"
    _content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    _previous_hash = logon_marker.read_text(encoding="utf-8").strip() if logon_marker.exists() else ""

    if _scheduled_task_invokes_wrapper(task_name, wrapper_path):
        log(f"scheduled-task: '{task_name}' already active on {wrapper_path.name}; no rewrite")
    else:
        r = _run_external(every30, timeout=60)
        if r.returncode != 0:
            log(f"scheduled-task: schtasks.exe failed for '{task_name}': {r.stdout}{r.stderr}")
            return False
        log(f"scheduled-task: '{task_name}' installed via schtasks.exe")

    if _scheduled_task_invokes_wrapper(f"{task_name} Logon", wrapper_path):
        log(f"scheduled-task: '{task_name} Logon' already active; no rewrite")
        _atomic_write_text(logon_marker, _content_hash)
        return heartbeat_ok
    if _previous_hash != _content_hash:
        r = _run_external(logon, timeout=60)
        if r.returncode == 0:
            _atomic_write_text(logon_marker, _content_hash)
            log(f"scheduled-task: '{task_name} Logon' installed via schtasks.exe")
            return heartbeat_ok
        # No marker on failure: the next cycle must retry instead of
        # concluding "failed previously; not retrying" over a stale hash.
        log(f"scheduled-task: logon trigger failed ({r.stdout}{r.stderr}); falling back to the Startup folder")
    else:
        log(f"scheduled-task: '{task_name} Logon' failed previously; not retrying (wrapper unchanged)")

    startup_dir = os.environ.get("APPDATA")
    if startup_dir:
        _install_startup_fallback(Path(startup_dir), content, heartbeat_ok, log)
        return heartbeat_ok
    log("scheduled-task: no logon coverage (schtasks denied and APPDATA unset)")
    return False


def install_scheduler(
    *,
    home: Path,
    engine_root: Path,
    vault_data: Path,
    vault: Path,
    branch: str = "main",
    log: Callable[[str], None] = print,
) -> bool:
    """Cross-platform dispatcher: systemd on POSIX, scheduled task on Windows."""
    if IS_WINDOWS:
        return install_scheduled_task(
            home=home, engine_root=engine_root, vault_data=vault_data, vault=vault, branch=branch, log=log
        )
    return install_systemd_units(home=home, engine_root=engine_root, vault_data=vault_data, vault=vault, log=log)
