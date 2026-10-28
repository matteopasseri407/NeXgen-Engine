"""The checks behind `nexgen openclaw doctor` and `nexgen doctor`.

One rule decides everything here: a machine that never chose OpenClaw must never
be told about it. When the module is not declared, nothing of OpenClaw is
installed and the Windows app has no gateway here, `collect` returns no
outcomes at all, and `nexgen doctor` looks exactly as it did before this
feature existed.

The checks are read-only by construction. They read the configuration in force
and they open one TCP connection. They never run `openclaw config validate`,
because starting or validating OpenClaw is B2's job, and they never write a file.
Three commands a check may run, and only when the caller hands them in: the node
list, and the exec approvals of the node an agent runs on, both behind the
`--nodes` flag with the read-only queries OpenClaw documents; and the gateway's own
device list, because `nexgen openclaw doctor` is where a person asks whether the
app is paired. None of them runs on `nexgen doctor`'s own schedule, which has no
business launching a CLI.
"""
from __future__ import annotations

import os
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from nexgen_core import quota_policy
from nexgen_core.i18n import t
from nexgen_core.openclaw import (
    background,
    companion,
    doctor_agy,
    doctor_config,
    doctor_mcp,
    doctor_runtime,
    node_approvals,
    node_checks,
    observation,
)
from nexgen_core.openclaw.config_file import read_config
from nexgen_core.openclaw.detect import Detection, detect
from nexgen_core.openclaw.doctor_install import check_config, check_declaration, check_installed
from nexgen_core.openclaw.evidence import check_evidence
from nexgen_core.openclaw.governor import check_fallbacks
from nexgen_core.openclaw.model_checks import (
    check_agent_models,
    check_cage,
    check_model_overrides,
    check_ollama_latest,
)
from nexgen_core.openclaw.pin import pinned_version, required_node_version
from nexgen_core.openclaw.profile import PENDING_TOKEN
from nexgen_core.openclaw.server_gateway import exposure
from nexgen_core.paths import (
    is_machine_home,
    mcp_manifest,
    openclaw_config,
    resolve_home,
    resolve_state_dir,
    resolve_vault_data,
)
from nexgen_core.report import CheckOutcome, Severity


def _declared(vault_data: Path | None, home: Path | None = None) -> bool:
    """Whether this machine declared the OpenClaw module `local`."""
    from nexgen_core.modules import load_state_file

    try:
        states = load_state_file(resolve_vault_data(home, vault_data))
    except (OSError, ValueError):  # nexgen: allow reason="an unreadable state file means 'not declared', and its own check reports the damage"
        return False
    return states.get("openclaw") == "local"


def _has_trace(home: Path, env: Mapping[str, str]) -> bool:
    """The cheap question asked before the expensive one: is there a file to judge?

    Deliberately the configuration file and not the binary on PATH: a global
    binary is a fact about the machine, while this check must answer about the
    home it was given and must never read another one. A machine that installed
    OpenClaw and never configured it gets silence, which is the same answer a
    machine without it gets; the module's own declaration, when present, is what
    makes the doctor speak.
    """
    return openclaw_config(home, env).is_file()


def _pinned(engine_root: Path | None) -> str | None:
    return pinned_version(engine_root)


def _has_real_token(auth: dict[str, Any]) -> bool:
    """Whether the token is a secret, not the placeholder `setup` prints first."""
    token = auth.get("token")
    return isinstance(token, str) and bool(token.strip()) and token != PENDING_TOKEN


def _remote_url(gateway: dict[str, Any]) -> str | None:
    """The gateway a node points at, when the file names one."""
    remote = gateway.get("remote") if isinstance(gateway.get("remote"), dict) else {}
    url = remote.get("url")
    return url.strip() if isinstance(url, str) and url.strip() else None


