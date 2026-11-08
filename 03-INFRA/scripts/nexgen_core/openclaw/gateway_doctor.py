"""One job: migrate the agents' databases after an OpenClaw install, before the gateway runs again.

From 2026.11.2 a gateway that starts on an unmigrated database exits with code 78 and asks for
`openclaw doctor --fix`. The npm path of the update is documented in three steps, in this
order: install, `doctor --fix`, restart (docs/cli/update.md). `version_align` runs the install
and the restart; this module runs the middle step, with the gateway stopped.

What it cannot do: undo a migration. A database that doctor migrated keeps its new shape when
npm puts the previous release back, so a failed upgrade after a migration must say so instead
of reporting a clean rollback (`failure_line`).
"""
from __future__ import annotations

from dataclasses import dataclass

from nexgen_core.i18n import t
from nexgen_core.openclaw import align_node

#: What doctor is asked to do: migrate, without a question nobody can answer from a timer.
DOCTOR_ARGV = ["doctor", "--fix", "--non-interactive"]

#: One retry: a plugin's migration can stop the first pass, and a second pass can finish it.
ATTEMPTS = 2


@dataclass(frozen=True)
class Migration:
    """What the middle step did. `migrated` is True once doctor exited 0; `problem` says why not."""

    migrated: bool = False
    problem: str = ""


def migrate(service, binary: str | None, run, env: dict[str, str]) -> Migration:
    """Stop the gateway, run doctor (up to two passes), and leave the gateway stopped for the restart."""
    if service is None:
        return Migration(problem=t("the gateway service controller is not available"))
    if not service.stop().get("ok"):
        return Migration(problem=t("the gateway did not stop, so its database was not migrated"))
    for _attempt in range(ATTEMPTS):
        result = run([binary or "openclaw", *DOCTOR_ARGV], env)
        if result.returncode == 0:
            return Migration(migrated=True)
    return Migration(problem=t("openclaw doctor --fix failed (exit {code}): {detail}",
                               code=result.returncode, detail=align_node.tail(result)))


def failure_line(pinned: str, previous: str, problem: str, rollback: str, migrated: bool) -> str:
    """The sentence a failed upgrade ends with: the old release is back only when no data was migrated."""
    if not migrated:
        return t("OpenClaw {pinned} did not work here ({problem}): {previous} was put back.{rollback}",
                 pinned=pinned, problem=problem, previous=previous, rollback=rollback)
    return t("OpenClaw {pinned} did not work here ({problem}). Its database was already migrated by openclaw "
             "doctor: putting {previous} back with npm does not undo that, so it may not start.{rollback}",
             pinned=pinned, problem=problem, previous=previous, rollback=rollback)
