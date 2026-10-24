"""OpenClaw come harness: il turno, la sessione che il motore sceglie, e il conto del turno.

Nessun `openclaw` vero: la fixture mette un eseguibile finto in testa al PATH, e un
registratore risponde le forme documentate (`agents list --json`, `sessions --json`,
l'envelope del turno). Il turno parte da un `Popen` finto: niente processo, niente
Control UI, niente casa del proprietario.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from nexgen_local import cli
from nexgen_local import supervision_adapters as registry
from nexgen_local import supervision_costs as costs
from nexgen_local import supervision_harness as harness
from nexgen_local import supervision_processes as processes
from nexgen_local import supervision_state as state
from nexgen_local.supervision_adapters import base, openclaw
from nexgen_local.supervision_costs import SessionMissing

from supervision_repo import build


def _arg(argv: list[str], name: str) -> str:
    return argv[argv.index(name) + 1] if name in argv else ""


@pytest.fixture
def fake_cli(tmp_path, monkeypatch):
    """`openclaw` in una cartella temporanea, in testa al PATH: l'eseguibile vero non è raggiungibile."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, text in (("openclaw", "#!/bin/sh\nexit 0\n"), ("openclaw.cmd", "@echo off\r\n")):
        target = bin_dir / name
        target.write_text(text, encoding="utf-8")
        target.chmod(0o755)

    def _has_real_openclaw(entry: str) -> bool:
        if not entry:
            return False
        try:
            p = Path(entry)
            return (p / "openclaw").exists() or (p / "openclaw.cmd").exists() or (p / "openclaw.exe").exists()
        except OSError:
            return False

    clean_path = os.pathsep.join(
        p for p in os.environ.get("PATH", "").split(os.pathsep)
        if not _has_real_openclaw(p)
    )
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{clean_path}")
    return bin_dir


@pytest.fixture
def lane(tmp_path, monkeypatch, fake_cli):
    """La lane e la casa finta: `OPENCLAW_STATE_DIR` sotto la cartella temporanea, mai quella vera."""
    monkeypatch.setattr(state, "lane_state_dir", lambda: tmp_path / "lane")
    monkeypatch.setenv("OPENCLAW_STATE_DIR", str(tmp_path / "openclaw"))
    return tmp_path / "lane"


class Recorder:
    """Risponde come OpenClaw, e ricorda ogni domanda: è l'unico harness di queste prove.

    Tutto quello che non parla di OpenClaw torna al runner vero: la prova crea
    repository git, e un registratore che rispondesse anche a git costruirebbe una
    copia dell'unità che non esiste.
    """

    def __init__(self, workspace: Path) -> None:
        self.workspace = Path(workspace)
        self.calls: list[list[str]] = []
        self.turns: list[list[str]] = []
        self.key = ""
        self.rows: list[dict] = []
        self.fail: dict[str, tuple[int, str, str]] = {}
        self.card = {"card": {"id": "7f4a2c10-ab12-cd34-ef56-0a1b2c3d4e5f", "status": "todo"}}
        self.updated_at = 1_700_000_000_000
        self.real_captured = processes.captured
        self.real_popen = subprocess.Popen

    def row(self, **over) -> dict:
        row = {
            "key": f"agent:sviluppo:{self.key}",
            "sessionId": "0192aaaa-12ab-7cd8-9ef0-1a2b3c4d5e6f",
            "model": "gpt-6-astra", "modelProvider": "openai",
            "inputTokens": 120, "outputTokens": 8, "totalTokens": 128,
            "updatedAt": self.updated_at, "sessionStartedAt": self.updated_at - 60_000,
            "status": "done",
        }
        row.update(over)
        return row

    def popen(self, argv, **kwargs):
        if not _speaks_openclaw(argv):
            return self.real_popen(argv, **kwargs)
        self.turns.append(list(argv))
        self.key = _arg(list(argv), "--session-key")
        self.rows = [self.row()]
        return SimpleNamespace(pid=4321)

    def captured(self, argv, *, timeout):
        if not _speaks_openclaw(argv):
            return self.real_captured(argv, timeout=timeout)
        argv = list(argv)
        self.calls.append(argv)
        for marker, answer in self.fail.items():
            if marker in argv:
                return subprocess.CompletedProcess(argv, answer[0], answer[1], answer[2])
        if "agents" in argv and "list" in argv:
            return _ok(argv, [{"id": "sviluppo", "workspace": str(self.workspace)}])
        if "sessions" in argv and "archive" in argv:
            return _ok(argv, {"archived": [_arg(argv, "archive")], "count": 1})
        if "sessions" in argv:
            return _ok(argv, {"sessions": self.rows})
        if "workboard" in argv:
            return _ok(argv, self.card)
        return _ok(argv, {})


