#!/usr/bin/env python3
"""The Judge (Doctor): universal alignment verifier for NeXgen Engine v2.

Single implementation shared across Linux and Windows:
- Eliminates the duplication between Bash and PowerShell entirely.
- Returns structured outcomes with automatic remedies (no false alarms).
- Respects operational silence: counts successes and reports only what needs attention.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.checks import schedule  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.checks.anthropic_catalog_checks import check_anthropic_catalog  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.checks.claude_loopback_checks import check_claude_loopback  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.checks.env_checks import check_state_dir, check_vault_path  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.checks.git_checks import (  # noqa: E402 - sys.path shim for cloned checkout
    check_engine_lane,
    check_git_alignment,
    check_mirror_alignment,
    check_quarantine_branches,
    check_readonly_clone,
    check_remotes_config,
    check_vault_remote_privacy,
)
from nexgen_core.checks.desktop_checks import (  # noqa: E402 - sys.path shim for cloned checkout
    check_codex_desktop_skew,
)
from nexgen_core.checks.identity_checks import (  # noqa: E402 - sys.path shim for cloned checkout
    check_agent_self,
    check_agent_self_metadata,
    check_native_memory_boundary,
)
from nexgen_core.checks.instructions_checks import (  # noqa: E402 - sys.path shim for cloned checkout
    check_bootstrap_notes_size,
    check_bootstrap_pointer_integrity,
    check_bootstrap_size_budget,
    check_canonical_instructions_present,
    check_claude_pointer,
    check_cli_instruction_pointers,
    check_openclaw_instructions,
    check_opencode_instructions,
)
from nexgen_core.checks.mcp_checks import (  # noqa: E402 - sys.path shim for cloned checkout
    check_desktop_allowlist,
    check_antigravity_fanout,
    check_mcp_commands,
    check_mcp_configs_rendered,
    check_mcp_placement,
    check_mcp_trials,
    check_mcp_content_drift,
    check_mcp_deps,
    check_mcp_manifest,
    check_mcp_orphans,
    check_mcp_unresolved_refs,
)
from nexgen_core.checks.module_checks import check_modules_catalog, check_modules_ready  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.checks.openclaw_checks import check_openclaw  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.checks.openclaw_parity_checks import check_openclaw_parity  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.checks.opencode_checks import check_opencode_config_dialect  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.checks.quota_checks import check_quota  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.checks.reachability_checks import (  # noqa: E402 - sys.path shim for cloned checkout
    check_cloud_connector_key,
    check_mcp_reachability,
)
from nexgen_core.checks.security_checks import (  # noqa: E402 - sys.path shim for cloned checkout
    check_leak_patterns_twin,
    check_required_rules,
    check_secrets_materialized,
    check_tokens_in_env,
)
from nexgen_core.checks.skill_checks import (  # noqa: E402 - sys.path shim for cloned checkout
    check_engine_starter_views,
    check_skill_engine_copies,
    check_skill_deps,
    check_skill_library_and_index,
    check_skill_library_symlinks,
    check_skills_manifest,
    check_skills_manifest_semantics,
    check_skills_not_materialized,
    check_skills_out_of_manifest,
    check_skills_pin_freshness,
)
from nexgen_core.checks.guardrail_checks import check_guardrail_consulted, check_openclaw_guardrail_plugin  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.marks import safe_mark, safe_text  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.checks.host_checks import (  # noqa: E402 - sys.path shim for cloned checkout
    check_launchers,
    check_timers_armed,
    check_unit_engine_root,
)
from nexgen_core.checks.runtime_checks import check_engine_runtime  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.checks.takeover_checks import (  # noqa: E402 - sys.path shim for cloned checkout
    check_engine_version_recorded,
    check_last_cycle_phases,
)
from nexgen_core.i18n import t  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.lock import LockTimeoutError, host_mutation  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.paths import (  # noqa: E402 - sys.path shim for cloned checkout
    resolve_engine_root,
    resolve_home,
    resolve_state_dir,
    resolve_vault_data,
)
from nexgen_core.report import CheckOutcome, Report, Severity  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.vault_readonly import readonly_reason  # noqa: E402 - sys.path shim for cloned checkout

#: How long `doctor --fix` waits for the host lock before giving up on remedies.
REMEDY_LOCK_WAIT_SECONDS = 10.0


class Doctor:
    """Judge of the agent layer's alignment state."""

    def __init__(
        self,
        vault_data: Path | None = None,
        state_dir: Path | None = None,
        home: Path | None = None,
        engine_root: Path | None = None,
    ) -> None:
        self.home = resolve_home(home)
        _v = resolve_vault_data(self.home, vault_data)
        self.vault_data = _v
        self.state_dir = resolve_state_dir(self.home, state_dir)
        self.engine_root = resolve_engine_root(self.home, engine_root)
        #: How long a read-only run may take before it reports what is still
        #: missing. `None` means "wait for everything", which is what the full
        #: doctor does: someone reading it wants the whole answer. `diagnose`
        #: sets it from the flags rather than the constructor, so every caller
        #: that builds a `Doctor` keeps the plain signature it already used.
        self.deadline_seconds: float | None = None

    def run_diagnostics(self, apply_remedies: bool = False) -> Report:
        """Runs every registered check (read-only by default).

        With ``apply_remedies`` the whole run happens under the host lock: a
        remedy rewrites generated configs and the skill library, which is what
        a guard cycle does, and the two used to be free to interleave. If the
        lock stays busy the checks still run, read-only, and say that the
        remedies were skipped, instead of waiting behind a long cycle.
        """
        if not apply_remedies:
            return self._diagnose(False)
        lock = host_mutation("doctor-fix", state_dir=self.state_dir, timeout=REMEDY_LOCK_WAIT_SECONDS)
        try:
            lock.acquire()
        except LockTimeoutError:
            report = self._diagnose(False)
            report.add(CheckOutcome(
                id="doctor.fix.skipped",
                severity=Severity.WARN,
                message=t("Automatic remedies were skipped: another sync is running on this machine."),
                action=t("Wait for it to finish, then run 'nexgen doctor --fix' again."),
            ))
            return report
        try:
            return self._diagnose(True)
        finally:
            lock.release()

    def _diagnose(self, apply_remedies: bool) -> Report:
        """The checks themselves (read-only unless ``apply_remedies``).

        This method says *what* is checked, family by family; `checks.schedule`
        decides *how*: in order when remedies are applied, together inside a
        deadline when this is the summary. A check that raises becomes a
        finding of its own and the report goes on: the doctor exists to
        describe a damaged machine, so a damaged file must never be able to
        end it before it has said anything (a corrupt manifest used to stop it
        with no report, and the alert that depends on it with it).
        """
        checks: list[schedule.Check] = []

        def run(check_id: str, produce, resource: str | None = None) -> None:
            """Register one check. Nothing runs here: the schedule runs it."""
            checks.append(schedule.Check(check_id, produce, resource))

        _environment_checks(run, self)
        if self.vault_data.is_dir():
            _git_checks(run, self)
            _mcp_and_module_checks(run, self)
            _skill_checks(run, self)
            _identity_and_secret_checks(run, self)
            _run_instruction_checks(run, self.vault_data, self.home)

        # A repair must never race the checks that judge the same machine, so
        # `--fix` keeps its one-at-a-time run. Everything else is read-only and
        # independent, and the summary runs it all at once inside its deadline.
        if apply_remedies:
            return schedule.run_in_order(checks, apply_remedies=True)
        return schedule.run(checks, deadline_seconds=self.deadline_seconds)


