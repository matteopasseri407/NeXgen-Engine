"""Test del loop agentico limitato: menu, provenienza, retry, tetti, trappole.

Il modello e' un fake che restituisce decisioni prescritte: qui si verifica il
contratto del motore, non il modello. Nessun framework serve.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from nexgen_local.config import LaneConfig
from nexgen_local.steps import Candidate, LoopState, build_menu, run_steps
from nexgen_local.tools import ToolRegistry


class ScriptedLLM:
    def __init__(self, route: dict | None = None, decisions: list | None = None, answers: list[str] | None = None) -> None:
        self.route = route or {"source": "vault", "keywords": ["airone"]}
        self.decisions = list(decisions or [])
        self.answers = list(answers or ["risposta finta"])
        self.choose_users: list[str] = []

    def json(self, system: str, user: str) -> dict | None:
        return self.route

    def text(self, system: str, user: str) -> str:
        return self.answers.pop(0) if self.answers else "risposta finta"

    def choose(self, system: str, user: str, actions: list[str]) -> dict | None:
        self.choose_users.append(user)
        if not self.decisions:
            return {"action": "escalate", "arg": ""}
        return self.decisions.pop(0)


def _cfg(tmp_path: Path) -> LaneConfig:
    vault = tmp_path / "vault"
    vault.mkdir(parents=True, exist_ok=True)
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    return LaneConfig(
        vault_root=vault,
        repo_roots=(repo,),
        model="fake-model",
        audit_path=tmp_path / "audit.jsonl",
    )


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _audit_lines(cfg: LaneConfig) -> list[dict]:
    return [json.loads(line) for line in cfg.audit_path.read_text(encoding="utf-8").strip().splitlines()]


def test_read_file_keeps_the_requested_repo_root(tmp_path: Path) -> None:
    """Explicit /B/nota.md reads B even when A holds the same relative path."""
    repo_a = tmp_path / "A"
    repo_b = tmp_path / "B"
    repo_a.mkdir()
    repo_b.mkdir()
    (repo_a / "nota.md").write_text("contenuto di A\n", encoding="utf-8")
    (repo_b / "nota.md").write_text("contenuto di B\n", encoding="utf-8")
    vault = tmp_path / "vault"
    vault.mkdir()
    cfg = LaneConfig(
        vault_root=vault,
        repo_roots=(repo_a, repo_b),
        model="fake-model",
        audit_path=tmp_path / "audit.jsonl",
    )
    target = str(repo_b / "nota.md")
    llm = ScriptedLLM(
        decisions=[
            {"action": "read_file", "arg": target},
            {"action": "answer", "arg": ""},
        ],
        answers=["letta la nota di B"],
    )
    result = run_steps(llm, ToolRegistry(cfg), cfg, f"Leggi {target} e riassumila.")
    assert result.escalated is False
    assert "contenuto di B" in result.collected
    assert "contenuto di A" not in result.collected
    assert any(str(repo_b) in str(receipt["args"].get("path", "")) for receipt in result.receipts)


def test_initial_menu_withholds_answer_until_retrieval(tmp_path: Path) -> None:
    """A request that needs a source cannot be answered from zero receipts."""
    cfg = _cfg(tmp_path)
    assert "answer" not in [c.action for c in build_menu(cfg, LoopState(task="x", route="vault"))]
    assert "answer" not in [
        c.action for c in build_menu(cfg, LoopState(task="x", route="vault", named_path="nota.md"))
    ]
    assert "answer" in [c.action for c in build_menu(cfg, LoopState(task="x", route="none"))]


def test_answer_first_is_refused_when_a_source_is_required(tmp_path: Path) -> None:
    """Choosing answer before any retrieval escalates with no answer."""
    cfg = _cfg(tmp_path)
    _write(cfg.vault_root / "nota.md", "La scadenza e' lunedi'.\n")
    llm = ScriptedLLM(decisions=[{"action": "answer", "arg": ""}])
    result = run_steps(llm, ToolRegistry(cfg), cfg, "Leggi nota.md e dimmi la scadenza.")
    assert result.answer == ""
    assert result.escalated is True
    assert result.receipts == []


def test_loop_mail_search_read_answer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The loop resolves sender, searches, reads the engine-found id, answers."""
    import nexgen_local.connectors.gmail as gmail_conn

    cfg = _cfg(tmp_path)
    monkeypatch.setattr(
        gmail_conn, "search_messages", lambda query, max_results=5: [{"id": "m1", "threadId": "t1"}]
    )
    monkeypatch.setattr(
        gmail_conn,
        "get_message",
        lambda mid, http=None: {
            "id": mid, "from": "commercialista@esempio.it", "to": "", "subject": "Budget",
            "date": "ieri", "snippet": "", "body": "Le richieste sono due.", "attachments": [],
        },
    )
    llm = ScriptedLLM(
        route={"source": "mail", "keywords": ["commercialista"]},
        decisions=[
            {"action": "search_mail", "arg": "commercialista"},
            {"action": "read_mail", "arg": "m1"},
            {"action": "answer", "arg": ""},
        ],
        answers=["Il commercialista chiede due cose. [mail:m1]"],
    )
    result = run_steps(llm, ToolRegistry(cfg), cfg, "Trova le mail del commercialista e riassumi le richieste.")
    assert result.escalated is False
    assert [d.action for d in result.decisions] == ["search_mail", "read_mail", "answer"]
    assert result.problems == []
    assert [r["tool"] for r in result.receipts] == ["search_mail", "read_mail"]


