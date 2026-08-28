"""The sync plan: what an apply would change, computed without changing it.

One object, three views. `nexgen plan` prints it for a human, `nexgen plan
--json` dumps it with provenance for a machine, and `nexgen sync
--dry-run/--check` is the same object reached from the verb people already
know. Before this module, "what would sync do" was answerable only by
running sync, and previewing a repair meant performing it.

Two honest limits, printed in the plan rather than hidden:
- no network: the upstream (behind/ahead) state is unknowable without a
  fetch, so plan mode says so instead of pretending. Preview and apply are
  therefore not perfectly equivalent for the Git domain — the plan declares
  the gap instead of papering over it.
- probes, not simulation: drift is detected by the same read-only checks the
  doctor runs, not by executing the writers with writes disabled. The
  idempotent self-repair phases (shims, scheduler, modules) are declared,
  not enumerated, because they have no pre-state worth probing: on an
  aligned machine they write nothing.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from nexgen_core import __version__
from nexgen_core.git_ops import (
    check_conflicts_or_rebase,
    get_current_branch,
    get_uncommitted_files,
    resolve_remotes,
)
from nexgen_core.i18n import t
from nexgen_core.paths import resolve_engine_root, resolve_home, resolve_vault_data
from nexgen_core.report import Severity


@dataclass
class SyncPlan:
    """What an apply would do on this machine, right now."""

    engine_version: str
    vault: str
    branch: str | None
    commit: str | None
    git_notes: list[str] = field(default_factory=list)
    planned_actions: list[str] = field(default_factory=list)
    in_sync: list[str] = field(default_factory=list)
    not_checked: list[str] = field(default_factory=list)

    @property
    def drift(self) -> bool:
        """True when an apply would change something, or be blocked."""
        return bool(self.planned_actions)

    def to_dict(self) -> dict:
        """Machine-readable dump, with the provenance of where it came from."""
        return {
            "engine_version": self.engine_version,
            "vault": self.vault,
            "branch": self.branch,
            "commit": self.commit,
            "drift": self.drift,
            "git_notes": list(self.git_notes),
            "planned_actions": list(self.planned_actions),
            "in_sync": list(self.in_sync),
            "not_checked": list(self.not_checked),
        }


def _git_probe(vault_data: Path) -> tuple[list[str], list[str]]:
    """Local-only Git findings: (notes, planned actions).

    Deliberately no `fetch`: a plan that touched the network would be a
    preview with side effects on remote state and on rate limits, and it
    would be slow. Local drift (conflicts, wrong branch, uncommitted work)
    is what blocks an apply; the upstream comparison is declared unchecked.
    """
    notes: list[str] = []
    actions: list[str] = []

    if not (vault_data / ".git").exists():
        notes.append(t("The vault is not a Git repository."))
        return notes, actions

    auth_remote, _ = resolve_remotes(vault_data)
    if auth_remote in ("local", "none"):
        notes.append(t("Local-Only mode: no remote to compare against."))
    else:
        notes.append(t(
            "Upstream state not checked (a plan never touches the network); "
            "'nexgen sync apply' fetches and fast-forwards if behind."
        ))

    conflict = check_conflicts_or_rebase(vault_data)
    if conflict:
        notes.append(t("Git conflict or pending operation: {msg}", msg=conflict))
        actions.append(t("Resolve the Git conflict (apply is blocked until then)."))

    branch = get_current_branch(vault_data)
    if not branch:
        notes.append(t("Detached HEAD: apply would refuse to run."))
        actions.append(t("Check out a branch before applying."))

    uncommitted = get_uncommitted_files(vault_data)
    if uncommitted:
        notes.append(t(
            "Uncommitted changes on {count} tracked files (apply auto-commits "
            "the engine's own files, everything else blocks it).",
            count=len(uncommitted),
        ))
        actions.append(t("Commit or stash the pending changes."))

    return notes, actions


def _probe(outcome) -> tuple[str | None, str | None]:
    """A check outcome becomes (planned action, in-sync line) or (None, None)."""
    if outcome is None or outcome.severity == Severity.UNDETERMINED:
        return None, None
    line = outcome.message
    if getattr(outcome, "action", ""):
        line += " → " + outcome.action
    if outcome.severity == Severity.BROKEN:
        return line, None
    return None, line


def build_sync_plan(
    home: Path | None = None,
    vault_data: Path | None = None,
    engine_root: Path | None = None,
) -> SyncPlan:
    """Computes the plan. Read-only and network-free by construction."""
    home_dir = resolve_home(home)
    vault = resolve_vault_data(home_dir, vault_data)
    resolve_engine_root(home_dir, engine_root)

    plan = SyncPlan(
        engine_version=__version__,
        vault=str(vault),
        branch=None,
        commit=None,
    )

    if (vault / ".git").exists():
        plan.branch = get_current_branch(vault)
        head = subprocess.run(
            ["git", "-C", str(vault), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=30, check=False,
        )
        if head.returncode == 0:
            plan.commit = head.stdout.strip()

    plan.git_notes, git_actions = _git_probe(vault)
    plan.planned_actions.extend(git_actions)

    if not vault.is_dir():
        plan.not_checked.append(t("The vault directory does not exist; nothing else was probed."))
        return plan

    # The probes are the doctor's own read-only checks, reused rather than
    # reimplemented: one definition of "aligned", two consumers.
    from nexgen_core.checks.instructions_checks import (
        check_claude_pointer,
        check_cli_instruction_pointers,
        check_opencode_instructions,
    )
    from nexgen_core.checks.mcp_checks import check_mcp_configs_rendered
    from nexgen_core.checks.skill_checks import (
        check_engine_starter_views,
        check_skills_not_materialized,
    )

    probes: list[tuple[str, object]] = [
        ("mcp", lambda: check_mcp_configs_rendered(vault, home_dir)),
        ("skills", lambda: check_skills_not_materialized(vault, home_dir)),
        ("skills", lambda: check_engine_starter_views(vault, home_dir)),
        ("instructions", lambda: check_claude_pointer(vault, home_dir)),
        ("instructions", lambda: check_cli_instruction_pointers(vault, home_dir)),
        ("instructions", lambda: check_opencode_instructions(vault, home_dir)),
    ]

    for domain, probe in probes:
        result = probe()
        outcomes = result if isinstance(result, list) else [result]
        for outcome in outcomes:
            action, ok = _probe(outcome)
            if action:
                plan.planned_actions.append(f"[{domain}] {action}")
            elif ok:
                plan.in_sync.append(f"[{domain}] {ok}")

    plan.not_checked.append(t(
        "Launcher shims, scheduler units and modules: idempotent self-repair "
        "phases; on an aligned machine they write nothing."
    ))
    return plan
