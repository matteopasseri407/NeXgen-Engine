"""OpenClaw su una copia di `sup`: dove il turno dell'agente gira davvero.

Un turno del Gateway esegue i suoi strumenti nella cartella dichiarata come `cwd`
dell'agente, quando il proprietario ne ha una (`agents.entries.<id>.cwd`, la
«cartella del compito» della documentazione OpenClaw), e nel workspace altrimenti.
La CLI non ha un flag per cartella, quindi `sup` può usare la sua copia solo se la
copia sta dentro una delle due radici: l'adattatore le chiede al harness stesso e
accetta la copia quando una delle due la contiene, senza mai allargare il permesso
quando una lettura fallisce.

Nessun `openclaw` vero: la fixture mette un eseguibile finto in testa al PATH e un
registratore risponde le forme documentate (`agents list --json`, `config get ...
--json`, `sessions --json`). Il turno parte da un `Popen` finto: niente processo,
niente Control UI, niente casa del proprietario.
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
from nexgen_local import supervision_harness as harness
from nexgen_local import supervision_processes as processes
from nexgen_local import supervision_state as state
from nexgen_local.supervision_adapters import base, openclaw

from supervision_repo import build

#: La domanda che porta la cartella del compito dell'agente, con la rotta della CLI.
AGENT_ENTRY = "agents.entries.sviluppo"
DEFAULTS_CWD = "agents.defaults.cwd"


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
    """Risponde come OpenClaw alle domande sulla sua configurazione, e ricorda ognuna."""

    def __init__(self, workspace: Path, *, entry=None, defaults="", unreadable=False) -> None:
        self.workspace = Path(workspace)
        self.entry = dict(entry or {})
        self.defaults = defaults
        self.unreadable = unreadable
        self.calls: list[list[str]] = []
        self.turns: list[list[str]] = []
        self.rows: list[dict] = []
        self.key = ""
        self.real = processes.captured
        self.real_popen = subprocess.Popen

    def wanted(self, key: str) -> list[list[str]]:
        """Ogni domanda fatta per questa chiave di configurazione."""
        return [argv for argv in self.calls if "config" in argv and key in argv]

    def captured(self, argv, *, timeout):
        if not _speaks_openclaw(argv):
            return self.real(argv, timeout=timeout)
        argv = list(argv)
        self.calls.append(argv)
        if "agents" in argv and "list" in argv:
            return _ok(argv, [{"id": "sviluppo", "workspace": str(self.workspace)}])
        if "config" in argv and "get" in argv:
            return self._config_get(argv, _arg(argv, "get"))
        if "sessions" in argv and "archive" in argv:
            return _ok(argv, {"archived": [_arg(argv, "archive")], "count": 1})
        if "sessions" in argv:
            return _ok(argv, {"sessions": self.rows})
        return _ok(argv, {})

    def _config_get(self, argv: list[str], key: str) -> subprocess.CompletedProcess:
        """Una chiave come la stampa `openclaw config get <chiave> --json`, o il suo rifiuto."""
        if self.unreadable:
            return _fail(argv, "config non leggibile")
        if key.startswith("agents.entries."):
            return _ok(argv, self.entry) if self.entry else _fail(argv, f"Config path is valid but unset: {key}")
        if key == DEFAULTS_CWD:
            return _ok(argv, self.defaults) if self.defaults else _fail(argv, f"Config path is valid but unset: {key}")
        return _fail(argv, f"Unknown config path: {key}")

    def popen(self, argv, **kwargs):
        if not _speaks_openclaw(argv):
            return self.real_popen(argv, **kwargs)
        self.turns.append(list(argv))
        self.key = _arg(list(argv), "--session-key")
        self.rows = [{
            "key": f"agent:sviluppo:{self.key}", "sessionId": "0192aaaa-12ab-7cd8-9ef0-1a2b3c4d5e6f",
            "model": "gpt-6-astra", "modelProvider": "openai",
            "inputTokens": 10, "outputTokens": 2, "totalTokens": 12,
            "updatedAt": 1_700_000_000_000, "sessionStartedAt": 1_699_999_940_000, "status": "done",
        }]
        return SimpleNamespace(pid=4321)


def _ok(argv: list[str], payload: object) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")


def _fail(argv: list[str], reason: str) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(argv, 1, json.dumps({"ok": False, "error": {"message": reason}}), "")


def _speaks_openclaw(argv) -> bool:
    """Solo il nome dell'eseguibile conta: un compito chiamato `-openclaw-` non rende git una domanda."""
    for part in list(argv)[:5]:
        if Path(str(part)).name.lower().startswith("openclaw"):
            return True
    return False


