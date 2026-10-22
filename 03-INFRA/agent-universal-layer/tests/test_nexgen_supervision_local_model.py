"""Il server del modello locale: profilo dichiarato, memoria verificata, marcatore, spegnimento.

Nessun llama-server vero: il processo, la sonda `/health` e la memoria sono
iniettati. Quello che si prova è la regola del proprietario: il motore avvia solo
ciò che la lane descrive, rifiuta quando la memoria non basta o non si legge, e
spegne solo il server che ha avviato lui e che nessun'altra unità usa.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from nexgen_local import supervision_local_model as local
from nexgen_local import supervision_state as state


@pytest.fixture
def lane(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "lane_state_dir", lambda: tmp_path / "lane")
    (tmp_path / "lane").mkdir(parents=True, exist_ok=True)
    return tmp_path / "lane"


@pytest.fixture
def task(lane):
    return state.start("prova modello locale")["id"]


@pytest.fixture
def chosen():
    return local.Profile(
        model="locale/prova", command=("server-locale", "--port", "8099"),
        health="http://127.0.0.1:8099/health", ram_free_gb=2.0, vram_free_gb=0.0,
        start_timeout_s=1.0, stop_timeout_s=1.0,
    )


def _config(tmp_path: Path, section: dict) -> Path:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"supervision": {"local_model": section}}), encoding="utf-8")
    return path


def test_the_profile_reads_the_lane_and_builds_the_health_address(tmp_path):
    path = _config(tmp_path, {"model": "locale/prova", "command": ["server", "-m", "x"], "port": 8080})

    chosen = local.profile(path)

    assert chosen.model == "locale/prova"
    assert chosen.command == ("server", "-m", "x")
    assert chosen.health == "http://127.0.0.1:8080/health"
    assert chosen.ram_free_gb == 2.0 and chosen.vram_free_gb == 0.0


def test_the_profile_keeps_an_explicit_health_and_splits_a_string_command(tmp_path):
    path = _config(tmp_path, {
        "command": "server -m modello --port 8080", "health": "http://127.0.0.1:9000/up",
        "ram_free_gb": 4, "vram_free_gb": 6, "start_timeout_s": 10, "stop_timeout_s": 2,
    })

    chosen = local.profile(path)

    assert chosen.command[0] == "server" and "--port" in chosen.command
    assert chosen.health == "http://127.0.0.1:9000/up"
    assert chosen.ram_free_gb == 4.0 and chosen.vram_free_gb == 6.0


@pytest.mark.parametrize("section, message", [
    ({}, "command"),
    ({"command": [], "port": 8080}, "command"),
    ({"command": ["server"]}, "senza `health` né `port`"),
    ({"command": ["server"], "port": "otto"}, "deve essere una porta"),
    ({"command": ["server"], "port": 8080, "ram_free_gb": -1}, "numero non negativo"),
    ({"command": ["server"], "port": 8080, "vram_free_gb": "tanta"}, "numero non negativo"),
    ({"command": ["server"], "port": 8080, "start_timeout_s": True}, "numero non negativo"),
])
def test_a_profile_that_cannot_start_a_server_is_refused_by_name(tmp_path, section, message):
    with pytest.raises(local.LocalModelError, match=message):
        local.profile(_config(tmp_path, section))


def test_the_memory_gate_refuses_when_ram_is_below_the_threshold(chosen):
    with pytest.raises(local.LocalModelError, match="RAM libera 1.0 GB sotto la soglia"):
        local.check_memory(chosen, ram_reader=lambda: 1.0)


def test_an_unreadable_memory_is_a_refusal_not_a_pass(chosen):
    def broken():
        raise OSError("memoria ignota")

    with pytest.raises(local.LocalModelError, match="non verificabile, non avvio"):
        local.check_memory(chosen, ram_reader=broken)


def test_a_declared_vram_threshold_on_an_unreadable_gpu_refuses(chosen):
    declared = local.Profile(**{**chosen.__dict__, "vram_free_gb": 4.0})

    with pytest.raises(local.LocalModelError, match="VRAM non leggibile"):
        local.check_memory(declared, ram_reader=lambda: 8.0, vram_reader=lambda: None)
    with pytest.raises(local.LocalModelError, match="VRAM libera 2.0 GB sotto"):
        local.check_memory(declared, ram_reader=lambda: 8.0, vram_reader=lambda: 2.0)


def test_the_memory_gate_says_the_room_it_checked(chosen):
    notes = local.check_memory(
        chosen, ram_reader=lambda: 8.0,
        vram_reader=lambda: 6.0,
    )
    declared = local.Profile(**{**chosen.__dict__, "vram_free_gb": 4.0})
    both = local.check_memory(declared, ram_reader=lambda: 8.0, vram_reader=lambda: 6.0)

    assert any("RAM libera 8.0 GB" in note for note in notes)
    assert any("VRAM libera 6.0 GB" in note for note in both)


def test_the_health_check_reads_the_status_of_the_answer(chosen):
    class Answer:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    assert local.health_ok(chosen, opener=lambda url, timeout: Answer()) is True

    class Broken:
        status = 503

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    assert local.health_ok(chosen, opener=lambda url, timeout: Broken()) is False

    def refused(url, timeout):
        raise OSError("nessuno ascolta")

    assert local.health_ok(chosen, opener=refused) is False


def test_a_server_already_answering_is_used_and_not_marked_as_the_engines(
    lane, chosen, monkeypatch,
):
    monkeypatch.setattr(local, "_write_marker", lambda row: pytest.fail("non deve marcarlo"))
    monkeypatch.setattr(local, "_spawn", lambda profile: pytest.fail("non deve avviarlo"))

    notes = local.ensure_running(
        "20261022-000000-prova", "prova", profile_reader=lambda: chosen,
        probe=lambda profile: True, memory=lambda profile: ["RAM libera 8.0 GB"],
    )

    assert notes == [f"modello locale: server già acceso su {chosen.health}"]


def test_the_engine_starts_the_server_waits_for_health_and_leaves_a_marker(
    lane, task, chosen, monkeypatch,
):
    alive = type("Processo", (), {"pid": 4242, "poll": lambda self: None, "kill": lambda self: None})()
    spawned: list = []
    answers = iter([False, True])

    monkeypatch.setattr(local, "POLL_S", 0.0)
    notes = local.ensure_running(
        task, "prova", profile_reader=lambda: chosen, probe=lambda profile: next(answers),
        spawn=lambda profile: spawned.append(profile) or alive,
        memory=lambda profile: ["RAM libera 8.0 GB"],
        clock=lambda: 0.0, sleep=lambda seconds: None,
    )

    assert spawned == [chosen]
    assert any("avviato dal motore (pid 4242)" in note for note in notes)
    marker = json.loads(local._marker_path().read_text(encoding="utf-8"))
    assert marker["pid"] == 4242 and marker["task"] == task and marker["unit"] == "prova"
    assert marker["started_by"] == "engine"
    assert [row["event"] for row in state.read_events(task)][-1] == "local_server_started"


def test_a_server_that_never_answers_is_stopped_and_leaves_no_marker(
    lane, task, chosen, monkeypatch,
):
    killed: list = []
    alive = type("Processo", (), {"pid": 7, "poll": lambda self: None, "kill": lambda self: killed.append(True)})()
    never = local.Profile(**{**chosen.__dict__, "start_timeout_s": 0.0})

    with pytest.raises(local.LocalModelError, match="non risponde"):
        local.ensure_running(
            task, "prova", profile_reader=lambda: never, probe=lambda profile: False,
            spawn=lambda profile: alive, memory=lambda profile: [],
            clock=lambda: 0.0, sleep=lambda seconds: None,
        )

    assert killed == [True]
    assert not local._marker_path().exists()


def _marker(lane: Path, **fields) -> None:
    row = {
        "pid": 99, "task": "20261022-000000-prova", "unit": "prova",
        "model": "locale/prova", "health": "http://127.0.0.1:8099/health",
        "port": 8099, "command": ["server"], "started_at": "2026-10-22T00:00:00+00:00",
        "started_by": "engine",
    }
    row.update(fields)
    local._marker_path().write_text(json.dumps(row), encoding="utf-8")


def test_release_with_no_marker_does_nothing(lane):
    assert local.release("20261022-000000-prova", "prova") == []


def test_release_keeps_a_server_another_unfinished_unit_is_using(lane, task, chosen, monkeypatch):
    monkeypatch.setattr(local, "profile", lambda path=None: chosen)
    _marker(lane, task=task, unit="altra")
    state.append_event(task, "unit_created", unit="altra", worktree=str(lane), branch="sup/x")
    state.append_event(task, "unit_started", unit="altra", mode="tab")

    notes = local.release(task, "prova", killer=lambda pids: pytest.fail("non deve fermarlo"))

    assert notes and "lo usa" in notes[0]
    assert local._marker_path().exists()


def test_release_stops_the_engine_server_of_a_finished_unit(lane, task, chosen, monkeypatch):
    monkeypatch.setattr(local, "profile", lambda path=None: chosen)
    _marker(lane, task=task, unit="prova")
    killed: list = []

    notes = local.release(
        task, "prova", killer=lambda pids: killed.append(pids),
        probe=lambda profile: False, clock=lambda: 0.0, sleep=lambda seconds: None,
    )

    assert killed == [[99]]
    assert any("server fermato (pid 99)" in note for note in notes)
    assert not local._marker_path().exists()
    assert [row["event"] for row in state.read_events(task)][-1] == "local_server_stopped"


def test_release_says_when_the_process_went_but_health_still_answers(lane, task, chosen, monkeypatch):
    still_up = local.Profile(**{**chosen.__dict__, "stop_timeout_s": 0.0})
    monkeypatch.setattr(local, "profile", lambda path=None: still_up)
    _marker(lane, task=task, unit="prova")

    notes = local.release(
        task, "prova", killer=lambda pids: None, probe=lambda profile: True,
        clock=lambda: 0.0, sleep=lambda seconds: None,
    )

    assert notes and "risponde ancora" in notes[-1]
    assert local._marker_path().exists()
    # The stop was attempted with the profile from the lane; the marker stays until health says it went.
    assert notes[-1].startswith("modello locale: pid 99 fermato")


def test_release_leaves_a_marker_whose_server_today_s_lane_no_longer_describes(lane, chosen, monkeypatch):
    monkeypatch.setattr(local, "profile", lambda path=None: chosen)
    _marker(lane, task="20261022-000000-prova", unit="prova", health="http://127.0.0.1:1/altro")

    notes = local.release("20261022-000000-prova", "prova", killer=lambda pids: pytest.fail("non deve fermarlo"))

    assert notes and "marcatore" in notes[0] and "lasciato acceso" in notes[0]
    assert local._marker_path().exists()


def test_a_unit_on_another_harness_has_no_local_server_to_release(monkeypatch):
    monkeypatch.setattr(local, "release", lambda *a, **k: pytest.fail("non deve cercarlo"))

    assert local.release_for_unit("20261022-000000-prova", "prova", {"harness": "claude"}) == []


def test_release_for_unit_calls_release_for_a_local_unit(monkeypatch):
    seen: dict = {}

    def fake_release(task_id, unit):
        seen.update({"task": task_id, "unit": unit})
        return ["fermato"]

    monkeypatch.setattr(local, "release", fake_release)

    assert local.release_for_unit("20261022-000000-prova", "prova", {"harness": "local"}) == ["fermato"]
    assert seen == {"task": "20261022-000000-prova", "unit": "prova"}


def test_describe_says_when_there_is_no_configuration_and_when_a_server_is_up(lane, chosen, monkeypatch):
    monkeypatch.setattr(local, "profile", lambda path=None: chosen)
    assert "nessun server avviato dal motore" in local.describe()

    _marker(lane, task="20261022-000000-prova", unit="prova")
    line = local.describe()
    assert "20261022-000000-prova/prova" in line and chosen.health in line
