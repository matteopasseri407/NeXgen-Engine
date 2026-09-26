"""Test del cancello calendario: proposta su campi espliciti, apply con --yes.

Nessun test tocca un account reale: il connettore e' sempre finto.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from nexgen_local.calendars import (
    CalendarError,
    apply_proposal,
    format_gate,
    list_proposals,
    propose_delete,
    propose_event,
)
from nexgen_local.config import LaneConfig
from nexgen_local.connectors import ConnectorError
from nexgen_local.connectors.auth import HttpResult


def _cfg(tmp_path: Path) -> LaneConfig:
    vault = tmp_path / "vault"
    vault.mkdir()
    return LaneConfig(
        vault_root=vault,
        repo_roots=(),
        model="fake-model",
        audit_path=tmp_path / "audit.jsonl",
        calendars_dir=tmp_path / "calendars",
    )


def _fake_calendar(monkeypatch: pytest.MonkeyPatch, store: dict) -> None:
    import nexgen_local.connectors.calendar as calendar_conn

    def _fake_http(req, timeout: int) -> HttpResult:
        url = req.full_url
        if url.endswith("/events") and req.data:
            body = json.loads(req.data.decode())
            eid = "e9"
            store[eid] = body
            return HttpResult(200, json.dumps({"id": eid, **body}).encode())
        if "/events/" in url and not req.data:
            eid = url.rsplit("/", 1)[-1]
            if req.get_method() == "DELETE":
                store.pop(eid, None)
                return HttpResult(200, b"")
            if eid in store:
                return HttpResult(200, json.dumps({"id": eid, **store[eid]}).encode())
            return HttpResult(404, b"{}")
        if "/events?" in url:
            items = [{"id": eid, **body} for eid, body in store.items()]
            return HttpResult(200, json.dumps({"items": items}).encode())
        raise AssertionError(f"chiamata non prevista: {url}")

    monkeypatch.setattr(calendar_conn, "_default_http", _fake_http)


def test_propose_and_apply_create(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _cfg(tmp_path)
    _fake_calendar(monkeypatch, {})
    proposal = propose_event(
        cfg, "Dentista", "2026-10-01T10:00:00+02:00", "2026-10-01T11:00:00+02:00", location="Studio"
    )
    assert proposal.kind == "create"
    gate = format_gate(proposal)
    assert "Dentista" in gate and "2026-10-01T10:00:00+02:00" in gate
    assert [p.id for p in list_proposals(cfg)] == [proposal.id]
    with pytest.raises(CalendarError, match="--yes"):
        apply_proposal(cfg, proposal.id, yes=False)
    result = apply_proposal(cfg, proposal.id, yes=True)
    assert result == {"id": proposal.id, "applied": True, "done_id": "e9", "kind": "create"}
    with pytest.raises(CalendarError, match="gia' applicata"):
        apply_proposal(cfg, proposal.id, yes=True)


def test_propose_delete_reads_target_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _cfg(tmp_path)
    store = {"e1": {"summary": "Vecchio", "start": {"dateTime": "2026-10-01T10:00:00+02:00"}}}
    _fake_calendar(monkeypatch, store)
    proposal = propose_delete(cfg, "primary", "e1")
    assert proposal.kind == "delete" and proposal.summary == "Vecchio"
    assert "e1" in format_gate(proposal)
    result = apply_proposal(cfg, proposal.id, yes=True)
    assert result["done_id"] == "e1"
    assert "e1" not in store
    with pytest.raises(CalendarError, match="non leggibile"):
        propose_delete(cfg, "primary", "e-sparito")


def test_propose_validates_fields(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    with pytest.raises(CalendarError, match="titolo"):
        propose_event(cfg, "", "2026-10-01T10:00:00+02:00", "2026-10-01T11:00:00+02:00")
    with pytest.raises(CalendarError, match="inizio/fine"):
        propose_event(cfg, "X", "", "")


def test_calendar_connector_lists_and_describes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import nexgen_local.connectors.calendar as calendar_conn

    cfg = _cfg(tmp_path)
    _fake_calendar(
        monkeypatch,
        {"e1": {"summary": "Dentista", "start": {"dateTime": "2026-10-01T10:00:00+02:00"}}},
    )
    _ = cfg
    items = calendar_conn.list_events("primary", "2026-10-01T00:00:00+02:00", "2026-10-08T00:00:00+02:00")
    assert len(items) == 1
    line = calendar_conn.describe_event(items[0])
    assert "Dentista" in line and "2026-10-01T10:00:00+02:00" in line


def test_cal_cli_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import argparse

    from nexgen_local import cli as cli_module

    cfg = _cfg(tmp_path)
    _fake_calendar(monkeypatch, {})
    monkeypatch.setattr(cli_module, "_config", lambda args: cfg)
    args = argparse.Namespace(
        summary="Dentista", start="2026-10-01T10:00:00+02:00", end="2026-10-01T11:00:00+02:00",
        description="", location="", calendar="primary", delete="", json=False,
    )
    assert cli_module.cmd_cal_propose(args) == 0
    proposal_id = next(iter(cfg.calendars_dir.glob("*.json"))).stem
    args = argparse.Namespace(proposal_id=proposal_id, yes=False, json=False)
    assert cli_module.cmd_cal_apply(args) == 1
    args = argparse.Namespace(proposal_id=proposal_id, yes=True, json=True)
    assert cli_module.cmd_cal_apply(args) == 0
