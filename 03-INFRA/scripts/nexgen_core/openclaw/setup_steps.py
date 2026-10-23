"""The nine steps of `nexgen openclaw setup`, one function each.

One job: do the work of one step and say what it did. The order, the plan and
the decision to continue live in `setup.py`; here is only "install the pin",
"let OpenClaw onboard", "merge and validate", "prepare the workspaces", "make
sure the service answers".

Every external program arrives through `Setup.run` and every service action
through `Setup.service`, so a test drives the whole machine with fakes and
nothing here ever touches the home or the gateway of the person running it.
"""
from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path

from nexgen_core.i18n import t
from nexgen_core.modules import current_host, write_state_file
from nexgen_core.openclaw import host_preflight, setup_commands, setup_write
from nexgen_core.openclaw.detect import Detection
from nexgen_core.openclaw.pin import node_satisfies, required_node_version
from nexgen_core.openclaw.setup_agents import agent_dirs, created_by_openclaw, prepare
from nexgen_core.openclaw_pairing import qr as pairing_qr
from nexgen_core.openclaw_processes import is_gateway_process
from nexgen_core.openclaw_service import OpenClawService, RunResult
from nexgen_core.paths import resolve_vault_data
from nexgen_core.processes import command_line_text, no_window_kwargs
from nexgen_core.runtimes.openclaw import PLUGIN_ID

#: How long the gateway gets to answer. A cold start takes about five minutes;
#: ten is the margin for a slow disk, not a different promise.
START_TIMEOUT_SECONDS = 600.0
#: How often the wait says something, so silence is never mistaken for a hang.
WAIT_REPORT_SECONDS = 10.0

#: `(argv, env)`: the executor a test replaces with a recorder.
Run = Callable[..., RunResult]
Which = Callable[[str], "str | None"]
Emit = Callable[[str], None]


def default_run(argv: list[str], env: Mapping[str, str] | None = None) -> RunResult:
    """Run a command and capture both streams, without a console window."""
    completed = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        env=dict(env) if env is not None else None,
        **no_window_kwargs(),
    )
    return RunResult(completed.returncode, completed.stdout, completed.stderr)


@dataclass
class Setup:
    """Everything one run needs to know, resolved once."""

    home: Path
    env: dict[str, str]
    config_path: Path
    engine_root: Path | None
    vault_data: Path | None
    pinned: str | None
    dry_run: bool
    out: Emit
    which: Which
    run: Run
    service: OpenClawService
    stamp: str
    platform: str | None = None

    @property
    def state_dir(self) -> Path:
        return self.config_path.parent


def print_output(emit: Emit, result: RunResult) -> None:
    """The child's own words, only when it failed: that is where the reason is."""
    for stream in (result.stdout, result.stderr):
        for line in (stream or "").splitlines():
            if line.strip():
                emit(f"    {line.rstrip()}")


def openclaw_bin(setup: Setup) -> str:
    """The resolved command, or the bare name the shell would have to find."""
    return setup.which("openclaw") or "openclaw"


def engine_plugin_path(setup: Setup) -> Path:
    """Where the guardrail plugin has to be for OpenClaw to load it."""
    return setup.state_dir / "extensions" / PLUGIN_ID