def test_read_mail_id_must_come_from_the_menu(tmp_path: Path) -> None:
    """A model-invented mail id is refused, like an off-menu path."""
    from nexgen_local.steps import Candidate, validate_action

    cfg = _cfg(tmp_path)
    state = LoopState(task="t", route="mail", receipts=[{"tool": "search_mail", "args": {}, "ok": True}])
    state.mail_ids = ["m1"]
    menu = [Candidate("read_mail", "m1"), Candidate("answer"), Candidate("escalate")]
    assert validate_action(cfg, state, menu, "read_mail", "m9-inventato") is not None
    assert validate_action(cfg, state, menu, "read_mail", "m1") is None


def test_draft_mail_accepts_the_read_id_or_empty(tmp_path: Path) -> None:
    """Echoing the just-read id is the same intent as the bare draft."""
    from nexgen_local.steps import Candidate, validate_action

    cfg = _cfg(tmp_path)
    state = LoopState(task="Rispondi", route="mail", want_reply=True, last_mail_id="m1")
    state.receipts = [{"tool": "read_mail", "args": {"id": "m1"}, "ok": True}]
    state.mail_ids = ["m1"]
    menu = [Candidate("draft_mail")]
    assert validate_action(cfg, state, menu, "draft_mail", "") is None
    assert validate_action(cfg, state, menu, "draft_mail", "m1") is None
    assert validate_action(cfg, state, menu, "draft_mail", "m9-inventato") is not None


