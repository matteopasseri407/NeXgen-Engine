"""Il ritiro smaltisce l'istanza della copia prima di toglierla dal disco.

L'endpoint non è inventato: `@opencode-ai/sdk/dist/v2/gen/sdk.gen.js`, classe
`Instance`, `POST /instance/dispose` con `directory` nella query. Questi test
leggono l'ordine delle chiamate e non avviano OpenCode né toccano un processo.
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


def test_the_folder_instance_is_disposed_before_its_location_is_evicted(tmp_path, monkeypatch):
    run, seen = _answers({})
    monkeypatch.setattr(opencode.subprocess, "run", run)
    monkeypatch.setattr(opencode, "DISPOSE_WAIT_S", 0)
    monkeypatch.setattr(opencode.proces, "rows", lambda: [])

    worked, reason = opencode.OpenCodeAdapter().dispose_location(tmp_path)

    assert worked is True
    assert "instance/dispose" in seen[0] and f"directory={tmp_path}" in seen[0]
    evict_at = next(index for index, line in enumerate(seen) if "debug.location.evict" in line)
    assert evict_at > 0, "la cartella si libera dopo lo smaltimento dell'istanza"


def test_a_refused_instance_is_a_note_and_the_eviction_still_answers(tmp_path, monkeypatch):
    run, _ = _answers({"instance/dispose": 4})
    monkeypatch.setattr(opencode.subprocess, "run", run)
    monkeypatch.setattr(opencode, "DISPOSE_WAIT_S", 0)
    monkeypatch.setattr(opencode.proces, "rows", lambda: [])

    worked, reason = opencode.OpenCodeAdapter().dispose_location(tmp_path)

    assert worked is True, "la liberazione della cartella è riuscita comunque"
    assert "istanza" in reason and "non smaltita" in reason and "esce con 4" in reason


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
