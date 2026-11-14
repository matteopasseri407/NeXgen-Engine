"""nexgen: one-job reason="test allowlist effort variants and agy model resolution without delivering refused brief"
Test that model allowlists accept effort variants for the agy harness,
refuse unrelated models, and do not deliver the brief before refusing
an unreadable or disallowed model.
"""
from __future__ import annotations

from pathlib import Path
import pytest

from nexgen_local import routing_tiers
from nexgen_local import supervision_policy as policy
from nexgen_local import supervision_harness as harness
from nexgen_local.supervision_adapters.antigravity import AntigravityAdapter
from nexgen_local import supervision_state as state


def test_routing_tiers_check_model_allowed_accepts_effort_variants_and_refuses_unrelated(monkeypatch, tmp_path):
    tiers_yaml = tmp_path / "routing-tiers.yaml"
    tiers_yaml.write_text(
        "tiers:\n"
        "  worker:\n"
        "    - model: gemini-3.8-flash\n"
        "      harness: agy\n",
        encoding="utf-8",
    )
    # Effort variants of allowed family must be accepted
    routing_tiers.check_model_allowed("gemini-3.8-flash-high", tier="worker", path=tiers_yaml)
    routing_tiers.check_model_allowed("gemini-3.8-flash-medium", tier="worker", path=tiers_yaml)
    routing_tiers.check_model_allowed("gemini-3.8-flash-low", tier="worker", path=tiers_yaml)

    # Bare model must still be accepted
    routing_tiers.check_model_allowed("gemini-3.8-flash", tier="worker", path=tiers_yaml)

    # Unrelated model must be refused
    with pytest.raises(routing_tiers.RoutingTiersError):
        routing_tiers.check_model_allowed("other-model-high", tier="worker", path=tiers_yaml)


def test_supervision_policy_model_allowed_accepts_effort_variants_and_refuses_unrelated():
    allowed = ("gemini-3.8-flash", "opencode-go/deepseek-v4.1-flash")
    assert policy.model_allowed("gemini-3.8-flash", allowed) is True
    assert policy.model_allowed("gemini-3.8-flash-high", allowed) is True
    assert policy.model_allowed("gemini-3.8-flash-medium", allowed) is True
    assert policy.model_allowed("gemini-3.8-flash-low", allowed) is True

    # Unrelated model with effort suffix is still refused
    assert policy.model_allowed("other-model-high", allowed) is False
    assert policy.model_allowed("unrelated-flash", allowed) is False


def test_antigravity_tab_argv_maps_bare_family_to_valid_id():
    adapter = AntigravityAdapter()
    argv = adapter.tab_argv("agy", Path("."), "prompt", model="gemini-3.8-flash")
    assert "--model" in argv
    idx = argv.index("--model")
    assert argv[idx + 1] == "gemini-3.8-flash-high"

    # Already qualified effort variant is preserved
    argv_med = adapter.tab_argv("agy", Path("."), "prompt", model="gemini-3.8-flash-medium")
    idx_med = argv_med.index("--model")
    assert argv_med[idx_med + 1] == "gemini-3.8-flash-medium"


def test_open_first_tab_refuses_unreadable_model_without_opening_tab(monkeypatch, tmp_path):
    monkeypatch.setattr(state, "_root", lambda: tmp_path)
    state.start("obiettivo test")
    task_id = "test-task"
    task_dir = tmp_path / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    state.append_jsonl(task_dir / state.JOURNAL_FILE, {"at": "now", "event": "started", "task": task_id})

    adapter = AntigravityAdapter()
    monkeypatch.setattr(harness, "_executable_for", lambda a: "agy")

    opener_called = False

    def fake_opener(title, argv, cwd=None, marker=None):
        nonlocal opener_called
        opener_called = True
        return {"pid": 1234, "title": title}

    res = harness._open_first_tab(
        task_id, "u1", tmp_path, "prompt", fake_opener,
        lister=lambda: [], wait_s=0.1,
        adapter=adapter,
        allowed=("gemini-3.8-flash",),
        model="",  # No model passed, and agy cannot report session_model
    )
    assert opener_called is False
    assert res.get("mode") == "refused"
    assert res.get("sent") is False
    assert "(non leggibile)" in res.get("reason", "")


def test_open_first_tab_refuses_disallowed_model_without_opening_tab(monkeypatch, tmp_path):
    monkeypatch.setattr(state, "_root", lambda: tmp_path)
    state.start("obiettivo test")
    task_id = "test-task-2"
    task_dir = tmp_path / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    state.append_jsonl(task_dir / state.JOURNAL_FILE, {"at": "now", "event": "started", "task": task_id})

    adapter = AntigravityAdapter()
    monkeypatch.setattr(harness, "_executable_for", lambda a: "agy")

    opener_called = False

    def fake_opener(title, argv, cwd=None, marker=None):
        nonlocal opener_called
        opener_called = True
        return {"pid": 1234, "title": title}

    res = harness._open_first_tab(
        task_id, "u1", tmp_path, "prompt", fake_opener,
        lister=lambda: [], wait_s=0.1,
        adapter=adapter,
        allowed=("gemini-3.8-flash",),
        model="disallowed-model-high",
    )
    assert opener_called is False
    assert res.get("mode") == "refused"
    assert res.get("sent") is False


def test_open_first_tab_trusts_model_passed_on_argv_for_agy_and_records_in_diary(monkeypatch, tmp_path):
    monkeypatch.setattr(state, "_root", lambda: tmp_path)
    state.start("obiettivo test")
    task_id = "test-task-3"
    task_dir = tmp_path / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    state.append_jsonl(task_dir / state.JOURNAL_FILE, {"at": "now", "event": "started", "task": task_id})

    adapter = AntigravityAdapter()
    monkeypatch.setattr(harness, "_executable_for", lambda a: "agy")

    opener_called = False

    def fake_opener(title, argv, cwd=None, marker=None):
        nonlocal opener_called
        opener_called = True
        return {"pid": 1234, "title": title}

    monkeypatch.setattr(
        harness, "_session_after",
        lambda ad, fld, start_ms, lister, ws, ch: ({"id": "a544d85d-f5b3-4741-bbe2-8044c85b348f", "title": "a544d85d-f5b3-4741-bbe2-8044c85b348f"}, "")
    )

    res = harness._open_first_tab(
        task_id, "u1", tmp_path, "prompt", fake_opener,
        lister=lambda: [], wait_s=0.1,
        adapter=adapter,
        allowed=("gemini-3.8-flash",),
        model="gemini-3.8-flash-high",
    )
    assert opener_called is True
    assert res.get("mode") == "tab"
    assert res.get("model") == "gemini-3.8-flash-high"
    assert res.get("model_trusted_from_argv") is True

    events = state.read_events(task_id)
    assert any(e.get("event") == "model_trusted_from_argv" for e in events)