@pytest.fixture
def task(lane):
    return state.start("prova openclaw copia")["id"]


@pytest.fixture
def copy_of_unit(lane, task):
    """La copia e il brief di un'unità, senza git: l'adattatore non ne ha bisogno."""
    folder = state.open_task(task) / "23-unita-prova"
    folder.mkdir(parents=True)
    (folder.parent / "20-brief-prova.md").write_text("# Brief\n\nsolo fatti.\n", encoding="utf-8")
    state.append_event(
        task, "unit_created", unit="prova", worktree=str(folder), branch=f"sup/{task}/prova",
        base="0" * 40, allow=["docs/*.md"], verify="", repo=str(lane),
    )
    return folder


def task_root(folder: Path) -> Path:
    """La cartella del compito che contiene la copia dell'unità (`<task>/23-unita-<nome>`)."""
    return Path(folder).parents[1]


# --- la cartella del compito -------------------------------------------------


def test_the_task_directory_is_asked_of_the_harness_itself(tmp_path, monkeypatch):
    wanted = tmp_path / "compito"
    recorder = Recorder(tmp_path / "altrove", entry={"cwd": str(wanted)})
    monkeypatch.setattr(openclaw.processes, "captured", recorder.captured)

    assert openclaw.OpenClawAdapter().cwd_of() == wanted
    asked = recorder.wanted(AGENT_ENTRY)
    assert asked, "la cartella del compito si chiede all'agente, non si indovina"
    assert asked[0][1:4] == ["config", "get", AGENT_ENTRY], "la forma documentata: `openclaw config get <chiave> --json`"
    assert "--json" in asked[0]


def test_a_copy_under_the_agents_task_directory_is_accepted(copy_of_unit, tmp_path, monkeypatch):
    recorder = Recorder(tmp_path / "workspace", entry={"cwd": str(task_root(copy_of_unit))})
    monkeypatch.setattr(openclaw.processes, "captured", recorder.captured)

    openclaw.OpenClawAdapter().check_folder(copy_of_unit)

    assert recorder.wanted(AGENT_ENTRY), "accettata perché la cartella del compito la contiene"


def test_a_copy_equal_to_the_task_directory_itself_is_accepted(lane, tmp_path, monkeypatch):
    recorder = Recorder(tmp_path / "workspace", entry={"cwd": str(lane)})
    monkeypatch.setattr(openclaw.processes, "captured", recorder.captured)

    openclaw.OpenClawAdapter().check_folder(lane)


def test_the_agents_own_task_directory_wins_over_the_default_one(copy_of_unit, tmp_path, monkeypatch):
    recorder = Recorder(
        tmp_path / "workspace",
        entry={"cwd": str(task_root(copy_of_unit))},
        defaults=str(tmp_path / "altrove"),
    )
    monkeypatch.setattr(openclaw.processes, "captured", recorder.captured)

    openclaw.OpenClawAdapter().check_folder(copy_of_unit)

    assert recorder.wanted(AGENT_ENTRY), "vince la chiave dell'agente"
    assert recorder.wanted(DEFAULTS_CWD) == [], "il default non si legge quando l'agente ha già la sua"


def test_the_default_task_directory_is_used_when_the_agent_has_none(copy_of_unit, tmp_path, monkeypatch):
    recorder = Recorder(
        tmp_path / "workspace",
        entry={"model": "openai/gpt-6-astra"},
        defaults=str(task_root(copy_of_unit)),
    )
    monkeypatch.setattr(openclaw.processes, "captured", recorder.captured)

    openclaw.OpenClawAdapter().check_folder(copy_of_unit)

    assert recorder.wanted(DEFAULTS_CWD), "senza una cartella dell'agente vale quella dei default"


def test_a_copy_outside_both_roots_names_both_clean_ways(copy_of_unit, tmp_path, monkeypatch):
    recorder = Recorder(tmp_path / "workspace", entry={"cwd": str(tmp_path / "altrove")})
    monkeypatch.setattr(openclaw.processes, "captured", recorder.captured)

    with pytest.raises(base.AdapterError) as caught:
        openclaw.OpenClawAdapter().check_folder(copy_of_unit)

    message = str(caught.value)
    assert "fuori dal workspace" in message
    assert AGENT_ENTRY in message, "il rifiuto nomina la cartella del compito, la via meno invasiva"
    assert "agents.entries.sviluppo.workspace" in message, "e il workspace, che è la via di prima"
    assert "--admin" in message, "e la strada che richiede il permesso pieno"