def _environment_checks(run, doc: Doctor) -> None:
    """The machine itself: its folders, its launchers, its last cycle, and the
    CLIs that read what a render wrote."""
    vault, home, state = doc.vault_data, doc.home, doc.state_dir
    run("env.state_dir", lambda: check_state_dir(state))
    run("env.vault_path", lambda: check_vault_path(vault))
    run("env.runtime", lambda: check_engine_runtime(home, doc.engine_root))
    # A config written in a dialect the installed CLI refuses stops OpenCode from
    # starting at all, and no guard phase asks this: only the doctor does.
    run("opencode.config_dialect", lambda: check_opencode_config_dialect(home))
    run("guardrail.consulted", lambda: check_guardrail_consulted(home))
    run("guardrail.openclaw_plugin", lambda: check_openclaw_guardrail_plugin(home))
    run("host.timers", check_timers_armed)
    run("host.unit_engine_root", lambda: check_unit_engine_root(home))
    run("host.launchers", lambda: check_launchers(home))
    # Which engine last completed a cycle here: the per-machine answer to
    # "are all my machines migrated?".
    run("takeover.engine_version", lambda: check_engine_version_recorded(state))
    run("guard.last_cycle", lambda: check_last_cycle_phases(state))


def _git_checks(run, doc: Doctor) -> None:
    """The vault's own repository, and the engine checkout's lane."""
    vault = doc.vault_data

    def alignment():
        """A clone that may only read is judged as a reader, not as a publisher."""
        reason = readonly_reason(vault, home=doc.home, engine_root=doc.engine_root)
        if reason:
            return check_readonly_clone(vault, reason)
        return check_git_alignment(vault)

    # The two that fetch share one repository lock: run together they contend
    # for `.git/FETCH_HEAD.lock`, and one fails for no reason.
    run("git.alignment", alignment, "vault-git-remote")
    run("git.remotes_config", lambda: check_remotes_config(vault))
    run("git.vault_remote_privacy", lambda: check_vault_remote_privacy(vault))
    run("git.engine_lane", lambda: check_engine_lane(doc.engine_root))
    run("git.quarantine", lambda: check_quarantine_branches(vault))
    run("git.mirror_alignment", lambda: check_mirror_alignment(vault), "vault-git-remote")