def ensure_installed(setup: Setup, snapshot: Detection) -> Detection | None:
    """Step 1: the pinned release, or a clear reason why it cannot be put there."""
    install = snapshot.openclaw
    if not setup.which("node"):
        setup.out(t("Node is not installed and OpenClaw needs it. Install it from {url}, then run the setup again.",
                    url=setup_commands.NODE_DOWNLOAD_URL))
        return None
    if not _check_node(setup, snapshot):
        return None
    npm = setup.which("npm")
    if not _check_linux_host(setup, npm):
        return None
    if install.installed and setup.pinned and install.version == setup.pinned:
        setup.out(t("OpenClaw {version} is already the pinned release.", version=install.version))
        return snapshot
    if not npm:
        setup.out(t("npm is not beside Node. Install Node from {url}: it brings npm with it.",
                    url=setup_commands.NODE_DOWNLOAD_URL))
        return None
    if not setup.pinned:
        setup.out(t("The engine cannot read its OpenClaw pin, so it will not install an unknown release. "
                    "Check agent-universal-layer/openclaw/pin.json and run the setup again."))
        return None
    argv = setup_commands.install_argv(npm, setup.pinned)
    if install.installed:
        setup.out(t("OpenClaw {version} is not the pinned {pinned}: installing the pin.",
                    version=install.version or "?", pinned=setup.pinned))
    else:
        setup.out(t("OpenClaw is not installed: installing {pinned}.", pinned=setup.pinned))
    setup.out("$ " + command_line_text(argv))
    if setup.dry_run:
        return replace(snapshot, openclaw=replace(install, installed=True, version=setup.pinned))
    result = setup.run(argv, setup.env)
    if result.returncode != 0:
        setup.out(t("The install failed (exit {code}).", code=result.returncode))
        print_output(setup.out, result)
        return None
    setup.out(t("OpenClaw {pinned} is installed.", pinned=setup.pinned))
    if setup.which("openclaw") is None:
        setup.out(t("Note: 'openclaw' is still not on PATH; a new terminal may be needed."))
    return replace(snapshot, openclaw=replace(install, installed=True, version=setup.pinned))


def _check_node(setup: Setup, snapshot: Detection) -> bool:
    """The Node in use must satisfy the range OpenClaw declares."""
    if setup.dry_run:
        return True
    requirement = snapshot.openclaw.node_requirement or required_node_version(setup.engine_root)
    if not requirement:
        return True
    version = host_preflight.read_node_version(setup.run, setup.which("node") or "node", setup.env)
    if node_satisfies(version, requirement):
        return True
    setup.out(t("Node {version} does not satisfy what OpenClaw {pinned} needs ({requirement}).",
                version=version or t("(unknown)"), pinned=setup.pinned or "?", requirement=requirement))
    setup.out(t("Install a Node release in that range from {url}, then run the setup again.",
                url=setup_commands.NODE_DOWNLOAD_URL))
    return False


def _check_linux_host(setup: Setup, npm: str | None) -> bool:
    """Linux only: the user bus, npm's global prefix, and lingering."""
    if (setup.platform or sys.platform) != "linux" or setup.dry_run:
        return True
    if host_preflight.user_bus_missing(setup.env) or host_preflight.user_bus_unreachable(setup.run, setup.env):
        setup.out(t("The systemd user bus is not available in this session, so the gateway service cannot be managed."))
        setup.out(t("Log in to a normal user session with systemd, or start one, then run the setup again."))
        return False
    if npm:
        prefix = host_preflight.npm_prefix(setup.run, npm, setup.env)
        if prefix is not None and not host_preflight.is_writable(prefix):
            setup.out(t("npm installs global packages under {prefix}, which this user cannot write.", prefix=prefix))
            setup.out(t("Run 'npm config set prefix ~/.local' once, or use a version manager for Node, "
                        "then run the setup again."))
            return False
    user = setup.env.get("USER") or setup.env.get("LOGNAME") or ""
    if user and not host_preflight.linger_enabled(user, host_preflight.LINGER_DIR):
        setup.out(t("Lingering is off for {user}: the gateway stops when your last session closes.", user=user))
        setup.out(t("Run 'loginctl enable-linger {user}' once, then run the setup again.", user=user))
    return True


def ensure_onboarded(setup: Setup) -> bool:
    """Step 2: a configuration file exists, because OpenClaw made one."""
    if setup.config_path.is_file():
        setup.out(t("The configuration at {path} already exists.", path=setup.config_path))
        return True
    argv = setup_commands.onboard_argv(openclaw_bin(setup))
    setup.out(t("No configuration yet: letting OpenClaw create the baseline, with no questions."))
    setup.out("$ " + command_line_text(argv))
    if setup.dry_run:
        return True
    result = setup.run(argv, setup.env)
    if result.returncode != 0:
        setup.out(t("Onboarding failed (exit {code}); nothing was written.", code=result.returncode))
        print_output(setup.out, result)
        return False
    if not setup.config_path.is_file():
        setup.out(t("Onboarding finished but {path} was not created.", path=setup.config_path))
        return False
    setup.out(t("OpenClaw created {path}.", path=setup.config_path))
    return True


