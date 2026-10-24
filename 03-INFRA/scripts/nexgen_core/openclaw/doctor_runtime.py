"""The live state around an installed OpenClaw: ports, other gateways, learned skills.

One job: the checks that read machine state rather than configuration, so
`doctor.py` stays the rules of the configuration and this module answers what is
running, what else is running, and what an agent has learned. Read-only by
construction: no file is written and nothing is started by itself. Two calls can
run a command, and only when the caller hands them in: asking Node for its version,
and the gateway's own device list, which `nexgen openclaw doctor` passes and a pass
with no business launching a CLI does not. A URL read from the Companion's settings
is never printed. Where the Windows app keeps its own gateway is `companion.py`'s
question; here it is only a value that decides which sentence a person reads.
"""
from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from nexgen_core.i18n import t
from nexgen_core.openclaw import companion as app
from nexgen_core.openclaw import host_preflight
from nexgen_core.openclaw.companion import Companion
from nexgen_core.openclaw.pin import node_satisfies
from nexgen_core.openclaw.profile import GATEWAY_PORT
from nexgen_core.openclaw_pairing import names_the_windows_app
from nexgen_core.report import CheckOutcome, Severity

DEFAULT_GATEWAY_PORT = GATEWAY_PORT
LISTEN_TIMEOUT_SECONDS = 0.4
#: The window the learned-skills line speaks about, and the count that is too many.
WORKSHOP_WINDOW_SECONDS = 24 * 60 * 60
WORKSHOP_WARN_OVER = 10

Connect = Callable[[str, int, float], bool]


def default_connect(host: str, port: int, timeout: float) -> bool:
    """Whether something accepts a TCP connection there, without sending a byte."""
    import socket

    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:  # nexgen: allow reason="a closed port is the expected answer when the gateway is stopped"
        return False


def check_listening(config: dict[str, Any], connect: Connect) -> CheckOutcome:
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


def check_listening_of_a_copy(config: dict[str, Any]) -> CheckOutcome:
    """The same question, unanswered on purpose, when the home is a copy.

    There is one gateway per machine, on a loopback port. A copy running beside
    the working installation would otherwise report the health of a process it
    does not own — and reading it means connecting to the owner's real gateway
    from a sandbox, which is how a test ends up touching the machine it was
    supposed to leave alone. The real home still gets the probe.
    """
    gateway = config.get("gateway") if isinstance(config.get("gateway"), dict) else {}
    port = gateway.get("port") if isinstance(gateway.get("port"), int) else DEFAULT_GATEWAY_PORT
    return CheckOutcome(
        id="openclaw.gateway.listening",
        severity=Severity.OK,
        message=t(
            "The gateway on 127.0.0.1:{port} is not probed from here: this is a copy of the machine, "
            "and the gateway belongs to the installation that runs.",
            port=port,
        ),
    )


