"""The checks behind `nexgen openclaw doctor` and `nexgen doctor`.

One rule decides everything here: a machine that never chose OpenClaw must never
be told about it. When the module is not declared and nothing of OpenClaw is
installed, `collect` returns no outcomes at all, and `nexgen doctor` looks
exactly as it did before this feature existed.

The checks are read-only by construction. They read the configuration in force
and they open one TCP connection; they never run `openclaw config validate`,
because starting or validating OpenClaw is B2's job, and never write a file.
"""
from __future__ import annotations

import os
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from nexgen_core import quota_policy
from nexgen_core.i18n import t
from nexgen_core.openclaw import doctor_runtime
from nexgen_core.openclaw.config_file import read_config
from nexgen_core.openclaw.detect import Detection, detect
from nexgen_core.openclaw.governor import check_fallbacks
from nexgen_core.openclaw.pin import pinned_version, required_node_version
from nexgen_core.openclaw.profile import PENDING_TOKEN
from nexgen_core.openclaw.terms import google_subscriptions
from nexgen_core.paths import is_machine_home, openclaw_config, resolve_home, resolve_vault_data
from nexgen_core.report import CheckOutcome, Severity

#: Which subscription a model provider depends on. Local providers are not here:
#: they are covered by the cage check, not by a login.
SUBSCRIPTION_LABELS = {
    "anthropic": "Claude",
    "openai": "Codex",
    "opencode-go": "the OpenCode Go key",
}

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


def _agent_model_refs(config: dict[str, Any]) -> dict[str, str]:
    """Every agent's model, read from the string, object or ACP-primary shape."""
    entries = config.get("agents", {}).get("entries") if isinstance(config.get("agents"), dict) else None
    if not isinstance(entries, dict):
        return {}
    refs: dict[str, str] = {}
    for name, entry in entries.items():
        model = entry.get("model") if isinstance(entry, dict) else entry
        if isinstance(model, dict):
            # An ACP agent selects its harness model through `model.primary`.
            model = model.get("primary")
        if isinstance(model, str) and model.strip():
            refs[str(name)] = model.strip()
    return refs


def _provider_model_ids(config: dict[str, Any], provider: str) -> list[str]:
    """The ids a provider block declares, tolerating both shapes OpenClaw accepts."""
    providers = config.get("models", {}).get("providers") if isinstance(config.get("models"), dict) else None
    block = providers.get(provider) if isinstance(providers, dict) else None
    models = block.get("models") if isinstance(block, dict) else None
    if not isinstance(models, list):
        return []
    ids: list[str] = []
    for item in models:
        model_id = item.get("id") if isinstance(item, dict) else item
        if isinstance(model_id, str) and model_id:
            ids.append(model_id)
    return ids


def _local_refs(config: dict[str, Any]) -> list[str]:
    """Every local model the configuration can actually name, provider-qualified."""
    refs: list[str] = []
    for provider in ("ollama", "llama-cpp"):
        refs.extend(f"{provider}/{model_id}" for model_id in _provider_model_ids(config, provider))
    refs.extend(
        ref for ref in _agent_model_refs(config).values()
        if ref.split("/", 1)[0] in ("ollama", "llama-cpp")
    )
    return sorted(set(refs))


def _check_ollama_latest(config: dict[str, Any]) -> CheckOutcome:
    """OpenClaw does not recognise `:latest`, and then guesses the context window."""
    suffixed = [model_id for model_id in _provider_model_ids(config, "ollama") if model_id.endswith(":latest")]
    suffixed.extend(ref for ref in _agent_model_refs(config).values() if ref.startswith("ollama/") and ref.endswith(":latest"))
    if suffixed:
        return CheckOutcome(
            id="openclaw.ollama_latest",
            severity=Severity.BROKEN,
            message=t("Ollama model ids carry ':latest', which OpenClaw does not recognise: {ids}",
                      ids=", ".join(sorted(set(suffixed)))),
            action=t("Write the id without ':latest' and run 'nexgen openclaw setup' again."),
        )
    return CheckOutcome(
        id="openclaw.ollama_latest",
        severity=Severity.OK,
        message=t("Every Ollama model id is written without ':latest'."),
    )