def _mcp_and_module_checks(run, doc: Doctor) -> None:
    """The connectors, the modules, and the harness with the interface."""
    vault, home, state, engine_root = doc.vault_data, doc.home, doc.state_dir, doc.engine_root
    manifest_mcp = vault / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml"
    run("mcp.manifest", lambda: check_mcp_manifest(manifest_mcp))
    run("mcp.rendered_configs", lambda: check_mcp_configs_rendered(vault, home))
    run("mcp.placement", lambda: check_mcp_placement(vault))
    run("mcp.trials", check_mcp_trials)
    run("mcp.antigravity_fanout", lambda: check_antigravity_fanout(vault, home))
    run("mcp.rendered_content", lambda: check_mcp_content_drift(vault, home, engine_root))
    run("mcp.commands", lambda: check_mcp_commands(vault, home))
    run("mcp.orphans", lambda: check_mcp_orphans(vault, home))
    run("mcp.desktop_allowlist", lambda: check_desktop_allowlist(vault, home))
    run("mcp.unresolved_refs", lambda: check_mcp_unresolved_refs(vault, home))
    run("mcp.reachability", lambda: check_mcp_reachability(vault, home))
    # The cloud connector has no port to probe: its key decides whether it exists here.
    run("mcp.cloud_key", lambda: check_cloud_connector_key(manifest_mcp))
    run("mcp.deps", lambda: check_mcp_deps(manifest_mcp, state))
    run("modules.catalog", lambda: check_modules_catalog(engine_root, vault))
    # Installed is not working: a module says so itself, and only the doctor
    # asks, never the guard cycle.
    run("modules.ready", lambda: check_modules_ready(engine_root, vault))
    _run_harness_checks(run, home, engine_root, vault)