def _bind_outcome(bind: Any, real_token: bool) -> CheckOutcome:
    """Where the listener sits, and whether a listener on the home network is safe."""
    if bind == "loopback" or (bind == "lan" and real_token):
        return CheckOutcome(
            id="openclaw.gateway.bind",
            severity=Severity.OK,
            message=t("The gateway binds to {bind}.", bind=bind),
        )
    if bind == "lan":
        return CheckOutcome(
            id="openclaw.gateway.bind",
            severity=Severity.BROKEN,
            message=t("The gateway binds to the home network, but its token is missing or still the placeholder."),
            action=t("Run 'nexgen openclaw setup' to generate a real token, or set gateway.bind to 'loopback'."),
        )
    return CheckOutcome(
        id="openclaw.gateway.bind",
        severity=Severity.BROKEN,
        message=t("The gateway binds to '{bind}', which is neither loopback nor the home network.",
                  bind=bind if isinstance(bind, str) and bind else t("(not set)")),
        action=t("Set gateway.bind to 'loopback', or 'lan' if you use the phone app on your home network."),
    )


def _check_gateway(config: dict[str, Any]) -> list[CheckOutcome]:
    """Loopback or the home network, and a real token: anything else is an open door.

    A node is the exception, and not a lenient one: it declares `remote` and
    opens no listener here, so it has no bind and no local token to judge. Asking
    it for both would turn every node red for a port it is not supposed to open.
    """
    gateway = config.get("gateway") if isinstance(config.get("gateway"), dict) else {}
    mode = gateway.get("mode")
    if isinstance(mode, str) and mode.strip() == "remote":
        return [CheckOutcome(
            id="openclaw.gateway.bind",
            severity=Severity.OK,
            message=t("This machine is a node of the gateway at {url}: it opens no listener of its own.",
                      url=_remote_url(gateway) or t("(not named)")),
        )]
    auth = gateway.get("auth") if isinstance(gateway.get("auth"), dict) else {}
    real_token = _has_real_token(auth)
    outcomes: list[CheckOutcome] = [_bind_outcome(gateway.get("bind"), real_token)]
    if auth.get("mode") == "token" and real_token:
        outcomes.append(CheckOutcome(
            id="openclaw.gateway.auth",
            severity=Severity.OK,
            message=t("The gateway asks for a token."),
        ))
    elif auth.get("mode") == "token":
        outcomes.append(CheckOutcome(
            id="openclaw.gateway.auth",
            severity=Severity.BROKEN,
            message=t("The gateway uses token authentication, but the token is missing or still the placeholder."),
            action=t("Run 'nexgen openclaw setup': it generates the token once and never prints it."),
        ))
    else:
        outcomes.append(CheckOutcome(
            id="openclaw.gateway.auth",
            severity=Severity.BROKEN,
            message=t("The gateway does not use token authentication (mode: {mode}).",
                      mode=auth.get("mode") or t("(not set)")),
            action=t("Set gateway.auth.mode to 'token' and let 'nexgen openclaw setup' generate the token."),
        ))
    return outcomes


def _listening(config: dict[str, Any], connect: doctor_runtime.Connect | None,
               home: Path) -> CheckOutcome:
    """The gateway probe, asked only where it can be about something here.

    An injected connector means the caller already decided (a test, or a caller
    with its own probe). Otherwise the question is skipped on a copy: there is
    one gateway per machine, and a copy beside the working installation would be
    reporting on a process it does not own.
    """
    if connect is not None:
        return doctor_runtime.check_listening(config, connect)
    if not is_machine_home(home):
        return doctor_runtime.check_listening_of_a_copy(config)
    return doctor_runtime.check_listening(config, doctor_runtime.default_connect)


def _probe(home: Path, connect: doctor_runtime.Connect | None) -> doctor_runtime.Connect:
    """The connector the probes use: the caller's, or one that answers nothing on a copy.

    There is one gateway per machine, so a copy of a home must never report on a
    process it does not own.
    """
    if connect is not None:
        return connect
    if not is_machine_home(home):
        return lambda *_args: False
    return doctor_runtime.default_connect


def _host_outcomes(
    snapshot: Detection,
    engine_root: Path | None,
    values: Mapping[str, str],
    system: str,
    node_version: Callable[[], str | None] | None,
    linger_dir: Path | None,
) -> list[CheckOutcome]:
    """The checks that read the host rather than the configuration."""
    requirement = snapshot.openclaw.node_requirement or required_node_version(engine_root)
    outcomes: list[CheckOutcome] = []
    node = doctor_runtime.check_node(requirement, (node_version or doctor_runtime.current_node_version)())
    if node is not None:
        outcomes.append(node)
    linger = doctor_runtime.check_linger(system, values.get("USER") or values.get("LOGNAME"), linger_dir)
    if linger is not None:
        outcomes.append(linger)
    return outcomes


