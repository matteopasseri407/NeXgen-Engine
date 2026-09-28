"""Test del server MCP Drive: ricerca, lettura, upload in due passi.

Nessun test tocca un account reale: l'HTTP e' sempre finto e i token
assenti o finti.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest
import yaml

from nexgen_local.config import LaneConfig
from nexgen_local.connectors import ConnectorError
from nexgen_local.connectors.auth import HttpResult
from nexgen_local.drive_mcp import (
    DriveGateError,
    confirm_upload,
    load_proposal,
    stage_upload,
)
from nexgen_local.tools import ToolRegistry


def _cfg(tmp_path: Path) -> LaneConfig:
    vault = tmp_path / "vault"
    vault.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    return LaneConfig(
        vault_root=vault,
        repo_roots=(repo,),
        model="fake-model",
        audit_path=tmp_path / "audit.jsonl",
        uploads_dir=tmp_path / "uploads",
    )


def _write(path: Path, text: str) -> None:
    # Byte-stable: write_text in modalita' testo traduce \n in \r\n su
    # Windows e gli hash dei byte non corrisponderebbero piu'.
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def test_stage_keeps_bytes_on_the_machine(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _write(tmp_path / "repo" / "contratto.txt", "Clausola 3.\n")
    staged = stage_upload(cfg, "contratto.txt", name="Contratto.txt")
    assert staged["name"] == "Contratto.txt"
    assert staged["size"] > 0 and staged["mime"] == "text/plain"
    assert staged["applied_at"] == "" and staged["drive_id"] == ""
    assert (cfg.uploads_dir / f"{staged['id']}.json").is_file()
    assert load_proposal(cfg, staged["id"]).sha == staged["sha"]


def test_stage_refuses_outside_roots_and_empty(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _write(tmp_path / "fuori.txt", "x\n")
    with pytest.raises(DriveGateError, match="radici"):
        stage_upload(cfg, str(tmp_path / "fuori.txt"))
    _write(tmp_path / "vault" / "99-SECRETS" / "s.md", "x\n")
    with pytest.raises(DriveGateError, match="radici|esclusa|inesistente"):
        stage_upload(cfg, "99-SECRETS/s.md")
    _write(tmp_path / "repo" / "vuoto.txt", "")
    with pytest.raises(DriveGateError, match="vuoto"):
        stage_upload(cfg, "vuoto.txt")
    with pytest.raises(DriveGateError, match="non valido"):
        load_proposal(cfg, "../evil")


def test_confirm_needs_explicit_confirm_and_sends_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import nexgen_local.connectors.drive as drive_conn

    cfg = _cfg(tmp_path)
    _write(tmp_path / "repo" / "a.txt", "contenuto\n")
    sent: dict = {}

    def _fake_upload(data: bytes, name: str, mime: str = "", folder_id: str = "", http=None) -> dict:
        sent["bytes"] = data
        sent["name"] = name
        return {"id": "d9", "name": name}

    monkeypatch.setattr(drive_conn, "upload_file", _fake_upload)
    staged = stage_upload(cfg, "a.txt")
    with pytest.raises(DriveGateError, match="confirm"):
        confirm_upload(cfg, staged["id"], False)
    assert sent == {}
    done = confirm_upload(cfg, staged["id"], True)
    assert done == {"id": staged["id"], "uploaded": True, "drive_id": "d9", "name": "a.txt"}
    assert sent["bytes"] == b"contenuto\n"
    with pytest.raises(DriveGateError, match="gia' caricata"):
        confirm_upload(cfg, staged["id"], True)
    lines = cfg.audit_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3  # proposta, intento prima, esito dopo


def test_confirm_refuses_changed_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import nexgen_local.connectors.drive as drive_conn

    cfg = _cfg(tmp_path)
    target = tmp_path / "repo" / "a.txt"
    _write(target, "v1\n")
    monkeypatch.setattr(
        drive_conn, "upload_file", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no send"))
    )
    staged = stage_upload(cfg, "a.txt")
    target.write_text("v2 nel frattempo\n", encoding="utf-8")
    with pytest.raises(DriveGateError, match="cambiato"):
        confirm_upload(cfg, staged["id"], True)


def test_confirm_without_account_sends_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import nexgen_local.connectors.drive as drive_conn

    cfg = _cfg(tmp_path)
    _write(tmp_path / "repo" / "a.txt", "x\n")
    staged = stage_upload(cfg, "a.txt")

    def _nologin(*args, **kwargs):
        raise ConnectorError("(drive non configurato: serve il login una tantum)")

    monkeypatch.setattr(drive_conn, "upload_file", _nologin)
    with pytest.raises(DriveGateError, match="caricamento fallito"):
        confirm_upload(cfg, staged["id"], True)
    assert load_proposal(cfg, staged["id"]).applied_at == ""


def test_upload_multipart_shape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import nexgen_local.connectors.drive as drive_conn

    directory = tmp_path / "tok"
    directory.mkdir()
    (directory / "tokens.json").write_text(
        json.dumps({"refresh_token": "r", "access_token": "a", "expires_at": 9999999999}),
        encoding="utf-8",
    )
    monkeypatch.setenv("WORKSPACE_MCP_TOKEN_DIR", str(directory))
    captured: dict = {}

    def _fake(req, timeout: int) -> HttpResult:
        captured["url"] = req.full_url
        captured["content_type"] = req.get_header("Content-type")
        captured["body"] = req.data
        return HttpResult(200, b'{"id": "d7", "name": "a.txt"}')

    out = drive_conn.upload_file(b"ciao", "a.txt", "text/plain", http=_fake)
    assert out == {"id": "d7", "name": "a.txt"}
    assert captured["url"].endswith("uploadType=multipart")
    assert "multipart/related" in captured["content_type"]
    assert b'"name": "a.txt"' in captured["body"]
    assert b"ciao" in captured["body"]


def test_drive_read_receipt_says_text_or_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(
        ToolRegistry, "search_drive", lambda self, q: self._record("search_drive", {"query": q}, "d1 | a.txt | text/plain | ieri")
    )
    monkeypatch.setattr(
        ToolRegistry, "read_drive", lambda self, fid: self._record("read_drive", {"id": fid}, "[drive:a.txt (d1)]\ntesto")
    )
    from nexgen_local.drive_mcp import tool_drive_read, tool_drive_search

    assert "a.txt" in tool_drive_search(cfg, "contratto")
    assert "testo" in tool_drive_read(cfg, "d1")


def test_drive_pdf_falls_back_to_pdftotext(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Drive-hosted PDFs convert via pdftotext; without it the refusal stands."""
    import shutil

    import nexgen_local.connectors.drive as drive_conn
    from nexgen_local.connectors import ConnectorError
    from nexgen_local.tools import RunResult

    cfg = _cfg(tmp_path)
    monkeypatch.setattr(
        drive_conn, "read_file",
        lambda fid, meta=None, http=None: (_ for _ in ()).throw(
            ConnectorError("(drive: formato non leggibile come testo: application/pdf)")
        ),
    )
    monkeypatch.setattr(drive_conn, "download_bytes", lambda fid, http=None: b"%PDF-1.4 fake")
    monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/pdftotext")
    monkeypatch.setattr(
        ToolRegistry, "_run",
        staticmethod(lambda cmd, timeout: RunResult(0, "Testo dalla scansione.", "")),
    )
    tools = ToolRegistry(cfg)
    assert "scansione" in tools.read_drive("d9")
    assert tools.calls[-1].ok is True

    monkeypatch.setattr(shutil, "which", lambda cmd: None)
    tools2 = ToolRegistry(cfg)
    out = tools2.read_drive("d9")
    assert out.startswith("(") and tools2.calls[-1].ok is False


