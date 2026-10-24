"""Il Workboard come specchio: la scheda nasce con l'unità e si muove come il diario.

Nessun `openclaw` vero: la fixture mette un eseguibile finto in testa al PATH e un
registratore risponde al posto del CLI. Quello che si prova è che la scheda non
diventi mai la verità: gli stati vengono dal diario, e una board assente, spenta o
che rifiuta costa una riga, mai un verbo che fallisce.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from nexgen_local import cli
from nexgen_local import supervision_costs as costs
from nexgen_local import supervision_harness as harness
from nexgen_local import supervision_policy as policy
from nexgen_local import supervision_processes as processes
from nexgen_local import supervision_review as review
from nexgen_local import supervision_state as state
from nexgen_local import supervision_workboard as workboard

from supervision_repo import build

CARD = "7f4a2c10-ab12-cd34-ef56-0a1b2c3d4e5f"


def _arg(argv: list[str], name: str) -> str:
    return argv[argv.index(name) + 1] if name in argv else ""


@pytest.fixture
def fake_cli(tmp_path, monkeypatch):
    """`openclaw` in una cartella temporanea, in testa al PATH: l'eseguibile vero non è raggiungibile."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, text in (("openclaw", "#!/bin/sh\nexit 0\n"), ("openclaw.cmd", "@echo off\r\n")):
        (bin_dir / name).write_text(text, encoding="utf-8")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.delenv("NEXGEN_DISABLE_HOST_MUTATIONS", raising=False)
    return bin_dir


@pytest.fixture
def lane(tmp_path, monkeypatch, fake_cli):
    """La lane, la casa finta e il registro della board: nessuno tocca lo stato del proprietario."""
    monkeypatch.setattr(state, "lane_state_dir", lambda: tmp_path / "lane")
    monkeypatch.setenv("OPENCLAW_STATE_DIR", str(tmp_path / "openclaw"))
    return tmp_path / "lane"


class Board:
    """Risponde come `openclaw workboard`, e ricorda ogni domanda."""

    def __init__(self, *, code: int = 0, stdout: str = "") -> None:
        self.calls: list[list[str]] = []
        self.code = code
        self.stdout = stdout or json.dumps({"card": {"id": CARD, "status": "todo"}})

    def __call__(self, argv, *, timeout):
        argv = list(argv)
        self.calls.append(argv)
        return subprocess.CompletedProcess(argv, self.code, self.stdout, "")

    def statuses(self) -> list[str]:
        return [_arg(call, "--status") for call in self.calls if "move" in call]


@pytest.fixture
def board(monkeypatch):
    recorder = Board()
    monkeypatch.setattr(processes, "captured", recorder)
    return recorder


@pytest.fixture
def task(lane):
    return state.start("prova workboard")["id"]


@pytest.fixture
def unit(lane, task):
    """Un'unità già avviata su OpenClaw: copia, brief e sessione, senza git."""
    folder = state.open_task(task) / "23-unita-prova"
    folder.mkdir(parents=True)
    (folder.parent / "20-brief-prova.md").write_text(
        "# Brief\n\nPorta il conteggio dentro il motore e non toccare altro.\n", encoding="utf-8",
    )
    state.append_event(
        task, "unit_created", unit="prova", worktree=str(folder), branch=f"sup/{task}/prova",
        base="0" * 40, allow=["docs/*.md"], verify="", repo=str(lane),
    )
    state.append_event(
        task, "attached", unit="prova", role="esecutore", harness="openclaw",
        session="agent:sviluppo:sup-prova", folder=str(folder),
    )
    return folder


def test_a_unit_gets_a_card_that_points_at_its_copy_and_brief(board, lane, tmp_path, capsys):
    repo = build(tmp_path / "sandbox")
    brief = tmp_path / "brief.md"
    brief.write_text("# Brief\n\nPorta il conteggio dentro il motore.\n", encoding="utf-8")
    task = state.start("prova workboard")["id"]
    capsys.readouterr()

    assert cli.main([
        "sup", "unit", task, "prova", "--repo", str(repo.path), "--base", "main",
        "--brief", str(brief), "--allow", "docs/*.md",
    ]) == 0

    out = capsys.readouterr().out
    assert f"scheda Workboard {CARD[:8]}" in out
    create = [call for call in board.calls if "create" in call][-1]
    assert f"unità prova · {task}" in create, "il titolo dice unità e compito"
    assert "todo" == _arg(create, "--status")
    assert f"{task},prova" == _arg(create, "--labels"), "le etichette portano compito e unità"
    assert _arg(create, "--agent") == "sviluppo"
    notes = _arg(create, "--notes")
    assert str(state.open_task(task) / "23-unita-prova") in notes, "la scheda dice dove sta la copia"
    assert "Porta il conteggio dentro il motore" in notes, "e il brief in breve"
    assert all("--json" in call for call in board.calls), "sempre l'output di macchina"
    card = [row for row in state.read_events(task) if row["event"] == "workboard_card"][-1]
    assert card["card"] == CARD and card["status"] == "todo"


