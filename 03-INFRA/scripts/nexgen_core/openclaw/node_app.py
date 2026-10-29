"""The Windows Companion app as the node of this PC: who says so, and how to clear the way.

One job: detect the CLI node service that `openclaw node install` left on this
PC, and carry out the one transition that removes it when the node is the app --
`nexgen openclaw node --use-app`. The app's own files are never written and the
app is never started: this module reads two facts about the CLI service and two
about the app (whether it wants to be the node, and whether its node is
connected), runs the documented `openclaw node uninstall` only when the person
asked with `--apply` AND the app's node is already connected, and records the
machine role so the rest of the engine knows which kind of machine this is.

The order is the whole point: the CLI service is the node this PC has today, so
taking it away before the app's node works would leave the machine with no node
at all. Every way out of that -- the app's own local MCP server, a command-line
argument, the `openclaw://` URI -- is closed while the app has no node
credential, which is exactly the state this command is meant to repair, so the
transition is a person's: pair the app, approve it on the gateway, and only then
run `--apply` again.

The two sources of the CLI fact are the scheduled task `openclaw node install`
creates on Windows and the `~/.openclaw/node.cmd` file it leaves beside it, the
same pair `node_transition.detect_local_gateway` reads for the gateway. The task
query is bounded by a short deadline, and the doctor does not run it at all when
the app's node mode is off.
"""
from __future__ import annotations

import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from nexgen_core.i18n import t
from nexgen_core.openclaw.companion import Companion, probe
from nexgen_core.openclaw.node_cli import SERVICE_TIMEOUT_S, Run, run_command
from nexgen_core.paths import openclaw_config, resolve_home

#: The scheduled task `openclaw node install` registers on Windows.
NODE_TASK_NAME = "OpenClaw Node"
#: The launcher file the same install leaves in the OpenClaw state folder.
NODE_COMMAND_FILE = "node.cmd"
#: Asking about a task is not changing one: it must answer or give up quickly.
SCHTASKS_TIMEOUT_S = 5.0

Emit = Callable[[str], None]


@dataclass(frozen=True)
class CliNodeService:
    """Whether the CLI node service is installed here, and which evidence said so."""

    present: bool = False
    scheduled_task: bool = False
    command_file: bool = False
    #: `schtasks`, `file`, or empty when nothing was found.
    mechanism: str = ""


def detect_cli_node_service(
    home: Path | None = None,
    platform: str | None = None,
    runner: Run | None = None,
    query: bool = False,
) -> CliNodeService:
    """The CLI node service on this PC, read from its command file and its scheduled task.

    `query` asks `schtasks` even when the command file is absent, for the caller
    that already knows the app has node mode on and wants the task answer too.
    """
    plat = platform if platform is not None else sys.platform
    if not plat.startswith("win"):
        return CliNodeService()
    folder = openclaw_config(resolve_home(home)).parent
    command_file = (folder / NODE_COMMAND_FILE).is_file()
    scheduled_task = False
    if command_file or query or runner is not None:
        run_fn = runner or run_command
        result = run_fn(["schtasks.exe", "/Query", "/TN", NODE_TASK_NAME], SCHTASKS_TIMEOUT_S)
        scheduled_task = result.returncode == 0
    mechanism = "schtasks" if scheduled_task else ("file" if command_file else "")
    return CliNodeService(
        present=scheduled_task or command_file,
        scheduled_task=scheduled_task,
        command_file=command_file,
        mechanism=mechanism,
    )


def _environ(home: Path | None, env: Mapping[str, str] | None) -> Mapping[str, str]:
    """The environment the app's folders come from.

    A caller that names a home gets no real app data: the app's folder is then
    looked for under that home, so a test never reads the machine it runs on and
    a real run, which passes no home, reads the real `%APPDATA%`.
    """
    if env is not None:
        return env
    return {} if home is not None else os.environ


def app_node(
    home: Path | None = None,
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
) -> Companion:
    """What the app's own files say about its node, read the way the doctor reads it.

    The WSL list is not asked: whether the app owns a gateway is not a fact this
    transition needs, and the registry is not worth a read for it.
    """
    return probe(resolve_home(home), _environ(home, env), platform or sys.platform,
                 wsl_distributions=lambda: [])


def is_pending(companion: Companion) -> bool:
    """Whether the app is meant to be the node here and its node is not connected yet."""
    return companion.wants_node and not companion.node_connected


