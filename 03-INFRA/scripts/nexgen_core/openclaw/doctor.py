"""The checks behind `nexgen openclaw doctor` and `nexgen doctor`.

One rule decides everything here: a machine that never chose OpenClaw must never
be told about it. When the module is not declared and nothing of OpenClaw is
installed, `collect` returns no outcomes at all, and `nexgen doctor` looks
exactly as it did before this feature existed.

The checks are read-only by construction. They read the configuration in force
and they open one TCP connection. When a caller asks for the node list — the
`--nodes` flag of `nexgen openclaw doctor` — they also ask a gateway that already
answers which nodes it knows, with the read-only query OpenClaw documents;
without that flag nothing of OpenClaw is started, because `nexgen doctor` runs on
a schedule of its own. They never validate the configuration and never write a
file.
"""
from __future__ import annotations

import os
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from nexgen_core import quota_policy
from nexgen_core.i18n import t
from nexgen_core.openclaw import background, doctor_runtime, observation
from nexgen_core.openclaw.config_file import read_config
from nexgen_core.openclaw.detect import Detection, detect
from nexgen_core.openclaw.evidence import check_evidence
from nexgen_core.openclaw.governor import check_fallbacks
from nexgen_core.openclaw.model_checks import check_agent_models, check_cage, check_ollama_latest
from nexgen_core.openclaw.pin import pinned_version, required_node_version
from nexgen_core.openclaw.profile import PENDING_TOKEN
from nexgen_core.openclaw.terms import google_subscriptions
from nexgen_core.paths import is_machine_home, openclaw_config, resolve_home, resolve_vault_data
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


def _check_installed(snapshot: Detection, declared: bool, pinned: str | None) -> CheckOutcome:
    """Installed, and on the release the engine configures against."""
    install = snapshot.openclaw
    install_command = f"npm install -g openclaw@{pinned or 'latest'}"
    if not install.installed:
        return CheckOutcome(
            id="openclaw.installed",
            severity=Severity.BROKEN,
            message=t("The OpenClaw module is declared on this machine but OpenClaw is not installed."),
            action=t("Install the pinned release: {command}", command=install_command),
        )
    if pinned is None:
        return CheckOutcome(
            id="openclaw.installed",
            severity=Severity.WARN,
            message=t("OpenClaw {version} is installed, but the engine cannot read its pin.",
                      version=install.version or "?"),
            action=t("Check agent-universal-layer/openclaw/pin.json: it holds the release the engine expects."),
        )
    if install.version != pinned:
        return CheckOutcome(
            id="openclaw.installed",
            severity=Severity.WARN,
            message=t("OpenClaw {version} is installed, the engine configures {pinned}.",
                      version=install.version or "?", pinned=pinned),
            action=t("Align it with: {command}", command=install_command),
        )
    return CheckOutcome(
        id="openclaw.installed",
        severity=Severity.OK,
        message=t("OpenClaw {version} is the pinned release.", version=install.version),
    )


def _check_declaration(declared: bool) -> CheckOutcome | None:
    """Installed but not declared: the engine will not keep it aligned."""
    if declared:
        return None
    return CheckOutcome(
        id="openclaw.declared",
        severity=Severity.WARN,
        message=t("OpenClaw is installed but this machine does not declare the module: the engine will not keep it aligned."),
        action=t("Declare 'openclaw: local' under this host in modules.state.yaml, or remove OpenClaw."),
    )


def _check_config(path: Path, config: dict[str, Any] | None, error: str | None) -> CheckOutcome:
    """The file exists, is JSON, and is an object OpenClaw can use."""
    if config is not None:
        return CheckOutcome(
            id="openclaw.config",
            severity=Severity.OK,
            message=t("The configuration at {path} is valid JSON.", path=path),
        )
    if error:
        return CheckOutcome(
            id="openclaw.config",
            severity=Severity.BROKEN,
            message=t("The configuration at {path} cannot be read: {error}", path=path, error=error),
            action=t("Fix the file or move it aside; 'nexgen openclaw plan' shows what the engine would write."),
        )
    return CheckOutcome(
        id="openclaw.config",
        severity=Severity.WARN,
        message=t("OpenClaw has no configuration at {path} yet.", path=path),
        action=t("Run 'nexgen openclaw plan' to see what the engine would write."),
    )


