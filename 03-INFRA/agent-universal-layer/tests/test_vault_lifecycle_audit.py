"""Tests for the read-only lifecycle audit shipped by the public engine."""
from __future__ import annotations

import os
import subprocess
import sys

from conftest import REAL_VAULT


SCRIPT = REAL_VAULT / "03-INFRA" / "scripts" / "vault-lifecycle-audit.py"


def run_audit(vault, extra_env=None):
    env = os.environ.copy()
    env["AGENT_VAULT_DATA"] = str(vault)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--today", "2026-07-09", "--limit", "50"],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def write_note(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_lifecycle_audit_has_no_public_private_crm_assumptions(tmp_path):
    vault = tmp_path / "vault"
    write_note(
        vault / "04-NOW" / "custom-records" / "item.md",
        "---\nstatus: submitted\n---\n# Item\n",
    )

    proc = run_audit(vault)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "04-NOW/custom-records/item.md" in proc.stdout
    assert "outside relaxed prefixes" in proc.stdout
    assert "outside CRM" not in proc.stdout


def test_lifecycle_audit_accepts_private_relaxed_prefix_config(tmp_path):
    vault = tmp_path / "vault"
    write_note(
        vault / "04-NOW" / "custom-records" / "item.md",
        "---\nstatus: submitted\n---\n# Item\n",
    )
    write_note(
        vault / "99-INDEX" / "vault-lifecycle-relaxed-prefixes.txt",
        "# local schemas\n04-NOW/custom-records/\n",
    )

    proc = run_audit(vault)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Relaxed prefixes: 04-NOW/custom-records/" in proc.stdout
    assert "submitted\t04-NOW/custom-records/item.md" not in proc.stdout


def test_lifecycle_audit_accepts_private_generated_dir_config(tmp_path):
    vault = tmp_path / "vault"
    write_note(vault / "01-LOCAL" / "artifacts" / "export.md", "# Generated\n")
    write_note(
        vault / "99-INDEX" / "vault-lifecycle-generated-dirs.txt",
        "# local generated payloads\n01-LOCAL/artifacts\n",
    )

    proc = run_audit(vault)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Generated dirs: 03-INFRA/n8n-backup, 01-LOCAL/artifacts" in proc.stdout
    assert "01-LOCAL/artifacts/export.md" not in proc.stdout


def test_lifecycle_audit_flags_a_leftover_handoff_note(tmp_path):
    vault = tmp_path / "vault"
    write_note(
        vault / "04-NOW" / "handoff-to-cheap-model.md",
        "## Handoff\n\n### Objective\n\nDo the thing.\n\n### Scope\n\nRead only:\n\n- `foo.py`\n",
    )

    proc = run_audit(vault)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Handoff notes still in the vault" in proc.stdout
    assert "04-NOW/handoff-to-cheap-model.md" in proc.stdout


def test_lifecycle_audit_does_not_flag_a_note_merely_mentioning_handoff(tmp_path):
    vault = tmp_path / "vault"
    write_note(
        vault / "01-NOTES" / "meeting.md",
        "---\nstatus: active\ntype: note\nlast_reviewed: 2026-07-01\n---\n"
        "# Meeting notes\n\nWe discussed the handoff process for new hires.\n",
    )

    proc = run_audit(vault)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "01-NOTES/meeting.md" not in proc.stdout.split("Handoff notes")[-1]


def test_lifecycle_audit_excludes_the_canonical_handoff_template_itself(tmp_path):
    vault = tmp_path / "vault"
    write_note(
        vault / "03-INFRA" / "agent-universal-layer" / "templates" / "cheap-model-handoff.md",
        "# Cheap Model Handoff Template\n\n## Handoff\n\n### Scope\n\nRead only:\n\n- `path/or/pattern`\n",
    )

    proc = run_audit(vault)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    handoff_section = proc.stdout.split("Handoff notes still in the vault")[-1]
    assert "cheap-model-handoff.md" not in handoff_section