def planned_commands(setup: Setup, snapshot: Detection, plan) -> list[str]:
    """What a real run would execute, for `--dry-run` to print instead."""
    lines: list[str] = []
    install = snapshot.openclaw
    if not (install.installed and setup.pinned and install.version == setup.pinned):
        npm = setup.which("npm") or "npm"
        lines.append(command_line_text(setup_commands.install_argv(npm, setup.pinned or "<pin>")))
    if not plan.path.is_file():
        lines.append(command_line_text(setup_commands.onboard_argv(openclaw_bin(setup))))
    lines.append("# " + t("the gateway service, only if it is not installed yet"))
    lines.append("openclaw gateway install")
    lines.append(command_line_text(setup_commands.validate_argv(openclaw_bin(setup))))
    return lines


def write_step(setup: Setup, plan) -> str:
    """Step 4: the merge, the backup, the atomic write, and OpenClaw's verdict.

    Returns `written`, `unchanged` or `failed`. The gateway is stopped for the
    whole write and restarted when the block exits, on the way out of an error
    too: a half-written file must never be left behind, and a stopped gateway
    must never stay stopped. It is started without waiting here, because step 6
    is where the wait is shown to the person.
    """
    document, generated = setup_write.with_token(setup_write.merged_document(plan.current, plan.config))
    text = setup_write.render(document)
    if plan.current_error:
        setup.out(t("The file in force cannot be read ({error}); it is backed up before it is replaced.",
                    error=plan.current_error))
    if plan.current_error is None and plan.current == document:
        setup.out(t("The configuration is already what the engine would write: nothing to write."))
        return "unchanged"
    with setup.service.stopped(wait=False) as stop:
        if not stop.get("ok", True):
            setup.out(t("The gateway did not stop ({pids}); its file was left alone.",
                        pids=stop.get("remaining_pids")))
            return "failed"
        backup = setup_write.backup_file(setup.config_path, setup.stamp)
        if backup is not None:
            setup.out(t("The previous configuration is saved as {name}.", name=backup.name))
        setup_write.write_document(setup.config_path, text)
        setup.out(t("Configuration written to {path}.", path=setup.config_path))
        if generated:
            setup.out(t("The gateway token was generated for this machine and is not shown."))
        result = setup.run(
            setup_commands.validate_argv(openclaw_bin(setup)),
            setup_commands.config_env(setup.env, setup.config_path),
        )
        if result.returncode == 0:
            setup.out(t("OpenClaw accepts the configuration."))
            setup.out(t("The gateway starts again now, so it reads the new configuration."))
            return "written"
        setup_write.restore_file(setup.config_path, backup)
        setup.out(t("OpenClaw rejected the configuration (exit {code}): the previous file was put back.",
                    code=result.returncode))
        print_output(setup.out, result)
        return "failed"


def workspaces_step(setup: Setup, plan, before: set[str]) -> None:
    """Step 5: the folders, the manuals, and the first-run file to remove."""
    dirs = agent_dirs(plan.config.get("agents", {}).get("entries") or {})
    created = {agent for agent, path in dirs.items() if created_by_openclaw(path, setup.state_dir, before)}
    prepare(dirs, created, plan.choices.lang or None, setup.out)


def wait_for_port(
    service: OpenClawService,
    timeout_s: float = START_TIMEOUT_SECONDS,
    emit: Emit = print,
) -> tuple[bool, float]:
    """Wait for the gateway's port, saying every few seconds that it is still waiting."""
    started = service.time_provider()
    deadline = started + max(0.0, timeout_s)
    reported = -WAIT_REPORT_SECONDS
    while True:
        if service.port_probe(service.port, "127.0.0.1"):
            return True, service.time_provider() - started
        elapsed = service.time_provider() - started
        if elapsed >= timeout_s:
            return False, elapsed
        if elapsed - reported >= WAIT_REPORT_SECONDS:
            reported = elapsed
            emit(t("Waiting for the gateway on 127.0.0.1:{port}: {seconds} seconds so far.",
                   port=service.port, seconds=int(elapsed)))
        service.sleeper(min(1.0, max(0.1, deadline - service.time_provider())))


