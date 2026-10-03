"""Real tool receipts distinguish retrieved text from operational outcomes."""
from __future__ import annotations

import json

import pytest

from nexgen_local.config import LaneConfig
from nexgen_local.engine import ENGINE_ABSENCE, ENGINE_ERROR, run_lane
from nexgen_local.evidence import retrieval_outcome
from nexgen_local.graph import run_graph
from nexgen_local.jobs import job_research
from nexgen_local.steps import run_steps
from nexgen_local.tools import ToolCall, ToolError, ToolRegistry


class ScriptedLLM:
    def __init__(self):
        self.users = []
        self.decisions = iter(({"action": "read_file", "arg": "nota.md"}, {"action": "answer", "arg": ""}))

    def json(self, *args):
        raise AssertionError("An explicit file must not invoke the router")

    def choose(self, *args):
        return next(self.decisions)

    def text(self, system, user):
        self.users.append(user)
        return "Il documento contiene una bozza."


def config(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    return LaneConfig(vault_root=vault, repo_roots=(), model="synthetic", audit_path=tmp_path / "audit.jsonl",
                      research_dir=tmp_path / "research")


@pytest.mark.parametrize("content", ["(bozza) Contenuto valido.", "(nessun risultato)", "(lettura fallita: citazione)"])
@pytest.mark.parametrize("driver", [run_lane, run_graph, run_steps])
def test_parenthesized_source_is_retrieved_by_every_driver(tmp_path, driver, content):
    cfg = config(tmp_path)
    (cfg.vault_root / "nota.md").write_text(content, encoding="utf-8")
    llm, tools = ScriptedLLM(), ToolRegistry(cfg)
    result = driver(llm, tools, cfg, "leggi nota.md")
    assert llm.users and content in llm.users[-1]
    assert not result.confabulation
    assert tools.calls[-1].ok
    events = [json.loads(line) for line in cfg.audit_path.read_text().splitlines()]
    reads = [event for event in events if event["tool"] == "read_vault"]
    assert reads and reads[-1]["ok"]
    assert reads[-1]["status"] == "ok"


def test_real_empty_search_is_distinct_from_literal_empty_message(tmp_path):
    cfg = config(tmp_path)
    tools = ToolRegistry(cfg)
    assert tools.search_vault("absent") == "(nessun risultato)"
    assert not tools.calls[-1].ok
    (cfg.vault_root / "nota.md").write_text("(nessun risultato)", encoding="utf-8")
    assert tools.read_vault("nota.md") == "(nessun risultato)"
    assert tools.calls[-1].ok


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_parenthesized_continuation_has_successful_receipt(tmp_path, newline):
    cfg = config(tmp_path)
    prefix = f"prima{newline}"
    (cfg.vault_root / "nota.md").write_bytes(f"{prefix}(bozza) seconda finestra".encode("utf-8"))
    tools = ToolRegistry(cfg)
    assert tools.read_vault("nota.md", offset=len(prefix)).startswith("(bozza)")
    assert tools.calls[-1].ok


@pytest.mark.parametrize("driver", [run_lane, run_graph, run_steps])
@pytest.mark.parametrize("status,message,answer", [
    ("empty", "Nessuna corrispondenza.", ENGINE_ABSENCE),
    ("error", "Connection unavailable.", ENGINE_ERROR),
])
def test_driver_outcome_does_not_depend_on_display_language(tmp_path, driver, status, message, answer):
    class Registry(ToolRegistry):
        def read_vault(self, path, offset=0):
            return self._record("read_vault", {"path": path}, message, status=status)

    cfg = config(tmp_path)
    (cfg.vault_root / "nota.md").write_text("Contenuto.", encoding="utf-8")
    llm = ScriptedLLM()
    result = driver(llm, Registry(cfg), cfg, "leggi nota.md")
    assert result.answer == answer
    assert not llm.users


class OfflineRegistry(ToolRegistry):
    """Empty remote sources are explicit and never contact personal accounts."""
    def web_search(self, query):
        return self._record("web_search", {"query": query}, "No matches.", status="empty")

    def call(self, name, args):
        if name in ("search_mail", "search_drive", "search_calendar", "search_outlook"):
            return self._record(name, args, "No matches.", status="empty")
        return super().call(name, args)


def test_research_job_retains_parenthesized_content(tmp_path):
    cfg = config(tmp_path)
    content = "(bozza) Contenuto valido."
    (cfg.vault_root / "nota.md").write_text(content, encoding="utf-8")
    llm = ScriptedLLM()
    result = job_research(llm, OfflineRegistry(cfg), cfg, "bozza")
    assert content in llm.users[-1]
    assert not result.confabulation


def test_explicit_status_survives_persistent_research_resume(tmp_path):
    from nexgen_local.research_graph import research_task
    cfg = config(tmp_path)
    (cfg.vault_root / "nota.md").write_text("(bozza) Contenuto valido.", encoding="utf-8")
    llm = ScriptedLLM()
    first = research_task(llm, cfg, "leggi nota.md", session_id="")
    llm.decisions = iter(({"action": "answer", "arg": ""},))
    resumed = research_task(llm, cfg, "continua", session_id=first["session_id"])
    assert "(bozza)" in llm.users[-1]
    assert first["receipts"] == resumed["receipts"]
    assert resumed["receipts"][0]["status"] == "ok"
    assert not resumed["confabulation"]


def test_audit_failure_cannot_leave_a_successful_receipt(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    (cfg.vault_root / "nota.md").write_text("Contenuto.", encoding="utf-8")
    tools = ToolRegistry(cfg)

    def fail(*args, **kwargs):
        raise ToolError("audit unavailable")

    monkeypatch.setattr(tools, "_audit", fail)
    with pytest.raises(ToolError, match="audit unavailable"):
        tools.call_result("read_vault", {"path": "nota.md"})
    assert tools.calls == []


@pytest.mark.parametrize("binary", [False, True])
def test_real_file_failure_has_an_explicit_error_receipt(tmp_path, monkeypatch, binary):
    cfg = config(tmp_path)
    path = cfg.vault_root / "nota.md"
    path.write_bytes(b"\x00data" if binary else b"text")
    tools = ToolRegistry(cfg)
    if not binary:
        def fail(*args):
            raise OSError("synthetic read failure")
        monkeypatch.setattr(type(path), "read_bytes", fail)
    result = tools.call_result("read_vault", {"path": "nota.md"})
    assert result.status == "error" and not result.usable
    assert retrieval_outcome(tools.calls, tools.refusals, "") == "error"


def test_empty_file_and_read_past_end_are_empty_operations(tmp_path):
    cfg = config(tmp_path)
    (cfg.vault_root / "nota.md").write_text("", encoding="utf-8")
    tools = ToolRegistry(cfg)
    result = tools.call_result("read_vault", {"path": "nota.md", "offset": 100})
    assert result.status == "empty" and not result.usable
    assert retrieval_outcome(tools.calls, tools.refusals, "") == "empty"


def test_old_receipts_remain_readable_without_status():
    old = ToolCall(name="search_vault", args={"query": "x"}, ok=False, chars=0)
    assert retrieval_outcome([old], ["(nessun risultato)"], "") == "empty"
    assert retrieval_outcome([old], ["(backend non raggiungibile)"], "") == "error"


def test_structured_dispatch_requires_a_fresh_receipt(tmp_path, monkeypatch):
    tools = ToolRegistry(config(tmp_path))
    tools._record("read_vault", {"path": "previous.md"}, "Prior result.")
    monkeypatch.setattr(tools, "read_vault", lambda *args: "unrecorded")
    with pytest.raises(ToolError, match="ricevuta"):
        tools.call_result("read_vault", {"path": "current.md"})


def test_unknown_tool_has_error_outcome_and_audit(tmp_path):
    cfg = config(tmp_path)
    tools = ToolRegistry(cfg)
    result = tools.call_result("unknown", {})
    assert result.status == "error" and not result.usable
    assert json.loads(cfg.audit_path.read_text())["status"] == "error"
