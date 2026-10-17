"""Pinning dello split stage/confirm/build di drive_mcp prima del refactor.

Fissa il comportamento attuale (non quello desiderato) con server finti:
stage_upload prepara senza inviare, confirm senza stage rifiuta,
build_server registra i tool attesi. Solo server finti in tmp_path,
MAI rete, MAI upload veri.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from nexgen_local.config import LaneConfig
from nexgen_local.drive_mcp import (
    DriveGateError,
    build_server,
    confirm_upload,
    stage_upload,
)


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
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def test_stage_upload_prepara_senza_inviare(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import nexgen_local.connectors.drive as drive_conn

    cfg = _cfg(tmp_path)
    _write(tmp_path / "repo" / "a.txt", "contenuto\n")

    def _no_send(*args, **kwargs):
        raise AssertionError("stage non deve inviare")

    monkeypatch.setattr(drive_conn, "upload_file", _no_send)
    staged = stage_upload(cfg, "a.txt")
    assert staged["name"] == "a.txt"
    assert staged["size"] == len(b"contenuto\n")
    assert staged["sha"] and staged["id"]
    assert staged["applied_at"] == "" and staged["drive_id"] == ""
    assert (cfg.uploads_dir / f"{staged['id']}.json").is_file()


def test_confirm_senza_stage_rifiuta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import nexgen_local.connectors.drive as drive_conn

    cfg = _cfg(tmp_path)
    _write(tmp_path / "repo" / "a.txt", "contenuto\n")
    sent: list = []

    def _no_send(*args, **kwargs):
        sent.append(args)
        raise AssertionError("confirm senza stage non deve inviare")

    monkeypatch.setattr(drive_conn, "upload_file", _no_send)
    with pytest.raises(DriveGateError, match="inesistente"):
        confirm_upload(cfg, "20260101-000000-deadbeef", True)
    with pytest.raises(DriveGateError, match="non valido"):
        confirm_upload(cfg, "../evil", True)
    assert sent == []
    staged = stage_upload(cfg, "a.txt")
    with pytest.raises(DriveGateError, match="confirm"):
        confirm_upload(cfg, staged["id"], False)
    assert sent == []


def test_build_server_registra_tool_attesi(tmp_path: Path) -> None:
    pytest.importorskip("mcp")

    server = build_server(_cfg(tmp_path))
    assert server.name == "nexgen-drive"
    names = {t.name for t in server._tool_manager.list_tools()}
    assert names == {
        "drive_search",
        "drive_read",
        "drive_propose_upload",
        "drive_confirm_upload",
    }


def test_public_signatures_unchanged() -> None:
    assert list(inspect.signature(stage_upload).parameters) == [
        "cfg",
        "local_path",
        "name",
        "folder_id",
    ]
    assert list(inspect.signature(confirm_upload).parameters) == [
        "cfg",
        "proposal_id",
        "confirm",
    ]
    assert list(inspect.signature(build_server).parameters) == ["cfg"]