def manual_lines(companion: Companion) -> list[str]:
    """The steps a person runs by hand, in the only order that leaves a node on this PC.

    The app first, the approval second, the CLI service last: every earlier
    attempt to hand the node over left the machine with none.
    """
    steps = [
        t("1. In OpenClaw Companion open Connections and pair the app with the gateway, "
          "leaving node mode on."),
    ]
    if companion.node_credential_missing:
        steps.append(t("   the app's log ends on 'No node credential available': the app needs a setup "
                       "code or a shared token, and only the app's own Connections screen can take one."))
    steps.append(t("2. On the gateway run 'openclaw devices list', then 'openclaw devices approve <id>', "
                   "and 'nexgen openclaw pair --approve-node <node request id>' for the node request."))
    steps.append(t("3. When OpenClaw Companion shows its node connected, run 'nexgen openclaw node "
                   "--use-app --apply' again: that is the step that removes the CLI node service."))
    return steps


def plan_lines(service: CliNodeService, companion: Companion | None = None) -> list[str]:
    """What `--use-app` would do, in the order it would do it: read only."""
    if service.present:
        removal = t("- CLI node service '{name}': uninstalled with 'openclaw node uninstall'",
                    name=NODE_TASK_NAME)
    else:
        removal = t("- CLI node service '{name}': not installed, nothing to remove", name=NODE_TASK_NAME)
    lines = [
        t("The OpenClaw Companion app is the node of this PC"),
        removal,
        t("- this machine: role 'node' recorded"),
        t("- the app's files: never written, and the app is not started"),
    ]
    if companion is not None:
        if companion.node_connected:
            lines.append(t("- the app's node: connected, so the CLI service can go"))
        elif companion.wants_node:
            lines.append(t("- the app's node: not connected, so the CLI service stays until it is"))
    return lines


def pairing_first_sentence() -> str:
    """The one sentence the plan and the doctor both say: the app first, the service last.

    Written once, here, because the two surfaces drifting apart is how a person
    ends up removing the CLI service before the app has a node.
    """
    return t("The node is the app, and the CLI service stays until the app's node is connected: "
             "pair the app on the gateway with 'openclaw devices list' and 'openclaw devices approve "
             "<id>', then remove the CLI service with 'nexgen openclaw node --use-app --apply'.")


def pairing_again_sentence() -> str:
    """The same order for the app that has node mode on and no credential to use it."""
    return t("Pair the app again: in OpenClaw Companion leave node mode on and connect it to the "
             "gateway, then on the gateway run 'openclaw devices list' and 'openclaw devices approve "
             "<id>'; the CLI node service is removed only after the app's node is connected.")


def _record_role(home: Path | None, out: Emit) -> None:
    """Records the node role, and says so plainly when it could not be written."""
    from nexgen_core import roles

    if roles.declare_role(roles.NODE_ROLE, home):
        out(t("This machine is recorded as a node."))
    else:
        out(t("The role could not be recorded: run 'nexgen role set node'."))


def _refuse(companion: Companion, out: Emit) -> int:
    """Says why nothing was removed, and prints the steps in the order that works."""
    out(t("The CLI node service '{name}' stays: the app's node is not connected yet, and removing it "
          "now would leave this PC with no node.", name=NODE_TASK_NAME))
    for line in manual_lines(companion):
        out(line)
    return 1


def _apply(
    service: CliNodeService,
    companion: Companion,
    runner: Run | None,
    home: Path | None,
    out: Emit,
) -> int:
    """Removes the CLI node service once the app's node works, then records the role."""
    if is_pending(companion):
        return _refuse(companion, out)
    if service.present:
        run_fn = runner or run_command
        result = run_fn(["openclaw", "node", "uninstall"], SERVICE_TIMEOUT_S)
        if result.returncode != 0:
            out(t("'openclaw node uninstall' failed (exit code {code}): the CLI node service is "
                  "still there.", code=result.returncode))
            return 1
        out(t("The CLI node service '{name}' is gone.", name=NODE_TASK_NAME))
    _record_role(home, out)
    out(t("The node of this PC is OpenClaw Companion now: leave node mode on and keep the app connected."))
    return 0


def use_app(
    *,
    apply: bool = False,
    home: Path | None = None,
    platform: str | None = None,
    runner: Run | None = None,
    env: Mapping[str, str] | None = None,
    out: Emit = print,
) -> int:
    """Make the app the node of this PC: show the plan, or with `--apply` carry it out."""
    plat = platform if platform is not None else sys.platform
    if not plat.startswith("win"):
        out(t("The Companion app exists on Windows only: there is no app node to choose here."))
        return 1
    companion = app_node(home=home, env=env, platform=plat)
    service = detect_cli_node_service(home=home, platform=plat, runner=runner, query=True)
    for line in plan_lines(service, companion):
        out(line)
    if not apply:
        for line in manual_lines(companion):
            out(line)
        out(t("Nothing was changed: run the same command with --apply."))
        return 0
    return _apply(service, companion, runner, home, out)