def _check_heartbeat(config: dict[str, Any]) -> CheckOutcome:
    """A live heartbeat starts system jobs that write on the channels continuously."""
    agents = config.get("agents") if isinstance(config.get("agents"), dict) else {}
    defaults = agents.get("defaults") if isinstance(agents.get("defaults"), dict) else {}
    heartbeat = defaults.get("heartbeat") if isinstance(defaults.get("heartbeat"), dict) else {}
    every = heartbeat.get("every")
    if every == "0m":
        return CheckOutcome(
            id="openclaw.heartbeat",
            severity=Severity.OK,
            message=t("The heartbeat is off, as the engine configures it."),
        )
    return CheckOutcome(
        id="openclaw.heartbeat",
        severity=Severity.WARN,
        message=t("The heartbeat runs every '{every}': it starts system jobs that write to the channels.",
                  every=every if isinstance(every, str) and every else t("(not set)")),
        action=t("Set agents.defaults.heartbeat.every to '0m'."),
    )


def _has_real_token(auth: dict[str, Any]) -> bool:
    """Whether the token is a secret, not the placeholder `setup` prints first."""
    token = auth.get("token")
    return isinstance(token, str) and bool(token.strip()) and token != PENDING_TOKEN


def _check_gateway(config: dict[str, Any]) -> list[CheckOutcome]:
    """Loopback or the home network, and a real token: anything else is an open door."""
    gateway = config.get("gateway") if isinstance(config.get("gateway"), dict) else {}
    outcomes: list[CheckOutcome] = []
    auth = gateway.get("auth") if isinstance(gateway.get("auth"), dict) else {}
    real_token = _has_real_token(auth)
    bind = gateway.get("bind")
    if bind == "loopback" or (bind == "lan" and real_token):
        outcomes.append(CheckOutcome(
            id="openclaw.gateway.bind",
            severity=Severity.OK,
            message=t("The gateway binds to {bind}.", bind=bind),
        ))
    elif bind == "lan":
        outcomes.append(CheckOutcome(
            id="openclaw.gateway.bind",
            severity=Severity.BROKEN,
            message=t("The gateway binds to the home network, but its token is missing or still the placeholder."),
            action=t("Run 'nexgen openclaw setup' to generate a real token, or set gateway.bind to 'loopback'."),
        ))
    else:
        outcomes.append(CheckOutcome(
            id="openclaw.gateway.bind",
            severity=Severity.BROKEN,
            message=t("The gateway binds to '{bind}', which is neither loopback nor the home network.",
                      bind=bind if isinstance(bind, str) and bind else t("(not set)")),
            action=t("Set gateway.bind to 'loopback', or 'lan' if you use the phone app on your home network."),
        ))
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


def _check_google(config: dict[str, Any]) -> CheckOutcome:
    """Google plans may not be used outside Google's own clients; an API key may."""
    forbidden = google_subscriptions(config)
    if forbidden:
        return CheckOutcome(
            id="openclaw.plugins.google",
            severity=Severity.BROKEN,
            message=t("OpenClaw is configured with a Google subscription, which its terms do not allow: {names}",
                      names=", ".join(forbidden)),
            action=t("Remove it: use Claude, Codex, the OpenCode Go key or a local model."),
        )
    return CheckOutcome(
        id="openclaw.plugins.google",
        severity=Severity.OK,
        message=t("No Google subscription is configured in OpenClaw."),
    )


