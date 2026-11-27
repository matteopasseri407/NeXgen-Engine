# nexgen: one-job reason="tests for the quota the supervisor reads before it starts a unit on a lane"
"""The gauge the supervisor reads before dispatch, and the lane it moves a unit to.

Every reading is injected: the cheap readers would answer about the machine this
suite runs on, and a test that changes with someone's subscription is not a test.
The tier file is the smallest one that can express two plans, and every expected
value is the literal the fixture wrote.
"""
from __future__ import annotations

import shutil

from types import SimpleNamespace

import pytest

from nexgen_core import quota, quota_policy
from nexgen_local import routing_tiers as rt
from nexgen_local import supervision_quota as gate
from nexgen_local import supervision_state as state

FLAT = "opencode-go/flat-a"
OTHER_PLAN = "gemini-3.8-flash"

TIERS_YAML = f"""
volume_min_5h: 20000

tiers:
  worker:
    - model: {FLAT}
      harness: opencode
      requests_5h: 40000
    - model: {OTHER_PLAN}
      harness: antigravity
      requests_5h: 50000
"""


@pytest.fixture(autouse=True)
def _the_owner_policy_is_the_shipped_one(monkeypatch):
    """The line comes from the owner's `quota.yaml`: this suite must not read this machine's.

    A test that needs a declared line replaces `load` itself, so no expected value
    depends on whose Vault the suite happens to run beside.
    """
    monkeypatch.setattr(gate.quota_policy, "load",
                        lambda home=None, *, vault=None: (quota_policy.Failover(), []))


@pytest.fixture
def tiers(tmp_path, monkeypatch):
    """A tier file with two plans, and a routing state file of its own."""
    yaml_file = tmp_path / "routing-tiers.yaml"
    yaml_file.write_text(TIERS_YAML, encoding="utf-8")
    monkeypatch.setenv("ROUTING_TIERS_PATH", str(yaml_file))
    monkeypatch.setenv("ROUTING_STATE_PATH", str(tmp_path / "routing-tiers-state.json"))
    monkeypatch.setattr(rt, "probe_cli_inventory", lambda harness_name: set())
    return yaml_file


@pytest.fixture
def env(tiers, monkeypatch):
    """The same tiers, with the journal captured instead of written."""
    box = SimpleNamespace(journal=[])
    monkeypatch.setattr(state, "append_event", lambda task_id, event, **fields: _write(box, event, fields))
    return box


def _write(box, event: str, fields: dict) -> None:
    """Keep one fake journal row, dropping the fields the real journal drops."""
    box.journal.append({"event": event, **{key: value for key, value in fields.items() if value is not None}})


def reading(percent: float | None, *, window: str = "5h", resets: float | None = None,
            state_name: str = "warn", provider: str = "OpenCode Go",
            provider_id: str = "opencode-go") -> quota.QuotaRow:
    """One row exactly as the cheap report would hand it over."""
    return quota.QuotaRow(
        provider=provider, provider_id=provider_id, window=window, used=percent,
        unit="percent", percent=percent, resets_at=resets, state=state_name,
    )


def test_a_plan_at_97_percent_moves_the_unit_and_says_why(env):
    model, harness, note = gate.gate_unit("task", "u1", FLAT, "opencode",
                                          rows=[reading(97.0, resets=1_800_000_000)])

    assert (model, harness) == (OTHER_PLAN, "antigravity")
    assert note == "quota al 97% (5h): passa a gemini-3.8-flash"
    assert env.journal == [{
        "event": "unit_quota", "unit": "u1", "harness": "opencode", "model": FLAT,
        "provider": "opencode-go", "window": "5h", "percent": 97.0, "state": "warn",
        "decision": "spostata", "reason": "quota al 97% (5h): passa a gemini-3.8-flash",
        "next_model": OTHER_PLAN,
    }]


def test_a_stale_97_percent_is_unknown_and_never_blocks(env):
    model, harness, note = gate.gate_unit(
        "task", "u1", FLAT, "opencode", rows=[reading(None, state_name="stale")],
    )

    assert (model, harness) == (FLAT, "opencode")
    assert note.startswith("quota unknown")
    assert [row["decision"] for row in env.journal] == ["quota unknown"]


def test_a_plan_nobody_can_read_is_unknown_and_never_blocks(env):
    model, harness, note = gate.gate_unit("task", "u1", FLAT, "opencode", rows=[])

    assert (model, harness) == (FLAT, "opencode")
    assert note.startswith("quota unknown")
    assert env.journal[0]["event"] == "unit_quota"
    assert "percent" not in env.journal[0], "nessun numero inventato nel diario"


