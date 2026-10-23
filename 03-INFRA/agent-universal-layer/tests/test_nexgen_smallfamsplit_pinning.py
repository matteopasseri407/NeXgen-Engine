"""Pinning dello smallfam-split prima del refactor.

Fissa il comportamento attuale (non quello desiderato) dei due file
calendar.py e cmds/tier.py con soli backend finti:
lettura calendario finta ritorna eventi, verdetto promozione blocca su
suite mancanti. Solo HTTP finto, solo tmp_path, nessuna rete, nessun
token vero.
"""
from __future__ import annotations

import argparse
import inspect
import json
import time
from pathlib import Path

import pytest

from nexgen_local.connectors import ConnectorError
from nexgen_local.connectors import calendar as calendar_mod
from nexgen_local.connectors.auth import HttpResult


def _token_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "tokens"
    directory.mkdir(exist_ok=True)
    (directory / "tokens.json").write_text(
        json.dumps(
            {"refresh_token": "r", "access_token": "a", "expires_at": int(time.time()) + 3600}
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("WORKSPACE_MCP_TOKEN_DIR", str(directory))
    return directory


def _cal_http(routes: dict[str, tuple[int, bytes]], calls: list[str]):
    def call(req, timeout: int) -> HttpResult:
        calls.append(req.full_url)
        for marker, (status, body) in routes.items():
            if marker in req.full_url:
                return HttpResult(status, body)
        raise AssertionError(f"chiamata non prevista: {req.full_url}")

    return call


def _cfg(tmp_path: Path):
    from nexgen_local.config import LaneConfig

    vault = tmp_path / "vault"
    vault.mkdir(parents=True, exist_ok=True)
    return LaneConfig(
        vault_root=vault,
        repo_roots=(),
        model="fake-model",
        audit_path=tmp_path / "audit.jsonl",
    )


# ------------------------------------------------------- calendar.py


def test_calendar_list_returns_items(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _token_dir(tmp_path, monkeypatch)
    calls: list[str] = []
    payload = {
        "items": [
            {
                "id": "e1",
                "summary": "Dentista",
                "location": "Studio Rossi",
                "start": {"dateTime": "2026-10-01T10:00:00+02:00"},
                "end": {"dateTime": "2026-10-01T11:00:00+02:00"},
            }
        ]
    }
    http = _cal_http({"/calendars/primary/events?": (200, json.dumps(payload).encode())}, calls)
    items = calendar_mod.list_events(http=http)
    assert len(items) == 1 and items[0]["id"] == "e1"
    assert len(calls) == 1


def test_calendar_empty_id_and_describe_stable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _token_dir(tmp_path, monkeypatch)
    with pytest.raises(ConnectorError, match="id evento vuoto"):
        calendar_mod.get_event(http=lambda req, t: HttpResult(200, b"{}"))
    with pytest.raises(ConnectorError, match="id evento vuoto"):
        calendar_mod.delete_event(event_id="  ", http=lambda req, t: HttpResult(200, b""))
    item = {
        "id": "e1",
        "summary": "Dentista",
        "location": "Studio Rossi",
        "start": {"dateTime": "2026-10-01T10:00:00+02:00"},
        "end": {"dateTime": "2026-10-01T11:00:00+02:00"},
    }
    assert calendar_mod.when({"dateTime": "2026-10-01T10:00:00+02:00"}) == "2026-10-01T10:00:00+02:00"
    assert calendar_mod.when({"date": "2026-10-02"}) == "2026-10-02"
    line = calendar_mod.describe_event(item)
    assert "e1" in line and "Dentista" in line and "Studio Rossi" in line


# ------------------------------------------------------- cmds/tier.py


def test_tier_promotion_verdict_blocks_missing_suite() -> None:
    from nexgen_local.cmds.tier import promotion_verdict

    clean = {"totals": {"ok": 4, "ko": 0, "injection": 0, "confab": 0}}
    ok, reasons = promotion_verdict({"traps": clean, "capability": clean, "agent": clean})
    assert ok is True and reasons == []
    ok, reasons = promotion_verdict({"traps": clean, "capability": clean})
    assert ok is False and any("non eseguita" in r for r in reasons)


def test_tier_evaluate_uses_fake_suites_without_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import nexgen_local.evals as evals_mod
    from nexgen_local.cmds.tier import evaluate_for_promotion, format_evaluation

    clean = {"totals": {"ok": 4, "ko": 0, "injection": 0, "confab": 0}, "tasks": []}

    def _fake_run_suite(llm, model, suite, workdir, tier="tutor"):
        assert suite in ("traps", "capability", "agent")
        assert Path(workdir).parent == tmp_path
        return dict(clean)

    monkeypatch.setattr(evals_mod, "run_suite", _fake_run_suite)
    result = evaluate_for_promotion(object(), "fake-model", "guided", tmp_path)
    assert result["recommend"] is True
    assert result["candidate"] == "guided" and result["model"] == "fake-model"
    text = format_evaluation(result, "tutor")
    assert "fake-model" in text and "guided" in text and "RACCOMANDAZIONE" in text


def test_tier_set_requires_yes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nexgen_local.cmds.tier import cmd_tier_set

    monkeypatch.setenv("NEXGEN_HOME", str(tmp_path / "home"))
    rc = cmd_tier_set(argparse.Namespace(model="fake-model", tier="guided", yes=False))
    assert rc == 2


# ------------------------------------------------------- firme pubbliche


def test_public_signatures_unchanged() -> None:
    assert list(inspect.signature(calendar_mod.list_events).parameters) == [
        "calendar_id", "time_min", "time_max", "max_results", "http", "q",
    ]
    assert list(inspect.signature(calendar_mod.get_event).parameters) == [
        "calendar_id", "event_id", "http",
    ]
    assert list(inspect.signature(calendar_mod.when).parameters) == ["value"]
    assert list(inspect.signature(calendar_mod.describe_event).parameters) == ["item"]
    assert list(inspect.signature(calendar_mod.create_event).parameters) == [
        "summary", "start", "end", "description", "location", "calendar_id", "http",
    ]
    assert list(inspect.signature(calendar_mod.delete_event).parameters) == [
        "calendar_id", "event_id", "http",
    ]
    from nexgen_local.cmds import tier as tier_mod

    assert list(inspect.signature(tier_mod.promotion_verdict).parameters) == ["reports"]
    assert list(inspect.signature(tier_mod.evaluate_for_promotion).parameters) == [
        "llm", "model", "candidate", "workdir",
    ]
    assert list(inspect.signature(tier_mod.format_evaluation).parameters) == ["result", "current"]
    assert list(inspect.signature(tier_mod.cmd_tier_evaluate).parameters) == ["args"]
    assert list(inspect.signature(tier_mod.cmd_tier_set).parameters) == ["args"]
