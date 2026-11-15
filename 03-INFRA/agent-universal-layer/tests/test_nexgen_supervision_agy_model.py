"""nexgen: one-job reason="test allowlist effort variants and agy model resolution without delivering refused brief"
Test that model allowlists accept effort variants for the agy harness,
refuse unrelated models, and do not deliver the brief before refusing
an unreadable or disallowed model.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from nexgen_local import routing_tiers
from nexgen_local import supervision_costs as costs
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


def test_antigravity_maps_every_family_its_catalogue_has_no_bare_id_for():
    """`gpt-oss-120b` used to travel as given, so `agy` ran whatever it chose and said nothing."""
    adapter = AntigravityAdapter()
    argv = adapter.tab_argv("agy", Path("."), "prompt", model="gpt-oss-120b")
    assert argv[argv.index("--model") + 1] == "gpt-oss-120b-medium"

    # A catalogue id is never rewritten, including the bare ones agy really lists.
    bare = adapter.tab_argv("agy", Path("."), "prompt", model="claude-sonnet-4-6")
    assert bare[bare.index("--model") + 1] == "claude-sonnet-4-6"

    # An id the catalogue does not carry travels as given: a newer release still runs.
    unknown = adapter.tab_argv("agy", Path("."), "prompt", model="modello-futuro")
    assert unknown[unknown.index("--model") + 1] == "modello-futuro"


def test_the_quota_preflight_accepts_only_what_the_adapter_can_resolve():
    """A family matches the catalogue's `<family>-<effort>` id, and nothing looser."""
    inventory = {"gemini-3.8-flash-high", "gpt-oss-120b-medium", "claude-sonnet-4-6"}
    assert routing_tiers.is_in_inventory("gemini-3.8-flash", "agy", inventory=inventory) is True
    assert routing_tiers.is_in_inventory("gpt-oss-120b", "agy", inventory=inventory) is True
    assert routing_tiers.is_in_inventory("gpt-oss", "agy", inventory=inventory) is True
    assert routing_tiers.is_in_inventory("claude-sonnet-4-6", "agy", inventory=inventory) is True
    assert routing_tiers.is_in_inventory("gemini-3.9-flash", "agy", inventory=inventory) is False
    assert routing_tiers.is_in_inventory("gpt-6-luna", "agy", inventory=inventory) is False


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


def _a_unit(monkeypatch, tmp_path, task_id: str, harness_name: str, folder: Path) -> None:
    """One task and one unit whose copy is `folder`, as the journal records them."""
    monkeypatch.setattr(state, "_root", lambda: tmp_path)
    state.start("obiettivo test")
    task_dir = tmp_path / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    state.append_jsonl(task_dir / state.JOURNAL_FILE, {"at": "now", "event": "started", "task": task_id})
    folder.mkdir(parents=True, exist_ok=True)
    state.append_event(
        task_id, "unit_created", unit="u1", worktree=str(folder), branch="sup/x/u1",
        base="0" * 40, allow=[], verify="", repo=str(tmp_path), harness=harness_name,
    )


def test_a_turn_records_the_model_it_ran_on(monkeypatch, tmp_path):
    """`_open_turn` reads the model and must write it: a later measurement falls back to that row."""
    task_id = "test-task-turn"
    folder = tmp_path / "copie" / "23-unita-u1"
    _a_unit(monkeypatch, tmp_path, task_id, "opencode", folder)
    sessions = [{"id": "ses_turno", "directory": str(folder), "created": 10**13}]

    res = harness._open_turn(
        task_id, "u1", folder, "prompt", session="", event="unit_started", extra={},
        runner=lambda *args: SimpleNamespace(pid=42),
        adapter=harness.adapters.get("opencode"),
        measure=lambda session: SimpleNamespace(model="opencode-go/deepseek-v4.1-flash"),
        allowed=("opencode-go/deepseek-v4.1-flash",), lister=lambda: sessions, wait_s=0,
    )

    assert res["model"] == "opencode-go/deepseek-v4.1-flash"
    attached = [row for row in state.read_events(task_id) if row.get("event") == "attached"]
    assert attached[-1]["model"] == "opencode-go/deepseek-v4.1-flash"


def test_a_foreign_session_reopens_on_the_model_the_journal_recorded(monkeypatch, tmp_path):
    """agy cannot report a session model, so a correction round reads the one the attach wrote."""
    task_id = "test-task-foreign"
    folder = tmp_path / "copie" / "23-unita-u1"
    _a_unit(monkeypatch, tmp_path, task_id, "agy", folder)
    conversation = "a544d85d-f5b3-4741-bbe2-8044c85b348f"
    state.append_event(
        task_id, "attached", unit="u1", role="esecutore", harness="agy", session=conversation,
        folder=str(tmp_path / "altra-copia"), model="gemini-3.8-flash-high",
    )
    review = tmp_path / "45-revisione-u1-1.md"
    review.write_text("# Revisione\n\ncorrezioni\n", encoding="utf-8")
    monkeypatch.setattr(harness, "_executable_for", lambda adapter: "agy")
    monkeypatch.setattr(harness.policy, "allowed_models", lambda: ("gemini-3.8-flash",))
    monkeypatch.setenv("ANTIGRAVITY_HOME", str(tmp_path / "agy-home"))
    opened: list = []

    def a_session_that_is_gone(session):
        raise costs.SessionMissing(f"sessione {session} eliminata")

    res = harness.send_corrections(
        task_id, "u1", review, 1,
        opener=lambda title, argv, *, cwd, marker="": opened.append(list(argv)) or {"title": title, "pid": 7},
        runner=lambda *args: pytest.fail("agy non riceve un run invisibile"),
        measure=a_session_that_is_gone, lister=lambda: [], wait_s=0,
    )

    assert opened, "la correzione deve riaprire la scheda, non rifiutarsi"
    assert opened[0][opened[0].index("--model") + 1] == "gemini-3.8-flash-high"
    assert res["mode"] == "tab"