def _check_acp(config: dict[str, Any]) -> CheckOutcome:
    """A Gemini seat through ACP is allowed by the protocol and not by the terms."""
    acp = config.get("acp") if isinstance(config.get("acp"), dict) else {}
    allowed = acp.get("allowedAgents") if isinstance(acp.get("allowedAgents"), list) else []
    if any(str(agent).lower() == "gemini" for agent in allowed):
        return CheckOutcome(
            id="openclaw.acp.gemini",
            severity=Severity.WARN,
            message=t("The ACP bridge allows the 'gemini' agent; its terms do not allow it outside Google's clients."),
            action=t("Remove 'gemini' from acp.allowedAgents."),
        )
    return CheckOutcome(
        id="openclaw.acp.gemini",
        severity=Severity.OK,
        message=t("The ACP bridge does not allow a Google agent."),
    )


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
    node_lister: doctor_runtime.NodeLister | None,
    platform: str,
) -> list[CheckOutcome]:
    """Every check that reads the configuration in force, in the order a person reads them.

    `failover` is the policy the caller already read, when it has one; without it the
    policy is read here, from the same Vault the other checks read.
    """
    policy = failover if failover is not None else quota_policy.load(
        home_dir, vault=resolve_vault_data(home_dir, vault_data),
    )[0]
    outcomes = filter(None, [
        check_ollama_latest(config),
        check_cage(config),
        check_evidence(config, path),
        _check_heartbeat(config),
        *_check_gateway(config),
        _check_google(config),
        _check_acp(config),
        check_agent_models(config, snapshot),
        background.report(config),
        observation.report(config, path, home_dir, platform),
        check_fallbacks(config, policy),
        _listening(config, connect, home_dir),
        *doctor_runtime.check_nodes(config, _probe(home_dir, connect), node_lister),
        *doctor_runtime.check_workshop_skills(config, path.parent, home_dir),
        doctor_runtime.check_unadopted(config, path, home_dir, resolve_vault_data(home_dir, vault_data)),
        doctor_runtime.check_bindings(config),
    ])
    return list(outcomes)


def _other_gateway_outcomes(
    home_dir: Path,
    values: Mapping[str, str],
    system: str,
    wsl_distributions: Callable[[], list[str]] | None,
) -> list[CheckOutcome]:
    """The second gateways a person may have installed beside this one."""
    outcomes: list[CheckOutcome] = []
    store = doctor_runtime.check_store_gateway(home_dir, values, system)
    if store is not None:
        outcomes.append(store)
    companion = doctor_runtime.check_companion_gateway(home_dir, values, system, wsl_distributions)
    if companion is not None:
        outcomes.append(companion)
    return outcomes


def collect(
    detected: Detection | None = None,
    *,
    home: Path | None = None,
    env: Mapping[str, str] | None = None,
    engine_root: Path | None = None,
    vault_data: Path | None = None,
    connect: doctor_runtime.Connect | None = None,
    node_lister: doctor_runtime.NodeLister | None = None,
    platform: str | None = None,
    wsl_distributions: Callable[[], list[str]] | None = None,
    node_version: Callable[[], str | None] | None = None,
    linger_dir: Path | None = None,
    failover: quota_policy.Failover | None = None,
) -> list[CheckOutcome]:
    """Every OpenClaw check that applies to this machine, or nothing at all."""
    values = dict(os.environ) if env is None else {str(k): str(v) for k, v in env.items()}
    home_dir = resolve_home(home)
    system = platform or sys.platform
    declared = _declared(vault_data, home_dir)

    snapshot = detected
    if snapshot is None:
        if not declared and not _has_trace(home_dir, values):
            return []
        snapshot = detect(
            home=home_dir, env=values, engine_root=engine_root, vault_data=vault_data, platform=system,
        )
    if not snapshot.openclaw.installed and not declared:
        return []

    pinned = _pinned(engine_root)
    path = Path(snapshot.openclaw.config_path)
    config, error = read_config(path)
    outcomes: list[CheckOutcome] = [_check_installed(snapshot, declared, pinned)]
    declaration = _check_declaration(declared)
    if declaration is not None:
        outcomes.append(declaration)
    outcomes.append(_check_config(path, config, error))
    if config is not None:
        outcomes.extend(_config_outcomes(
            config, snapshot, path, home_dir, vault_data, failover, connect, node_lister, system,
        ))
    outcomes.extend(_other_gateway_outcomes(home_dir, values, system, wsl_distributions))
    outcomes.extend(_host_outcomes(snapshot, engine_root, values, system, node_version, linger_dir))
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