def test_a_plan_at_50_percent_starts_where_it_was_going(env):
    model, harness, note = gate.gate_unit("task", "u1", FLAT, "opencode", rows=[reading(50.0)])

    assert (model, harness) == (FLAT, "opencode")
    assert note == ""
    assert env.journal == [], "una corsia tranquilla non scrive niente"


def test_the_whole_plan_moves_and_not_only_its_model(env, monkeypatch, tmp_path):
    """A second model of the same provider is not the next candidate: it is the same spent plan."""
    yaml_file = tmp_path / "two-flat.yaml"
    yaml_file.write_text(f"""
volume_min_5h: 20000

tiers:
  worker:
    - model: {FLAT}
      harness: opencode
      requests_5h: 40000
    - model: opencode-go/flat-b
      harness: opencode
      requests_5h: 40000
""", encoding="utf-8")
    monkeypatch.setenv("ROUTING_TIERS_PATH", str(yaml_file))

    model, harness, note = gate.gate_unit("task", "u1", FLAT, "opencode", rows=[reading(97.0)])

    assert (model, harness) == (FLAT, "opencode")
    assert note == "quota al 97% (5h): nessun altro candidato, si parte qui"


def test_a_plan_at_94_point_9_percent_is_below_the_line(env):
    model, _harness, note = gate.gate_unit("task", "u1", FLAT, "opencode", rows=[reading(94.9)])

    assert model == FLAT
    assert note == ""


def test_the_line_is_the_one_the_owner_declared(env, monkeypatch):
    """`failover.block_at: 80` is what the plugin obeys, so it is what the supervisor obeys.

    A supervisor reading the alert band instead would keep dispatching to a plan
    the plugin had already stopped using, at a number nobody declared.
    """
    monkeypatch.setattr(gate.quota_policy, "load",
                        lambda home=None, *, vault=None: (quota_policy.Failover(block_at=80.0), []))

    model, harness, note = gate.gate_unit("task", "u1", FLAT, "opencode", rows=[reading(85.0)])

    assert (model, harness) == (OTHER_PLAN, "antigravity")
    assert note == "quota al 85% (5h): passa a gemini-3.8-flash"


def test_the_shipped_line_and_the_loudest_alert_are_the_same_number(env):
    """Both are 95 today, which is why the divergence was invisible: the file decides."""
    assert quota.ALERT_AT[-1] == 95.0
    assert gate.gate_unit("task", "u1", FLAT, "opencode", rows=[reading(94.9)])[0] == FLAT


def test_a_headless_unit_moves_to_a_harness_that_opens_without_a_window(env):
    """Un'unità headless può cambiare harness: ogni harness registrato sa aprire la prima sessione."""
    model, harness, note = gate.gate_unit("task", "u1", FLAT, "opencode",
                                          rows=[reading(97.0)], headless=True)

    assert (model, harness) == (OTHER_PLAN, "antigravity")
    assert note == "quota al 97% (5h): passa a gemini-3.8-flash"
    assert env.journal == [{
        "event": "unit_quota", "unit": "u1", "harness": "opencode", "model": FLAT,
        "provider": "opencode-go", "window": "5h", "percent": 97.0, "state": "warn",
        "decision": "spostata", "reason": "quota al 97% (5h): passa a gemini-3.8-flash",
        "next_model": OTHER_PLAN,
    }]


def test_a_headless_unit_stays_put_when_the_candidate_cannot_open_without_a_window(env, monkeypatch):
    """Se il candidato non sa partire senza finestra, lo spostamento non si fa e il diario lo dice."""
    monkeypatch.setattr(gate, "_opens_headless", lambda name: False)

    model, harness, note = gate.gate_unit("task", "u1", FLAT, "opencode",
                                          rows=[reading(97.0)], headless=True)

    assert (model, harness) == (FLAT, "opencode")
    assert note == ("quota al 97% (5h): antigravity non sa partire senza finestra, resto qui")
    assert env.journal == [{
        "event": "unit_quota", "unit": "u1", "harness": "opencode", "model": FLAT,
        "provider": "opencode-go", "window": "5h", "percent": 97.0, "state": "warn",
        "decision": "headless",
        "reason": "quota al 97% (5h): antigravity non sa partire senza finestra, resto qui",
    }]


