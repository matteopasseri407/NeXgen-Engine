"""La finestra dell'esecutore e la sessione che nasce lì: nessun OpenCode vero, nessuna finestra vera.

Quello che si prova è l'argv che il motore costruisce, il fatto che il brief non
finisca in una riga di comando, e la ricerca della sessione per cartella e ora.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from nexgen_local import supervision_harness as harness
from nexgen_local import supervision_costs as costs
from nexgen_local import supervision_policy as policy
from nexgen_local import supervision_state as state


@pytest.fixture
def lane(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "lane_state_dir", lambda: tmp_path / "lane")
    return tmp_path / "lane" / "supervision"


@pytest.fixture
def task(lane):
    return state.start("prova fase 2")["id"]


@pytest.fixture
def unit(task, lane, tmp_path):
    """La copia e il brief di un'unità, senza git: la finestra non ne ha bisogno."""
    folder = lane / task / "23-unita-prova"
    folder.mkdir(parents=True)
    (lane / task / "20-brief-prova.md").write_text(
        "# Brief\n\ncontenuto segreto del brief\n", encoding="utf-8",
    )
    state.append_event(
        task, "unit_created", unit="prova", worktree=str(folder), branch=f"sup/{task}/prova",
        base="0" * 40, allow=["docs/*.md"], verify="", repo=str(tmp_path),
    )
    return folder


def test_run_opens_the_harness_on_the_unit_and_attaches_its_session(task, unit, monkeypatch):
    opened: dict = {}

    def fake_open(title, argv, *, cwd, marker=""):
        opened.update({"title": title, "argv": list(argv), "cwd": cwd, "marker": marker})
        return {"title": title, "launcher": "finto", "pid": 4242}

    recorded: dict = {}

    def fake_record(task_id, unit_name, role, session, **kwargs):
        recorded.update({"task": task_id, "unit": unit_name, "role": role, "session": session, **kwargs})
        return {"session": session, "unit": unit_name, "cost_usd": 0.5, "id": session}

    monkeypatch.setattr(harness, "_opencode_executable", lambda: "opencode-finto")
    monkeypatch.setattr(harness.costs, "record", fake_record)
    sessions = [{"id": "ses_nuova", "directory": str(unit), "created": 10**13}]

    result = harness.run_unit(task, "prova", opener=fake_open, lister=lambda: sessions, wait_s=0)

    assert opened["argv"][0] == "opencode-finto"
    assert opened["argv"][1] == str(unit)
    assert opened["argv"][2] == "--prompt"
    assert "--auto" not in opened["argv"], "nessuna auto-approvazione, mai"
    prompt = opened["argv"][3]
    assert str(unit.parent / "20-brief-prova.md") in prompt, "il prompt punta al brief"
    assert "contenuto segreto del brief" not in prompt, "il brief non finisce in una riga di comando"
    assert len(prompt) < 800, "un prompt breve che punta al brief, non il brief"
    assert opened["cwd"] == unit
    assert result["session"]["id"] == "ses_nuova"
    assert recorded["session"] == "ses_nuova"
    assert recorded["role"] == "esecutore"
    assert [row["event"] for row in state.read_events(task)] == [
        "started", "unit_created", "unit_started", "attached",
    ]


def test_run_says_so_when_no_session_can_be_told_apart(task, unit, monkeypatch):
    monkeypatch.setattr(harness, "_opencode_executable", lambda: "opencode-finto")
    monkeypatch.setattr(harness.costs, "record", lambda *a, **k: pytest.fail("non deve agganciare niente"))

    result = harness.run_unit(
        task, "prova",
        opener=lambda title, argv, *, cwd, marker="": {"title": title, "launcher": "finto", "pid": 1},
        lister=lambda: [],
        wait_s=0,
    )

    assert result["session"] is None
    assert [row["event"] for row in state.read_events(task)] == ["started", "unit_created", "unit_started"]


def test_run_refuses_a_unit_whose_copy_is_gone(task, unit, monkeypatch):
    harness_unit = unit
    harness_unit.rmdir()
    with pytest.raises(harness.HarnessError, match="copia dell'unità sparita"):
        harness.run_unit(task, "prova", opener=lambda *a, **k: None, lister=lambda: [], wait_s=0)