def _config_outcomes(
    config: dict[str, Any],
    snapshot: Detection,
    path: Path,
    home_dir: Path,
    vault_data: Path | None,
    failover: quota_policy.Failover | None,
    connect: doctor_runtime.Connect | None,
    node_lister: node_checks.NodeLister | None,
    platform: str,
    env: Mapping[str, str] | None = None,
    approvals: node_approvals.Reader | None = None,
) -> list[CheckOutcome]:
    """Every check that reads the configuration in force, in the order a person reads them.

    `failover` is the policy the caller already read, when it has one; without it the
    policy is read here, from the same Vault the other checks read. `approvals` is the
    node query the caller asked for, and it is asked only where a node is named.
    """
    policy = failover if failover is not None else quota_policy.load(
        home_dir, vault=resolve_vault_data(home_dir, vault_data),
    )[0]
    manifest_p = mcp_manifest(resolve_vault_data(home_dir, vault_data))
    outcomes = filter(None, [
        check_ollama_latest(config),
        check_cage(config),
        check_evidence(config, path),
        doctor_config.check_heartbeat(config),
        *_check_gateway(config),
        exposure(config),
        doctor_config.check_google(config),
        doctor_agy.check_local_agy(config),
        doctor_config.check_acp(config),
        doctor_config.check_tool_search(config),
        doctor_config.check_mcp_lazy(config),
        doctor_mcp.check_mcp_secret_url(config, manifest_p, env or os.environ),
        check_agent_models(config, snapshot),
        check_model_overrides(config),
        background.report(config),
        observation.report(config, path, home_dir, platform),
        check_fallbacks(config, policy),
        _listening(config, connect, home_dir),
        *node_checks.check_nodes(config, _probe(home_dir, connect), node_lister),
        *node_approvals.check_agents(config, approvals),
        *doctor_runtime.check_workshop_skills(config, path.parent, home_dir),
        doctor_runtime.check_unadopted(config, path, home_dir, resolve_vault_data(home_dir, vault_data)),
        doctor_runtime.check_bindings(config),
        *_plugin_outcomes(path, config),
    ])
    return list(outcomes)


def _store_gateway_outcomes(
    home_dir: Path,
    values: Mapping[str, str],
    system: str,
) -> list[CheckOutcome]:
    """The second gateway a person may have installed beside this one."""
    store = doctor_runtime.check_store_gateway(home_dir, values, system)
    return [store] if store is not None else []


def _plugin_outcomes(path: Path, config: dict[str, Any]) -> list[CheckOutcome]:
    """Whether OpenClaw loads the plugin, and the switches it reads on every call."""
    plugin_dir = path.parent / "extensions" / "nexgen"
    outcomes = [
        doctor_runtime.check_plugin_enabled(config, plugin_dir),
        doctor_runtime.check_checkpoint(plugin_dir),
    ]
    return [outcome for outcome in outcomes if outcome is not None]


def _speaking_snapshot(
    detected: Detection | None,
    *,
    declared: bool,
    home_dir: Path,
    values: Mapping[str, str],
    system: str,
    engine_root: Path | None,
    vault_data: Path | None,
    wsl_distributions: Callable[[], list[str]] | None,
) -> tuple[companion.Companion, Detection | None]:
    """The companion probe and snapshot, or None when this machine has nothing to say."""
    app_gateway = detected.companion if detected is not None else companion.probe(
        home_dir, values, system, wsl_distributions,
    )
    if detected is not None:
        return app_gateway, detected
    if not declared and not _has_trace(home_dir, values) and app_gateway.situation(False) == companion.NONE:
        return app_gateway, None
    return app_gateway, detect(
        home=home_dir, env=values, engine_root=engine_root, vault_data=vault_data, platform=system,
        wsl_distributions=wsl_distributions,
    )