def test_loop_reply_flow_drafts_but_never_sends(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """search_mail -> read_mail -> draft_mail -> answer: a draft artifact, zero sends."""
    import nexgen_local.connectors.gmail as gmail_conn

    vault = tmp_path / "vault"
    vault.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    cfg = LaneConfig(
        vault_root=vault, repo_roots=(repo,), model="fake-model",
        audit_path=tmp_path / "audit.jsonl", mails_dir=tmp_path / "mails",
        uploads_dir=tmp_path / "uploads",
    )
    monkeypatch.setattr(
        gmail_conn, "search_messages", lambda query, max_results=5: [{"id": "m1", "threadId": "t1"}]
    )
    monkeypatch.setattr(
        gmail_conn,
        "get_message",
        lambda mid, http=None: {
            "id": mid, "threadId": "t1", "from": "commercialista@esempio.it", "to": "",
            "subject": "Budget", "date": "ieri", "message-id": "", "snippet": "",
            "body": "Mandami le fatture.", "attachments": [],
        },
    )
    sent: list = []
    monkeypatch.setattr(
        gmail_conn, "reply_to", lambda *a, **k: sent.append((a, k)) or {"id": "x"}
    )
    llm = ScriptedLLM(
        decisions=[
            {"action": "search_mail", "arg": "commercialista"},
            {"action": "read_mail", "arg": "m1"},
            {"action": "draft_mail", "arg": ""},
            {"action": "answer", "arg": ""},
        ],
        answers=["Confermo tutto entro lunedi'.", "Bozza pronta."],
    )
    result = run_steps(llm, ToolRegistry(cfg), cfg, "Rispondi alla mail del commercialista confermando.")
    assert [d.action for d in result.decisions] == ["search_mail", "read_mail", "draft_mail", "answer"]
    assert sent == [], "il loop non invia mai"
    assert len(list((tmp_path / "mails").glob("*.json"))) == 1
    assert result.problems == []
    assert result.escalated is False


def test_loop_upload_flow_stages_but_never_sends(tmp_path: Path) -> None:
    """read_file -> propose_upload -> answer: a staged artifact, zero uploads."""
    vault = tmp_path / "vault"
    vault.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    cfg = LaneConfig(
        vault_root=vault, repo_roots=(repo,), model="fake-model",
        audit_path=tmp_path / "audit.jsonl", mails_dir=tmp_path / "mails",
        uploads_dir=tmp_path / "uploads",
    )
    _write(vault / "01-NOTE" / "airone.md", "Airone Blu e' un progetto dimostrativo.\n")
    target = str(vault / "01-NOTE" / "airone.md")
    llm = ScriptedLLM(
        decisions=[
            {"action": "read_file", "arg": target},
            {"action": "propose_upload", "arg": target},
            {"action": "answer", "arg": ""},
        ],
        answers=["Nota caricata in proposta."],
    )
    result = run_steps(llm, ToolRegistry(cfg), cfg, f"Carica {target} su Drive.")
    assert [d.action for d in result.decisions] == ["read_file", "propose_upload", "answer"]
    assert len(list((tmp_path / "uploads").glob("*.json"))) == 1
    assert result.problems == []
    assert result.escalated is False


def test_loop_calendar_search_read_answer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """search_calendar -> read_calendar -> answer, ids from the engine."""
    import nexgen_local.connectors.calendar as calendar_conn

    cfg = _cfg(tmp_path)
    monkeypatch.setattr(
        calendar_conn, "list_events",
        lambda *a, **k: [{"id": "e1", "summary": "Dentista",
                          "start": {"dateTime": "2026-10-01T10:00:00+02:00"},
                          "end": {"dateTime": "2026-10-01T11:00:00+02:00"}, "location": "Studio"}],
    )
    monkeypatch.setattr(
        calendar_conn, "get_event",
        lambda *a, **k: {"id": "e1", "summary": "Dentista",
                         "start": {"dateTime": "2026-10-01T10:00:00+02:00"},
                         "end": {"dateTime": "2026-10-01T11:00:00+02:00"},
                         "location": "Studio", "description": "Pulizia."},
    )
    llm = ScriptedLLM(
        route={"source": "calendar", "keywords": ["dentista"]},
        decisions=[
            {"action": "search_calendar", "arg": "dentista"},
            {"action": "read_calendar", "arg": "e1"},
            {"action": "answer", "arg": ""},
        ],
        answers=["Dentista il 2026-10-01 alle 10:00. [calendar:e1]"],
    )
    result = run_steps(llm, ToolRegistry(cfg), cfg, "Quando ho il dentista sul calendario?")
    assert [d.action for d in result.decisions] == ["search_calendar", "read_calendar", "answer"]
    assert result.problems == []
    assert result.escalated is False


def test_calendar_menu_forces_read_before_answer(tmp_path: Path) -> None:
    """Search hits carry times+title: the read is mandatory, no early answer."""
    cfg = _cfg(tmp_path)
    state = LoopState(task="Quando ho il dentista?", route="calendar")
    state.receipts = [{"tool": "search_calendar", "args": {"query": "dentista"}, "ok": True}]
    state.calendar_ids = ["e1"]
    actions = [c.action for c in build_menu(cfg, state)]
    assert actions[0] == "read_calendar"
    assert "answer" not in actions


def test_draft_mail_missing_without_reply_intent(tmp_path: Path) -> None:
    """No reply words in the task: the menu never offers draft_mail."""
    cfg = _cfg(tmp_path)
    state = LoopState(task="Riassumi la mail", route="mail")
    state.receipts = [{"tool": "read_mail", "args": {"id": "m1"}, "ok": True}]
    state.last_mail_id = "m1"
    actions = [c.action for c in build_menu(cfg, state)]
    assert "draft_mail" not in actions
    assert "answer" in actions


def test_reply_menu_prescribes_draft_before_answer(tmp_path: Path) -> None:
    """Task asks to reply + mail read: draft, sibling reads at most, no outs."""
    cfg = _cfg(tmp_path)
    state = LoopState(task="Rispondi alla mail", route="mail", want_reply=True)
    state.receipts = [{"tool": "read_mail", "args": {"id": "m1"}, "ok": True}]
    state.last_mail_id = "m1"
    state.read_ids = ["m1"]
    state.mail_ids = ["m1", "m2"]
    actions = [c.action for c in build_menu(cfg, state)]
    assert actions[0] == "draft_mail"
    assert "answer" not in actions
    assert "search_mail" not in actions
    assert "escalate" not in actions
    assert [c.arg for c in build_menu(cfg, state) if c.action == "read_mail"] == ["m2"]
    state.mail_draft = "20240101-000000-abcdef12"
    assert "answer" in [c.action for c in build_menu(cfg, state)]


def test_upload_menu_prescribes_propose_before_answer(tmp_path: Path) -> None:
    """Task asks to upload + file read: the gated propose alone."""
    cfg = _cfg(tmp_path)
    state = LoopState(task="Carica su Drive", route="vault", want_upload=True)
    state.receipts = [{"tool": "read_vault", "args": {"path": "a.md"}, "ok": True}]
    state.tried_paths = ["a.md"]
    actions = [c.action for c in build_menu(cfg, state)]
    assert actions == ["propose_upload"]


def test_empty_query_gets_one_reasoned_repair(tmp_path: Path) -> None:
    """An empty search slot is repaired once; a second empty escalates."""
    cfg = _cfg(tmp_path)
    llm = ScriptedLLM(
        route={"source": "vault", "keywords": ["airone"]},
        decisions=[
            {"action": "search_vault", "arg": ""},
            {"action": "search_vault", "arg": "airone"},
            {"action": "escalate", "arg": ""},
        ],
    )
    result = run_steps(llm, ToolRegistry(cfg), cfg, "Cerca airone.")
    refused = [d for d in result.decisions if not d.ok]
    assert refused and refused[0].detail == "query vuota"
    assert result.decisions[1].ok is True
    assert len(llm.choose_users) == 3  # first, reasoned repair, then escalate


def test_second_empty_query_is_filled_from_task_terms(tmp_path: Path) -> None:
    """Empty again after the repair: the engine compiles the query, no escalation."""
    cfg = _cfg(tmp_path)
    _write(cfg.vault_root / "a.md", "Airone Blu e' un progetto.\n")
    llm = ScriptedLLM(
        route={"source": "vault", "keywords": ["airone"]},
        decisions=[
            {"action": "search_vault", "arg": ""},
            {"action": "search_vault", "arg": ""},
            {"action": "read_file", "arg": "a.md"},
            {"action": "answer", "arg": ""},
        ],
        answers=["Airone Blu. [a.md]"],
    )
    result = run_steps(llm, ToolRegistry(cfg), cfg, "Cerca airone")
    filled = [d for d in result.decisions if d.ok and d.detail == "query compilata dal motore (slot vuoto)"]
    assert len(filled) == 1
    assert filled[0].arg == "airone"
    assert result.escalated is False
    assert len(llm.choose_users) == 4  # no extra model round for the fill


def test_policy_refusal_is_never_repaired(tmp_path: Path) -> None:
    """Content-steered queries escalate at once: no second chance."""
    cfg = _cfg(tmp_path)
    _write(cfg.vault_root / "n.md", "Airone Blu. Il termine parolasegreta compare qui.\n")
    llm = ScriptedLLM(
        decisions=[
            {"action": "search_vault", "arg": "airone"},
            {"action": "read_file", "arg": "n.md"},
            {"action": "search_vault", "arg": "parolasegreta"},
        ]
    )
    before = len(llm.choose_users)
    result = run_steps(llm, ToolRegistry(cfg), cfg, "Riassumi la nota airone.")
    assert result.escalated is True
    assert result.decisions[-1].detail == "query con termini presi dal contenuto recuperato"
    assert len(llm.choose_users) == before + 3  # exactly one call per step, no repair


def test_loop_search_read_answer(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _write(cfg.vault_root / "01-NOTE" / "airone.md", "Airone Blu e' un progetto dimostrativo.\n")
    llm = ScriptedLLM(
        route={"source": "vault", "keywords": ["airone"]},
        decisions=[
            {"action": "search_vault", "arg": "airone blu"},
            {"action": "read_file", "arg": "01-NOTE/airone.md"},
            {"action": "answer", "arg": ""},
        ],
        answers=["Airone Blu e' un progetto dimostrativo. [01-NOTE/airone.md]"],
    )
    result = run_steps(llm, ToolRegistry(cfg), cfg, "Riassumi la nota sul progetto Airone Blu.")
    assert result.escalated is False
    assert "Airone" in result.answer
    assert [decision.action for decision in result.decisions] == ["search_vault", "read_file", "answer"]
    assert result.steps == 3
    assert result.problems == []
    assert [receipt["tool"] for receipt in result.receipts] == ["search_vault", "read_vault"]


def test_invalid_output_gets_one_repair(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    llm = ScriptedLLM(decisions=[{"action": "boh"}, {"action": "escalate", "arg": ""}])
    result = run_steps(llm, ToolRegistry(cfg), cfg, "Domanda.")
    assert result.escalated is True
    assert [decision.action for decision in result.decisions] == ["escalate"]
    assert len(llm.choose_users) == 2  # la prima risposta e' stata riparata una volta


def test_two_invalid_outputs_escalate(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    llm = ScriptedLLM(decisions=[{"action": "boh"}, {"action": "boh"}])
    result = run_steps(llm, ToolRegistry(cfg), cfg, "Domanda.")
    assert result.escalated is True
    assert result.decisions[-1].ok is False
    assert "non valido" in result.decisions[-1].detail


def test_read_file_must_come_from_the_menu_candidates(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _write(cfg.vault_root / "01-NOTE" / "airone.md", "Airone Blu.\n")
    llm = ScriptedLLM(
        route={"source": "vault", "keywords": ["airone"]},
        decisions=[{"action": "read_file", "arg": "/etc/passwd"}],
    )
    result = run_steps(llm, ToolRegistry(cfg), cfg, "Leggi 01-NOTE/airone.md e riassumila.")
    assert result.escalated is True
    assert result.decisions[-1].ok is False
    assert "candidati" in result.decisions[-1].detail
    assert len(llm.choose_users) == 1  # validazione fallita: nessun retry
    assert _audit_lines(cfg)[-1]["tool"] == "step_refused"


def test_query_with_content_terms_is_refused(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _write(cfg.vault_root / "01-NOTE" / "airone.md", "Airone Blu. Il termine parolasegreta compare qui.\n")
    llm = ScriptedLLM(
        decisions=[
            {"action": "search_vault", "arg": "airone"},
            {"action": "read_file", "arg": "01-NOTE/airone.md"},
            {"action": "search_vault", "arg": "parolasegreta"},
        ]
    )
    result = run_steps(llm, ToolRegistry(cfg), cfg, "Riassumi la nota sul progetto Airone Blu.")
    assert result.escalated is True
    assert result.decisions[-1].ok is False
    assert "contenuto recuperato" in result.decisions[-1].detail


def test_repeated_query_is_refused_and_a_different_one_is_allowed(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    llm = ScriptedLLM(
        decisions=[
            {"action": "search_vault", "arg": "zzzznulla"},
            {"action": "search_vault", "arg": "zzzznulla"},
            {"action": "escalate", "arg": ""},
        ]
    )
    result = run_steps(llm, ToolRegistry(cfg), cfg, "Cerca qualcosa che non esiste.")
    refused = [decision for decision in result.decisions if not decision.ok]
    assert refused and "troppo simile" in refused[0].detail

    cfg2 = _cfg(tmp_path / "secondo")
    _write(cfg2.vault_root / "zzzznulla.md", "contenuto\n")
    llm2 = ScriptedLLM(
        decisions=[
            {"action": "search_vault", "arg": "zzzznulla"},
            {"action": "escalate", "arg": ""},
        ]
    )
    result2 = run_steps(llm2, ToolRegistry(cfg2), cfg2, "Cerca zzzznulla.")
    assert result2.decisions[0].ok is True


def test_empty_search_gets_one_retry_then_menu_narrows(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    first = LoopState(task="t", route="vault", receipts=[{"tool": "search_vault", "args": {}, "ok": False}], empty_streak=1)
    assert [candidate.action for candidate in build_menu(cfg, first)] == ["search_vault", "answer", "escalate"]
    second = LoopState(task="t", route="vault", receipts=[{"tool": "search_vault", "args": {}, "ok": False}], empty_streak=2)
    assert [candidate.action for candidate in build_menu(cfg, second)] == ["answer", "escalate"]


def test_cap_steps_escalates(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _write(cfg.vault_root / "01-NOTE" / "a.md", "Airone Blu e' un progetto.\n")
    _write(cfg.vault_root / "01-NOTE" / "b.md", "Airone Blu, seconda nota di progetto.\n")
    llm = ScriptedLLM(
        decisions=[
            {"action": "search_vault", "arg": "airone"},
            {"action": "read_file", "arg": "01-NOTE/a.md"},
            {"action": "search_vault", "arg": "progetto blu"},
            {"action": "read_file", "arg": "01-NOTE/b.md"},
        ]
    )
    result = run_steps(llm, ToolRegistry(cfg), cfg, "Riassumi la nota sul progetto Airone Blu.", max_steps=4)
    assert result.escalated is True
    assert result.decisions[-1].detail == "cap"


def test_route_none_offers_only_answer_or_escalate(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    menu = build_menu(cfg, LoopState(task="Che ore sono?", route="none"))
    assert [candidate.action for candidate in menu] == ["answer", "escalate"]


def test_answer_claim_check_runs_in_the_loop(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _write(cfg.vault_root / "01-NOTE" / "airone.md", "Airone Blu e' un progetto.\n")
    llm = ScriptedLLM(
        decisions=[
            {"action": "search_vault", "arg": "airone"},
            {"action": "read_file", "arg": "01-NOTE/airone.md"},
            {"action": "answer", "arg": ""},
        ],
        answers=[
            "Ho letto la nota fantasma.md e l'ho riassunta.",
            "Ho letto la nota fantasma.md di nuovo.",
        ],
    )
    result = run_steps(llm, ToolRegistry(cfg), cfg, "Riassumi la nota sul progetto Airone Blu.")
    assert result.confabulation is True
    assert result.corrections == 1
    assert result.problems


def test_retry_with_a_generic_word_does_not_read_the_wrong_note(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _write(cfg.vault_root / "01-NOTE" / "airone.md", "Airone Blu e' un progetto dimostrativo.\n")
    llm = ScriptedLLM(
        decisions=[
            {"action": "search_vault", "arg": "Falco Rosso"},
            {"action": "search_vault", "arg": "progetto Falco"},
            {"action": "answer", "arg": ""},
        ],
        answers=["Non ho trovato nessuna nota su Falco Rosso."],
    )
    result = run_steps(llm, ToolRegistry(cfg), cfg, "Trova e riassumi la nota sul progetto Falco Rosso.")
    tools_called = [receipt["tool"] for receipt in result.receipts]
    assert "read_vault" not in tools_called
    assert result.escalated is False
    assert "Non ho trovato" in result.answer


def test_answer_correction_clears_unsupported_claims(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _write(cfg.vault_root / "01-NOTE" / "airone.md", "Airone Blu e' un progetto.\n")
    llm = ScriptedLLM(
        decisions=[
            {"action": "search_vault", "arg": "airone"},
            {"action": "read_file", "arg": "01-NOTE/airone.md"},
            {"action": "answer", "arg": ""},
        ],
        answers=[
            "Ho letto la nota fantasma.md e l'ho riassunta.",
            "Il contenuto letto dice che Airone Blu e' un progetto. [01-NOTE/airone.md]",
        ],
    )
    result = run_steps(llm, ToolRegistry(cfg), cfg, "Riassumi la nota sul progetto Airone Blu.")
    assert result.correction_used is True
    assert result.corrections == 1
    assert result.problems == []
    assert "Airone" in result.answer


def test_answer_correction_failure_keeps_the_flag(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _write(cfg.vault_root / "01-NOTE" / "airone.md", "Airone Blu e' un progetto.\n")
    llm = ScriptedLLM(
        decisions=[
            {"action": "search_vault", "arg": "airone"},
            {"action": "read_file", "arg": "01-NOTE/airone.md"},
            {"action": "answer", "arg": ""},
        ],
        answers=[
            "Ho letto la nota fantasma.md e l'ho riassunta.",
            "Ho letto la nota fantasma.md di nuovo.",
        ],
    )
    result = run_steps(llm, ToolRegistry(cfg), cfg, "Riassumi la nota sul progetto Airone Blu.")
    assert result.corrections == 1
    assert result.correction_used is False
    assert result.confabulation is True


def test_prompt_keeps_only_recent_observations(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _write(cfg.vault_root / "01-NOTE" / "a.md", "Airone Blu e' un progetto.\n")
    _write(cfg.vault_root / "01-NOTE" / "b.md", "Airone Blu, seconda nota di progetto.\n")
    llm = ScriptedLLM(
        decisions=[
            {"action": "search_vault", "arg": "airone"},
            {"action": "read_file", "arg": "01-NOTE/a.md"},
            {"action": "search_vault", "arg": "progetto blu"},
            {"action": "answer", "arg": ""},
        ],
        answers=["Ok [01-NOTE/a.md]"],
    )
    run_steps(llm, ToolRegistry(cfg), cfg, "Riassumi la nota sul progetto Airone Blu.")
    last = llm.choose_users[-1]
    assert "passi precedenti: 1" in last
    assert last.count("Osservazioni recenti") == 1


def test_loop_outlook_search_read_answer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """search_outlook -> read_outlook -> answer, ids from the engine."""
    import nexgen_local.connectors.outlook as outlook_conn

    cfg = _cfg(tmp_path)
    monkeypatch.setattr(
        outlook_conn, "search_messages", lambda query, max_results=5: [{"id": "o9"}]
    )
    monkeypatch.setattr(
        outlook_conn,
        "get_message",
        lambda mid, http=None: {
            "id": mid, "from": "capo@esempio.it", "to": "", "subject": "Riunione",
            "date": "ieri", "snippet": "", "body": "Riunione giovedi.", "attachments": [],
        },
    )
    llm = ScriptedLLM(
        route={"source": "outlook", "keywords": ["riunione"]},
        decisions=[
            {"action": "search_outlook", "arg": "riunione"},
            {"action": "read_outlook", "arg": "o9"},
            {"action": "answer", "arg": ""},
        ],
        answers=["Riunione giovedi'. [outlook:o9]"],
    )
    result = run_steps(llm, ToolRegistry(cfg), cfg, "Trova su Outlook la riunione.")
    assert [d.action for d in result.decisions] == ["search_outlook", "read_outlook", "answer"]
    assert result.problems == []
    assert result.escalated is False


def test_post_search_menu_has_no_answer_before_read(tmp_path: Path) -> None:
    """Search lines are not content: mail/drive/outlook read first, like calendar."""
    from nexgen_local.steps import validate_action

    cfg = _cfg(tmp_path)
    cases = [
        ("mail", "search_mail", "mail_ids", "read_mail", ["m1", "m2"]),
        ("drive", "search_drive", "drive_ids", "read_drive", ["d1", "d2"]),
        ("outlook", "search_outlook", "outlook_ids", "read_outlook", ["o1", "o2"]),
    ]
    for route, tool, ids_attr, read_action, ids in cases:
        state = LoopState(task="cerca qualcosa", route=route)
        state.receipts = [{"tool": tool, "args": {"query": "x"}, "ok": True}]
        setattr(state, ids_attr, list(ids))
        menu = build_menu(cfg, state)
        assert "answer" not in [c.action for c in menu], route
        assert [c.arg for c in menu if c.action == read_action] == ids, route
        # Answer is not even on the menu; the guard below is the second wall
        # for any path that still offers it with results unread.
        guarded = [Candidate("answer"), Candidate("escalate")]
        problem = validate_action(cfg, state, guarded, "answer", "")
        assert problem is not None and "ancora da leggere" in problem, route


def test_answer_allowed_after_empty_search(tmp_path: Path) -> None:
    """No ids means truly empty: the engine may report the void."""
    from nexgen_local.steps import validate_action

    cfg = _cfg(tmp_path)
    state = LoopState(task="cerca il nulla", route="mail", empty_streak=1)
    state.receipts = [{"tool": "search_mail", "args": {"query": "zzz"}, "ok": True}]
    state.mail_ids = []
    menu = build_menu(cfg, state)
    assert "answer" in [c.action for c in menu]
    assert validate_action(cfg, state, menu, "answer", "") is None


def test_read_menu_keeps_unread_siblings(tmp_path: Path) -> None:
    """After the first read the second stays one choice away: compare, don't re-find."""
    cfg = _cfg(tmp_path)
    state = LoopState(task="Confronta i due contratti", route="drive")
    state.receipts = [{"tool": "read_drive", "args": {"id": "d1"}, "ok": True}]
    state.drive_ids = ["d1", "d2"]
    state.read_ids = ["d1"]
    state.drive = ["[drive:Contratto (d1)]\ntesto uno"]
    menu = build_menu(cfg, state)
    actions = [c.action for c in menu]
    assert actions[0] == "answer"
    assert Candidate("read_drive", "d2") in menu
    assert Candidate("read_drive", "d1") not in menu
    assert "escalate" in actions


def test_post_read_search_matches_route(tmp_path: Path) -> None:
    """After an Outlook read the menu offers Outlook search, never Drive."""
    cfg = _cfg(tmp_path)
    state = LoopState(task="Trova su Outlook la riunione", route="outlook")
    state.receipts = [{"tool": "read_outlook", "args": {"id": "o9"}, "ok": True}]
    state.outlook_ids = ["o9"]
    state.read_ids = ["o9"]
    state.outlook = ["[outlook:o9]\nRiunione giovedi'."]
    actions = [c.action for c in build_menu(cfg, state)]
    assert "search_outlook" in actions
    assert "search_drive" not in actions
    assert "search_mail" not in actions


def test_result_carries_draft_id_and_preview(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The draft exists and the answer prompt saw it: id in collected, id in result."""
    import nexgen_local.connectors.gmail as gmail_conn

    cfg = _cfg(tmp_path)
    monkeypatch.setattr(
        gmail_conn, "search_messages", lambda query, max_results=5: [{"id": "m1", "threadId": "t1"}]
    )
    monkeypatch.setattr(
        gmail_conn,
        "get_message",
        lambda mid, http=None: {
            "id": mid, "threadId": "t1", "from": "commercialista@esempio.it", "to": "",
            "subject": "Budget", "date": "ieri", "message-id": "", "snippet": "",
            "body": "Mandami le fatture.", "attachments": [],
        },
    )
    llm = ScriptedLLM(
        route={"source": "mail", "keywords": ["commercialista"]},
        decisions=[
            {"action": "search_mail", "arg": "commercialista"},
            {"action": "read_mail", "arg": "m1"},
            {"action": "draft_mail", "arg": ""},
            {"action": "answer", "arg": ""},
        ],
        answers=["Confermo entro lunedi'.", "Bozza pronta con id."],
    )
    result = run_steps(llm, ToolRegistry(cfg), cfg, "Rispondi alla mail del commercialista.")
    assert result.mail_draft, "l'id bozza deve tornare dal motore, non dalla prosa"
    assert result.mail_draft in result.collected
    assert result.mail_draft in result.answer, "il puntatore di approvazione e' nel testo finale"
    assert "commercialista@esempio.it" in result.answer
    assert result.problems == []
    assert (cfg.mails_dir / f"{result.mail_draft}.json").is_file()


def test_result_carries_upload_id_and_preview(tmp_path: Path) -> None:
    """Same contract for uploads: id in collected, id in result."""
    vault = tmp_path / "vault"
    vault.mkdir()
    cfg = LaneConfig(
        vault_root=vault, repo_roots=(), model="fake-model",
        audit_path=tmp_path / "audit.jsonl", mails_dir=tmp_path / "mails",
        uploads_dir=tmp_path / "uploads",
    )
    _write(vault / "nota.md", "Contenuto da caricare.\n")
    target = str(vault / "nota.md")
    llm = ScriptedLLM(
        decisions=[
            {"action": "read_file", "arg": target},
            {"action": "propose_upload", "arg": target},
            {"action": "answer", "arg": ""},
        ],
        answers=["Proposta pronta."],
    )
    result = run_steps(llm, ToolRegistry(cfg), cfg, f"Carica {target} su Drive.")
    assert result.upload_proposal, "l'id proposta deve tornare dal motore, non dalla prosa"
    assert result.upload_proposal in result.collected
    assert result.upload_proposal in result.answer, "il puntatore di approvazione e' nel testo finale"
    assert result.problems == []


def test_answer_without_read_escalates_instead_of_false_absence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A search followed by answer-without-read never reports 'no results'."""
    import nexgen_local.connectors.gmail as gmail_conn
    from nexgen_local.engine import ENGINE_ABSENCE

    cfg = _cfg(tmp_path)
    monkeypatch.setattr(
        gmail_conn, "search_messages", lambda query, max_results=5: [{"id": "m1", "threadId": "t1"}]
    )
    monkeypatch.setattr(
        gmail_conn,
        "get_message",
        lambda mid, http=None: {
            "id": mid, "threadId": "t1", "from": "a@b.cc", "to": "", "subject": "S",
            "date": "", "message-id": "", "snippet": "", "body": "B", "attachments": [],
        },
    )
    llm = ScriptedLLM(
        route={"source": "mail", "keywords": ["budget"]},
        decisions=[
            {"action": "search_mail", "arg": "budget"},
            {"action": "answer", "arg": ""},
        ],
    )
    result = run_steps(llm, ToolRegistry(cfg), cfg, "Trova la mail sul budget.")
    assert result.escalated is True
    assert result.answer != ENGINE_ABSENCE
