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
import socket
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from nexgen_core.i18n import t
from nexgen_core.openclaw.config_file import read_config
from nexgen_core.openclaw.detect import Detection, detect
from nexgen_core.openclaw.pin import pinned_version
from nexgen_core.openclaw.profile import GATEWAY_PORT
from nexgen_core.openclaw.terms import google_subscriptions
from nexgen_core.paths import openclaw_config, resolve_home, resolve_vault_data
from nexgen_core.report import CheckOutcome, Severity

#: Which subscription a model provider depends on. Local providers are not here:
#: they are covered by the cage check, not by a login.
SUBSCRIPTION_LABELS = {
    "anthropic": "Claude",
    "openai": "Codex",
    "opencode-go": "the OpenCode Go key",
}

#: Numbers that mean "no port": the check reads the gateway's own setting.
DEFAULT_GATEWAY_PORT = GATEWAY_PORT
LISTEN_TIMEOUT_SECONDS = 0.4

Connect = Callable[[str, int, float], bool]


def _tcp_open(host: str, port: int, timeout: float) -> bool:
    """Whether something accepts a TCP connection there, without sending a byte."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:  # nexgen: allow reason="a closed port is the expected answer when the gateway is stopped"
        return False


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
    """Every agent's model, read from either the object or the plain-string shape."""
    entries = config.get("agents", {}).get("entries") if isinstance(config.get("agents"), dict) else None
    if not isinstance(entries, dict):
        return {}
    refs: dict[str, str] = {}
    for name, entry in entries.items():
        model = entry.get("model") if isinstance(entry, dict) else entry
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
    for provider in ("ollama", "llamacpp"):
        refs.extend(f"{provider}/{model_id}" for model_id in _provider_model_ids(config, provider))
    refs.extend(
        ref for ref in _agent_model_refs(config).values()
        if ref.split("/", 1)[0] in ("ollama", "llamacpp")
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


def _check_gateway(config: dict[str, Any]) -> list[CheckOutcome]:
    """Loopback or the home network, and a token: anything else is an open door."""
    gateway = config.get("gateway") if isinstance(config.get("gateway"), dict) else {}
    outcomes: list[CheckOutcome] = []
    bind = gateway.get("bind")
    if bind in ("loopback", "lan"):
        outcomes.append(CheckOutcome(
            id="openclaw.gateway.bind",
            severity=Severity.OK,
            message=t("The gateway binds to {bind}.", bind=bind),
        ))
    else:
        outcomes.append(CheckOutcome(
            id="openclaw.gateway.bind",
            severity=Severity.BROKEN,
            message=t("The gateway binds to '{bind}', which is neither loopback nor the home network.",
                      bind=bind if isinstance(bind, str) and bind else t("(not set)")),
            action=t("Set gateway.bind to 'loopback', or 'lan' if you use the phone app on your home network."),
        ))
    auth = gateway.get("auth") if isinstance(gateway.get("auth"), dict) else {}
    if auth.get("mode") == "token":
        outcomes.append(CheckOutcome(
            id="openclaw.gateway.auth",
            severity=Severity.OK,
            message=t("The gateway asks for a token."),
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


def _check_listening(config: dict[str, Any], connect: Connect) -> CheckOutcome:
    """Informational: a gateway that is stopped is a normal state, never an error."""
    gateway = config.get("gateway") if isinstance(config.get("gateway"), dict) else {}
    port = gateway.get("port") if isinstance(gateway.get("port"), int) else DEFAULT_GATEWAY_PORT
    if connect("127.0.0.1", int(port), LISTEN_TIMEOUT_SECONDS):
        return CheckOutcome(
            id="openclaw.gateway.listening",
            severity=Severity.OK,
            message=t("The gateway answers on 127.0.0.1:{port}.", port=port),
        )
    return CheckOutcome(
        id="openclaw.gateway.listening",
        severity=Severity.OK,
        message=t("The gateway is not listening on 127.0.0.1:{port} (normal when it is stopped).", port=port),
    )


def _store_packages(home: Path, env: Mapping[str, str], platform: str) -> list[str]:
    """The Microsoft Store package of OpenClaw, when Windows installed one."""
    if not platform.startswith("win"):
        return []
    local_appdata = env.get("LOCALAPPDATA") or str(home / "AppData" / "Local")
    packages = Path(local_appdata) / "Packages"
    if not packages.is_dir():
        return []
    return sorted(
        child.name for child in packages.iterdir()
        if child.is_dir() and "openclaw" in child.name.lower()
    )


def _check_store_gateway(home: Path, env: Mapping[str, str], platform: str) -> CheckOutcome | None:
    """Two gateways on the same machine fight over the same port and state."""
    found = _store_packages(home, env, platform)
    if not found:
        return None
    return CheckOutcome(
        id="openclaw.store_gateway",
        severity=Severity.WARN,
        message=t("A second OpenClaw from the Microsoft Store is installed ({names}): two gateways in conflict.",
                  names=", ".join(found)),
        action=t("Remove the Microsoft Store app: the engine manages the npm one."),
    )


def collect(
    detected: Detection | None = None,
    *,
    home: Path | None = None,
    env: Mapping[str, str] | None = None,
    engine_root: Path | None = None,
    vault_data: Path | None = None,
    connect: Connect | None = None,
    platform: str | None = None,
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
        outcomes.extend([
            _check_ollama_latest(config),
            _check_cage(config),
            _check_heartbeat(config),
            *_check_gateway(config),
            _check_google(config),
            _check_acp(config),
            _check_agent_models(config, snapshot),
            _check_listening(config, connect or _tcp_open),
        ])
    store = _check_store_gateway(home_dir, values, system)
    if store is not None:
        outcomes.append(store)
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
