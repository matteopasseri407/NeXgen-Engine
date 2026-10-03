"""Failure paths at the boundaries changed by the stabilization refactor."""
from __future__ import annotations

import importlib.metadata
import json
import logging
import os

import pytest

from nexgen_core import beat, files, git_ops, megaphone, module_install, provision, scheduler
from nexgen_core.tools import notifier_boot
from nexgen_local.connectors import auth, outlook, token_store
from nexgen_local.version import engine_version


def test_source_version_without_installed_distribution(tmp_path, monkeypatch):
    def missing(name):
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr("nexgen_local.version.version", missing)
    (tmp_path / "VERSION").write_text("2.3.9\n", encoding="utf-8")
    assert engine_version(tmp_path) == "2.3.9"


def test_local_config_compatibility_allows_wrapping_original(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from nexgen_local import cli
    from nexgen_local.cmds import service

    original = cli._config
    called = []

    def wrapper(args):
        called.append(args)
        return original(args)

    monkeypatch.setattr(cli, "_config", wrapper)
    args = SimpleNamespace(vault=tmp_path)
    assert service._config(args).vault_root == tmp_path
    assert called == [args]


def test_local_model_compatibility_allows_wrapping_original(monkeypatch):
    from nexgen_local import cli, llm
    from nexgen_local.cmds import run

    original = cli._llm
    marker = object()
    called = []
    monkeypatch.setattr(llm, "ChatOllamaLLM", lambda cfg: marker)

    def wrapper(cfg):
        called.append(cfg)
        return original(cfg)

    monkeypatch.setattr(cli, "_llm", wrapper)
    cfg = object()
    assert run._llm(cfg) is marker
    assert called == [cfg]


def test_failed_governor_inventory_publication_is_reported(tmp_path, monkeypatch, capsys):
    from types import SimpleNamespace

    script = tmp_path / "03-INFRA" / "governor-publish-inventory.py"
    script.parent.mkdir()
    script.write_text("# synthetic publisher\n", encoding="utf-8")
    monkeypatch.setattr("nexgen_core.paths.resolve_vault_data", lambda: tmp_path)
    monkeypatch.setattr(notifier_boot.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=7, stdout="", stderr="synthetic failure"))
    notifier_boot._publish_inventory()
    output = capsys.readouterr().out
    assert "non pubblicato" in output
    assert "7" in output
    assert "nexgen boot-check" in output


@pytest.mark.parametrize("transport", ["telegram", "webhook"])
def test_notification_failure_logs_no_credentials(transport, tmp_path, monkeypatch, caplog):
    marker = "synthetic-private-credential"

    def unavailable(*a, **k):
        raise OSError(f"unavailable endpoint containing {marker}")

    monkeypatch.setattr(megaphone.urllib.request, "urlopen", unavailable)
    notifier = megaphone.Megaphone(tmp_path)
    with caplog.at_level(logging.DEBUG, logger="nexgen_core.megaphone"):
        if transport == "telegram":
            assert notifier._send_telegram(marker, "synthetic-chat", "synthetic") is False
        else:
            assert notifier._send_webhook(f"https://example.invalid/{marker}", {}) is False
    assert marker not in caplog.text
    assert "OSError" in caplog.text


@pytest.mark.parametrize("kind", ["remotes", "mcp", "skills"])
def test_corrupt_manifest_logs_no_embedded_credentials(kind, tmp_path, monkeypatch, caplog):
    from types import SimpleNamespace

    marker = "synthetic-private-credential"
    folder = {"remotes": "sync", "mcp": "mcp", "skills": "skills"}[kind]
    filename = "remotes.yaml" if kind == "remotes" else f"{kind}.manifest.yaml"
    path = tmp_path / "03-INFRA" / "agent-universal-layer" / folder / filename
    path.parent.mkdir(parents=True)
    path.write_text(f"field: [https://example.invalid/{marker}\n", encoding="utf-8")
    monkeypatch.delenv("KNOWLEDGE_VAULT_REMOTE", raising=False)
    monkeypatch.delenv("KNOWLEDGE_VAULT_MIRRORS", raising=False)
    with caplog.at_level(logging.DEBUG):
        if kind == "remotes":
            assert git_ops.resolve_remotes(tmp_path) == ("origin", [])
        elif kind == "mcp":
            assert provision.report_unsatisfied_deps(tmp_path, tmp_path / "state") == []
        else:
            assert beat.Heartbeat._skill_scopes(SimpleNamespace(vault_data=tmp_path)) == {}
    assert caplog.records
    assert marker not in caplog.text