def _install_left_a_gateway(setup: Setup, result: dict) -> bool:
    """Whether `openclaw gateway install` launched the gateway it registered.

    The status the install returns is the first evidence. When it shows no
    process the install may still be starting one: for a bounded grace period
    (ten seconds in production, zero in tests) this waits for the port or the
    process, and only then does `service_step` fall back to an explicit start.
    Calling both at once is how two gateways ended up on one port.
    """
    status = result.get("status") if isinstance(result.get("status"), dict) else {}
    try:
        if int(status.get("process_count") or 0) > 0:
            return True
    except (TypeError, ValueError):  # nexgen: allow reason="a status a test double did not fill is read as no process"
        pass
    service = setup.service
    deadline = service.time_provider() + max(0.0, service.install_grace_seconds)
    while True:
        if service.port_probe(service.port, "127.0.0.1"):
            return True
        if any(is_gateway_process(proc) for proc in service.process_lister()):
            return True
        if service.time_provider() >= deadline:
            return False
        service.sleeper(1.0)


def service_step(setup: Setup, restarted: bool) -> bool:
    """Step 6: a service that is installed, and a port that answers."""
    status = setup.service.status()
    installed_now = False
    if status.get("installed"):
        setup.out(t("The gateway is already installed as a service ({mechanism}).",
                    mechanism=status.get("mechanism") or "?"))
    else:
        result = setup.service.install()
        setup.out(t("Gateway service install: {state}", state=t("done") if result.get("ok") else t("failed")))
        if not result.get("ok"):
            return False
        installed_now = _install_left_a_gateway(setup, result)
    if setup.service.port_probe(setup.service.port, "127.0.0.1"):
        setup.out(t("The gateway already answers on 127.0.0.1:{port}.", port=setup.service.port))
        return True
    if restarted:
        setup.out(t("Waiting for the gateway the write step restarted: a cold start takes about five minutes."))
    elif installed_now:
        setup.out(t("Waiting for the gateway the install started: a cold start takes about five minutes."))
    else:
        setup.out(t("Starting the gateway: a cold start takes about five minutes."))
        started = setup.service.start()
        for argv in started.get("commands") or []:
            setup.out("$ " + command_line_text(argv))
    ready, elapsed = wait_for_port(setup.service, START_TIMEOUT_SECONDS, setup.out)
    if ready:
        setup.out(t("The gateway answers on 127.0.0.1:{port} after {seconds} seconds.",
                    port=setup.service.port, seconds=int(elapsed)))
        return True
    setup.out(t("The gateway did not answer on 127.0.0.1:{port} within {seconds} seconds.",
                port=setup.service.port, seconds=int(elapsed)))
    return False


def module_step(setup: Setup) -> None:
    """Step 7: this machine declares the module, so the doctor keeps it aligned."""
    vault = resolve_vault_data(setup.home, setup.vault_data)
    try:
        write_state_file(vault, module="openclaw", state="local", host=current_host())
    except (OSError, ValueError) as exc:  # nexgen: allow reason="a read-only Vault is reported as a step that did not land, not as a crash after a successful install"
        setup.out(t("The module declaration did not land: {error}", error=exc))
        return
    setup.out(t("This machine declares 'openclaw: local' for host {host}.", host=current_host()))


def phone_step(setup: Setup) -> None:
    """Step 8: how the phone app gets in, in the person's own two commands."""
    setup.out(t("To connect the phone, run this in a terminal of yours: {command}",
                command=pairing_qr()["command"]))
    setup.out(t("Then approve the request from here: 'nexgen openclaw pair' lists it, "
                "'nexgen openclaw pair --approve <id>' approves it."))


def plugin_step(setup: Setup) -> None:
    """The engine's plugin is enabled only when its folder is actually there."""
    if engine_plugin_path(setup).is_dir():
        setup.out(t("The engine plugin {plugin} is deposited and enabled.", plugin=PLUGIN_ID))
    else:
        setup.out(t("The engine plugin {plugin} is not deposited on this machine: not enabled.", plugin=PLUGIN_ID))
