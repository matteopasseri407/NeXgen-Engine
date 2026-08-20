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
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Sequence

IS_WINDOWS = sys.platform == "win32"
HOST_MUTATIONS_DISABLED_ENV = "NEXGEN_DISABLE_HOST_MUTATIONS"

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
ExecStart=%h/.local/bin/agent-sync notify-failure %i
"""

_VBS_TEMPLATE = (
    'Set shell = CreateObject("WScript.Shell")\r\n'
    'Set processEnv = shell.Environment("PROCESS")\r\n'
    'processEnv("AGENT_ENGINE_ROOT") = "{engine_root}"\r\n'
    'processEnv("AGENT_VAULT_DATA") = "{vault_data}"\r\n'
    'processEnv("KNOWLEDGE_VAULT_PATH") = "{vault}"\r\n'
    'processEnv("KNOWLEDGE_VAULT_BRANCH") = "{branch}"\r\n'
    'script = "{script}"\r\n'
    'shell.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -File " & Chr(34) & script & Chr(34) '
    '& " {mode}", 0, True\r\n'
)


def _systemd_env_line(key: str, value: str) -> str:
    """Quotes the whole assignment per systemd.syntax(7): a path with a space
    (e.g. '/opt/agents/nexgen engine') must not silently truncate the var."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'Environment="{key}={escaped}"'


def _scheduler_path(home: Path) -> str:
    return os.pathsep.join([
        str(home / ".local" / "bin"),
        str(home / ".opencode" / "bin"),
        os.environ.get("PATH", os.defpath),
    ])


def _systemd_service_content(home: Path, engine_root: Path, vault_data: Path, vault: Path) -> str:
    lines = [
        "[Unit]",
        "Description=KnowledgeVault agent sync guard (pull + apply + healthcheck, no publish)",
        "OnFailure=agent-alert@%n.service",
        "",
        "[Service]",
        "Type=oneshot",
    ]
    default_engine_root = (vault / "03-INFRA").resolve()
    if engine_root.resolve() != default_engine_root:
        lines.append(_systemd_env_line("AGENT_ENGINE_ROOT", str(engine_root)))
    if vault_data.resolve() != vault.resolve():
        lines.append(_systemd_env_line("AGENT_VAULT_DATA", str(vault_data)))
    lines.append(_systemd_env_line("PATH", _scheduler_path(home)))
    lines.append("ExecStart=%h/.local/bin/agent-sync guard")
    return "\n".join(lines) + "\n"


def _systemd_heartbeat_content(home: Path, engine_root: Path, vault_data: Path, vault: Path) -> str:
    lines = [
        "[Unit]",
        "Description=Liveness beat, third-party pin scan and released-upgrade uptake",
        "",
        "[Service]",
        "Type=oneshot",
    ]
    default_engine_root = (vault / "03-INFRA").resolve()
    if engine_root.resolve() != default_engine_root:
        lines.append(_systemd_env_line("AGENT_ENGINE_ROOT", str(engine_root)))
    if vault_data.resolve() != vault.resolve():
        lines.append(_systemd_env_line("AGENT_VAULT_DATA", str(vault_data)))
    lines.append(_systemd_env_line("PATH", _scheduler_path(home)))
    lines.append("ExecStart=%h/.local/bin/agent-sync heartbeat")
    return "\n".join(lines) + "\n"


def _run_external(args: Sequence[str], *, timeout: int = 30) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(list(args), timeout=timeout, capture_output=True, text=True, check=False)
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else (exc.stdout or b"").decode("utf-8", "replace")
        return subprocess.CompletedProcess(list(args), 1, stdout, f"timed out after {timeout}s")


def _atomic_write_text(path: Path, content: str) -> None:
    old_mode = None
    if path.exists():
        try:
            old_mode = path.stat().st_mode
        except OSError:
            pass
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(content, encoding="utf-8")
    if old_mode is not None:
        try:
            os.chmod(tmp, old_mode)
        except OSError:
            pass
    delay = 0.05
    for _ in range(5):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if delay >= 0.8:
                raise
            time.sleep(delay)
            delay *= 2


def _write_if_different(path: Path, content: str) -> bool:
    if path.exists() and not path.is_symlink():
        try:
            if path.read_text(encoding="utf-8") == content:
                return False
        except (OSError, UnicodeDecodeError):
            pass
    if path.is_symlink() or path.exists():
        path.unlink()
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(path, content)
    return True


def _resolve_cmd(name: str) -> str | None:
    candidates = {"python3": ["python3", "py"], "npx": ["npx", "npx.cmd"]}.get(name, [name])
    for c in candidates:
        p = shutil.which(c)
        if p:
            return p
    return None


def _host_mutations_disabled() -> bool:
    value = os.environ.get(HOST_MUTATIONS_DISABLED_ENV, "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def install_systemd_units(
    *,
    home: Path,
    engine_root: Path,
    vault_data: Path,
    vault: Path,
    log: Callable[[str], None] = print,
) -> bool:
    """Writes and enables the guard cycle's systemd user units."""
    if not (home / ".local" / "bin" / "agent-sync").exists():
        warning = (
            "systemd: ~/.local/bin/agent-sync doesn't exist yet; timer not armed "
            "(will be retried on a future run once the shim is created)"
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
        log("systemd: systemctl not found — files written but not enabled")
        return healthy
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
    script_path = engine_root / "scripts" / "agent-sync.ps1"
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

    def vbs_string(value: Path | str) -> str:
        return str(value).replace('"', '""')

    def wrapper_for(mode: str) -> str:
        return _VBS_TEMPLATE.format(
            script=vbs_string(script_path),
            engine_root=vbs_string(engine_root),
            vault_data=vbs_string(vault_data),
            vault=vbs_string(vault),
            branch=vbs_string(branch),
            mode=mode,
        )

    content = wrapper_for("guard")
    if _write_if_different(wrapper_path, content):
        log("scheduled-task: hidden wrapper updated")

    beat_path = log_dir / "start-agent-heartbeat-hidden.vbs"
    if _write_if_different(beat_path, wrapper_for("heartbeat")):
        log("scheduled-task: heartbeat wrapper updated")
    beat_task = "KnowledgeVault Agent Heartbeat"
    if not _scheduled_task_invokes_wrapper(beat_task, beat_path):
        r = _run_external(
            ["schtasks.exe", "/Create", "/TN", beat_task, "/SC", "HOURLY", "/TR", f'wscript.exe "{beat_path}"', "/F"],
            timeout=60,
        )
        if r.returncode == 0:
            log(f"scheduled-task: '{beat_task}' installed via schtasks.exe")
        else:
            log(f"scheduled-task: heartbeat task failed ({r.stdout}{r.stderr})")

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
        logon_marker.write_text(_content_hash, encoding="utf-8")
        return True
    if _previous_hash != _content_hash:
        r = _run_external(logon, timeout=60)
        logon_marker.write_text(_content_hash, encoding="utf-8")
        if r.returncode == 0:
            log(f"scheduled-task: '{task_name} Logon' installed via schtasks.exe")
            return True
        log(f"scheduled-task: logon trigger failed ({r.stdout}{r.stderr}); falling back to the Startup folder")
    else:
        log(f"scheduled-task: '{task_name} Logon' failed previously; not retrying (wrapper unchanged)")

    startup_dir = os.environ.get("APPDATA")
    if startup_dir:
        startup_vbs = Path(startup_dir) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "KnowledgeVault Agent Sync.vbs"
        startup_vbs.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(wrapper_path, startup_vbs)
        log(f"startup: fallback logon installed {startup_vbs}")
    return True


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
