"""L'adattatore DeepSeek Harness: la patch per chiamata, i rifiuti espliciti, il flusso NDJSON.

Nessun `dsh` vero: il lanciatore è sostituito e lo stream è quello catturato dal vivo
il 2026-11-25 su DeepSeek Harness 0.2.2-rc.5 (Windows).
"""
from __future__ import annotations

import json

import pytest
import yaml

from nexgen_local import supervision_adapters as registry
from nexgen_local.supervision_adapters import base, dsh
from nexgen_local.supervision_costs import SessionMissing

SESSION = "session-3170bd6e-6e7b-4ef4-bf38-2b8d172a53fa"
STREAM = [
    {"type": "session", "sessionId": SESSION, "cwd": "C:\\copia"},
    {"type": "status", "phase": "turn_start", "turn": 1},
    {"type": "text", "text": "OK"},
    {"type": "status", "phase": "step_end", "turn": 1, "step": 1,
     "usage": {"inputTokens": 7930, "outputTokens": 3, "totalTokens": 7933}},
    {"type": "status", "phase": "turn_end", "turn": 1, "reason": {"kind": "completed"}},
    {"type": "final", "sessionId": SESSION, "reason": {"kind": "completed"}, "text": "OK"},
]
UNKNOWN_MODEL = {
    "type": "final", "sessionId": SESSION,
    "reason": {"kind": "error", "error": {"code": "UNKNOWN_MODEL",
                                          "message": 'pi-ai provider "opencode-go" has no configured model "x"'}},
    "text": "",
}


@pytest.fixture
def adapter(tmp_path, monkeypatch):
    """Lo stato della lane in una cartella temporanea, una chiave finta, il lanciatore finto."""
    monkeypatch.setenv("NEXGEN_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(dsh.dsh_cli, "credential_value", lambda provider: "chiave-finta" if provider == "opencode-go" else "")
    monkeypatch.setattr(dsh.dsh_cli, "launcher", lambda scratch, writer: "dsh")
    monkeypatch.setattr(dsh.processes, "rows", lambda: [])
    fresh = dsh.DshAdapter()
    return fresh


def _copy(tmp_path):
    copy = tmp_path / "compito" / "23-unita-prova"
    copy.mkdir(parents=True)
    return copy


def _finish(adapter, key, events):
    """Quello che dsh scriverebbe nel log del turno dopo la riga di richiesta."""
    with adapter.turn_log(key).open("a", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event) + "\n")


def _patch_of(argv):
    return yaml.safe_load(open(argv[argv.index("--patch") + 1], encoding="utf-8"))


def test_dsh_is_registered_with_its_long_name_too():
    assert registry.get("dsh").name == "dsh"
    assert registry.get("deepseek-harness").name == "dsh"


def test_the_first_turn_is_headless_and_pins_the_default_model(adapter, tmp_path):
    copy = _copy(tmp_path)
    key = adapter.new_session_id()
    argv = adapter.headless_argv("dsh", copy, "leggi il brief", session=key)

    assert argv[:3] == ["dsh", "--profile", "headless"]
    assert "--session-id" not in argv, "il primo turno lascia scegliere l'id a dsh"
    assert argv[-2:] == ["--json", "leggi il brief"]
    assert argv[argv.index("--permission-mode") + 1] == "workspace-write"
    assert argv[argv.index("--approval-policy") + 1] == "never"
    entries = {entry["id"]: entry["config"] for entry in _patch_of(argv)}
    assert entries["agent-default-model"] == {"provider": "opencode-go", "model": "deepseek-v4.1-flash"}
    assert entries["session-telemetry-otel"] == {"mode": "DISABLED"}
    assert "session-persistence-jsonl" not in entries, "le sessioni restano nello store che l'app mostra"


def test_the_model_sup_asks_for_travels_in_the_patch(adapter, tmp_path):
    argv = adapter.headless_argv("dsh", _copy(tmp_path), "p", session=adapter.new_session_id(),
                                 model="opencode-go/glm-5.3-flash")
    entries = {entry["id"]: entry["config"] for entry in _patch_of(argv)}
    assert entries["agent-default-model"]["model"] == "glm-5.3-flash"