@pytest.mark.parametrize("provider,env", [(auth, "WORKSPACE_MCP_TOKEN_DIR"), (outlook, "OUTLOOK_TOKEN_DIR")])
@pytest.mark.parametrize("rotated", ["synthetic-rotated", None])
def test_refresh_persists_rotated_token_or_keeps_previous(provider, env, rotated, tmp_path, monkeypatch):
    directory = tmp_path / "tokens"
    monkeypatch.setenv(env, str(directory))
    monkeypatch.setattr(provider, "client_id", lambda: "synthetic-client")
    monkeypatch.setattr(provider, "client_secret", lambda: "")
    previous = {"refresh_token": "synthetic-old", "access_token": "expired"}
    payload = {"access_token": "synthetic-access", "expires_in": 3600}
    if rotated is not None:
        payload["refresh_token"] = rotated

    def http(req, timeout):
        return token_store.HttpResult(200, json.dumps(payload).encode())

    result = provider.refresh_tokens(previous, http=http)
    assert result["refresh_token"] == (rotated or previous["refresh_token"])
    assert result["access_token"] == payload["access_token"]
    assert provider.load_tokens() == result
    if os.name != "nt":
        assert provider.token_file().stat().st_mode & 0o777 == 0o600
        assert directory.stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize("provider,env", [(auth, "WORKSPACE_MCP_TOKEN_DIR"), (outlook, "OUTLOOK_TOKEN_DIR")])
@pytest.mark.parametrize("payload", [None, [], {"access_token": None},
                                     {"access_token": "synthetic", "expires_in": None}])
def test_invalid_refresh_response_keeps_previous_tokens(provider, env, payload, tmp_path, monkeypatch):
    monkeypatch.setenv(env, str(tmp_path))
    monkeypatch.setattr(provider, "client_id", lambda: "synthetic-client")
    monkeypatch.setattr(provider, "client_secret", lambda: "")
    previous = {"refresh_token": "synthetic-old", "access_token": "expired"}
    provider.save_tokens(previous)
    before = provider.token_file().read_bytes()

    def http(req, timeout):
        return token_store.HttpResult(200, json.dumps(payload).encode())

    with pytest.raises(provider.AuthError, match="risposta illeggibile"):
        provider.refresh_tokens(previous, http=http)
    assert provider.token_file().read_bytes() == before


def test_failed_token_publication_preserves_previous_bytes(tmp_path, monkeypatch):
    target = tmp_path / "tokens.json"
    target.write_text('{"refresh_token":"synthetic-old"}\n', encoding="utf-8")
    previous = target.read_bytes()

    def denied(*args):
        raise OSError("synthetic publication failure")

    monkeypatch.setattr(files.os, "replace", denied)
    with pytest.raises(OSError, match="synthetic publication failure"):
        token_store.save_tokens_to(tmp_path, target, {"refresh_token": "synthetic-new"})
    assert target.read_bytes() == previous
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes are not Windows ACLs")
def test_failed_token_permissions_do_not_publish_credentials(tmp_path, monkeypatch):
    target = tmp_path / "private" / "tokens.json"

    def denied(*args):
        raise PermissionError("synthetic chmod denial")

    monkeypatch.setattr(files.os, "chmod", denied)
    with pytest.raises(PermissionError, match="synthetic chmod denial"):
        token_store.save_tokens_to(target.parent, target, {"refresh_token": "synthetic"})
    assert not target.exists()


@pytest.mark.parametrize("writer", [
    lambda p, s: module_install._write_if_different(p, s, False),
    notifier_boot._write_if_different,
    lambda p, s: notifier_boot._write_text_if_different(str(p), s),
])
def test_refactored_writers_preserve_previous_bytes_on_failure(writer, tmp_path, monkeypatch):
    target = tmp_path / "managed"
    target.write_text("previous\n", encoding="utf-8")

    def denied(*args):
        raise OSError("synthetic publication failure")

    monkeypatch.setattr(files.os, "replace", denied)
    try:
        assert writer(target, "new\n") is None
    except OSError as exc:
        assert str(exc) == "synthetic publication failure"
    assert target.read_text(encoding="utf-8") == "previous\n"
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.skipif(os.name == "nt", reason="symlinks require Windows developer mode or privileges")
def test_scheduler_symlink_writes_atomically_through_target(tmp_path, monkeypatch):
    target = tmp_path / "target"
    target.write_text("previous\n", encoding="utf-8")
    link = tmp_path / "managed"
    link.symlink_to(target)

    def denied(*args):
        raise OSError("synthetic publication failure")

    with monkeypatch.context() as failure:
        failure.setattr(files.os, "replace", denied)
        with pytest.raises(OSError, match="synthetic publication failure"):
            scheduler._write_if_different(link, "new\n")
    assert link.is_symlink()
    assert target.read_text(encoding="utf-8") == "previous\n"
    assert scheduler._write_if_different(link, "new\n") is True
    assert link.is_symlink()
    assert target.read_text(encoding="utf-8") == "new\n"
    assert scheduler._write_if_different(link, "new\n") is False