def test_the_card_follows_run_and_collect(board, task, unit, capsys, monkeypatch):
    state.append_event(task, "workboard_card", unit="prova", card=CARD, status="todo")
    monkeypatch.setattr(harness, "run_unit", lambda task_id, name: {
        "unit": name, "mode": "run", "session": "agent:sviluppo:sup-prova", "pid": 4321,
        "row": {"cost_usd": 0.1},
    })
    monkeypatch.setattr("nexgen_local.cmds.sup.gates.collect", lambda task_id, name: {
        "unit": name, "verdict": "PASS", "gates": [], "cost": costs.CollectCost(stopped=True),
        "package": "p", "raw": "r",
    })
    capsys.readouterr()

    assert cli.main(["sup", "run", task, "prova"]) == 0
    assert cli.main(["sup", "collect", task, "prova"]) == 0

    assert board.statuses() == ["running", "review"], "gli stati del motore, nell'ordine dei verbi"
    moved = [row["status"] for row in state.read_events(task) if row["event"] == "workboard_moved"]
    assert moved == ["running", "review"]


def test_a_rework_sends_the_card_back_to_running(board, task, unit, capsys, monkeypatch):
    state.append_event(task, "workboard_card", unit="prova", card=CARD, status="review")
    corrections = state.open_task(task) / "45-revisione-prova-2.md"
    corrections.write_text("# correzioni\n\nrifai il conteggio.\n", encoding="utf-8")
    monkeypatch.setattr(review, "rework", lambda task_id, name, path: {
        "unit": name, "round": 2, "path": Path(path), "verdict": state.REWORKED,
        "head": "0" * 40, "delivery": {"mode": "run", "session": "agent:sviluppo:sup-prova", "pid": 9},
    })
    capsys.readouterr()

    assert cli.main(["sup", "review", task, "prova", "--rework", str(corrections)]) == 0

    assert board.statuses() == ["running"]
    assert "running" in capsys.readouterr().out


def test_publishing_moves_the_card_to_done_and_closes_the_session(board, task, unit, capsys, monkeypatch):
    from nexgen_local import supervision_publish as publish
    from nexgen_local.supervision_adapters import openclaw

    state.append_event(task, "workboard_card", unit="prova", card=CARD, status="review")
    monkeypatch.setattr(publish, "publish", lambda *a, **k: {
        "unit": "prova", "replayed": True, "commits": [], "signing": "firma ok",
        "branch": "main", "repo": "checkout", "pushed": False, "command": "",
        "output": "", "remote": "origin", "retirement": None,
    })
    monkeypatch.setattr(openclaw.OpenClawAdapter, "close_session", lambda self, session: "sessione archiviata")
    capsys.readouterr()

    assert cli.main(["sup", "publish", task, "prova", "--repo", "checkout", "--branch", "main"]) == 0

    out = capsys.readouterr().out
    assert "sessione archiviata" in out, "la fine del lavoro chiude la sessione del harness"
    assert board.statuses() == ["done"]


def test_a_refused_card_is_reported_in_one_line_and_the_unit_still_exists(task, unit, capsys, monkeypatch):
    monkeypatch.setattr(processes, "captured", Board(code=1, stdout="plugin workboard non abilitato"))
    record = state.unit_record(task, "prova")

    line = workboard.create(task, "prova", record)

    assert line.startswith("scheda Workboard non creata:")
    assert "non abilitato" in line
    assert not [row for row in state.read_events(task) if row["event"] == "workboard_card"]


def test_without_the_harness_cli_there_is_no_board_and_no_noise(task, unit, monkeypatch):
    monkeypatch.setattr(workboard, "_executable", lambda: "")

    assert workboard.enabled() is False
    assert workboard.create(task, "prova", state.unit_record(task, "prova")) == ""
    assert workboard.move(task, "prova", "running") == ""


def test_the_lane_can_turn_the_mirror_off(task, unit, board, monkeypatch):
    config = state.lane_state_dir() / policy.CONFIG_FILE
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(json.dumps({"supervision": {"openclaw": {"workboard": False}}}), encoding="utf-8")

    assert workboard.create(task, "prova", state.unit_record(task, "prova")) == ""
    assert board.calls == [], "spento vuol dire nessuna domanda alla board"


def test_the_lane_names_the_agent_the_turn_runs_on(task, unit, board, monkeypatch):
    config = state.lane_state_dir() / policy.CONFIG_FILE
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(json.dumps({"supervision": {"openclaw": {"agent": "capo"}}}), encoding="utf-8")

    assert "capo" in workboard.create(task, "prova", state.unit_record(task, "prova"))
    assert _arg(board.calls[-1], "--agent") == "capo"


def test_a_long_brief_is_cut_and_the_copy_is_never_guessed(unit, task):
    notes = workboard._notes(task, "prova", state.unit_record(task, "prova"))

    assert len(notes) < 1000
    assert f"copia: {unit}" in notes
    assert notes.endswith("non toccare altro."), "il brief in breve è la testa del brief"
