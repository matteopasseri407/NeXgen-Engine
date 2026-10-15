"""Pinning dello split lifecycle prima del refactor.

Fissa il comportamento attuale (non quello desiderato): main su vault
finto ritorna esito atteso, report stabile (due corse stesso esito),
firme pubbliche invariate. Solo vault finti in tmp_path, mai vault vero,
mai publish reale.
"""
from __future__ import annotations

import inspect
import os
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import nexgen_core.tools.vault_lifecycle_audit as lifecycle_mod  # noqa: E402

SCRIPT = SCRIPTS_DIR / "vault-lifecycle-audit.py"


def _write_note(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _make_fake_vault(base: Path) -> Path:
    vault = base / "vault"
    _write_note(
        vault / "01-NOTES" / "active.md",
        "---\nstatus: active\ntype: note\nlast_reviewed: 2026-07-01\n---\n# Active\n",
    )
    _write_note(
        vault / "04-NOW" / "handoff-to-cheap-model.md",
        "## Handoff\n\n### Objective\n\nDo the thing.\n\n### Scope\n\nRead only:\n\n- `foo.py`\n",
    )
    _write_note(
        vault / "01-NOTES" / "meeting.md",
        "---\nstatus: active\ntype: note\nlast_reviewed: 2026-07-01\n---\n"
        "# Meeting\n\nWe discussed the handoff process for new hires.\n",
    )
    return vault


def _run_audit(vault: Path) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["AGENT_VAULT_DATA"] = str(vault)
    env["KNOWLEDGE_VAULT_PATH"] = str(vault)
    for key in ("VAULT_LIFECYCLE_RELAXED_PREFIXES", "VAULT_LIFECYCLE_GENERATED_DIRS"):
        env.pop(key, None)
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--today", "2026-07-09", "--limit", "50"],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_main_su_vault_finto_ritorna_esito_atteso(tmp_path: Path) -> None:
    vault = _make_fake_vault(tmp_path)
    proc = _run_audit(vault)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "KnowledgeVault lifecycle audit" in proc.stdout
    assert "Markdown notes: 3" in proc.stdout
    assert "Handoff notes still in the vault" in proc.stdout
    assert "04-NOW/handoff-to-cheap-model.md" in proc.stdout
    handoff_section = proc.stdout.split("Handoff notes still in the vault")[-1]
    assert "01-NOTES/meeting.md" not in handoff_section


def test_report_stabile_due_corse_stesso_esito(tmp_path: Path) -> None:
    vault = _make_fake_vault(tmp_path)
    first = _run_audit(vault)
    second = _run_audit(vault)
    assert first.returncode == 0, first.stdout + first.stderr
    assert second.returncode == 0, second.stdout + second.stderr
    assert first.stdout == second.stdout


def test_firme_pubbliche_invariate() -> None:
    assert list(inspect.signature(lifecycle_mod.parse_note).parameters) == ["path"]
    assert list(inspect.signature(lifecycle_mod.iter_notes).parameters) == []
    assert list(inspect.signature(lifecycle_mod.parse_note_date).parameters) == ["note"]
    assert list(inspect.signature(lifecycle_mod.dir_size).parameters) == ["path"]
    assert list(inspect.signature(lifecycle_mod.human_size).parameters) == ["size"]
    assert list(inspect.signature(lifecycle_mod.normalize_prefix).parameters) == ["value"]
    assert list(inspect.signature(lifecycle_mod.normalize_relpath).parameters) == ["value"]
    assert list(inspect.signature(lifecycle_mod.load_relaxed_prefixes).parameters) == ["root"]
    assert list(inspect.signature(lifecycle_mod.load_generated_dirs).parameters) == ["root"]
    assert list(inspect.signature(lifecycle_mod.is_relaxed).parameters) == ["note", "prefixes"]
    assert list(inspect.signature(lifecycle_mod.print_section).parameters) == ["title", "rows", "limit"]
    assert list(inspect.signature(lifecycle_mod.main).parameters) == ["argv"]
    public_callables = {
        n for n, v in vars(lifecycle_mod).items()
        if callable(v) and not n.startswith("_") and getattr(v, "__module__", "") == lifecycle_mod.__name__
    }
    assert public_callables == {
        "Note", "parse_note", "iter_notes", "parse_note_date", "dir_size",
        "human_size", "normalize_prefix", "normalize_relpath",
        "load_relaxed_prefixes", "load_generated_dirs", "is_relaxed",
        "print_section", "main",
    }