def _ok(argv: list[str], payload: object) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")


def _speaks_openclaw(argv) -> bool:
    """Whether this command line is the harness this recorder answers for.

    Only the executable's own name counts: a task called "-openclaw-..." appears
    in the paths of every command that touches it, git included, and answering
    for git would build a copy of the unit that does not exist.
    """
    for part in list(argv)[:5]:
        if Path(str(part)).name.lower().startswith("openclaw"):
            return True
    return False


@pytest.fixture
def task(lane):
    return state.start("prova openclaw")["id"]


@pytest.fixture
def unit(lane, task):
    """La copia e il brief di un'unità, senza git: l'adattatore non ne ha bisogno."""
    folder = state.open_task(task) / "23-unita-prova"
    folder.mkdir(parents=True)
    (folder.parent / "20-brief-prova.md").write_text(
        "# Brief\n\ncontenuto segreto\n", encoding="utf-8",
    )
    state.append_event(
        task, "unit_created", unit="prova", worktree=str(folder), branch=f"sup/{task}/prova",
        base="0" * 40, allow=["docs/*.md"], verify="", repo=str(lane),
    )
    return folder


# --- il contratto dell'adattatore -------------------------------------------


def test_the_capability_table_says_openclaw_has_no_window_of_its_own():
    adapter = registry.get("openclaw")
    assert adapter.capabilities.window is False, "il turno si guarda nella Control UI, non in una scheda"
    assert adapter.capabilities.has("send_turn") and adapter.capabilities.has("cost")
    assert adapter.capabilities.has("write_permissions") is False
    assert adapter.capabilities.has("scout") is False
    assert "workspace" in adapter.capabilities.limits


def test_the_turn_is_the_documented_agent_command_and_never_carries_the_brief(unit, task):
    adapter = openclaw.OpenClawAdapter()
    session = adapter.new_session_id(task, "prova")
    prompt = harness.prompt_text(task, "prova", unit.parent / "20-brief-prova.md")

    argv = adapter.send_argv("openclaw-finto", session, prompt)

    assert argv[1:3] == ["agent", "--agent"]
    assert _arg(argv, "--agent") == "sviluppo"
    assert _arg(argv, "--session-key") == session
    assert _arg(argv, "--message") == prompt
    assert "--json" in argv, "il turno deve lasciare il suo envelope, dove sta il costo"
    assert "contenuto segreto" not in " ".join(argv), "il brief non finisce in una riga di comando"
    assert session.startswith("sup-") and task in session, "la chiave dice compito e unità"


def test_the_key_the_engine_chooses_is_the_one_the_journal_records(task):
    adapter = openclaw.OpenClawAdapter()
    first = adapter.new_session_id(task, "prova")
    assert first == adapter.new_session_id(task, "prova")
    assert adapter.session_key(first) == first
    with pytest.raises(base.AdapterError, match="chiave di sessione OpenClaw non valida"):
        adapter.session_key("sup-prova; rm -rf /")


def test_a_copy_outside_the_agents_workspace_is_refused_with_the_clean_way(unit, lane, task, monkeypatch):
    recorder = Recorder(lane / "altrove")
    monkeypatch.setattr(openclaw.processes, "captured", recorder.captured)

    with pytest.raises(base.AdapterError, match="--admin") as caught:
        openclaw.OpenClawAdapter().check_folder(unit)

    message = str(caught.value)
    assert "fuori dal workspace" in message
    assert "agents.entries.sviluppo.workspace" in message, "il rifiuto dice la strada pulita"


def test_a_copy_inside_the_agents_workspace_is_accepted(unit, lane, task, monkeypatch):
    recorder = Recorder(unit.parent)
    monkeypatch.setattr(openclaw.processes, "captured", recorder.captured)

    openclaw.OpenClawAdapter().check_folder(unit)

    assert recorder.calls and "agents" in recorder.calls[0]