def current_node_version() -> str | None:
    """The Node on PATH, asked for its version, or None when it cannot answer."""
    import shutil
    import subprocess

    from nexgen_core.processes import no_window_kwargs

    node = shutil.which("node")
    if not node:
        return None
    try:
        done = subprocess.run(
            [node, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):  # nexgen: allow reason="a Node that cannot be asked is an unknown version, not a crash"
        return None
    text = (done.stdout or "").strip()
    return text.splitlines()[-1].strip() if text else None


def check_node(requirement: str | None, version: str | None) -> CheckOutcome | None:
    """OpenClaw declares the Node it runs on; a different major is a broken install."""
    if not requirement:
        return None
    if version is None:
        return CheckOutcome(
            id="openclaw.node",
            severity=Severity.WARN,
            message=t("The Node version cannot be read, so OpenClaw's requirement ({requirement}) is unverified.",
                      requirement=requirement),
            action=t("Make sure 'node --version' answers in this shell, then run 'nexgen doctor' again."),
        )
    if node_satisfies(version, requirement):
        return CheckOutcome(
            id="openclaw.node",
            severity=Severity.OK,
            message=t("Node {version} satisfies OpenClaw's requirement ({requirement}).",
                      version=version, requirement=requirement),
        )
    return CheckOutcome(
        id="openclaw.node",
        severity=Severity.BROKEN,
        message=t("Node {version} does not satisfy OpenClaw's requirement ({requirement}).",
                  version=version, requirement=requirement),
        action=t("Install a Node release in that range from {url}, then run 'nexgen doctor' again.",
                 url="https://nodejs.org/en/download"),
    )


def check_linger(platform: str, user: str | None, directory: Path | None = None) -> CheckOutcome | None:
    """On Linux, a service without lingering dies with the last session."""
    if not platform.startswith("linux") or not user:
        return None
    if host_preflight.linger_enabled(user, directory or host_preflight.LINGER_DIR):
        return CheckOutcome(
            id="openclaw.linger",
            severity=Severity.OK,
            message=t("Lingering is enabled for {user}: the gateway survives logout.", user=user),
        )
    return CheckOutcome(
        id="openclaw.linger",
        severity=Severity.WARN,
        message=t("Lingering is off for {user}: the gateway stops when the last session closes.", user=user),
        action=t("Run 'loginctl enable-linger {user}' once.", user=user),
    )


def check_bindings(config: dict[str, Any]) -> CheckOutcome | None:
    """With explicit ownership, a channel without a binding reaches no agent."""
    channels = config.get("channels") if isinstance(config.get("channels"), dict) else {}
    configured = {
        str(name): block for name, block in channels.items()
        if isinstance(block, dict) and block and block.get("enabled") is not False
    }
    if not configured:
        return None
    bindings = config.get("bindings") if isinstance(config.get("bindings"), list) else []
    bound: set[str] = set()
    for binding in bindings:
        match = binding.get("match") if isinstance(binding, dict) else None
        channel = match.get("channel") if isinstance(match, dict) else None
        if isinstance(channel, str):
            bound.add(channel)
    missing = sorted(name for name in configured if name not in bound and "*" not in bound)
    if missing:
        return CheckOutcome(
            id="openclaw.bindings",
            severity=Severity.WARN,
            message=t("These configured channels have no agent binding: {names}. "
                      "With explicit ownership their messages reach no agent.", names=", ".join(missing)),
            action=t("Run 'nexgen openclaw setup': it binds them to the main agent and leaves your bindings untouched."),
        )
    return CheckOutcome(
        id="openclaw.bindings",
        severity=Severity.OK,
        message=t("Every configured channel has an agent binding."),
    )


def _is_isolated_gateway_package(child: Path) -> bool:
    """Whether this package folder is the Companion's isolated native gateway."""
    lowered = child.name.casefold()
    if "openclawfoundation.openclawgateway" in lowered:
        return True
    return (child / "LocalState" / "OpenClawGatewayMSIX").is_dir()


def store_packages(home: Path, env: Mapping[str, str], platform: str) -> list[str]:
    """The Microsoft Store package of OpenClaw, when Windows installed one."""
    if not platform.startswith("win"):
        return []
    local_appdata = env.get("LOCALAPPDATA") or str(home / "AppData" / "Local")
    packages = Path(local_appdata) / "Packages"
    if not packages.is_dir():
        return []
    return sorted(
        child.name for child in packages.iterdir()
        if child.is_dir() and "openclaw" in child.name.lower() and not _is_isolated_gateway_package(child)
    )


def check_store_gateway(home: Path, env: Mapping[str, str], platform: str) -> CheckOutcome | None:
    """Two gateways on the same machine fight over the same port and state."""
    found = store_packages(home, env, platform)
    if not found:
        return None
    return CheckOutcome(
        id="openclaw.store_gateway",
        severity=Severity.WARN,
        message=t("A second OpenClaw from the Microsoft Store is installed ({names}): two gateways in conflict.",
                  names=", ".join(found)),
        action=t("Remove the Microsoft Store app: the engine manages the npm one."),
    )


# --------------------------------------------------------------- learned skills


def agent_path(value: str, home: Path, base: Path) -> Path:
    """A path from OpenClaw's own config, with `~` expanded against `home`."""
    if value == "~":
        return home
    if value.startswith(("~/", "~\\")):
        return home / value[2:]
    candidate = Path(value)
    return candidate if candidate.is_absolute() else base / candidate


def workshop_dir(state_dir: Path, home: Path, agent_id: str, entry: Any) -> Path:
    """Where Skill Workshop keeps this agent's learned skills.

    OpenClaw's default is `<state dir>/agents/<id>/agent/workshop-skills`; an
    explicit `agentDir` replaces that per-agent folder, so the skills sit in
    `<agentDir>/workshop-skills`.
    """
    custom = entry.get("agentDir") if isinstance(entry, dict) else None
    if isinstance(custom, str) and custom.strip():
        base = agent_path(custom.strip(), home, state_dir)
    else:
        base = state_dir / "agents" / str(agent_id) / "agent"
    return base / "workshop-skills"


def _changed_since(path: Path, cutoff: float) -> bool:
    """Whether one skill changed since `cutoff`, from the folder and its SKILL.md.

    Walking every reference and `.archive` copy made the doctor slow on a real
    machine; the skill's own folder and its `SKILL.md` are the two timestamps
    that change when the skill itself is created, rewritten, or archived.
    """
    for candidate in (path, path / "SKILL.md"):
        try:
            if candidate.stat().st_mtime >= cutoff:
                return True
        except OSError:  # nexgen: allow reason="a skill folder that vanished is not counted"
            continue
    return False


def _recent_skills(directory: Path, cutoff: float) -> int:
    """How many learned skills were created or changed since `cutoff`."""
    try:
        children = list(directory.iterdir())
    except OSError:  # nexgen: allow reason="no folder means no learned skills yet"
        return 0
    return sum(1 for child in children if _changed_since(child, cutoff))


def check_workshop_skills(
    config: dict[str, Any],
    state_dir: Path,
    home: Path,
    now: float | None = None,
) -> list[CheckOutcome]:
    """One line per agent: how many skills it learned in the last 24 hours."""
    agents = config.get("agents") if isinstance(config.get("agents"), dict) else {}
    entries = agents.get("entries") if isinstance(agents.get("entries"), dict) else {}
    if not entries:
        return []
    cutoff = (time.time() if now is None else now) - WORKSHOP_WINDOW_SECONDS
    outcomes: list[CheckOutcome] = []
    for agent_id in sorted(entries, key=str):
        count = _recent_skills(workshop_dir(state_dir, home, agent_id, entries[agent_id]), cutoff)
        too_many = count > WORKSHOP_WARN_OVER
        outcomes.append(CheckOutcome(
            id=f"openclaw.workshop_skills.{agent_id}",
            severity=Severity.WARN if too_many else Severity.OK,
            message=t("Agent {agent}: {count} learned skills created or changed in the last 24 hours.",
                      agent=agent_id, count=count),
            action=(
                t("Review what {agent} learned: more than ten new procedures in a day is a lot to keep.",
                  agent=agent_id)
                if too_many else None
            ),
        ))
    return outcomes


def check_unadopted(
    config: dict[str, Any],
    config_path: Path,
    home: Path,
    vault_data: Path,
) -> CheckOutcome | None:
    """What OpenClaw created and the canonical library does not declare yet.

    One line and one action, because adoption is the person's act: the doctor
    only says how much is waiting. Silent when either manifest cannot be read
    -- the configuration checks own that finding, and a count computed without
    the manifest would name the engine's own resources as the person's.
    """
    from nexgen_core.openclaw import adopt_scan

    known = adopt_scan.library(Path(vault_data))
    if not known.complete:
        return None
    found = adopt_scan.scan(
        config_path=Path(config_path),
        state_dir=Path(config_path).parent,
        home=Path(home),
        skills_manifest=known.skills,
        mcp_manifest=known.mcp,
        config=config,
    )
    if found.total == 0:
        return CheckOutcome(
            id="openclaw.unadopted",
            severity=Severity.OK,
            message=t("Every skill and server OpenClaw created is in the canonical library."),
        )
    return CheckOutcome(
        id="openclaw.unadopted",
        severity=Severity.WARN,
        message=t("{count} resources created in OpenClaw are not adopted yet.", count=found.total),
        action=t("Run 'nexgen openclaw adopt': it shows each one and asks before writing."),
    )


# ------------------------------------------------------------- the Companion app


def _isolated_companion_outcome(companion: Companion, situation: str) -> CheckOutcome | None:
    """Outcome when the app points to its own isolated native gateway."""
    if situation == app.BOTH:
        return CheckOutcome(
            id="openclaw.companion_gateway",
            severity=Severity.WARN,
            message=t("The OpenClaw Companion app points to its own isolated native gateway ({signals}) and the engine "
                      "gateway is here too.", signals=", ".join(companion.signals)),
            action=t("In OpenClaw Companion choose 'a local Gateway on this PC' with the engine gateway address, "
                     "and turn off autostart for the native gateway (or uninstall the OpenClaw Gateway package; "
                     "the app remains)."),
        )
    if situation == app.APP:
        return CheckOutcome(
            id="openclaw.companion_gateway",
            severity=Severity.OK,
            message=t("The OpenClaw Companion app points to its own isolated native gateway ({signals}). This version "
                      "of the engine configures the native Node gateway, not the isolated one: it writes nothing here "
                      "and starts no second gateway.", signals=", ".join(companion.signals)),
            action=t("Use the app as it is, or install the native gateway with {command} and run "
                     "'nexgen openclaw setup'.", command="npm install -g openclaw"),
        )
    return None


def check_companion_gateway(
    companion: Companion,
    native_installed: bool,
    platform: str,
    pending: Callable[[], list[dict[str, Any]] | None] | None = None,
) -> CheckOutcome | None:
    """Where the Windows app's gateway is, said in the terms of the case at hand.

    The app is never something to remove: it is a gateway the person chose, and
    what the engine owes them is which gateway it configures itself and the
    promise to stay out of the other one's way. Only two gateways claiming the
    same PC is a conflict.

    When the app is installed and the native gateway is the one here, the line
    answers the two questions a person has: is the app pointed at this gateway,
    and does its device show up in the gateway's own list. `pending` is that
    list, injected because asking means running the CLI: a caller that has no
    business launching it passes nothing and the line says what it can.
    """
    if not platform.startswith("win"):
        return None
    situation = companion.situation(native_installed)
    if companion.is_isolated:
        isolated_outcome = _isolated_companion_outcome(companion, situation)
        if isolated_outcome is not None:
            return isolated_outcome
    if situation == app.APP:
        return CheckOutcome(
            id="openclaw.companion_gateway",
            severity=Severity.OK,
            message=t("The OpenClaw Companion app runs the gateway on this PC ({signals}). This version of the "
                      "engine configures the native gateway, not the app's one: it writes nothing here and "
                      "starts no second gateway.", signals=", ".join(companion.signals)),
            action=t("Use the app as it is, or install the native gateway with {command} and run "
                     "'nexgen openclaw setup'.", command="npm install -g openclaw"),
        )
    if situation == app.BOTH:
        return CheckOutcome(
            id="openclaw.companion_gateway",
            severity=Severity.WARN,
            message=t("The OpenClaw Companion app runs a gateway of its own on this PC ({signals}) and the native "
                      "gateway is here too: two gateways in conflict.",
                      signals=", ".join(companion.signals)),
            action=t("Keep the one you want: in OpenClaw Companion open Connections and point it at this PC's "
                     "gateway, or stay with the app's one and remove the native gateway with "
                     "'npm uninstall -g openclaw'."),
        )
    if situation == app.APP_REMOTE:
        return CheckOutcome(
            id="openclaw.companion_gateway",
            severity=Severity.OK,
            message=t("The OpenClaw Companion app is connected to a gateway elsewhere. Nothing here is in "
                      "conflict: this version of the engine configures the native gateway on this PC only."),
            action=t("Nothing to do: the app keeps its own gateway."),
        )
    if situation == app.NATIVE and companion.present:
        return _app_and_this_gateway(companion, pending)
    return None


def _app_and_this_gateway(
    companion: Companion,
    pending: Callable[[], list[dict[str, Any]] | None] | None,
) -> CheckOutcome:
    """The app is installed and this PC's gateway is the only one: is it pointed here?

    An app aimed at this gateway is connected, and its device is worth a
    sentence when the gateway can be asked. An app that names no gateway is one
    step from working, and that step is the app's own Connections screen.
    """
    if not companion.names_this_gateway():
        return CheckOutcome(
            id="openclaw.companion_gateway",
            severity=Severity.OK,
            message=t("The OpenClaw Companion app is installed and names no gateway on this PC."),
            action=t("In OpenClaw Companion open Connections and choose 'a local Gateway on this PC', then "
                     "'nexgen openclaw pair' approves the request it leaves."),
        )
    port = companion.port if companion.port is not None else GATEWAY_PORT
    message = t("The OpenClaw Companion app is set to this PC's gateway on port {port}.", port=port)
    return CheckOutcome(
        id="openclaw.companion_gateway",
        severity=Severity.OK,
        message=" ".join(part for part in (message, _devices_sentence(pending)) if part),
    )


def _devices_sentence(
    pending: Callable[[], list[dict[str, Any]] | None] | None,
) -> str:
    """What the gateway's own list says about the app's device, when it can be read.

    A gateway that is stopped cannot be asked, and that is a normal state: the
    sentence is then empty rather than a fault about the app.
    """
    if pending is None:
        return ""
    try:
        devices = pending()
    except OSError:  # nexgen: allow reason="a lister that cannot be launched answers 'unknown', not a doctor failure"
        return ""
    if devices is None:
        return ""
    if not devices:
        return t("The gateway lists no device for it yet: 'nexgen openclaw pair' shows the request the app leaves "
                 "and approves it.")
    if any(names_the_windows_app(device) for device in devices):
        return t("The gateway lists its device.")
    return t("The gateway lists {count} device(s), none of them naming the app: 'nexgen openclaw pair' shows the "
             "request and approves it.", count=len(devices))


def check_checkpoint(plugin_dir: Path) -> CheckOutcome | None:
    """Whether session continuity checkpoint is enabled in nexgen.config.json."""
    config_path = plugin_dir / "nexgen.config.json"
    if not config_path.is_file():
        return None
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # nexgen: allow reason="an unreadable plugin config file means checkpoint cannot be judged"
        return None
    checkpoint = data.get("checkpoint") if isinstance(data, dict) else None
    enabled = checkpoint.get("enabled") if isinstance(checkpoint, dict) else None
    if enabled is True:
        return CheckOutcome(
            id="openclaw.checkpoint",
            severity=Severity.OK,
            message=t("Session continuity checkpoint is enabled."),
        )
    if enabled is False:
        return CheckOutcome(
            id="openclaw.checkpoint",
            severity=Severity.WARN,
            message=t("Session continuity checkpoint is disabled."),
            action=t("Enable 'checkpoint' in {path} or run 'nexgen sync'.", path=config_path),
        )
    return None
