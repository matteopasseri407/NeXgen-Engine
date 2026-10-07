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

from nexgen_core.checks.env_checks import check_state_dir, check_vault_path  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.checks.git_checks import (  # noqa: E402 - sys.path shim for cloned checkout
    check_engine_lane,
    check_git_alignment,
    check_mirror_alignment,
    check_quarantine_branches,
    check_remotes_config,
    check_vault_remote_privacy,
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
    check_opencode_instructions,
)
from nexgen_core.checks.mcp_checks import (  # noqa: E402 - sys.path shim for cloned checkout
    check_mcp_commands,
    check_mcp_configs_rendered,
    check_mcp_placement,
    check_mcp_content_drift,
    check_mcp_deps,
    check_mcp_manifest,
    check_mcp_orphans,
)
from nexgen_core.checks.module_checks import check_modules_catalog, check_modules_ready  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.checks.reachability_checks import check_mcp_reachability  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.checks.security_checks import (  # noqa: E402 - sys.path shim for cloned checkout
    check_leak_patterns_twin,
    check_required_rules,
    check_secrets_materialized,
    check_tokens_in_env,
)
from nexgen_core.checks.skill_checks import (  # noqa: E402 - sys.path shim for cloned checkout
    check_engine_starter_views,
    check_skill_deps,
    check_skill_library_and_index,
    check_skill_library_symlinks,
    check_skills_manifest,
    check_skills_manifest_semantics,
    check_skills_not_materialized,
    check_skills_out_of_manifest,
    check_skills_pin_freshness,
)
from nexgen_core.checks.guardrail_checks import check_guardrail_consulted  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.checks.host_checks import check_launchers, check_timers_armed  # noqa: E402 - sys.path shim for cloned checkout
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

        Each check runs isolated: a check that raises becomes a finding of its
        own and the report goes on. The doctor exists to describe a damaged
        machine, so a damaged file must never be able to end it before it
        has said anything (a corrupt manifest used to stop it with no report,
        and the alert that depends on it with it).
        """
        report = Report()

        def run(check_id: str, produce) -> None:
            try:
                produced = produce()
            except Exception as exc:  # noqa: BLE001 - the doctor must survive the damage it diagnoses
                report.add(CheckOutcome(
                    id=f"{check_id}.crashed",
                    severity=Severity.BROKEN,
                    message=t(
                        "The check '{check}' could not run: {error}",
                        check=check_id, error=f"{type(exc).__name__}: {exc}",
                    ),
                    action=t("Run 'nexgen doctor --json' and look at this check; the rest of the report is still valid."),
                ))
                return
            for outcome in (produced if isinstance(produced, list) else [produced]):
                if outcome is not None:
                    report.add(outcome, apply_remedy=apply_remedies)

        vault, home, state = self.vault_data, self.home, self.state_dir
        manifest_mcp = vault / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml"
        manifest_skills = vault / "03-INFRA" / "agent-universal-layer" / "skills" / "skills.manifest.yaml"

        # 1. Environment and directory checks
        run("env.state_dir", lambda: check_state_dir(state))
        run("env.vault_path", lambda: check_vault_path(vault))
        run("env.runtime", lambda: check_engine_runtime(home, self.engine_root))
        run("guardrail.consulted", lambda: check_guardrail_consulted(home))
        run("host.timers", check_timers_armed)
        run("host.launchers", lambda: check_launchers(home))

        # 1b. Which engine last completed a cycle here: the per-machine
        # answer to "are all my machines migrated?".
        run("takeover.engine_version", lambda: check_engine_version_recorded(state))
        run("guard.last_cycle", lambda: check_last_cycle_phases(state))

        if vault.is_dir():
            # 2. Git checks
            run("git.alignment", lambda: check_git_alignment(vault))
            run("git.remotes_config", lambda: check_remotes_config(vault))
            run("git.vault_remote_privacy", lambda: check_vault_remote_privacy(vault))
            run("git.engine_lane", lambda: check_engine_lane(self.engine_root))
            run("git.quarantine", lambda: check_quarantine_branches(vault))
            run("git.mirror_alignment", lambda: check_mirror_alignment(vault))

            # 3. MCP checks
            run("mcp.manifest", lambda: check_mcp_manifest(manifest_mcp))
            run("mcp.rendered_configs", lambda: check_mcp_configs_rendered(vault, home))
            run("mcp.placement", lambda: check_mcp_placement(vault))
            run("mcp.rendered_content", lambda: check_mcp_content_drift(vault, home, self.engine_root))
            run("mcp.commands", lambda: check_mcp_commands(vault, home))
            run("mcp.orphans", lambda: check_mcp_orphans(vault, home))
            run("mcp.reachability", lambda: check_mcp_reachability(vault, home))
            run("mcp.deps", lambda: check_mcp_deps(manifest_mcp, state))
            run("modules.catalog", lambda: check_modules_catalog(self.engine_root, vault))
            # Installed is not working: a module says so itself, and only the
            # doctor asks, never the guard cycle.
            run("modules.ready", lambda: check_modules_ready(self.engine_root, vault))

            # 4. Skill checks
            run("skills.manifest", lambda: check_skills_manifest(manifest_skills))
            run("skills.deps", lambda: check_skill_deps(manifest_skills, state))
            run("skills.library_and_index", lambda: check_skill_library_and_index(vault, home))
            run("skills.library_symlinks", lambda: check_skill_library_symlinks(home))
            run("skills.not_materialized", lambda: check_skills_not_materialized(vault, home))
            run("skills.pin_freshness", lambda: check_skills_pin_freshness(vault, home))
            run("skills.out_of_manifest", lambda: check_skills_out_of_manifest(vault, home))
            run("skills.engine_starter_views", lambda: check_engine_starter_views(vault, home))
            run("skills.manifest_semantics", lambda: check_skills_manifest_semantics(vault, home))

            # 5. Identity checks
            run("identity.agent_self", lambda: check_agent_self(vault))
            run("identity.agent_self_metadata", lambda: check_agent_self_metadata(vault))
            run("identity.native_memory_boundary", lambda: check_native_memory_boundary(home))

            # 6. Bootstrap and env-secret checks (ported from the release)
            run("bootstrap.rules_guard", lambda: check_required_rules(vault, self.engine_root))
            run("env.tokens_in_env", lambda: check_tokens_in_env(vault))
            run("security.secrets_materialized", lambda: check_secrets_materialized(home, vault))
            run("security.leak_patterns_twin", lambda: check_leak_patterns_twin(vault, self.engine_root))

            # 7. Canonical instructions and bootstrap hygiene
            run("instructions.canonical_present", lambda: check_canonical_instructions_present(vault))
            run("instructions.claude_pointer", lambda: check_claude_pointer(vault, home))
            run("instructions.cli_pointers", lambda: check_cli_instruction_pointers(vault, home))
            run("instructions.opencode", lambda: check_opencode_instructions(vault, home))
            run("instructions.bootstrap_size_budget", lambda: check_bootstrap_size_budget(vault))
            run("instructions.bootstrap_notes_size", lambda: check_bootstrap_notes_size(vault))
            run("instructions.bootstrap_pointer_integrity", lambda: check_bootstrap_pointer_integrity(vault))

        return report


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for the agent-doctor command."""
    parser = argparse.ArgumentParser(description=t("NeXgen Engine Doctor (v2) - Alignment diagnostics and verification"))
    parser.add_argument("-v", "--verbose", action="store_true", help=t("Show every check run, including the ones that passed"))
    parser.add_argument("--strict", action="store_true", help=t("Strict mode: treat undetermined states as non-compliant too"))
    parser.add_argument("--json", action="store_true", help=t("Print the output in JSON format"))
    parser.add_argument("--summary", action="store_true", help=t("Print the summary with FAIL=N OK=N counts"))
    parser.add_argument("--fix", "--remedy", action="store_true", help=t("Apply automatic remedies for fixable problems"))

    args = parser.parse_args(argv)

    doc = Doctor()
    report = doc.run_diagnostics(apply_remedies=args.fix)

    if args.json:
        print(report.format_json())
    elif args.summary:
        fail_count = len(report.broken)
        ok_count = report.ok_count
        undet_count = len(report.undetermined)
        print(f"FAIL={fail_count} OK={ok_count} WARN={len(report.warnings)} UNDETERMINED={undet_count}")
        if fail_count > 0:
            for b in report.broken:
                print(f"  ✗ {b.message}")
    else:
        print(report.format_human(verbose=args.verbose))

    return report.exit_code(strict=args.strict)


if __name__ == "__main__":
    sys.exit(main())