def test_a_route_without_a_key_is_refused_before_anything_starts(adapter, tmp_path):
    with pytest.raises(base.AdapterError, match="nessuna chiave per la corsia opencode"):
        adapter.headless_argv("dsh", _copy(tmp_path), "p", model="opencode/qualcosa")


def test_a_model_without_provider_is_refused(adapter, tmp_path):
    with pytest.raises(base.AdapterError, match="<provider>/<modello>"):
        adapter.headless_argv("dsh", _copy(tmp_path), "p", model="deepseek-v4.1-flash")


def test_the_session_dsh_prints_is_the_one_attached(adapter, tmp_path):
    copy = _copy(tmp_path)
    key = adapter.new_session_id()
    adapter.headless_argv("dsh", copy, "p", session=key)
    _finish(adapter, key, STREAM)

    found = adapter.find_session(copy, 0, expected=key, wait_s=0)

    assert found.id == SESSION
    assert found.folder == "C:\\copia"


def test_tokens_model_and_end_are_read_from_the_turn_log(adapter, tmp_path):
    key = adapter.new_session_id()
    adapter.headless_argv("dsh", _copy(tmp_path), "p", session=key, model="opencode-go/glm-5.3-flash")
    _finish(adapter, key, STREAM)

    facts = adapter.measure(SESSION)

    assert facts.tokens_total == 7933
    assert facts.cost_usd is None and "token" in facts.cost_note
    assert facts.model == "opencode-go/glm-5.3-flash"
    assert facts.stopped, "un turno con il suo `final` è finito"
    assert adapter.last_turn(SESSION).outcome == "completed"


def test_a_later_turn_resumes_by_session_id_on_the_same_model(adapter, tmp_path):
    key = adapter.new_session_id()
    adapter.headless_argv("dsh", _copy(tmp_path), "p", session=key, model="opencode-go/glm-5.3-flash")
    _finish(adapter, key, STREAM)

    argv = adapter.send_argv("dsh", SESSION, "correzioni")

    assert argv[argv.index("--session-id") + 1] == SESSION
    entries = {entry["id"]: entry["config"] for entry in _patch_of(argv)}
    assert entries["agent-default-model"]["model"] == "glm-5.3-flash"
    assert adapter.turn_log(SESSION).read_text(encoding="utf-8").startswith('{"type": "nexgen-request"')


def test_an_unknown_model_is_the_harness_refusal_not_a_fallback(adapter, tmp_path):
    key = adapter.new_session_id()
    adapter.headless_argv("dsh", _copy(tmp_path), "p", session=key, model="opencode-go/x")
    _finish(adapter, key, [STREAM[0], UNKNOWN_MODEL])

    turn = adapter.last_turn(SESSION)

    assert turn.failed
    assert turn.error_type == "UNKNOWN_MODEL"


def test_a_session_dsh_never_reported_is_missing(adapter):
    with pytest.raises(SessionMissing):
        adapter.measure("session-00000000-0000-0000-0000-000000000000")
    with pytest.raises(SessionMissing):
        adapter.send_argv("dsh", "session-00000000-0000-0000-0000-000000000000", "p")


def test_a_running_turn_is_not_stopped(adapter, tmp_path, monkeypatch):
    key = adapter.new_session_id()
    argv = adapter.headless_argv("dsh", _copy(tmp_path), "p", session=key)
    _finish(adapter, key, STREAM[:3])
    patch = argv[argv.index("--patch") + 1]
    monkeypatch.setattr(dsh.processes, "rows", lambda: [{"pid": 5, "command_line": f"node cli.js --patch {patch}"}])

    facts = adapter.measure(SESSION)

    assert facts.outcome == "working"
    assert not facts.stopped


def test_the_key_reaches_the_harness_through_its_own_variable(adapter):
    assert adapter.turn_env("opencode-go/deepseek-v4.1-flash") == {"OPENCODE_API_KEY": "chiave-finta"}