# --- la sessione e la misura -------------------------------------------------


def test_a_session_is_found_by_the_key_the_engine_chose(task, unit, monkeypatch):
    recorder = Recorder(unit.parent)
    recorder.key = openclaw.OpenClawAdapter().new_session_id(task, "prova")
    recorder.rows = [recorder.row()]
    monkeypatch.setattr(openclaw.processes, "captured", recorder.captured)

    found = openclaw.OpenClawAdapter().find_session(unit, 0, expected=recorder.key, wait_s=0)

    assert found is not None
    assert found.id == f"agent:sviluppo:{recorder.key}", "la chiave scelta dal motore, non un id indovinato"
    assert found.created_ms == recorder.updated_at - 60_000


def test_a_session_the_harness_no_longer_lists_is_said_gone(task, unit, monkeypatch):
    recorder = Recorder(unit.parent)
    monkeypatch.setattr(openclaw.processes, "captured", recorder.captured)

    with pytest.raises(SessionMissing, match="non è nell'elenco"):
        openclaw.OpenClawAdapter().measure("sup-altra-prova")


def test_the_money_of_a_session_is_what_its_turns_printed(task, unit, monkeypatch):
    recorder = Recorder(unit.parent)
    adapter = openclaw.OpenClawAdapter()
    session = adapter.new_session_id(task, "prova")
    recorder.key = session
    recorder.rows = [recorder.row()]
    monkeypatch.setattr(openclaw.processes, "captured", recorder.captured)
    log = adapter.turn_log(session)
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(
        json.dumps({"status": "ok", "meta": {"agentMeta": {
            "provider": "openai", "model": "gpt-6-astra", "costUsd": 0.25,
            "usage": {"input": 120, "output": 8},
        }}}) + "\n"
        + json.dumps({"ok": True, "status": "ok", "costUsd": 0.5, "usage": {"input": 3, "output": 1}}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(openclaw.processes, "rows", lambda: [])

    facts = adapter.measure(session)

    assert facts.cost_usd == 0.75, "il costo è la somma degli envelope, in entrambe le forme documentate"
    assert (facts.tokens_in, facts.tokens_out, facts.tokens_total) == (120, 8, 128)
    assert facts.model == "openai/gpt-6-astra"
    assert facts.outcome == "completed"
    assert facts.stopped is True, "nessun processo con quella chiave: il turno è finito"


def test_a_turn_still_working_is_not_called_finished(task, unit, monkeypatch):
    recorder = Recorder(unit.parent)
    adapter = openclaw.OpenClawAdapter()
    session = adapter.new_session_id(task, "prova")
    recorder.key = session
    recorder.rows = [recorder.row()]
    monkeypatch.setattr(openclaw.processes, "captured", recorder.captured)
    monkeypatch.setattr(
        openclaw.processes, "rows",
        lambda: [{"pid": 7, "command_line": f"cmd.exe /c openclaw.cmd agent --session-key {session}"}],
    )

    facts = adapter.measure(session)

    assert facts.outcome == "working"
    assert facts.stopped is False


def test_no_cost_printed_leaves_the_number_absent_and_says_why(task, unit, monkeypatch):
    recorder = Recorder(unit.parent)
    adapter = openclaw.OpenClawAdapter()
    session = adapter.new_session_id(task, "prova")
    recorder.key = session
    recorder.rows = [recorder.row()]
    monkeypatch.setattr(openclaw.processes, "captured", recorder.captured)
    monkeypatch.setattr(openclaw.processes, "rows", lambda: [])

    facts = adapter.measure(session)

    assert facts.cost_usd is None, "un costo che nessun turno ha stampato non diventa zero"
    assert "valgono i token" in facts.cost_note


def test_a_failed_turn_is_read_from_its_own_envelope(task, unit, monkeypatch):
    recorder = Recorder(unit.parent)
    adapter = openclaw.OpenClawAdapter()
    session = adapter.new_session_id(task, "prova")
    recorder.key = session
    recorder.rows = [recorder.row(status="failed")]
    monkeypatch.setattr(openclaw.processes, "captured", recorder.captured)
    monkeypatch.setattr(openclaw.processes, "rows", lambda: [])
    log = adapter.turn_log(session)
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(
        json.dumps({"ok": False, "status": "error",
                    "error": {"type": "rate_limit", "message": "429 from provider"}}) + "\n",
        encoding="utf-8",
    )

    turn = adapter.last_turn(session)

    assert turn.failed is True
    assert turn.error_type == "rate_limit"


def test_closing_the_session_archives_it_and_a_refusal_is_one_line(task, unit, monkeypatch):
    recorder = Recorder(unit.parent)
    adapter = openclaw.OpenClawAdapter()
    session = adapter.new_session_id(task, "prova")
    monkeypatch.setattr(openclaw.processes, "captured", recorder.captured)

    assert "archiviata" in adapter.close_session(session)
    assert "archive" in recorder.calls[-1] and session in recorder.calls[-1]

    recorder.fail = {"archive": (1, "gateway not connected", "")}
    line = adapter.close_session(session)
    assert "non archiviata" in line and "gateway not connected" in line


# --- il turno dall'inizio alla fine ------------------------------------------


def test_run_on_openclaw_starts_a_turn_and_attaches_the_session_it_chose(task, unit, monkeypatch):
    recorder = Recorder(unit.parent)
    monkeypatch.setattr(openclaw.processes, "captured", recorder.captured)
    turns: list[dict] = []

    def turn(session, prompt, folder, model=""):
        recorder.key = session
        recorder.rows = [recorder.row()]
        turns.append({"session": session, "prompt": prompt, "folder": folder, "model": model})
        return SimpleNamespace(pid=4321)

    result = harness.run_unit(
        task, "prova", harness="openclaw", runner=turn,
        opener=lambda *a, **k: pytest.fail("nessuna finestra: OpenClaw ha le sue sessioni"),
        wait_s=0,
    )

    assert result["mode"] == "run"
    assert result["session"] == f"agent:sviluppo:{recorder.key}"
    assert result["pid"] == 4321
    assert len(turns) == 1, "un turno solo, non una scheda per tentativo"
    assert "contenuto segreto" not in turns[0]["prompt"], "il brief non viaggia nel turno"
    events = [row["event"] for row in state.read_events(task)]
    assert events == ["started", "unit_created", "unit_started", "attached"]
    assert state.read_costs(task)[-1]["harness"] == "openclaw"


def test_the_cli_runs_a_unit_on_openclaw_from_start_to_finish(lane, tmp_path, capsys, monkeypatch):
    recorder = Recorder(lane)
    monkeypatch.setattr(processes, "captured", recorder.captured)
    monkeypatch.setattr(harness.subprocess, "Popen", recorder.popen)
    repo = build(tmp_path / "sandbox")
    brief = tmp_path / "brief.md"
    brief.write_text("# Brief\n\nsolo fatti.\n", encoding="utf-8")
    task = state.start("prova openclaw")["id"]
    capsys.readouterr()

    assert cli.main([
        "sup", "unit", task, "prova", "--repo", str(repo.path), "--base", "main",
        "--brief", str(brief), "--allow", "docs/*.md",
    ]) == 0
    assert cli.main(["sup", "run", task, "prova", "--harness", "openclaw"]) == 0

    out = capsys.readouterr().out
    assert "lavoro inviato alla sessione agent:sviluppo:sup-" in out
    assert "nessuna scheda nuova" in out
    events = [row["event"] for row in state.read_events(task)]
    assert events == ["started", "unit_created", "unit_started", "attached"]
    row = state.read_costs(task)[-1]
    assert row["harness"] == "openclaw" and row["session"].startswith("agent:sviluppo:sup-")
    assert row["cost_usd"] is None, "il turno non ha ancora stampato il suo envelope"


def test_an_openclaw_session_key_is_not_refused_by_the_cost_ledger(task, unit, monkeypatch):
    recorder = Recorder(unit.parent)
    session = f"agent:sviluppo:{openclaw.OpenClawAdapter().new_session_id(task, 'prova')}"
    recorder.key = session
    recorder.rows = [recorder.row()]
    monkeypatch.setattr(openclaw.processes, "captured", recorder.captured)
    monkeypatch.setattr(openclaw.processes, "rows", lambda: [])

    row = costs.record(task, "prova", "esecutore", session, harness="openclaw")

    assert row["session"] == session
    assert row["tokens_in"] == 120
