"""Il ritiro smaltisce l'istanza della copia prima di toglierla dal disco.

In OpenCode 2.3.2 lo smaltimento non è più un endpoint separato: il documento
`GET /openapi.json` del servizio in esecuzione non ha nessun percorso
`/instance/dispose`, e `DELETE /api/debug/location` (`debug.location.evict`) è
descritto lì come "Dispose the requested location's cached services so its next
use boots them fresh". Questi test leggono la forma e l'ordine delle chiamate e
non avviano OpenCode né toccano un processo.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from nexgen_local import supervision_adapters as adapters
from nexgen_local import supervision_retire as retire
from nexgen_local import supervision_state as state
from nexgen_local.supervision_adapters import opencode

SERVER_PID = 900


@pytest.fixture
def lane(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "lane_state_dir", lambda: tmp_path / "lane")
    return tmp_path / "lane"


@pytest.fixture
def task(lane):
    return state.start("prova smaltimento dell'istanza")["id"]


def _answers(returncodes: dict[str, int]):
    """Un finto `subprocess.run` che risponde per endpoint e registra l'ordine delle chiamate."""
    seen: list[str] = []

    def run(argv, **kwargs):
        line = " ".join(str(part) for part in argv)
        seen.append(line)
        if "/api/info" in line:
            return SimpleNamespace(returncode=0, stdout=json.dumps({"pid": SERVER_PID}), stderr="")
        code = next((value for key, value in returncodes.items() if key in line), 0)
        return SimpleNamespace(returncode=code, stdout="", stderr="")

    return run, seen


def test_the_eviction_is_the_only_call_that_disposes_the_copy(tmp_path, monkeypatch):
    """In 2.3.2 lo smaltimento è lo sfrattamento della posizione, non un endpoint a parte."""
    run, seen = _answers({})
    monkeypatch.setattr(opencode.subprocess, "run", run)
    monkeypatch.setattr(opencode, "DISPOSE_WAIT_S", 0)
    monkeypatch.setattr(opencode.proces, "rows", lambda: [])

    worked, reason = opencode.OpenCodeAdapter().dispose_location(tmp_path)

    assert worked is True
    calls = [line for line in seen if "/api/info" not in line]
    assert len(calls) == 1, calls
    assert "debug.location.evict" in calls[0]
    assert f"location[directory]={tmp_path}" in calls[0]
    assert "instance" not in calls[0], "l'endpoint /instance/dispose non esiste più nella 2.3.2"


def test_a_refused_eviction_is_reported_with_its_own_answer(tmp_path, monkeypatch):
    run, _ = _answers({"debug.location.evict": 4})
    monkeypatch.setattr(opencode.subprocess, "run", run)
    monkeypatch.setattr(opencode, "DISPOSE_WAIT_S", 0)
    monkeypatch.setattr(opencode.proces, "rows", lambda: [])

    worked, reason = opencode.OpenCodeAdapter().dispose_location(tmp_path)

    assert worked is False, "senza sfrattamento la copia non è libera e il ritiro lo deve dire"
    assert "debug.location.evict" in reason and "esce con 4" in reason


def test_the_copy_is_disposed_before_it_is_removed(task, tmp_path, monkeypatch):
    calls: list[str] = []
    copy = tmp_path / "copia"
    copy.mkdir()
    record = {
        "unit": "prova", "worktree": str(copy), "branch": "sup/x",
        "repo": str(tmp_path / "repo"), "published": ["a" * 40],
    }
    monkeypatch.setattr(retire.state, "unit_record", lambda task_id, unit: record)
    monkeypatch.setattr(retire, "_keep_history", lambda *a, **k: {"ref": "", "head": "a" * 40})
    monkeypatch.setattr(retire, "_release_local", lambda *a, **k: [])
    monkeypatch.setattr(
        adapters.get("opencode"), "dispose_location",
        lambda folder: (calls.append("dispose") or True, f"cartella liberata: {folder}"),
    )
    monkeypatch.setattr(retire, "_remove_copy", lambda *a, **k: calls.append("remove"))
    monkeypatch.setattr(retire, "_delete_branch", lambda *a, **k: None)

    result = retire.retire(task, "prova", why="prova", commits=["a" * 40])

    assert calls == ["dispose", "remove"]
    assert any("cartella liberata" in note for note in result["notes"])