def _check_cage(config: dict[str, Any]) -> CheckOutcome:
    """A local model without its cage is a model that can write the whole machine."""
    tools = config.get("tools") if isinstance(config.get("tools"), dict) else {}
    by_provider = tools.get("byProvider") if isinstance(tools.get("byProvider"), dict) else {}
    caged = [
        ref for ref in _local_refs(config)
        if isinstance(by_provider.get(ref), dict) and by_provider[ref].get("deny")
    ]
    missing = [ref for ref in _local_refs(config) if ref not in caged]
    if missing:
        return CheckOutcome(
            id="openclaw.cage",
            severity=Severity.BROKEN,
            message=t("This local model has no cage in tools.byProvider: {models}", models=", ".join(missing)),
            action=t("Run 'nexgen openclaw plan', then setup: a local model must not write, execute or browse."),
        )
    return CheckOutcome(
        id="openclaw.cage",
        severity=Severity.OK,
        message=t("Every local model is caged ({count}).", count=len(caged)),
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


def _check_agent_models(config: dict[str, Any], snapshot: Detection) -> CheckOutcome:
    """An agent on a subscription this machine does not have cannot answer."""
    available = {
        "anthropic": snapshot.claude.available,
        "openai": snapshot.codex.available,
        "opencode-go": snapshot.opencode_go,
    }
    unusable = []
    for agent, model in sorted(_agent_model_refs(config).items()):
        provider = model.split("/", 1)[0]
        if provider in available and not available[provider]:
            unusable.append(f"{agent} -> {model} ({SUBSCRIPTION_LABELS[provider]})")
    if unusable:
        return CheckOutcome(
            id="openclaw.agent_models",
            severity=Severity.WARN,
            message=t("These agents use a subscription this machine does not have: {items}",
                      items="; ".join(unusable)),
            action=t("Log in to the subscription, or run 'nexgen openclaw plan' to see the model the engine would choose."),
        )
    return CheckOutcome(
        id="openclaw.agent_models",
        severity=Severity.OK,
        message=t("Every agent uses a model this machine can reach."),
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
) -> list[CheckOutcome]:
    """Every check that reads the configuration in force, once it is readable.

    `failover` is the policy the caller already read, when it has one; without it the
    policy is read here, from the same Vault the other checks read.
    """
    policy = failover if failover is not None else quota_policy.load(
        home_dir, vault=resolve_vault_data(home_dir, vault_data),
    )[0]
    outcomes = filter(None, [
        _check_ollama_latest(config),
        _check_cage(config),
        _check_heartbeat(config),
        *_check_gateway(config),
        _check_google(config),
        _check_acp(config),
        _check_agent_models(config, snapshot),
        check_fallbacks(config, policy),
        _listening(config, connect, home_dir),
        *doctor_runtime.check_workshop_skills(config, path.parent, home_dir),
        doctor_runtime.check_unadopted(config, path, home_dir, resolve_vault_data(home_dir, vault_data)),
        doctor_runtime.check_bindings(config),
    ])
    return list(outcomes)


def collect(
    detected: Detection | None = None,
    *,
    home: Path | None = None,
    env: Mapping[str, str] | None = None,
    engine_root: Path | None = None,
    vault_data: Path | None = None,
    connect: doctor_runtime.Connect | None = None,
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
            config, snapshot, path, home_dir, vault_data, failover, connect,
        ))
    store = doctor_runtime.check_store_gateway(home_dir, values, system)
    if store is not None:
        outcomes.append(store)
    companion = doctor_runtime.check_companion_gateway(home_dir, values, system, wsl_distributions)
    if companion is not None:
        outcomes.append(companion)
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
