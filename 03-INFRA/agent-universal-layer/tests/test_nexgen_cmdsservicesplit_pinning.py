"""Pinning dello split salute/servizio di cmds/service prima del refactor.

Fissa il comportamento attuale (non quello desiderato) con comandi finti:
cmd_doctor finto ritorna esito salute, cmd_mcp finto ritorna esito,
comando sconosciuto rifiutato con errore chiaro. Solo tmp_path,
solo comandi finti, nessuna rete, nessun servizio reale.
"""
from __future__ import annotations

import argparse
import inspect
import io
import json
import sys
import types
from pathlib import Path

import pytest

from nexgen_local.config import LaneConfig


def _cfg(tmp_path: Path) -> LaneConfig:
    vault = tmp_path / "vault"
    vault.mkdir(exist_ok=True)
    return LaneConfig(
        vault_root=vault,
        repo_roots=(),
        model="fake-model",
        audit_path=tmp_path / "audit.jsonl",
    )


def _doctor_args(as_json: bool = False) -> argparse.Namespace:
    return argparse.Namespace(
        model=None,
        vault=None,
        audit=None,
        json=as_json,
    )


def _mcp_args(jobs_only: bool = False) -> argparse.Namespace:
    return argparse.Namespace(
        jobs_only=jobs_only,
        model=None,
        router_model=None,
        answer_model=None,
        vault=None,
        repo=None,
        audit=None,
    )


def _stub_doctor_deps(monkeypatch: pytest.MonkeyPatch, cfg: LaneConfig) -> None:
    """Solo finti in tmp_path: mai servizi reali, mai rete."""
    from nexgen_local.cmds import service as svc_mod

    monkeypatch.setattr(svc_mod, "_config", lambda args: cfg)
    monkeypatch.setattr(svc_mod, "audit_writable", lambda _cfg: True)
    monkeypatch.setattr(svc_mod.shutil, "which", lambda _cmd: None)
    # Dipendenze locali finte: esito deterministico senza installazioni reali.
    for name in ("langchain_ollama", "langgraph"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    import nexgen_local.connectors.auth as auth_mod
    import nexgen_local.connectors.outlook as outlook_mod

    monkeypatch.setattr(auth_mod, "status", lambda: (True, "finto gmail ok"))
    monkeypatch.setattr(outlook_mod, "status", lambda: (True, "finto outlook ok"))

    payload = json.dumps({"models": [{"name": cfg.model}]}).encode("utf-8")

    class _FakeResp(io.BytesIO):
        def __enter__(self) -> "_FakeResp":
            return self

        def __exit__(self, *exc: object) -> bool:
            return False

    def _fake_urlopen(_url: str, timeout: float = 5.0) -> _FakeResp:
        return _FakeResp(payload)

    monkeypatch.setattr(svc_mod.urllib.request, "urlopen", _fake_urlopen)


def test_fake_doctor_returns_health_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    from nexgen_local.cmds import service as svc_mod

    cfg = _cfg(tmp_path)
    _stub_doctor_deps(monkeypatch, cfg)
    assert svc_mod.cmd_doctor(_doctor_args()) == 0
    out = capsys.readouterr().out
    assert "doctor" in out.lower()
    assert "OK" in out


def test_fake_doctor_json_returns_structured_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    from nexgen_local.cmds import service as svc_mod

    cfg = _cfg(tmp_path)
    _stub_doctor_deps(monkeypatch, cfg)
    assert svc_mod.cmd_doctor(_doctor_args(as_json=True)) == 0
    rows = json.loads(capsys.readouterr().out)
    assert isinstance(rows, list) and rows
    assert all({"check", "ok", "detail", "required"} <= set(row) for row in rows)


def test_fake_mcp_returns_outcome(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nexgen_local.cmds import service as svc_mod
    import nexgen_local.mcp_server as mcp_mod

    cfg = _cfg(tmp_path)
    monkeypatch.setattr(svc_mod, "_config", lambda args: cfg)
    seen: dict[str, object] = {}

    def _fake_run_server(cfg_arg: LaneConfig, include_ask: bool = True) -> int:
        assert cfg_arg is cfg
        seen["include_ask"] = include_ask
        return 7

    monkeypatch.setattr(mcp_mod, "run_server", _fake_run_server)
    assert svc_mod.cmd_mcp(_mcp_args(jobs_only=False)) == 7
    assert seen["include_ask"] is True
    assert svc_mod.cmd_mcp(_mcp_args(jobs_only=True)) == 7
    assert seen["include_ask"] is False


def test_unknown_command_rejected_with_clear_error(capsys: pytest.CaptureFixture) -> None:
    from nexgen_local import cli as cli_mod

    with pytest.raises(SystemExit) as exc:
        cli_mod.main(["questo-comando-non-esiste-xyz"])
    assert exc.value.code != 0
    assert "invalid choice" in capsys.readouterr().err


def test_public_signatures_unchanged() -> None:
    from nexgen_local.cmds import service as svc_mod

    assert list(inspect.signature(svc_mod.cmd_mcp).parameters) == ["args"]
    assert list(inspect.signature(svc_mod.cmd_doctor).parameters) == ["args"]
