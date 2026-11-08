# nexgen: one-job reason="tests for the supervisor's relaunch of a unit on another worker when its session is spent or stalled"
"""Tests for routing_failover: quota and stall relaunch, candidate order, idempotency, no network."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from nexgen_local import routing_failover as failover
from nexgen_local import routing_tiers as rt
from nexgen_local import supervision_costs as costs
from nexgen_local import supervision_harness as harness
from nexgen_local import supervision_review as review
from nexgen_local import supervision_state as state

FLAT_A = "opencode-go/flat-a"
FLAT_B = "opencode-go/flat-b"
FREE_1 = "opencode/free-1"
FREE_2 = "opencode/free-2"

TIERS_YAML = f"""
volume_min_5h: 20000

tiers:
  worker:
    - model: {FLAT_A}
      harness: opencode
      requests_5h: 40000
    - model: {FLAT_B}
      harness: opencode
      requests_5h: 40000

  limited:
    - model: {FREE_1}
      harness: opencode
    - model: {FREE_2}
      harness: opencode
"""

QUOTA_ERROR = {"error_type": "provider.quota", "error_message": "monthly usage limit reached"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Fake journal, unit and harness around the real tier file and exhaustion state."""
    yaml_file = tmp_path / "routing-tiers.yaml"
    yaml_file.write_text(TIERS_YAML, encoding="utf-8")
    monkeypatch.setenv("ROUTING_TIERS_PATH", str(yaml_file))
    monkeypatch.setenv("ROUTING_STATE_PATH", str(tmp_path / "routing-tiers-state.json"))
    monkeypatch.setattr(rt, "probe_cli_inventory", lambda harness_name: set())

    box = SimpleNamespace(
        journal=[], calls=[], alerts=[], model=FLAT_A, failure={},
        units=[{"unit": "unit-a", "state": "in corso", "session": "ses-1", "harness": "opencode"}],
    )
    monkeypatch.setattr(state, "read_events", lambda task_id: list(box.journal))
    monkeypatch.setattr(state, "append_event", lambda task_id, event, **fields: _write(box, event, fields))
    monkeypatch.setattr(state, "status", lambda task_id: {"units": box.units})
    monkeypatch.setattr(review, "delivery_failure", lambda task_id, unit, reader=None: dict(box.failure))
    monkeypatch.setattr(costs, "measure_for", lambda harness_name, session: SimpleNamespace(model=box.model))
    monkeypatch.setattr(harness, "run_unit", lambda task_id, unit, **kw: box.calls.append(kw) or {"mode": "run"})
    monkeypatch.setattr(failover, "_alert", lambda session, line: box.alerts.append((session, line)))
    return box


def _write(box, event, fields):
    """Keep one fake journal row, as the real journal would."""
    box.journal.append({"event": event, **fields})


def _move(env_box):
    """One `sup status` pass over the unit."""
    return failover._move_if_blocked("task-1", "unit-a")


def _quota_on(env_box, session="ses-1"):
    """The unit's last delivery ended with a spent-quota error in this session."""
    env_box.failure = {"unit": "unit-a", "round": 0, "session": session, **QUOTA_ERROR, "turn_at": 1}


def test_flat_quota_relaunches_on_flat_never_on_limited(env):
    _quota_on(env)
    line = _move(env)
    assert env.calls == [{"harness": "opencode", "model": FLAT_B, "limited": False, "force_new": True}]
    assert "rilanciato su " + FLAT_B in line
    assert env.alerts[0][0] == "ses-1"


def test_session_model_with_variant_excludes_the_real_candidate(env):
    env.model = f"{FLAT_A}#default"
    _quota_on(env)
    _move(env)
    assert env.calls[0]["model"] == FLAT_B


def test_flat_quota_with_no_flat_left_stops_and_never_uses_free(env):
    rt.mark_exhausted(FLAT_B, "test")
    _quota_on(env)
    line = _move(env)
    assert env.calls == []
    assert "nessun candidato libero" in line
    assert len(env.alerts) == 1


def test_no_second_alert_for_the_same_blocked_session(env):
    rt.mark_exhausted(FLAT_B, "test")
    _quota_on(env)
    _move(env)
    assert _move(env) == ""
    assert len(env.alerts) == 1
    assert env.calls == []


def test_free_blocked_moves_to_next_free_then_to_flat(env):
    env.model = FREE_1
    _quota_on(env)
    _move(env)
    assert env.calls[-1]["model"] == FREE_2
    assert env.calls[-1]["limited"] is True

    env.model = FREE_2
    env.units[0]["session"] = "ses-2"
    _quota_on(env, session="ses-2")
    _move(env)
    assert env.calls[-1]["model"] == FLAT_A
    assert env.calls[-1]["limited"] is True


def test_stall_relaunches_and_excludes_model_for_one_hour(env):
    env.units[0]["state"] = "in stallo"
    line = _move(env)
    assert env.calls[-1]["model"] == FLAT_B
    assert "stallo" in line
    record = rt.load_state()["exhausted"][FLAT_A]
    assert round(record["expires_at"] - record["exhausted_at"]) == 3600


def test_quota_exclusion_lasts_six_hours(env):
    _quota_on(env)
    _move(env)
    record = rt.load_state()["exhausted"][FLAT_A]
    assert round(record["expires_at"] - record["exhausted_at"]) == 6 * 3600


def test_same_blocked_session_is_relaunched_only_once(env):
    _quota_on(env)
    _move(env)
    _move(env)
    assert len(env.calls) == 1
    assert [row["event"] for row in env.journal].count("unit_failover") == 1


def test_healthy_unit_is_left_alone(env):
    assert _move(env) == ""
    assert env.calls == []


def test_without_tier_file_nothing_happens(env, tmp_path, monkeypatch):
    monkeypatch.setenv("ROUTING_TIERS_PATH", str(tmp_path / "missing.yaml"))
    monkeypatch.setattr(state, "status", lambda task_id: pytest.fail("sup status must not look at units"))
    _quota_on(env)
    assert failover.unit_note("task-1", "unit-a") == ""
    assert env.calls == []


def test_relaunch_error_is_printed_and_does_not_raise(env, monkeypatch):
    _quota_on(env)

    def broken(task_id, unit, **kw):
        raise harness.HarnessError("copia sparita")

    monkeypatch.setattr(harness, "run_unit", broken)
    line = failover.unit_note("task-1", "unit-a")
    assert "rilancio non riuscito" in line
    assert "copia sparita" in line