def test_an_unreadable_task_directory_never_widens_the_check(copy_of_unit, tmp_path, monkeypatch):
    recorder = Recorder(tmp_path / "workspace", entry={"cwd": str(task_root(copy_of_unit))}, unreadable=True)
    monkeypatch.setattr(openclaw.processes, "captured", recorder.captured)

    with pytest.raises(base.AdapterError, match="fuori dal workspace"):
        openclaw.OpenClawAdapter().check_folder(copy_of_unit)


def test_a_garbled_answer_never_widens_the_check(copy_of_unit, tmp_path, monkeypatch):
    recorder = Recorder(tmp_path / "workspace")

    def captured(argv, *, timeout):
        if "config" in list(argv) and "get" in list(argv):
            return subprocess.CompletedProcess(list(argv), 0, "not json at all", "")
        return recorder.captured(argv, timeout=timeout)

    monkeypatch.setattr(openclaw.processes, "captured", captured)

    with pytest.raises(base.AdapterError, match="fuori dal workspace"):
        openclaw.OpenClawAdapter().check_folder(copy_of_unit)


def test_a_sibling_folder_that_only_shares_the_name_prefix_is_not_inside(lane, tmp_path, monkeypatch):
    """`supervision-2` non è dentro `supervision`: il confronto è per segmenti, non per testo."""
    recorder = Recorder(tmp_path / "workspace", entry={"cwd": str(lane)})
    monkeypatch.setattr(openclaw.processes, "captured", recorder.captured)
    sibling = lane.parent / f"{lane.name}-2" / "23-unita-prova"
    sibling.mkdir(parents=True)

    with pytest.raises(base.AdapterError, match="fuori dal workspace"):
        openclaw.OpenClawAdapter().check_folder(sibling)


def test_the_same_folder_with_a_trailing_separator_is_the_same_folder(copy_of_unit, tmp_path, monkeypatch):
    recorder = Recorder(tmp_path / "workspace", entry={"cwd": str(task_root(copy_of_unit))})
    monkeypatch.setattr(openclaw.processes, "captured", recorder.captured)

    openclaw.OpenClawAdapter().check_folder(Path(f"{copy_of_unit}{os.sep}"))


def test_an_agent_the_harness_does_not_list_is_a_refusal_not_a_pass(tmp_path, monkeypatch):
    """Un agente che il harness non elenca non apre niente: la lettura fallita non è un permesso."""
    monkeypatch.setattr(
        openclaw.processes, "captured",
        lambda argv, *, timeout: _ok(list(argv), [{"id": "altro", "workspace": str(tmp_path)}]),
    )

    with pytest.raises(base.AdapterError, match="agente OpenClaw sconosciuto"):
        openclaw.OpenClawAdapter().check_folder(tmp_path)


def test_the_capability_line_says_where_a_turn_can_run():
    line = registry.get("openclaw").capabilities.line()

    assert "workspace" in line and "cwd" in line, "il limite dice tutte e due le radici, non una"


# --- il turno, dalla parte del motore ---------------------------------------


def test_run_starts_a_turn_in_a_copy_the_task_directory_covers(lane, tmp_path, capsys, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    recorder = Recorder(workspace, entry={"cwd": str(lane)})
    monkeypatch.setattr(processes, "captured", recorder.captured)
    monkeypatch.setattr(harness.subprocess, "Popen", recorder.popen)
    repo = build(tmp_path / "sandbox")
    brief = tmp_path / "brief.md"
    brief.write_text("# Brief\n\nsolo fatti.\n", encoding="utf-8")
    task = state.start("prova openclaw copia")["id"]
    capsys.readouterr()

    assert cli.main([
        "sup", "unit", task, "prova", "--repo", str(repo.path), "--base", "main",
        "--brief", str(brief), "--allow", "docs/*.md",
    ]) == 0
    assert cli.main(["sup", "run", task, "prova", "--harness", "openclaw"]) == 0

    out = capsys.readouterr().out
    assert "lavoro inviato alla sessione agent:sviluppo:sup-" in out
    record = state.unit_record(task, "prova")
    assert not str(record["worktree"]).lower().startswith(str(workspace).lower()), (
        "la copia sta fuori dal workspace dell'agente: è il caso che prima veniva rifiutato"
    )
    assert recorder.wanted(AGENT_ENTRY), "ed è accettata perché la cartella del compito la contiene"
    events = [row["event"] for row in state.read_events(task)]
    assert events == ["started", "unit_created", "unit_started", "attached"]
    assert state.read_costs(task)[-1]["harness"] == "openclaw"