def test_a_headless_dispatch_moves_to_the_harness_that_opens_without_a_window(tiers, tmp_path, monkeypatch):
    """La corsia spesa sposta il turno su `agy`, che sa aprire la sua prima conversazione senza scheda."""
    from nexgen_local import supervision_harness as harness

    monkeypatch.setattr(state, "lane_state_dir", lambda: tmp_path / "lane")
    task = _started_unit(tmp_path)
    monkeypatch.setattr(gate.quota, "percentage_rows", lambda **_kw: ([reading(97.0)], []))
    monkeypatch.setattr(harness, "_executable_for", lambda adapter: f"{adapter.name}-finto")
    monkeypatch.setattr(harness, "_hold_turn", lambda *a: None)
    monkeypatch.setattr(harness.costs, "record", lambda *a, **k: {"session": "ses_headless"})
    monkeypatch.setattr(harness.policy, "allowed_models", lambda: ())
    monkeypatch.setenv("ANTIGRAVITY_HOME", str(tmp_path / "nessuna-conversazione"))
    turns: list[str] = []

    result = harness.run_unit(
        task, "prova", harness="opencode", headless=True, wait_s=0,
        opener=lambda *a, **k: pytest.fail("nessuna scheda su un turno headless"),
        runner=lambda session, prompt, copy, model="": turns.append(model) or SimpleNamespace(pid=7),
        lister=lambda: [],
    )

    assert result["harness"] == "agy", "il turno va dove la corsia spesa manda"
    assert result["mode"] == "run"
    assert turns == ["gemini-3.8-flash"], "il turno riceve il modello del candidato scelto"
    decisions = [row.get("decision") for row in state.read_events(task) if row.get("event") == "unit_quota"]
    assert decisions == ["spostata"]


def _started_unit(tmp_path) -> str:
    """A task with one unit whose brief and copy exist, as `sup run` needs to find them."""
    task = state.start("prova quota headless")["id"]
    task_folder = state.open_task(task)
    folder = task_folder / "23-unita-prova"
    folder.mkdir(parents=True)
    (task_folder / "20-brief-prova.md").write_text("# Brief\n", encoding="utf-8")
    state.append_event(
        task, "unit_created", unit="prova", worktree=str(folder), branch=f"sup/{task}/prova",
        base="0" * 40, allow=["docs/*.md"], verify="", repo=str(tmp_path),
    )
    return task


def test_a_bare_model_is_matched_to_the_plan_its_harness_runs(env):
    """`gemini-3.8-flash` names no provider: the row Antigravity writes is still its plan."""
    antigravity = reading(97.0, provider="Antigravity", provider_id="")

    model, harness, _note = gate.gate_unit("task", "u1", OTHER_PLAN, "antigravity", rows=[antigravity])

    assert (model, harness) == (FLAT, "opencode")


def test_a_model_whose_lane_cannot_be_named_is_never_blocked(env):
    opencode_go = reading(99.0)

    model, harness, note = gate.gate_unit("task", "u1", "some-bare-model", "opencode", rows=[opencode_go])

    assert (model, harness) == ("some-bare-model", "opencode")
    assert note.startswith("quota unknown")


def test_run_unit_starts_the_unit_on_the_other_plan_when_this_one_is_spent(tiers, tmp_path, monkeypatch):
    """The point of the gauge: `sup run` really opens the tab on the next candidate."""
    from nexgen_local import supervision_harness as harness

    lane = tmp_path / "lane"
    monkeypatch.setattr(state, "lane_state_dir", lambda: lane)
    task = state.start("prova quota")["id"]
    task_folder = state.open_task(task)
    folder = task_folder / "23-unita-prova"
    folder.mkdir(parents=True)
    (task_folder / "20-brief-prova.md").write_text("# Brief\n", encoding="utf-8")
    state.append_event(
        task, "unit_created", unit="prova", worktree=str(folder), branch=f"sup/{task}/prova",
        base="0" * 40, allow=["docs/*.md"], verify="", repo=str(tmp_path),
    )
    monkeypatch.setattr(gate.quota, "percentage_rows", lambda **_kw: ([reading(97.0)], []))
    monkeypatch.setattr(shutil, "which", lambda name: f"{name}-finto")
    monkeypatch.setenv("ANTIGRAVITY_HOME", str(tmp_path / "antigravity"))
    opened: list = []

    result = harness.run_unit(
        task, "prova", wait_s=0,
        opener=lambda title, argv, *, cwd, marker="": opened.append(list(argv)) or {"title": title, "pid": 9},
        lister=lambda: [],
    )

    assert opened[0][opened[0].index("--model") + 1] == "gemini-3.8-flash-high"
    assert result["harness"] == "agy", "il nome canonico dell'adattatore"
    assert result["notes"] == ["quota al 97% (5h): passa a gemini-3.8-flash"]
    decisions = [row.get("decision") for row in state.read_events(task) if row.get("event") == "unit_quota"]
    assert decisions == ["spostata"]