def test_the_session_list_is_asked_from_the_copys_own_project(tmp_path, monkeypatch):
    """Il harness elenca le sessioni del progetto in cui gira: la domanda si fa dalla copia.

    Una copia che viene da un altro repository non comparirebbe mai nell'elenco del
    progetto del motore, e `sup run` direbbe «sessione non determinata» per sempre.
    """
    folder = tmp_path / "copia"
    folder.mkdir()
    seen: dict = {}

    def fake_run(argv, **kwargs):
        seen.update({"argv": list(argv), "cwd": kwargs.get("cwd")})
        return SimpleNamespace(returncode=0, stdout="[]", stderr="")

    monkeypatch.setattr(harness, "_opencode_executable", lambda: "opencode-finto")
    monkeypatch.setattr(harness.subprocess, "run", fake_run)

    assert harness._list_sessions(folder) == []
    assert seen["cwd"] == str(folder)
    assert "session" in seen["argv"] and "list" in seen["argv"]


def test_find_session_takes_the_newest_session_in_that_folder(tmp_path):
    folder = tmp_path / "copia"
    folder.mkdir()
    sessions = [
        {"id": "ses_nuova", "directory": str(folder), "created": 2_000_000},
        {"id": "ses_vecchia", "directory": str(folder), "created": 1_000_000},
        {"id": "ses_altrove", "directory": str(tmp_path / "altrove"), "created": 3_000_000},
    ]

    found = harness.find_session(folder, 1_500_000, wait_s=0, lister=lambda: sessions)

    assert found["id"] == "ses_nuova"


def test_find_session_ignores_a_session_older_than_the_window(tmp_path):
    folder = tmp_path / "copia"
    folder.mkdir()
    sessions = [{"id": "ses_vecchia", "directory": str(folder), "created": 1_000_000}]

    assert harness.find_session(folder, 2_000_000, wait_s=0, lister=lambda: sessions) is None


def test_find_session_waits_for_the_session_to_appear(tmp_path):
    folder = tmp_path / "copia"
    folder.mkdir()
    answers = [[], [{"id": "ses_tardi", "directory": str(folder), "created": 5_000_000}]]
    times = iter([0.0, 0.0, 1.0, 2.0, 3.0])

    found = harness.find_session(
        folder, 1_000_000, wait_s=10, poll_s=1, lister=lambda: answers.pop(0) if answers else [],
        clock=lambda: next(times), sleep=lambda seconds: None,
    )

    assert found["id"] == "ses_tardi"


def test_find_session_gives_up_when_nothing_appears(tmp_path):
    folder = tmp_path / "copia"
    folder.mkdir()
    times = iter([0.0, 5.0])

    assert harness.find_session(
        folder, 1_000_000, wait_s=5, poll_s=1, lister=lambda: [], clock=lambda: next(times),
        sleep=lambda seconds: None,
    ) is None


def test_the_prompt_names_where_the_report_goes():
    brief = Path("/tmp/compito/20-brief-prova.md")
    prompt = harness.prompt_text("compito", "prova", brief)
    assert "30-resoconto-prova.md" in prompt
    assert "Niente push" in prompt


def _attached(task: str, unit: str, session: str) -> None:
    state.append_event(task, "attached", unit=unit, role="esecutore", harness="opencode", session=session)


@pytest.fixture
def settings(lane):
    """L'elenco dei modelli consentiti, scritto dove la lane lo legge davvero."""
    def apply(patterns) -> None:
        (lane.parent / policy.CONFIG_FILE).write_text(
            json.dumps({"supervision": {"allowed_models": list(patterns)}}), encoding="utf-8",
        )

    return apply


