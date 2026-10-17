"""Pinning dello split publisher prima del refactor.

Fissa il comportamento attuale (non quello desiderato) con repo finti:
publish su repo finto ritorna esito, main con --dry-run non pubblica,
firme pubbliche invariate. Solo repo finti in tmp_path, MAI publish reale,
MAI rete.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

import nexgen_core.publisher as pubmod
from nexgen_core.publisher import Publisher, main


def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir(exist_ok=True)
    monkeypatch.setenv("AGENT_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("AGENT_SYNC_LOCK_FILE", str(tmp_path / "state" / "agent-sync.lock"))
    monkeypatch.setenv("KNOWLEDGE_VAULT_PATH", str(vault))
    monkeypatch.delenv("AGENT_VAULT_DATA", raising=False)
    return vault


def test_publish_on_fake_repo_returns_outcome(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = _isolate(tmp_path, monkeypatch)
    monkeypatch.setattr(pubmod, "resolve_remotes", lambda vd: ("local", []))
    monkeypatch.setattr(pubmod, "get_current_branch", lambda vd: "main")
    seen: dict = {}

    def _fake_publish(repo_dir, branch="main", remote="origin", mirrors=None,
                      commit_msg=None, files_to_commit=None):
        seen.update({
            "repo_dir": Path(repo_dir),
            "branch": branch,
            "remote": remote,
            "mirrors": mirrors,
            "commit_msg": commit_msg,
            "files_to_commit": files_to_commit,
        })
        return (True, "Published successfully")

    monkeypatch.setattr(pubmod, "publish_changes", _fake_publish)
    code, msg = Publisher(vault_data=vault).publish(message="test sync", files=None)
    assert code == 0
    assert msg == "Published successfully"
    assert seen["repo_dir"] == vault
    assert seen["branch"] == "main"
    assert seen["remote"] == "local"
    assert seen["commit_msg"] == "test sync"


def test_publish_failure_maps_to_exit_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = _isolate(tmp_path, monkeypatch)
    monkeypatch.setattr(pubmod, "resolve_remotes", lambda vd: ("local", []))
    monkeypatch.setattr(pubmod, "get_current_branch", lambda vd: "main")
    monkeypatch.setattr(pubmod, "publish_changes", lambda *a, **k: (False, "boom"))
    code, msg = Publisher(vault_data=vault).publish(message="x", files=None)
    assert code == 1
    assert msg == "boom"


def test_main_with_dry_run_does_not_publish(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate(tmp_path, monkeypatch)
    calls: list = []

    def _must_not_run(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("publish reale toccato con --dry-run")

    monkeypatch.setattr(pubmod, "publish_changes", _must_not_run)
    with pytest.raises(SystemExit) as exc:
        main(["--dry-run"])
    assert exc.value.code != 0
    assert calls == []


def test_public_signatures_unchanged() -> None:
    assert list(inspect.signature(Publisher.__init__).parameters) == ["self", "vault_data"]
    assert list(inspect.signature(Publisher.publish).parameters) == ["self", "message", "files", "timeout"]
    assert list(inspect.signature(main).parameters) == ["argv"]
