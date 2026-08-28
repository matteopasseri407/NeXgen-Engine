"""Il piano unificato: preview di un apply, senza scrivere e senza rete.

Una regola per test: il piano che dice "non scrivo" deve poter girare su una
snapshot del filesystem che non cambia. Tutto il resto (drift rilevato,
exit code, provenance JSON) è comportamento osservabile della stessa
funzione che il preview e il dump condividono.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.cli import engine as engine_cli  # noqa: E402
from nexgen_core.plan import SyncPlan, build_sync_plan  # noqa: E402


def _declare_one_skill(sandbox) -> None:
    """Un manifest che dichiara una skill che la sandbox non ha materializzato."""
    manifest = sandbox.skills_dir / "skills.manifest.yaml"
    manifest.write_text(
        "skills:\n"
        "  fake-skill-a:\n"
        "    origin: vault\n"
        "    targets: [claude, codex]\n",
        encoding="utf-8",
    )


def test_drift_is_reported_as_a_planned_action(sandbox):
    _declare_one_skill(sandbox)

    plan = build_sync_plan()

    assert plan.drift
    assert any("[skills]" in action for action in plan.planned_actions)


def test_the_plan_writes_nothing_at_all(sandbox):
    _declare_one_skill(sandbox)
    before = sandbox.tree_snapshot()

    build_sync_plan()

    after = sandbox.tree_snapshot()
    assert before == after, "il piano ha mutato qualcosa nella sandbox"


def test_the_human_view_prints_no_drift_when_nothing_is_planned(sandbox, monkeypatch, capsys):
    """Un piano senza azioni stampato come allineato (la sandbox nuda di
    per sé dipende dalla macchina: qui il caso allineato è costruito)."""
    empty = SyncPlan(
        engine_version="test", vault=str(sandbox.vault),
        branch=None, commit=None,
    )
    monkeypatch.setattr("nexgen_core.plan.build_sync_plan", lambda *a, **k: empty)

    assert engine_cli.cmd_plan(SimpleNamespace(check=True, json=False)) == 0
    out = capsys.readouterr().out
    assert "DRIFT" not in out


def test_check_makes_drift_exit_non_zero(sandbox, capsys):
    _declare_one_skill(sandbox)

    assert engine_cli.cmd_plan(SimpleNamespace(check=True, json=False)) == 1
    # senza --check lo stesso drift è solo visibile: exit 0
    assert engine_cli.cmd_plan(SimpleNamespace(check=False, json=False)) == 0
    out = capsys.readouterr().out
    assert "DRIFT" in out


def test_the_json_dump_carries_provenance(sandbox, capsys):
    _declare_one_skill(sandbox)

    assert engine_cli.cmd_plan(SimpleNamespace(check=False, json=True)) == 0
    payload = json.loads(capsys.readouterr().out)

    for key in ("engine_version", "vault", "branch", "commit", "drift", "planned_actions"):
        assert key in payload
    assert payload["drift"] is True
    assert isinstance(payload["planned_actions"], list)


def test_sync_dry_run_is_the_same_plan_and_applies_nothing(sandbox, capsys):
    _declare_one_skill(sandbox)
    before = sandbox.tree_snapshot()

    # `nexgen sync --dry-run` raggiunge lo stesso oggetto: DRIFT visibile, exit 0
    assert engine_cli.cmd_sync(SimpleNamespace(dry_run=True, check=False, json=False)) == 0
    out = capsys.readouterr().out
    assert "DRIFT" in out
    assert sandbox.tree_snapshot() == before


def test_the_git_probe_stays_offline(sandbox, monkeypatch):
    """Nessun fetch: un piano tocca il repository solo in lettura locale."""
    import subprocess

    calls: list[list[str]] = []
    real_run = subprocess.run

    def spy(cmd, *args, **kwargs):
        if cmd[:2] == ["git", "-C"]:
            calls.append(list(cmd))
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", spy)

    build_sync_plan()

    for cmd in calls:
        assert "fetch" not in cmd, f"il piano ha eseguito {cmd}"


def test_the_plan_declares_what_it_cannot_see(sandbox):
    plan = build_sync_plan()

    assert plan.not_checked, "i limiti del piano vanno dichiarati, non taciuti"