def test_build_server_builds(tmp_path: Path) -> None:
    pytest.importorskip("mcp")
    from nexgen_local.drive_mcp import build_server

    server = build_server(_cfg(tmp_path))
    assert server.name == "nexgen-drive"


def test_engine_manifest_declares_drive() -> None:
    repo = Path(__file__).resolve().parents[3]
    manifest = repo / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml"
    data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    drive = data["servers"]["drive"]
    assert drive["transport"] == "stdio"
    assert drive["command"] == "nexgen-local"
    assert drive["args"] == ["drive-mcp"]


def test_drive_cli_upload_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import nexgen_local.connectors.drive as drive_conn
    from nexgen_local import cli as cli_module

    cfg = _cfg(tmp_path)
    _write(tmp_path / "repo" / "a.txt", "contenuto\n")
    monkeypatch.setattr(
        drive_conn, "upload_file", lambda data, name, mime="", folder_id="", http=None: {"id": "d5", "name": name}
    )
    monkeypatch.setattr(cli_module, "_config", lambda args: cfg)
    args = argparse.Namespace(file="a.txt", name="", folder="", json=False)
    assert cli_module.cmd_drive_propose(args) == 0
    proposal_id = next(iter(cfg.uploads_dir.glob("*.json"))).stem
    args = argparse.Namespace(proposal_id=proposal_id, yes=False, json=False)
    assert cli_module.cmd_drive_upload(args) == 1
    args = argparse.Namespace(proposal_id=proposal_id, yes=True, json=True)
    assert cli_module.cmd_drive_upload(args) == 0


def test_stage_same_bytes_different_names_get_unique_ids(tmp_path: Path) -> None:
    """Same file staged twice with different names: two artifacts, no overwrite."""
    cfg = _cfg(tmp_path)
    _write(tmp_path / "repo" / "contratto.txt", "Clausola 3.\n")
    first = stage_upload(cfg, "contratto.txt", name="Contratto.txt")
    second = stage_upload(cfg, "contratto.txt", name="Altro-nome.txt", folder_id="cartella")
    assert first["id"] != second["id"]
    assert load_proposal(cfg, first["id"]).name == "Contratto.txt"
    assert load_proposal(cfg, second["id"]).name == "Altro-nome.txt"


def test_stage_never_overwrites_existing_proposal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A colliding id refuses instead of replacing the shown proposal."""
    import nexgen_local.drive_mcp as drive_gate

    cfg = _cfg(tmp_path)
    _write(tmp_path / "repo" / "a.txt", "contenuto\n")
    monkeypatch.setattr(drive_gate, "new_proposal_id", lambda: "20260101-000000-deadbeef")
    first = stage_upload(cfg, "a.txt", name="Prima.txt")
    assert first["id"] == "20260101-000000-deadbeef"
    with pytest.raises(DriveGateError, match="collisione"):
        stage_upload(cfg, "a.txt", name="Seconda.txt")
    assert load_proposal(cfg, first["id"]).name == "Prima.txt"