def test_a_free_session_takes_the_work_without_a_new_window(task, unit, monkeypatch):
    _attached(task, "prima", "ses_libera")
    opened: list = []
    runs: list = []

    def fake_runner(session, prompt, folder, model=""):
        runs.append((session, prompt, folder))
        return SimpleNamespace(pid=777)

    monkeypatch.setattr(harness.costs, "record", lambda *a, **k: {"session": a[3], "cost_usd": 0.1})

    result = harness.run_unit(
        task, "prova",
        opener=lambda *a, **k: opened.append(a) or {"title": "mai"},
        runner=fake_runner,
        measure=lambda session: SimpleNamespace(stopped=True),
        lister=lambda: [],
    )

    assert result["mode"] == "run"
    assert runs and runs[0][0] == "ses_libera"
    assert opened == [], "una sessione libera non deve aprire una scheda nuova"
    assert str(unit) in runs[0][2].__str__()
    events = [row["event"] for row in state.read_events(task)]
    assert events == ["started", "unit_created", "attached", "unit_started"]


def test_a_session_on_a_model_outside_the_list_is_refused_before_any_work(
    task, unit, monkeypatch, settings,
):
    """Con un elenco configurato, la sessione sbagliata non riceve niente e il diario la nomina."""
    settings(["opencode-go/*"])
    _attached(task, "prima", "ses_libera")
    monkeypatch.setattr(harness.costs, "record", lambda *a, **k: pytest.fail("un rifiuto non aggancia"))

    result = harness.run_unit(
        task, "prova",
        opener=lambda *a, **k: pytest.fail("nessuna scheda nuova"),
        runner=lambda *a: pytest.fail("nessun lavoro alla sessione sbagliata"),
        measure=lambda session: SimpleNamespace(stopped=True, model="altro-provider/modello"),
        lister=lambda: [],
    )

    assert result["mode"] == "refused"
    assert result["sent"] is False
    assert "altro-provider/modello" in result["reason"]
    assert "opencode-go/*" in result["reason"]
    events = state.read_events(task)
    assert events[-1]["event"] == "unit_model_refused"
    assert events[-1]["model"] == "altro-provider/modello"
    assert not any(row["event"] == "unit_started" for row in events)
    unit_record = next(item for item in state.status(task)["units"] if item["unit"] == "prova")
    assert unit_record["state"] == "rifiutata"
    assert unit_record["model"] == "altro-provider/modello"


def test_a_session_on_an_allowed_model_takes_the_work(task, unit, monkeypatch, settings):
    settings(["opencode-go/*"])
    _attached(task, "prima", "ses_libera")
    runs: list = []
    monkeypatch.setattr(harness.costs, "record", lambda *a, **k: {"session": a[3], "cost_usd": 0.1})

    result = harness.run_unit(
        task, "prova",
        opener=lambda *a, **k: pytest.fail("nessuna scheda nuova"),
        runner=lambda session, prompt, folder, model="": runs.append(session) or SimpleNamespace(pid=9),
        measure=lambda session: SimpleNamespace(
            stopped=True, model="opencode-go/deepseek-v4.1-flash#max",
        ),
        lister=lambda: [],
    )

    assert result["mode"] == "run"
    assert runs == ["ses_libera"]
    assert not any(row["event"] == "unit_model_refused" for row in state.read_events(task))


def test_a_first_tab_on_a_disallowed_model_is_named_and_not_attached(
    task, unit, monkeypatch, settings,
):
    """La scheda appena aperta si legge prima di agganciarla: il modello sbagliato resta fuori dal compito."""
    settings(["opencode-go/*"])
    sessions = [{"id": "ses_nuova", "directory": str(unit), "created": 10**13}]
    monkeypatch.setattr(harness, "_opencode_executable", lambda: "opencode-finto")
    monkeypatch.setattr(harness.costs, "record", lambda *a, **k: pytest.fail("non deve agganciare"))

    result = harness.run_unit(
        task, "prova",
        opener=lambda title, argv, *, cwd, marker="": {"title": title, "launcher": "finto", "pid": 5},
        lister=lambda: sessions,
        measure=lambda session: SimpleNamespace(model="altro-provider/modello"),
        wait_s=0,
    )

    assert result["mode"] == "refused"
    assert result["sent"] is True
    assert "altro-provider/modello" in result["reason"]
    assert state.harness_session(task, "opencode") == "", "una scheda rifiutata non diventa la sessione del compito"
    assert [row["event"] for row in state.read_events(task)][-1] == "unit_model_refused"


