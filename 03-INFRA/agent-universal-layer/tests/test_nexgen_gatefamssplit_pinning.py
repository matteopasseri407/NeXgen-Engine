"""Pinning dello split gatefams prima del refactor.

Fissa il comportamento attuale (non quello desiderato) dei tre file
proposals.py, calendars.py e workflows.py con sole proposte e gate finti
in tmp_path: identita e hash delle proposte, propose/apply calendario
finto, propose/confirm workflow finto. MAI approvazioni vere, MAI rete,
MAI scritture fuori tmp.
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from nexgen_local.config import LaneConfig


def _cal_cfg(tmp_path: Path) -> LaneConfig:
    vault = tmp_path / "vault"
    vault.mkdir(parents=True, exist_ok=True)
    return LaneConfig(
        vault_root=vault,
        repo_roots=(),
        model="fake-model",
        audit_path=tmp_path / "audit.jsonl",
        calendars_dir=tmp_path / "calendars",
    )


def _wf_cfg(tmp_path: Path) -> LaneConfig:
    vault = tmp_path / "vault"
    vault.mkdir(parents=True, exist_ok=True)
    return LaneConfig(
        vault_root=vault,
        repo_roots=(),
        model="fake-model",
        audit_path=tmp_path / "audit.jsonl",
        workflows_dir=tmp_path / "workflows",
    )


def _wf_allowlist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "allowlist.json"
    path.write_text(
        json.dumps(
            {"workflows": {"telegram-send": {
                "webhook_url": "https://n8n.example/webhook/tg",
                "description": "Invia un file su Telegram",
            }}}
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("NEXGEN_WORKFLOWS_ALLOWLIST", str(path))


# ------------------------------------------------------- proposals.py


def test_proposals_id_format_and_validation() -> None:
    from nexgen_local import proposals as p

    assert p.valid_proposal_id("20261004-123000-aabbccdd") is True
    assert p.valid_proposal_id("../evil") is False
    assert p.valid_proposal_id("") is False
    fresh = p.new_proposal_id()
    assert p.valid_proposal_id(fresh) is True


def test_proposals_content_sha_stable_and_validated() -> None:
    from nexgen_local import proposals as p

    first = p.content_sha("create", "primary", "Dentista")
    second = p.content_sha("create", "primary", "Dentista")
    assert first == second
    assert first.startswith("v1:")
    assert p.content_sha("create", "primary", "Altro") != first
    # Contenuto invariato: nessuna eccezione.
    p.validate_content(first, ValueError, "create", "primary", "Dentista")
    # Contenuto cambiato: rifiuto prima di ogni tentativo.
    with pytest.raises(ValueError, match="contenuto cambiato"):
        p.validate_content(first, ValueError, "create", "primary", "Altro")
    with pytest.raises(ValueError, match="vincolo di integrita"):
        p.validate_content("", ValueError, "create")


def test_proposals_migrate_read_status_and_refuse(tmp_path: Path) -> None:
    from nexgen_local import proposals as p

    # Il marcatore legacy diventa attempted_at senza riaprire lavoro incerto.
    data = {"id": "x", "sending_at": "vecchio tentativo"}
    assert p.migrate_attempt(data)["attempted_at"] == "vecchio tentativo"

    pid = "20261004-123000-aabbccdd"
    target = tmp_path / f"{pid}.json"
    target.write_text(json.dumps({"id": pid, "attempted_at": ""}), encoding="utf-8")
    loaded = p.read_proposal_data(target, pid, ValueError)
    assert loaded["id"] == pid
    target.write_text(json.dumps({"id": "20261004-123000-eeeeeeee"}), encoding="utf-8")
    with pytest.raises(ValueError, match="identita"):
        p.read_proposal_data(target, pid, ValueError)

    pending = SimpleNamespace(attempted_at="", applied_at="")
    assert p.proposal_status(pending, "fatto") == "da approvare"
    uncertain = SimpleNamespace(attempted_at="t", applied_at="")
    assert p.proposal_status(uncertain, "fatto") == "esito da verificare"
    done = SimpleNamespace(attempted_at="t", applied_at="a")
    assert p.proposal_status(done, "fatto") == "fatto"
    with pytest.raises(ValueError, match="verifica l'esito"):
        p.refuse_prior_attempt(uncertain, ValueError)
    # Nessun tentativo precedente: nessun rifiuto.
    p.refuse_prior_attempt(pending, ValueError)


def test_proposals_record_attempt_needs_durable_save() -> None:
    from nexgen_local import proposals as p

    proposal = SimpleNamespace(attempted_at="", applied_at="")
    calls: list[str] = []
    p.record_attempt(proposal, lambda: calls.append("saved"), ValueError)
    assert proposal.attempted_at and calls == ["saved"]
    with pytest.raises(ValueError, match="verifica l'esito"):
        p.record_attempt(proposal, lambda: calls.append("again"), ValueError)
    assert calls == ["saved"]


# ------------------------------------------------------- calendars.py


def test_calendars_propose_event_and_gate(tmp_path: Path) -> None:
    from nexgen_local.calendars import format_gate, list_proposals, propose_event

    cfg = _cal_cfg(tmp_path)
    proposal = propose_event(
        cfg, "Dentista", "2026-10-01T10:00:00+02:00", "2026-10-01T11:00:00+02:00",
        location="Studio",
    )
    assert proposal.kind == "create"
    assert proposal.content_sha.startswith("v1:")
    gate = format_gate(proposal)
    assert "Dentista" in gate and "2026-10-01T10:00:00+02:00" in gate
    assert [item.id for item in list_proposals(cfg)] == [proposal.id]
    with pytest.raises(Exception, match="titolo"):
        propose_event(cfg, "", "2026-10-01T10:00:00+02:00", "2026-10-01T11:00:00+02:00")


def test_calendars_apply_with_fake_connector(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import nexgen_local.calendars as cal_mod
    from nexgen_local.calendars import CalendarError, apply_proposal, propose_event

    cfg = _cal_cfg(tmp_path)
    proposal = propose_event(cfg, "Dentista", "2026-10-01T10:00:00+02:00", "2026-10-01T11:00:00+02:00")
    with pytest.raises(CalendarError, match="--yes"):
        apply_proposal(cfg, proposal.id, yes=False)
    monkeypatch.setattr(cal_mod.calendar_conn, "create_event", lambda *a, **k: {"id": "e9"})
    result = apply_proposal(cfg, proposal.id, yes=True)
    assert result == {"id": proposal.id, "applied": True, "done_id": "e9", "kind": "create"}
    with pytest.raises(CalendarError, match="gia' applicata"):
        apply_proposal(cfg, proposal.id, yes=True)


# ------------------------------------------------------- workflows.py


def test_workflows_propose_and_gate_never_prints_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nexgen_local.workflows import format_gate, list_proposals, propose_run

    cfg = _wf_cfg(tmp_path)
    _wf_allowlist(tmp_path, monkeypatch)
    proposal = propose_run(cfg, "telegram-send", {"file": "a.txt"})
    assert proposal.workflow == "telegram-send"
    assert proposal.content_sha.startswith("v1:")
    gate = format_gate(proposal)
    assert "telegram-send" in gate
    assert "n8n.example" not in gate
    assert [item.id for item in list_proposals(cfg)] == [proposal.id]


def test_workflows_confirm_with_fake_http(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nexgen_local.workflows import HttpResult, WorkflowError, confirm_run, propose_run

    cfg = _wf_cfg(tmp_path)
    _wf_allowlist(tmp_path, monkeypatch)
    proposal = propose_run(cfg, "telegram-send", {"file": "a.txt"})
    with pytest.raises(WorkflowError, match="confirm"):
        confirm_run(cfg, proposal.id, False)
    calls: list[tuple] = []

    def _fake(url: str, payload: bytes, headers: dict, timeout: int) -> HttpResult:
        calls.append((url, json.loads(payload.decode()), headers))
        return HttpResult(200, b'{"ok": true}')

    done = confirm_run(cfg, proposal.id, True, http=_fake)
    assert done["ran"] is True and done["outcome"] == '{"ok": true}'
    assert calls[0][0] == "https://n8n.example/webhook/tg"
    assert calls[0][1] == {"file": "a.txt"}
    with pytest.raises(WorkflowError, match="gia' eseguita"):
        confirm_run(cfg, proposal.id, True, http=_fake)
    assert len(calls) == 1


def test_workflows_unknown_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nexgen_local.workflows import WorkflowError, propose_run

    cfg = _wf_cfg(tmp_path)
    _wf_allowlist(tmp_path, monkeypatch)
    with pytest.raises(WorkflowError, match="non consentito"):
        propose_run(cfg, " distruggi-tutto ", {})


# ------------------------------------------------------- firme pubbliche


def test_public_signatures_unchanged() -> None:
    from nexgen_local import calendars as cal_mod
    from nexgen_local import proposals as p
    from nexgen_local import workflows as wf_mod

    assert list(inspect.signature(p.valid_proposal_id).parameters) == ["proposal_id"]
    assert list(inspect.signature(p.new_proposal_id).parameters) == []
    assert list(inspect.signature(p.migrate_attempt).parameters) == ["data"]
    assert list(inspect.signature(p.read_proposal_data).parameters) == ["path", "proposal_id", "error"]
    assert list(inspect.signature(p.content_sha).parameters) == ["parts"]
    assert list(inspect.signature(p.validate_content).parameters) == ["saved", "error", "parts"]
    assert list(inspect.signature(p.refuse_prior_attempt).parameters) == ["proposal", "error"]
    assert list(inspect.signature(p.proposal_status).parameters) == ["proposal", "completed", "pending"]
    assert list(inspect.signature(p.record_attempt).parameters) == ["proposal", "save", "error"]
    assert list(inspect.signature(cal_mod.propose_event).parameters) == [
        "cfg", "summary", "start", "end", "description", "location", "calendar_id",
    ]
    assert list(inspect.signature(cal_mod.propose_delete).parameters) == ["cfg", "calendar_id", "event_id"]
    assert list(inspect.signature(cal_mod.format_gate).parameters) == ["proposal"]
    assert list(inspect.signature(cal_mod.apply_proposal).parameters) == ["cfg", "proposal_id", "yes"]
    assert list(inspect.signature(cal_mod.load_proposal).parameters) == ["cfg", "proposal_id"]
    assert list(inspect.signature(cal_mod.list_proposals).parameters) == ["cfg"]
    assert list(inspect.signature(wf_mod.propose_run).parameters) == ["cfg", "workflow", "params"]
    assert list(inspect.signature(wf_mod.format_gate).parameters) == ["proposal"]
    assert list(inspect.signature(wf_mod.confirm_run).parameters) == ["cfg", "proposal_id", "confirm", "http"]
    assert list(inspect.signature(wf_mod.load_proposal).parameters) == ["cfg", "proposal_id"]
    assert list(inspect.signature(wf_mod.list_proposals).parameters) == ["cfg"]
    assert list(inspect.signature(wf_mod.load_allowlist).parameters) == []
    assert list(inspect.signature(wf_mod.allowlist_path).parameters) == []


def test_split_helpers_in_sequence() -> None:
    import nexgen_local.calendars as cal_mod
    import nexgen_local.proposals as p_mod
    import nexgen_local.workflows as wf_mod

    p_src = Path(p_mod.__file__).read_text(encoding="utf-8")
    assert "def _valid_proposal_id_impl" in p_src
    assert "def _content_sha_impl" in p_src
    assert "def _validate_content_impl" in p_src
    valid_body = p_src[p_src.index("def valid_proposal_id"):]
    assert "_valid_proposal_id_impl(" in valid_body[: valid_body.index("\ndef ")]
    sha_body = p_src[p_src.index("def content_sha"):]
    assert "_content_sha_impl(" in sha_body[: sha_body.index("\ndef ")]

    cal_src = Path(cal_mod.__file__).read_text(encoding="utf-8")
    assert "def _propose_event_impl" in cal_src
    assert "def _propose_delete_impl" in cal_src
    assert "def _format_gate_impl" in cal_src
    propose_body = cal_src[cal_src.index("def propose_event"):]
    propose_body = propose_body[: propose_body.index("\ndef ")]
    assert "_propose_event_impl(" in propose_body

    wf_src = Path(wf_mod.__file__).read_text(encoding="utf-8")
    assert "def _propose_run_impl" in wf_src
    assert "def _format_gate_impl" in wf_src
    assert "def _confirm_run" in wf_src
    run_body = wf_src[wf_src.index("def propose_run"):]
    run_body = run_body[: run_body.index("\ndef ")]
    assert "_propose_run_impl(" in run_body