def collect(
    detected: Detection | None = None,
    *,
    home: Path | None = None,
    env: Mapping[str, str] | None = None,
    engine_root: Path | None = None,
    vault_data: Path | None = None,
    connect: doctor_runtime.Connect | None = None,
    node_lister: node_checks.NodeLister | None = None,
    platform: str | None = None,
    wsl_distributions: Callable[[], list[str]] | None = None,
    node_version: Callable[[], str | None] | None = None,
    linger_dir: Path | None = None,
    failover: quota_policy.Failover | None = None,
    pending: Callable[[], list[dict[str, Any]] | None] | None = None,
    node_approvals_reader: node_approvals.Reader | None = None,
) -> list[CheckOutcome]:
    """Every OpenClaw check that applies to this machine, or nothing at all.

    `pending` and `node_approvals_reader` both start OpenClaw's CLI, so each is asked
    only by a caller that handed one in: `nexgen doctor` on its schedule passes neither.
    """
    values = dict(os.environ) if env is None else {str(k): str(v) for k, v in env.items()}
    home_dir = resolve_home(home)
    system = platform or sys.platform
    declared = _declared(vault_data, home_dir)
    app_gateway, snapshot = _speaking_snapshot(
        detected, declared=declared, home_dir=home_dir, values=values,
        system=system, engine_root=engine_root, vault_data=vault_data, wsl_distributions=wsl_distributions,
    )
    if snapshot is None:
        return []
    if not snapshot.openclaw.installed and not declared:
        # Only the app's gateway is here: the native checks have nothing to judge.
        app_only = doctor_runtime.check_companion_gateway(app_gateway, False, system)
        return [app_only] if app_only is not None else []

    pinned = _pinned(engine_root)
    path = Path(snapshot.openclaw.config_path)
    config, error = read_config(path)
    outcomes: list[CheckOutcome] = [check_installed(snapshot, declared, pinned)]
    outcomes.extend(filter(None, [check_declaration(declared), check_config(path, config, error)]))
    if config is not None:
        outcomes.extend(_config_outcomes(
            config, snapshot, path, home_dir, vault_data, failover, connect, node_lister, system,
            env=values, approvals=node_approvals_reader,
        ))
    outcomes.extend(_store_gateway_outcomes(home_dir, values, system))
    app_outcome = doctor_runtime.check_companion_gateway(
        app_gateway, snapshot.openclaw.installed, system, pending,
    )
    if app_outcome is not None:
        outcomes.append(app_outcome)
    outcomes.extend(_host_outcomes(snapshot, engine_root, values, system, node_version, linger_dir))
    from nexgen_core.openclaw.adopt_auto import check_adopt_doctor
    outcomes.append(check_adopt_doctor(resolve_state_dir(home_dir)))
    return outcomes


def _label(severity: Severity | str) -> str:
    """The word a person reads, not the enum the code uses."""
    return {
        Severity.OK: "PASS",
        Severity.WARN: "WARN",
        Severity.BROKEN: "FAIL",
        Severity.UNDETERMINED: "WARN",
    }.get(severity, str(severity).upper())


def render(outcomes: list[CheckOutcome]) -> str:
    """One line per check, with the reason a person can act on."""
    if not outcomes:
        return t("OpenClaw is not installed here and this machine does not declare the module: nothing to check.")
    lines: list[str] = []
    for outcome in outcomes:
        lines.append(f"{_label(outcome.severity)}  {outcome.id}  {outcome.message}")
        if outcome.action:
            lines.append(f"      -> {outcome.action}")
    failed = sum(1 for outcome in outcomes if outcome.severity == Severity.BROKEN)
    warned = sum(1 for outcome in outcomes if outcome.severity == Severity.WARN)
    passed = len(outcomes) - failed - warned
    lines.append("")
    lines.append(t("{passed} passed, {warned} warnings, {failed} failures", passed=passed, warned=warned, failed=failed))
    return "\n".join(lines)


def exit_code(outcomes: list[CheckOutcome]) -> int:
    """Non-zero only for a failure: a warning is work for an agent, not a broken machine."""
    return 1 if any(outcome.severity == Severity.BROKEN for outcome in outcomes) else 0