def test_a_busy_session_puts_the_unit_in_the_queue(task, unit, monkeypatch):
    _attached(task, "prima", "ses_occupata")
    monkeypatch.setattr(harness.costs, "record", lambda *a, **k: pytest.fail("niente aggancio quando è in coda"))

    def never(session, prompt, folder):
        raise AssertionError("una sessione occupata non deve ricevere il lavoro")

    result = harness.run_unit(
        task, "prova",
        opener=lambda *a, **k: pytest.fail("nessuna scheda nuova quando c'è già una sessione"),
        runner=never,
        measure=lambda session: SimpleNamespace(stopped=False),
        lister=lambda: [],
    )

    assert result["mode"] == "queued"
    assert result["reason"] == "sessione occupata"
    events = state.read_events(task)
    assert [row["event"] for row in events] == ["started", "unit_created", "attached", "unit_queued"]
    assert events[-1]["session"] == "ses_occupata"
    states = {unit["unit"]: unit["state"] for unit in state.status(task)["units"]}
    assert states["prova"] == "in coda"


def test_a_unit_that_entered_a_session_names_its_harness(task, unit, monkeypatch):
    _attached(task, "prima", "ses_libera")
    monkeypatch.setattr(harness.costs, "record", lambda *a, **k: {"session": a[3], "cost_usd": 0.0})

    harness.run_unit(
        task, "prova",
        opener=lambda *a, **k: pytest.fail("una sessione libera non apre schede"),
        runner=lambda *a: SimpleNamespace(pid=1),
        measure=lambda session: SimpleNamespace(stopped=True),
        lister=lambda: [],
    )

    units = {record["unit"]: record for record in state.status(task)["units"]}
    assert units["prova"]["harness"] == "opencode", "lo stato deve dire in quale harness è entrata"
    assert units["prova"]["session"] == "ses_libera"
    assert units["prova"]["state"] == "in corso"


def test_a_session_the_export_says_is_gone_opens_the_one_new_tab(task, unit, monkeypatch):
    """La sessione non esiste più: si scrive nel diario e si apre la scheda, una volta sola."""
    _attached(task, "prima", "ses_sparita")
    opened: list = []
    sessions = [{"id": "ses_nuova", "directory": str(unit), "created": 10**13}]
    calls: list[str] = []

    def broken(session):
        calls.append(session)
        raise harness.costs.SessionMissing("l'export dice che non esiste")

    monkeypatch.setattr(harness, "_opencode_executable", lambda: "opencode-finto")
    monkeypatch.setattr(
        harness.costs, "record",
        lambda task_id, unit_name, role, session_id, **kwargs: {"session": session_id, "unit": unit_name, "cost_usd": 0.0},
    )

    result = harness.run_unit(
        task, "prova",
        opener=lambda title, argv, *, cwd, marker="": opened.append((title, argv)) or {
            "title": title, "launcher": "finto", "pid": 3,
        },
        runner=lambda *a: pytest.fail("una sessione sparita non riceve il lavoro"),
        measure=broken, lister=lambda: sessions, wait_s=0,
    )

    assert result["mode"] == "tab"
    assert len(opened) == 1, "una scheda sola, non una per tentativo"
    assert calls == ["ses_sparita"], "un export che dice «non esiste» non si ritenta"
    events = [row["event"] for row in state.read_events(task)]
    assert events == [
        "started", "unit_created", "attached", "harness_session_lost", "unit_started", "attached",
    ]
    assert state.harness_session(task, "opencode") == "ses_nuova"
    lost = next(row for row in state.read_events(task) if row["event"] == harness.state.LOST_EVENT)
    assert lost["session"] == "ses_sparita"
    assert "non esiste" in lost["reason"]


def test_a_transient_export_failure_is_retried_before_the_session_is_called_lost(task, unit, monkeypatch):
    """Un timeout dell'export non dice che la sessione è sparita: si ritenta una volta."""
    _attached(task, "prima", "ses_viva")
    calls: list[str] = []
    facts = SimpleNamespace(stopped=True)

    def flaky(session):
        calls.append(session)
        if len(calls) == 1:
            raise harness.costs.CostError("export della sessione non riuscito (TimeoutExpired)")
        return facts

    opened: list = []
    monkeypatch.setattr(harness.costs, "record", lambda *a, **k: {"session": a[3], "cost_usd": 0.0})

    result = harness.run_unit(
        task, "prova",
        opener=lambda *a, **k: opened.append(a) or {"title": "mai"},
        runner=lambda *a: SimpleNamespace(pid=11),
        measure=flaky, lister=lambda: [], wait_s=0,
    )

    assert result["mode"] == "run"
    assert calls == ["ses_viva", "ses_viva"], "un secondo tentativo, poi si decide"
    assert opened == [], "una sessione viva non apre schede"
    assert not any(row["event"] == "harness_session_lost" for row in state.read_events(task))


