"""The Windows Companion app as the node of this PC: who says so, and how to clear the way.

One job: detect the CLI node service that `openclaw node install` left on this
PC, and carry out the one transition that removes it when the node is the app --
`nexgen openclaw node --use-app`. The app's own files are never written and the
app is never started: this module reads two facts about the CLI service, runs
the documented `openclaw node uninstall` only when the person asked with
`--apply`, and records the machine role so the rest of the engine knows which
kind of machine this is.

The two sources are the scheduled task `openclaw node install` creates on
Windows and the `~/.openclaw/node.cmd` file it leaves beside it, the same pair
`node_transition.detect_local_gateway` reads for the gateway. The task query is
bounded by a short deadline, and the doctor does not run it at all when the app's
node mode is off.
"""
from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from nexgen_core.i18n import t
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


def plan_lines(service: CliNodeService) -> list[str]:
    """What `--use-app` would do, in the order it would do it: read only."""
    if service.present:
        removal = t("- CLI node service '{name}': uninstalled with 'openclaw node uninstall'",
                    name=NODE_TASK_NAME)
    else:
        removal = t("- CLI node service '{name}': not installed, nothing to remove", name=NODE_TASK_NAME)
    return [
        t("The OpenClaw Companion app is the node of this PC"),
        removal,
        t("- this machine: role 'node' recorded"),
        t("- the app's files: never written, and the app is not started"),
    ]


def _record_role(home: Path | None, out: Emit) -> None:
    """Records the node role, and says so plainly when it could not be written."""
    from nexgen_core import roles

    if roles.declare_role(roles.NODE_ROLE, home):
        out(t("This machine is recorded as a node."))
    else:
        out(t("The role could not be recorded: run 'nexgen role set node'."))


def _apply(service: CliNodeService, runner: Run | None, home: Path | None, out: Emit) -> int:
    """Removes the CLI node service when it is there, then records the role."""
    if service.present:
        run_fn = runner or run_command
        result = run_fn(["openclaw", "node", "uninstall"], SERVICE_TIMEOUT_S)
        if result.returncode != 0:
            out(t("'openclaw node uninstall' failed (exit code {code}): the CLI node service is "
                  "still there.", code=result.returncode))
            return 1
        out(t("The CLI node service '{name}' is gone.", name=NODE_TASK_NAME))
    _record_role(home, out)
    out(t("In OpenClaw Companion leave node mode on and connect the app to the gateway; on the "
          "gateway run 'openclaw devices list', then 'openclaw devices approve <id>'."))
    return 0


def use_app(
    *,
    apply: bool = False,
    home: Path | None = None,
    platform: str | None = None,
    runner: Run | None = None,
    out: Emit = print,
) -> int:
    """Make the app the node of this PC: show the plan, or with `--apply` carry it out."""
    plat = platform if platform is not None else sys.platform
    if not plat.startswith("win"):
        out(t("The Companion app exists on Windows only: there is no app node to choose here."))
        return 1
    service = detect_cli_node_service(home=home, platform=plat, runner=runner, query=True)
    for line in plan_lines(service):
        out(line)
    if not apply:
        out(t("Nothing was changed: run the same command with --apply."))
        return 0
    return _apply(service, runner, home, out)