def _skill_checks(run, doc: Doctor) -> None:
    """The skills: declared, installed, materialized, and on the pinned commit."""
    vault, home, state, engine_root = doc.vault_data, doc.home, doc.state_dir, doc.engine_root
    manifest_skills = vault / "03-INFRA" / "agent-universal-layer" / "skills" / "skills.manifest.yaml"
    run("skills.manifest", lambda: check_skills_manifest(manifest_skills))
    run("skills.deps", lambda: check_skill_deps(manifest_skills, state))
    run("skills.library_and_index", lambda: check_skill_library_and_index(vault, home))
    run("skills.library_symlinks", lambda: check_skill_library_symlinks(home))
    run("skills.not_materialized", lambda: check_skills_not_materialized(vault, home))
    run("skills.pin_freshness", lambda: check_skills_pin_freshness(vault, home))
    run("skills.out_of_manifest", lambda: check_skills_out_of_manifest(vault, home))
    run("skills.engine_starter_views", lambda: check_engine_starter_views(vault, home))
    run("skills.engine_copies", lambda: check_skill_engine_copies(vault, home, engine_root))
    run("skills.manifest_semantics", lambda: check_skills_manifest_semantics(vault, home))


def _identity_and_secret_checks(run, doc: Doctor) -> None:
    """Who the machine is, what it keeps secret, and the apps beside it."""
    vault, home, engine_root = doc.vault_data, doc.home, doc.engine_root
    run("identity.agent_self", lambda: check_agent_self(vault))
    run("identity.agent_self_metadata", lambda: check_agent_self_metadata(vault))
    run("identity.native_memory_boundary", lambda: check_native_memory_boundary(home))
    run("bootstrap.rules_guard", lambda: check_required_rules(vault, engine_root))
    run("env.tokens_in_env", lambda: check_tokens_in_env(vault))
    run("security.secrets_materialized", lambda: check_secrets_materialized(home, vault))
    run("security.leak_patterns_twin", lambda: check_leak_patterns_twin(vault, engine_root))
    # Desktop apps that share a config with their CLI.
    run("desktop.codex_version_skew", check_codex_desktop_skew)


def _run_harness_checks(run, home: Path, engine_root: Path, vault: Path) -> None:
    """The harness with the interface, and what the plans this machine pays for say.

    Split out of `_diagnose` because the group grows with every capability the
    harness gains, and the coordinator is not where that belongs.
    """
    run("openclaw", lambda: check_openclaw(home, engine_root, vault))
    # One plugin, two hosts: every layer OpenClaw must receive from Claude Code.
    run("openclaw.parity", lambda: check_openclaw_parity(home, vault))
    # The loopback server a `claude-cli` agent's connectors must not appear on.
    run("openclaw.claude_loopback", lambda: check_claude_loopback(home, vault, engine_root))
    # The Anthropic key no `claude-cli` agent reads, which breaks the model catalogue.
    run("openclaw.anthropic_catalog", lambda: check_anthropic_catalog(home))
    # What is left of each plan, as far as a few kilobytes can say.
    run("quota.known", lambda: check_quota(home, vault))


def _run_instruction_checks(run, vault: Path, home: Path) -> None:
    """Every check about the canonical bootstrap and the pointers to it.

    Split out of `_diagnose`: the group grows every time a CLI learns to read
    instructions, and the coordinator is not where that belongs.
    """
    run("instructions.canonical_present", lambda: check_canonical_instructions_present(vault))
    run("instructions.claude_pointer", lambda: check_claude_pointer(vault, home))
    run("instructions.cli_pointers", lambda: check_cli_instruction_pointers(vault, home))
    run("instructions.opencode", lambda: check_opencode_instructions(vault, home))
    run("instructions.openclaw", lambda: check_openclaw_instructions(vault, home))
    run("instructions.bootstrap_size_budget", lambda: check_bootstrap_size_budget(vault))
    run("instructions.bootstrap_notes_size", lambda: check_bootstrap_notes_size(vault))
    run("instructions.bootstrap_pointer_integrity", lambda: check_bootstrap_pointer_integrity(vault))