def test_two_transient_export_failures_open_nothing_and_say_retry(task, unit, monkeypatch):
    """Due errori transitori: la sessione non è persa, è non verificabile, e nessuna scheda si apre."""
    _attached(task, "prima", "ses_viva")
    calls: list[str] = []

    def broken(session):
        calls.append(session)
        raise harness.costs.CostError("export della sessione non riuscito (TimeoutExpired)")

    monkeypatch.setattr(harness.windows, "open_window", lambda *a, **k: pytest.fail("nessuna scheda"))
    monkeypatch.setattr(harness.costs, "record", lambda *a, **k: pytest.fail("niente aggancio"))

    with pytest.raises(harness.HarnessError, match="non verificabile, riprova"):
        harness.run_unit(
            task, "prova",
            opener=lambda *a, **k: pytest.fail("nessuna scheda"),
            runner=lambda *a: pytest.fail("nessun invio"),
            measure=broken, lister=lambda: [], wait_s=0,
        )

    assert calls == ["ses_viva", "ses_viva"]
    assert not any(row["event"] == "harness_session_lost" for row in state.read_events(task))


def test_corrections_with_a_lost_session_open_the_tab_and_keep_the_round(task, unit, monkeypatch):
    _attached(task, "prima", "ses_sparita")
    review_file = unit.parent / "45-revisione-prova-2.md"
    review_file.write_text("# Revisione\n\ncorrezioni\n", encoding="utf-8")
    opened: list = []
    sessions = [{"id": "ses_nuova", "directory": str(unit), "created": 10**13}]

    def broken(session):
        raise harness.costs.SessionMissing("sessione sparita")

    monkeypatch.setattr(harness, "_opencode_executable", lambda: "opencode-finto")
    monkeypatch.setattr(
        harness.costs, "record",
        lambda task_id, unit_name, role, session_id, **kwargs: {"session": session_id, "unit": unit_name, "cost_usd": 0.0},
    )

    result = harness.send_corrections(
        task, "prova", review_file, 2,
        opener=lambda title, argv, *, cwd, marker="": opened.append(argv) or {"title": title, "launcher": "finto", "pid": 4},
        runner=lambda *a: pytest.fail("la sessione sparita non riceve le correzioni"),
        measure=broken, lister=lambda: sessions, wait_s=0,
    )

    assert result["mode"] == "tab"
    assert str(review_file) in opened[0][3], "la scheda nuova punta al file di revisione"
    events = [row["event"] for row in state.read_events(task)]
    assert events == [
        "started", "unit_created", "attached", "harness_session_lost", "unit_reworked", "attached",
    ]
    reworked = next(row for row in state.read_events(task) if row["event"] == "unit_reworked")
    assert reworked["round"] == 2


def test_one_shot_runs_are_launched_detached_with_the_session(tmp_path, monkeypatch):
    seen: dict = {}

    def fake_popen(argv, **kwargs):
        seen.update({"argv": list(argv), "kwargs": kwargs})
        return SimpleNamespace(pid=1234)

    monkeypatch.setattr(harness, "_opencode_executable", lambda: "opencode-finto")
    monkeypatch.setattr(harness.subprocess, "Popen", fake_popen)

    harness._run_in_session("ses_libera", "leggi il brief", tmp_path)

    assert seen["argv"] == ["opencode-finto", "run", "-s", "ses_libera", "leggi il brief"]
    assert "--auto" not in seen["argv"]
    assert seen["kwargs"]["stdout"] is not None
    assert seen["kwargs"]["stdin"] is not None
    assert seen["kwargs"]["cwd"] == tmp_path


