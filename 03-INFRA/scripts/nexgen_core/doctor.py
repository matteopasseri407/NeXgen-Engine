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

from nexgen_core.checks.env_checks import check_state_dir, check_vault_path
from nexgen_core.checks.git_checks import check_git_alignment, check_mirror_alignment
from nexgen_core.checks.identity_checks import (
    check_agent_self,
    check_agent_self_metadata,
    check_native_memory_boundary,
)
from nexgen_core.checks.instructions_checks import (
    check_bootstrap_notes_size,
    check_bootstrap_pointer_integrity,
    check_bootstrap_size_budget,
    check_canonical_instructions_present,
    check_claude_pointer,
    check_cli_instruction_pointers,
    check_opencode_instructions,
)
from nexgen_core.checks.mcp_checks import check_mcp_configs_rendered, check_mcp_manifest
from nexgen_core.checks.reachability_checks import check_mcp_reachability
from nexgen_core.checks.security_checks import check_required_rules, check_tokens_in_env
from nexgen_core.checks.skill_checks import (
    check_engine_starter_views,
    check_skill_library_and_index,
    check_skill_library_symlinks,
    check_skills_manifest,
    check_skills_manifest_semantics,
    check_skills_not_materialized,
    check_skills_out_of_manifest,
)
from nexgen_core.paths import resolve_state_dir, resolve_vault_data
from nexgen_core.report import Report


class Doctor:
    """Judge of the agent layer's alignment state."""

    def __init__(
        self,
        vault_data: Path | None = None,
        state_dir: Path | None = None,
        home: Path | None = None,
    ) -> None:
        self.home = home or Path.home()
        _v = resolve_vault_data(self.home, vault_data)
        self.vault_data = _v
        self.state_dir = resolve_state_dir(self.home, state_dir)

    def run_diagnostics(self, apply_remedies: bool = False) -> Report:
        """Runs every registered check (read-only by default)."""
        report = Report()

        # 1. Environment and directory checks
        report.add(check_state_dir(self.state_dir), apply_remedy=apply_remedies)
        report.add(check_vault_path(self.vault_data), apply_remedy=apply_remedies)

        # 2. Git checks (if the Vault exists)
        if self.vault_data.is_dir():
            report.add(check_git_alignment(self.vault_data), apply_remedy=apply_remedies)
            for outcome in check_mirror_alignment(self.vault_data):
                report.add(outcome, apply_remedy=apply_remedies)

            # 3. MCP checks
            manifest_mcp = self.vault_data / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml"
            report.add(check_mcp_manifest(manifest_mcp), apply_remedy=apply_remedies)
            report.add(check_mcp_configs_rendered(self.vault_data, self.home), apply_remedy=apply_remedies)
            for outcome in check_mcp_reachability(self.vault_data, self.home):
                report.add(outcome, apply_remedy=apply_remedies)

            # 4. Skill checks
            manifest_skills = self.vault_data / "03-INFRA" / "agent-universal-layer" / "skills" / "skills.manifest.yaml"
            report.add(check_skills_manifest(manifest_skills), apply_remedy=apply_remedies)
            report.add(check_skill_library_and_index(self.vault_data, self.home), apply_remedy=apply_remedies)
            report.add(check_skill_library_symlinks(self.home), apply_remedy=apply_remedies)
            report.add(check_skills_not_materialized(self.vault_data, self.home), apply_remedy=apply_remedies)
            report.add(check_skills_out_of_manifest(self.vault_data, self.home), apply_remedy=apply_remedies)
            report.add(check_engine_starter_views(self.vault_data, self.home), apply_remedy=apply_remedies)
            semantics = check_skills_manifest_semantics(self.vault_data, self.home)
            if semantics is not None:
                report.add(semantics, apply_remedy=apply_remedies)

            # 5. Identity checks
            report.add(check_agent_self(self.vault_data), apply_remedy=apply_remedies)
            report.add(check_agent_self_metadata(self.vault_data), apply_remedy=apply_remedies)
            report.add(check_native_memory_boundary(self.home), apply_remedy=apply_remedies)

            # 6. Bootstrap and env-secret checks (ported from the release)
            report.add(check_required_rules(self.vault_data), apply_remedy=apply_remedies)
            report.add(check_tokens_in_env(self.vault_data), apply_remedy=apply_remedies)

            # 7. Canonical instructions and bootstrap hygiene
            report.add(check_canonical_instructions_present(self.vault_data), apply_remedy=apply_remedies)
            report.add(check_claude_pointer(self.vault_data, self.home), apply_remedy=apply_remedies)
            for outcome in check_cli_instruction_pointers(self.vault_data, self.home):
                report.add(outcome, apply_remedy=apply_remedies)
            opencode_outcome = check_opencode_instructions(self.vault_data, self.home)
            if opencode_outcome is not None:
                report.add(opencode_outcome, apply_remedy=apply_remedies)
            for hygiene_check in (
                check_bootstrap_size_budget,
                check_bootstrap_notes_size,
                check_bootstrap_pointer_integrity,
            ):
                outcome = hygiene_check(self.vault_data)
                if outcome is not None:
                    report.add(outcome, apply_remedy=apply_remedies)

        return report


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for the agent-doctor command."""
    parser = argparse.ArgumentParser(description="NeXgen Engine Doctor (v2) - Alignment diagnostics and verification")
    parser.add_argument("-v", "--verbose", action="store_true", help="Show every check run, including the ones that passed")
    parser.add_argument("--strict", action="store_true", help="Strict mode: treat undetermined states as non-compliant too")
    parser.add_argument("--json", action="store_true", help="Print the output in JSON format")
    parser.add_argument("--summary", action="store_true", help="Print the summary with FAIL=N OK=N counts")
    parser.add_argument("--fix", "--remedy", action="store_true", help="Apply automatic remedies for fixable problems")

    args = parser.parse_args(argv)

    doc = Doctor()
    report = doc.run_diagnostics(apply_remedies=args.fix)

    if args.json:
        print(report.format_json())
    elif args.summary:
        fail_count = len(report.broken)
        ok_count = report.ok_count
        undet_count = len(report.undetermined)
        print(f"FAIL={fail_count} OK={ok_count} UNDETERMINED={undet_count}")
        if fail_count > 0:
            for b in report.broken:
                print(f"  ✗ {b.message}")
    else:
        print(report.format_human(verbose=args.verbose))

    return report.exit_code(strict=args.strict)


if __name__ == "__main__":
    sys.exit(main())
