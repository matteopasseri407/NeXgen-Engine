"""Il nucleo condiviso del launcher DeepSeek Harness (dsh), senza processi reali.

Lo stream NDJSON e' un run reale catturato il 2026-11-25 con la stessa forma degli argv del
seggio. Qui si prova il comportamento del modulo: la rotta, la patch, e la lettura dello stream.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import dsh_cli  # noqa: E402

#: Un run live riuscito: risposta "OK", 7933 token in un passo, nessun costo riportato.
STREAM = """\
{"type":"session","sessionId":"session-3170bd6e-6e7b-4ef4-bf38-2b8d172a53fa","cwd":"C:\\\\work"}
{"type":"status","phase":"turn_start","turn":1}
{"type":"status","phase":"step_start","turn":1,"step":1}
{"type":"text","text":"OK"}
{"type":"status","phase":"step_end","turn":1,"step":1,"usage":{"inputTokens":7930,"outputTokens":3,"totalTokens":7933}}
{"type":"status","phase":"turn_end","turn":1,"reason":{"kind":"completed"}}
{"type":"final","sessionId":"session-3170bd6e-6e7b-4ef4-bf38-2b8d172a53fa","reason":{"kind":"completed"},"text":"OK"}
"""

SESSION = "session-3170bd6e-6e7b-4ef4-bf38-2b8d172a53fa"
PRIVACY_IDS = [
    "session-log-deepseek",
    "session-telemetry-otel",
    "plugin-package-inventory-deepseek",
]


def test_a_route_splits_into_the_provider_and_the_model_the_provider_owns():
    assert dsh_cli.split_route("opencode-go/deepseek-v4.1-flash") == (
        "opencode-go", "deepseek-v4.1-flash",
    )
    assert dsh_cli.split_route("  opencode / qwen3-coder-free  ") == ("opencode", "qwen3-coder-free")


@pytest.mark.parametrize("route", ["", "deepseek-v4.1-flash", "/deepseek-v4.1-flash", "opencode-go/"])
def test_a_route_without_both_halves_is_refused(route):
    with pytest.raises(dsh_cli.DshError) as error:
        dsh_cli.split_route(route)
    assert "<provider>/<model>" in str(error.value)


def test_the_patch_pins_the_route_and_leaves_the_storage_where_the_person_keeps_it():
    document = dsh_cli.patch_document("opencode-go", "deepseek-v4.1-flash", effort="high")
    assert document[:2] == [
        {"id": "llm-pi-ai", "config": {"providers": {"opencode-go": {"apiKeyEnv": "OPENCODE_API_KEY"}}}},
        {
            "id": "agent-default-model",
            "config": {"provider": "opencode-go", "model": "deepseek-v4.1-flash", "reasoningEffort": "high"},
        },
    ]
    ids = [entry["id"] for entry in document]
    assert "session-persistence-jsonl" not in ids and "storage-json" not in ids
    # I tre upload verso DeepSeek sono spenti in ogni caso, con o senza radice privata.
    assert ids[-3:] == PRIVACY_IDS
    assert document[-3:] == [
        {"id": "session-log-deepseek", "config": {"enabled": False}},
        {"id": "session-telemetry-otel", "config": {"mode": "DISABLED"}},
        {"id": "plugin-package-inventory-deepseek", "config": {"enabled": False}},
    ]


def test_a_data_root_moves_the_session_and_the_projection_store_into_it(tmp_path):
    document = dsh_cli.patch_document("opencode", "qwen3-coder-free", data_root=tmp_path)
    assert {"id": "session-persistence-jsonl", "config": {"root": str(tmp_path / "sessions")}} in document
    assert {"id": "storage-json", "config": {"root": str(tmp_path / "storages")}} in document
    assert [entry["id"] for entry in document][-3:] == PRIVACY_IDS


@pytest.mark.parametrize("effort", ["", "none", "  "])
def test_no_effort_leaves_the_selection_without_a_reasoning_level(effort):
    selection = dsh_cli.patch_document("opencode-go", "deepseek-v4.1-flash", effort=effort)[1]["config"]
    assert selection == {"provider": "opencode-go", "model": "deepseek-v4.1-flash"}


def test_the_patch_text_is_the_document_yaml_unchanged():
    document = dsh_cli.patch_document("opencode-go", "deepseek-v4.1-flash")
    assert dsh_cli.patch_text(document).splitlines()[0] == "- id: llm-pi-ai"


def test_a_stream_cut_mid_line_is_read_up_to_the_last_whole_event():
    raw = STREAM.split('{"type":"final"')[0] + '{"type":"final","sessionId":"session-3170'
    events = dsh_cli.read_events(raw)
    assert len(events) == 6
    assert dsh_cli.final_event(events) is None
    assert dsh_cli.session_id(events) == SESSION


def test_the_stream_is_read_as_events_and_the_final_one_is_the_answer():
    events = dsh_cli.read_events(STREAM)
    final = dsh_cli.final_event(events)
    assert [event["type"] for event in events[:2]] == ["session", "status"]
    assert final is not None and final["text"] == "OK"
    assert final["reason"] == {"kind": "completed"}


def test_the_session_id_is_the_first_session_events_id():
    assert dsh_cli.session_id(dsh_cli.read_events(STREAM)) == SESSION


def test_the_session_id_falls_back_to_the_final_event():
    events = dsh_cli.read_events(
        '{"type":"final","sessionId":"session-9","reason":{"kind":"completed"},"text":"OK"}'
    )
    assert dsh_cli.session_id(events) == "session-9"


def test_a_stream_with_no_id_at_all_has_no_session_id():
    assert dsh_cli.session_id([]) == ""
    assert dsh_cli.session_id([{"type": "final", "text": "OK"}]) == ""


def test_the_tokens_are_the_sum_of_the_steps_the_harness_finished():
    assert dsh_cli.total_tokens(dsh_cli.read_events(STREAM)) == 7933


def test_a_total_that_is_not_a_whole_number_contributes_nothing():
    events = [
        {"type": "status", "phase": "step_end", "usage": {"totalTokens": 120}},
        {"type": "status", "phase": "step_end", "usage": {"totalTokens": True}},
        {"type": "status", "phase": "step_end", "usage": "n/a"},
        # Il totale di un turno intero non e' il totale di un passo.
        {"type": "status", "phase": "turn_end", "usage": {"totalTokens": 999}},
    ]
    assert dsh_cli.total_tokens(events) == 120


def test_the_route_names_the_plan_key_before_the_harness_own_variable(monkeypatch):
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "plan-key")
    monkeypatch.setenv("OPENCODE_API_KEY", "harness-key")
    assert dsh_cli.credential_names("opencode-go") == ("OPENCODE_GO_API_KEY", "OPENCODE_API_KEY")
    assert dsh_cli.credential_value("opencode-go") == "plan-key"


def test_an_unknown_route_falls_back_to_the_harness_own_variable(monkeypatch):
    monkeypatch.delenv("OPENCODE_API_KEY", raising=False)
    monkeypatch.setattr("nexgen_core.deposit_env.read_deposit", lambda: {})
    assert dsh_cli.credential_names("anthropic") == ("OPENCODE_API_KEY",)
    assert dsh_cli.credential_value("anthropic") == ""


def test_the_secrets_deposit_is_read_when_nothing_exported_the_key(monkeypatch):
    monkeypatch.delenv("OPENCODE_GO_API_KEY", raising=False)
    monkeypatch.delenv("OPENCODE_API_KEY", raising=False)
    monkeypatch.setattr(
        "nexgen_core.deposit_env.read_deposit", lambda: {"OPENCODE_GO_API_KEY": "deposit-key"},
    )
    assert dsh_cli.credential_value("opencode-go") == "deposit-key"