def summary_budget(*, summary: bool, fix: bool) -> float | None:
    """The deadline a run gets: the summary's, or none at all.

    A repair keeps the old, unhurried behaviour: it changes files, and cutting
    it off half way is worse than waiting. A reading that someone is waiting on
    from a git hook is the case the deadline exists for.
    """
    return schedule.SUMMARY_DEADLINE_SECONDS if summary and not fix else None


def diagnose(*, summary: bool = False, fix: bool = False) -> Report:
    """Run every check the way the caller asked: no deadline, or the summary's."""
    doctor = Doctor()
    doctor.deadline_seconds = summary_budget(summary=summary, fix=fix)
    return doctor.run_diagnostics(apply_remedies=fix)


def _print_timings(report: Report) -> None:
    """How long each check took, slowest first.

    The pre-push hook and the verification of a collection run `--summary`
    under a budget, so the one number that has to stay inside it is the run's
    own duration. This is the table that says which check spent it.
    """
    if not report.timings:
        return
    slowest = sorted(report.timings, key=lambda item: item[1], reverse=True)
    print("\n" + t("Check timings (slowest first): {count} checks", count=len(slowest)))
    for check_id, seconds in slowest:
        print(f"{seconds:8.3f}s  {check_id}")


def render_report(
    report: Report,
    *,
    as_json: bool = False,
    summary: bool = False,
    verbose: bool = False,
    timings: bool = False,
    strict: bool = False,
) -> int:
    """Print one report and return the exit code. The only renderer there is.

    `nexgen doctor` and this module's own entry both come here: the command the
    hooks run is the `nexgen` one, and while it kept its own copy of this
    function a flag added here (a timing table, a new count) silently existed on
    one path and not on the other. The signature is keyword-only on purpose, so
    a caller reads as what it asks for rather than as six booleans in a row.
    """
    if as_json:
        print(report.format_json())
    elif summary:
        fail_count = len(report.broken)
        ok_count = report.ok_count
        undet_count = len(report.undetermined)
        print(f"FAIL={fail_count} OK={ok_count} WARN={len(report.warnings)} UNDETERMINED={undet_count}")
        if fail_count > 0:
            for b in report.broken:
                print(f"  {safe_mark('✗')} {safe_text(b.message)}")
    else:
        print(safe_text(report.format_human(verbose=verbose)))

    if timings:
        _print_timings(report)

    return report.exit_code(strict=strict)


def _render_report(report: Report, args: argparse.Namespace) -> int:
    """Render phase (no checks): prints the report and computes the exit code."""
    return render_report(
        report,
        as_json=args.json,
        summary=args.summary,
        verbose=args.verbose,
        timings=getattr(args, "timings", False),
        strict=args.strict,
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for the agent-doctor command."""
    parser = argparse.ArgumentParser(description=t("NeXgen Engine Doctor (v2) - Alignment diagnostics and verification"))
    parser.add_argument("-v", "--verbose", action="store_true", help=t("Show every check run, including the ones that passed"))
    parser.add_argument("--strict", action="store_true", help=t("Strict mode: treat undetermined states as non-compliant too"))
    parser.add_argument("--json", action="store_true", help=t("Print the output in JSON format"))
    parser.add_argument("--summary", action="store_true", help=t("Print the summary with FAIL=N OK=N counts"))
    parser.add_argument("--fix", "--remedy", action="store_true", help=t("Apply automatic remedies for fixable problems"))
    parser.add_argument("--timings", action="store_true", help=t("Print how long each check took, slowest first"))

    args = parser.parse_args(argv)

    report = diagnose(summary=args.summary, fix=args.fix)

    return _render_report(report, args)


if __name__ == "__main__":
    sys.exit(main())