def test_a_model_chosen_for_one_delivery_travels_on_the_command_line(tmp_path, monkeypatch):
    """La via d'uscita da un provider che rifiuta il modello: `run -m`, e nient'altro.

    `opencode run` non ha `--variant`, quindi il modello è l'unica cosa che parte.
    """
    seen: dict = {}

    def fake_popen(argv, **kwargs):
        seen["argv"] = list(argv)
        return SimpleNamespace(pid=1234)

    monkeypatch.setattr(harness, "_opencode_executable", lambda: "opencode-finto")
    monkeypatch.setattr(harness.subprocess, "Popen", fake_popen)

    harness._run_in_session(
        "ses_libera", "leggi le correzioni", tmp_path, "opencode-go/deepseek-v4.1-flash",
    )

    assert seen["argv"] == [
        "opencode-finto", "run", "-s", "ses_libera",
        "-m", "opencode-go/deepseek-v4.1-flash", "leggi le correzioni",
    ]
    assert "--variant" not in seen["argv"]


def test_corrections_can_be_resent_on_a_model_the_owner_names(task, unit, monkeypatch):
    _attached(task, "prima", "ses_libera")
    review_file = unit.parent / "45-revisione-prova-1.md"
    review_file.write_text("# Revisione\n\ncorrezioni\n", encoding="utf-8")
    seen: list = []
    monkeypatch.setattr(
        harness.costs, "record",
        lambda task_id, unit_name, role, session_id, **kwargs: {
            "session": session_id, "unit": unit_name, "cost_usd": 0.0,
        },
    )

    result = harness.send_corrections(
        task, "prova", review_file, 1,
        runner=lambda session, prompt, folder, model="": seen.append(model) or SimpleNamespace(pid=7),
        measure=lambda session: SimpleNamespace(stopped=True), lister=lambda: [],
        model="opencode-go/deepseek-v4.1-flash",
    )

    assert seen == ["opencode-go/deepseek-v4.1-flash"]
    assert result["model"] == "opencode-go/deepseek-v4.1-flash"
    assert result["model_applied"] is True

def test_a_delivery_after_the_engine_closed_the_tab_reopens_it_on_that_session(task, unit, monkeypatch):
    """Dopo la chiusura il lavoro non torna invisibile: si riapre una scheda sulla stessa sessione."""
    _attached(task, "prima", "ses_libera")
    state.append_event(task, "tab_closed", role="esecutore", pids=[1])
    review_file = unit.parent / "45-revisione-prova-1.md"
    review_file.write_text("# Revisione\n\ncorrezioni\n", encoding="utf-8")
    opened: list = []
    monkeypatch.setattr(harness, "_opencode_executable", lambda: "opencode-finto")
    monkeypatch.setattr(
        harness.costs, "record",
        lambda task_id, unit_name, role, session_id, **kwargs: {
            "session": session_id, "unit": unit_name, "cost_usd": 0.0,
        },
    )

    result = harness.send_corrections(
        task, "prova", review_file, 1,
        opener=lambda title, argv, *, cwd, marker="": opened.append(argv) or {"title": title, "pid": 6},
        runner=lambda *a: pytest.fail("nessun `run` invisibile dopo la chiusura"),
        measure=lambda session: SimpleNamespace(stopped=True), lister=lambda: [], wait_s=0,
    )

    assert opened[0][:5] == ["opencode-finto", str(unit), "-s", "ses_libera", "--prompt"]
    assert str(review_file) in opened[0][5], "la scheda riapre la sessione che c'è, non ne crea una"
    assert result["mode"] == "tab" and result["reopened"] is True
    assert result["session"] == {"id": "ses_libera"}
    reworked = [row for row in state.read_events(task) if row.get("event") == "unit_reworked"]
    assert reworked[-1]["mode"] == "tab" and reworked[-1]["session"] == "ses_libera"


