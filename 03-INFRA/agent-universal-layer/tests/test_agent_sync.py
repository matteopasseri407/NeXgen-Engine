"""Test 9-12 su agent-sync.sh, eseguito per davvero (subprocess bash) dentro
la sandbox. MAI sulla HOME reale: ogni chiamata passa da run_agent_sync(),
che si rifiuta di partire se manca il sentinel di sandbox.
"""
from __future__ import annotations

import os

import pytest

from conftest import run_agent_sync

pytestmark = pytest.mark.skipif(
    os.name == "nt",
    reason="POSIX launcher/symlink regression tests run on Ubuntu; Windows runs agent_sync.py smoke.",
)

RUNTIME_DIRS = (".claude/skills", ".codex/skills")


def _make_eager_root_symlink_runtime(sandbox) -> None:
    """Set up one valid Claude library view and one invalid Codex eager view."""
    (sandbox.home / ".agents").mkdir(parents=True, exist_ok=True)
    targets = {
        ".claude/skills": sandbox.skill_library,
        ".codex/skills": sandbox.active_skills,
    }
    for rt, source in targets.items():
        p = sandbox.home / rt
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists() or p.is_symlink():
            p.unlink()
        os.symlink(source, p, target_is_directory=True)


def _make_real_runtime_dirs(sandbox) -> None:
    for rt in RUNTIME_DIRS:
        (sandbox.home / rt).mkdir(parents=True, exist_ok=True)


# ---- test 9: regressione self-loop (bug 2026-07-01) ------------------------

def test_eager_root_regression_keeps_library_bytes_and_repairs_codex_view(sandbox):
    sb = sandbox
    _make_eager_root_symlink_runtime(sb)
    vault_skill_md = sb.skills_dir / "fake-skill-a" / "SKILL.md"
    original_bytes = vault_skill_md.read_bytes()

    proc = run_agent_sync(sb, "apply")
    assert proc.returncode == 0, proc.stdout + proc.stderr

    # i byte veri nella sorgente vault non sono mai stati toccati
    assert vault_skill_md.read_bytes() == original_bytes

    library_skill_md = sb.skill_library / "fake-skill-a" / "SKILL.md"
    assert library_skill_md.is_file(), "library: fake-skill-a non leggibile dopo il run"
    assert library_skill_md.read_bytes() == original_bytes
    assert not (sb.active_skills / "fake-skill-a").exists()

    claude = sb.home / ".claude" / "skills"
    assert claude.is_symlink() and claude.resolve() == sb.skill_library.resolve()
    assert (claude / "fake-skill-a" / "SKILL.md").read_bytes() == original_bytes

    codex = sb.home / ".codex" / "skills"
    assert codex.is_dir() and not codex.is_symlink()
    assert not (codex / "fake-skill-a").exists()


def test_old_claude_link_to_active_view_is_normalized_before_skill_sync(sandbox):
    sb = sandbox
    (sb.home / ".agents").mkdir(parents=True, exist_ok=True)
    sb.active_skills.mkdir(parents=True, exist_ok=True)
    claude = sb.home / ".claude" / "skills"
    claude.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(sb.active_skills, claude, target_is_directory=True)

    proc = run_agent_sync(sb, "apply")
    assert proc.returncode == 0, proc.stdout + proc.stderr

    assert claude.is_dir() and not claude.is_symlink()
    assert (claude / "fake-skill-a").resolve() == (sb.skill_library / "fake-skill-a").resolve()


def test_broken_active_root_link_is_repaired_before_sync(sandbox):
    sb = sandbox
    active = sb.active_skills
    active.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(sb.home / "missing-skill-root", active, target_is_directory=True)

    proc = run_agent_sync(sb, "apply")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert active.is_dir() and not active.is_symlink()
    assert (active / "INDEX.md").is_file()


# ---- test 10: self-healing symlink -----------------------------------------

def test_self_healing_symlink_restored_after_deletion(sandbox):
    sb = sandbox
    _make_real_runtime_dirs(sb)

    proc1 = run_agent_sync(sb, "apply")
    assert proc1.returncode == 0, proc1.stdout + proc1.stderr

    codex_agents = sb.home / ".codex" / "AGENTS.md"
    assert codex_agents.is_symlink(), "pointer AGENTS.md per Codex non creato al primo giro"
    canonical = sb.ul / "instructions" / "AGENTS.md"
    assert codex_agents.resolve() == canonical.resolve()

    codex_agents.unlink()
    assert not codex_agents.exists()

    proc2 = run_agent_sync(sb, "apply")
    assert proc2.returncode == 0, proc2.stdout + proc2.stderr
    assert codex_agents.is_symlink(), "il pointer cancellato non e' stato ripristinato"
    assert codex_agents.resolve() == canonical.resolve()


# ---- test 11: manual exposure ----------------------------------------------

def test_manual_skills_are_available_to_claude_but_not_eager_runtime_views(sandbox):
    sb = sandbox
    _make_real_runtime_dirs(sb)

    proc = run_agent_sync(sb, "apply")
    assert proc.returncode == 0, proc.stdout + proc.stderr

    # Claude riceve la vista native-lazy dichiarata dal manifest.
    assert (sb.home / ".claude" / "skills" / "fake-skill-excluded").exists()
    assert (sb.home / ".claude" / "skills" / "fake-skill-a").exists()
    # Codex non riceve corpi manuali nella vista eager.
    assert not (sb.home / ".codex" / "skills" / "fake-skill-excluded").exists()
    assert not (sb.home / ".codex" / "skills" / "fake-skill-a").exists()
    # Le due restano nel library cache, non nella discovery root.
    assert (sb.skill_library / "fake-skill-excluded" / "SKILL.md").is_file()
    assert not (sb.active_skills / "fake-skill-excluded").exists()


# ---- test 12: idempotenza (doppio giro = zero modifiche al filesystem) ----

def test_apply_is_idempotent(sandbox):
    sb = sandbox
    _make_real_runtime_dirs(sb)

    # priming run: porta la sandbox a regime (la prima esecuzione fa sempre
    # qualche scrittura iniziale: pointer, catalogo skill, backup di render.py, ecc.)
    proc0 = run_agent_sync(sb, "apply")
    assert proc0.returncode == 0, proc0.stdout + proc0.stderr

    exclude = frozenset({"agent-sync.log"})
    snap_before = sb.tree_snapshot(exclude_names=exclude)

    proc1 = run_agent_sync(sb, "apply")
    assert proc1.returncode == 0, proc1.stdout + proc1.stderr
    snap_after = sb.tree_snapshot(exclude_names=exclude)

    if snap_after != snap_before:
        only_before = {k: v for k, v in snap_before.items() if snap_after.get(k) != v}
        only_after = {k: v for k, v in snap_after.items() if snap_before.get(k) != v}
        raise AssertionError(
            "il secondo giro ha modificato il filesystem sandbox.\n"
            f"cambiati/rimossi: {only_before}\naggiunti/cambiati: {only_after}"
        )