def test_a_delivery_with_the_tab_still_open_stays_off_the_screen(task, unit, monkeypatch):
    """La sessione ha già la sua scheda: le correzioni entrano con `run`, senza aprirne una seconda."""
    _attached(task, "prima", "ses_libera")
    review_file = unit.parent / "45-revisione-prova-1.md"
    review_file.write_text("# Revisione\n\ncorrezioni\n", encoding="utf-8")
    monkeypatch.setattr(
        harness.costs, "record",
        lambda task_id, unit_name, role, session_id, **kwargs: {
            "session": session_id, "unit": unit_name, "cost_usd": 0.0,
        },
    )
    sent: list = []

    result = harness.send_corrections(
        task, "prova", review_file, 1,
        opener=lambda *a, **k: pytest.fail("la scheda è già aperta"),
        runner=lambda session, prompt, folder, model="": sent.append(session) or SimpleNamespace(pid=3),
        measure=lambda session: SimpleNamespace(stopped=True), lister=lambda: [], wait_s=0,
    )

    assert result["mode"] == "run" and sent == ["ses_libera"]


def test_rework_to_busy_session_queues_and_never_opens_a_second_tab_in_the_same_copy(task, unit, monkeypatch):
    """Una sessione occupata al lavoro riceve le correzioni in coda; mai una seconda scheda nella stessa copia."""
    state.append_event(
        task, "attached", unit="prova", role="esecutore", harness="opencode",
        session="ses_occupata", folder=str(unit),
    )
    other_folder = unit.parent / "23-unita-seconda"
    other_folder.mkdir(parents=True, exist_ok=True)
    state.append_event(
        task, "unit_created", unit="seconda", worktree=str(other_folder),
        branch=f"sup/{task}/seconda", base="0" * 40, allow=["docs/*.md"],
    )
    state.append_event(
        task, "attached", unit="seconda", role="esecutore", harness="opencode",
        session="ses_seconda", folder=str(other_folder),
    )

    review_file = unit.parent / "45-revisione-prova-1.md"
    review_file.write_text("# Revisione\n\ncorrezioni da applicare\n", encoding="utf-8")

    opened: list = []
    sent: list = []

    def fake_measure(session):
        if session == "ses_occupata":
            return SimpleNamespace(stopped=False, model="opencode-go/m")
        return SimpleNamespace(stopped=True, model="opencode-go/m")

    result = harness.send_corrections(
        task, "prova", review_file, 1,
        opener=lambda *a, **k: opened.append(a) or {"title": "finto", "pid": 99},
        runner=lambda *a, **k: sent.append(a) or SimpleNamespace(pid=100),
        measure=fake_measure, lister=lambda: [], wait_s=0,
    )

    assert result["mode"] == "queued"
    assert result["session"] == "ses_occupata"
    assert opened == [], "non deve mai aprire una scheda nuova nella copia con sessione viva"
    assert sent == [], "nessun turno inviato con la sessione occupata"

    events = [r for r in state.read_events(task) if r.get("unit") == "prova"]
    queued = [r for r in events if r.get("event") == "unit_queued"]
    assert len(queued) == 1
    assert queued[0]["session"] == "ses_occupata"
    assert queued[0]["reason"] == "sessione occupata"
    assert not any(r.get("event") == "harness_session_foreign" for r in events)


def test_rework_on_an_unverifiable_session_queues_instead_of_opening_a_tab(task, unit, monkeypatch):
    """Una sessione che il motore non riesce a leggere non è morta: niente seconda scheda sopra di lei."""
    other_folder = unit.parent / "23-unita-altra"
    other_folder.mkdir(parents=True, exist_ok=True)
    state.append_event(
        task, "attached", unit="prova", role="esecutore", harness="opencode",
        session="ses_illeggibile", folder=str(other_folder),
    )
    review_file = unit.parent / "45-revisione-prova-1.md"
    review_file.write_text("# Revisione\n\ncorrezioni\n", encoding="utf-8")

    def unreadable(session):
        raise costs.CostError("export non leggibile")

    result = harness.send_corrections(
        task, "prova", review_file, 1,
        opener=lambda *a, **k: pytest.fail("nessuna scheda su una sessione non verificabile"),
        runner=lambda *a, **k: pytest.fail("nessun turno su una sessione non verificabile"),
        measure=unreadable, lister=lambda: [], wait_s=0,
    )

    assert result["mode"] == "queued"
    assert "non verificabile" in result["reason"]
    queued = [r for r in state.read_events(task) if r.get("event") == "unit_queued"]
    assert len(queued) == 1 and queued[0]["round"] == 1
