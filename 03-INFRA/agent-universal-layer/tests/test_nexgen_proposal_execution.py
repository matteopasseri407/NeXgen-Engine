"""Approval gates use synthetic effects only, including uncertain outcomes."""
from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace

import pytest

from nexgen_local.config import LaneConfig


@pytest.fixture(params=["mail", "calendar", "upload", "workflow", "patch"])
def gate(request, tmp_path, monkeypatch):
    from nexgen_local import calendars, compose, drive_mcp, patch, workflows

    cfg = LaneConfig(
        vault_root=tmp_path / "vault", repo_roots=(tmp_path,),
        audit_path=tmp_path / "audit.jsonl", mails_dir=tmp_path / "mails",
        calendars_dir=tmp_path / "calendars", uploads_dir=tmp_path / "uploads",
        workflows_dir=tmp_path / "workflows", proposals_dir=tmp_path / "patches",
    )
    pid = "20261004-123000-aabbccdd"
    h = SimpleNamespace(calls=[], effect=lambda: None, restore=lambda: None)

    def effect(*args, **kwargs):
        h.calls.append(1)
        h.effect()
        return {"id": "synthetic"}

    kind = request.param
    if kind == "mail":
        mod, error = compose, compose.MailError
        proposal = mod.MailProposal(pid, "send", "gmail", "test@example.com", "synthetic", "", "synthetic", "synthetic")
        monkeypatch.setitem(mod._BACKENDS, "gmail", SimpleNamespace(send_message=effect))
        def run(yes=True):
            return mod.apply_mail(cfg, pid, yes=yes)
    elif kind == "calendar":
        mod, error = calendars, calendars.CalendarError
        proposal = mod.CalendarProposal(pid, "create", "primary", "synthetic", "2026-10-04T12:00:00", "2026-10-04T13:00:00", "", "", "")
        monkeypatch.setattr(mod.calendar_conn, "create_event", effect)
        def run(yes=True):
            return mod.apply_proposal(cfg, pid, yes=yes)
    elif kind == "upload":
        mod, error = drive_mcp, drive_mcp.DriveGateError
        source = tmp_path / "source.txt"
        source.write_bytes(b"synthetic")
        proposal = mod.UploadProposal(pid, str(source), "source.txt", "text/plain", 9, hashlib.sha256(b"synthetic").hexdigest(), "", "synthetic")
        monkeypatch.setattr(mod.drive_conn, "upload_file", effect)
        def run(yes=True):
            return mod.confirm_upload(cfg, pid, yes)
    elif kind == "workflow":
        mod, error = workflows, workflows.WorkflowError
        proposal = mod.WorkflowProposal(pid, "synthetic", "synthetic", {})
        monkeypatch.setattr(mod, "load_allowlist", lambda: {"synthetic": {"webhook_url": "https://example.invalid/never-called"}})

        def http(*args):
            effect()
            return mod.HttpResult(200, b"synthetic")

        def run(yes=True):
            return mod.confirm_run(cfg, pid, yes, http=http)
    else:
        mod, error = patch, patch.PatchError
        target = tmp_path / "target.txt"
        target.write_bytes(b"old\n")
        proposal = mod.Proposal(pid, "target.txt", str(tmp_path), "synthetic", mod._sha("old\n"), mod._sha("new\n"), "synthetic patch", True)

        def git(*args):
            effect()
            target.write_bytes(b"new\n")
            return 0, ""

        monkeypatch.setattr(mod, "_run_git", git)
        h.restore = lambda: target.write_bytes(b"old\n")
        def run(yes=True):
            return mod.apply_proposal(cfg, pid, yes=yes)
    if kind in ("mail", "calendar", "workflow"):
        proposal.content_sha = mod._proposal_sha(proposal)
    elif kind == "upload":
        proposal.meta_sha = mod._meta_sha(proposal)
    mod._save(cfg, proposal)
    h.module, h.error, h.run = mod, error, run
    h.cfg, h.pid, h.proposal = cfg, pid, proposal
    h.kind = kind
    return h


def test_effect_is_not_repeated_after_final_save_failure(gate, monkeypatch):
    save = gate.module._save

    def fail_final(cfg, proposal):
        if proposal.applied_at:
            raise OSError("synthetic final storage failure")
        save(cfg, proposal)

    monkeypatch.setattr(gate.module, "_save", fail_final)
    with pytest.raises(gate.error):
        gate.run()
    assert len(gate.calls) == 1
    gate.restore()
    with pytest.raises(gate.error):
        gate.run()
    assert len(gate.calls) == 1, "unknown completion must never repeat the effect"


def test_concurrent_confirmations_cannot_repeat_effect(gate):
    entered, release = Event(), Event()

    def wait():
        entered.set()
        assert release.wait(10)

    gate.effect = wait
    with ThreadPoolExecutor(max_workers=1) as pool:
        active = pool.submit(gate.run)
        try:
            assert entered.wait(10)
            with pytest.raises(gate.error, match="in uso"):
                gate.run()
        finally:
            release.set()
        active.result(timeout=10)
    assert len(gate.calls) == 1


def test_interruption_after_effect_keeps_attempt_and_refuses_retry(gate):
    def stop():
        raise KeyboardInterrupt

    gate.effect = stop
    with pytest.raises(KeyboardInterrupt):
        gate.run()
    gate.effect = lambda: None
    with pytest.raises(gate.error):
        gate.run()
    assert len(gate.calls) == 1


def test_missing_approval_does_not_consume_proposal(gate):
    with pytest.raises(gate.error):
        gate.run(False)
    assert not gate.calls
    gate.run()
    assert len(gate.calls) == 1


@pytest.mark.parametrize("gate", ["mail", "calendar", "upload"], indirect=True)
def test_accepted_effect_with_connector_error_refuses_second_attempt(gate):
    from nexgen_local.connectors import ConnectorError

    def lost_reply():
        raise ConnectorError("synthetic reply lost after acceptance")

    gate.effect = lost_reply
    with pytest.raises(gate.error):
        gate.run()
    gate.effect = lambda: None
    with pytest.raises(gate.error, match="verifica"):
        gate.run()
    assert len(gate.calls) == 1


@pytest.mark.parametrize("gate", ["workflow"], indirect=True)
def test_webhook_failure_after_acceptance_does_not_reopen_proposal(gate):
    def http(*args):
        gate.calls.append(1)
        return gate.module.HttpResult(500, b"synthetic accepted then failed")

    for _ in range(2):
        with pytest.raises(gate.error):
            gate.module.confirm_run(gate.cfg, gate.pid, True, http=http)
    assert len(gate.calls) == 1


@pytest.mark.parametrize("gate", ["mail", "calendar", "upload", "workflow"], indirect=True)
def test_old_sending_marker_is_displayed_and_refused_as_uncertain(gate):
    import json
    from nexgen_local.proposals import proposal_status

    directory = getattr(gate.cfg, {"mail": "mails_dir", "calendar": "calendars_dir", "upload": "uploads_dir", "workflow": "workflows_dir"}[gate.kind])
    artifact = directory / f"{gate.pid}.json"
    data = json.loads(artifact.read_text())
    data.pop("attempted_at")
    data["sending_at"] = "synthetic earlier attempt"
    artifact.write_text(json.dumps(data))
    loaded = gate.module.load_proposal(gate.cfg, gate.pid)
    assert proposal_status(loaded, "complete") == "esito da verificare"
    with pytest.raises(gate.error, match="verifica"):
        gate.run()
    assert not gate.calls


@pytest.mark.parametrize("gate", ["mail", "calendar", "upload", "workflow"], indirect=True)
def test_changed_approval_fields_refuse_before_attempt(gate):
    import json

    directory = getattr(gate.cfg, {"mail": "mails_dir", "calendar": "calendars_dir", "upload": "uploads_dir", "workflow": "workflows_dir"}[gate.kind])
    artifact = directory / f"{gate.pid}.json"
    data = json.loads(artifact.read_text())
    field = {"mail": "to", "calendar": "calendar_id", "upload": "folder_id", "workflow": "workflow"}[gate.kind]
    data[field] = "synthetic changed destination"
    artifact.write_text(json.dumps(data))
    with pytest.raises(gate.error, match="contenuto cambiato"):
        gate.run()
    assert not gate.calls
    assert not gate.module.load_proposal(gate.cfg, gate.pid).attempted_at


def test_fingerprint_cannot_move_nul_between_approval_fields():
    from nexgen_local.proposals import content_sha, validate_content
    assert content_sha("a\x00b", "c") != content_sha("a", "b\x00c")
    old = hashlib.sha256(b"\x00a\x00b\x00c").hexdigest()
    with pytest.raises(ValueError):
        validate_content(old, ValueError, "a", "b\x00c")


def test_artifact_identity_cannot_redirect_attempt_storage(gate):
    import json
    directory = getattr(gate.cfg, {"mail": "mails_dir", "calendar": "calendars_dir", "upload": "uploads_dir", "workflow": "workflows_dir", "patch": "proposals_dir"}[gate.kind])
    artifact = directory / f"{gate.pid}.json"
    data = json.loads(artifact.read_text())
    data["id"] = "20261004-123000-eeeeeeee"
    artifact.write_text(json.dumps(data))
    with pytest.raises(gate.error, match="identita"):
        gate.run()
    assert not gate.calls


def test_old_unambiguous_fingerprints_still_validate():
    from nexgen_local.proposals import validate_content
    old = hashlib.sha256(b"\x00a\x00b").hexdigest()
    validate_content(old, ValueError, "a", "b")


def test_failed_attempt_record_refuses_before_effect(gate, monkeypatch):
    monkeypatch.setattr(gate.module, "_save", lambda *a: (_ for _ in ()).throw(OSError("synthetic failure")))
    with pytest.raises(gate.error):
        gate.run()
    assert not gate.calls, "effect happened before its durable attempt record"


def test_failed_record_write_preserves_previous_complete_artifact(gate, monkeypatch):
    import os
    from pathlib import Path

    directory = getattr(gate.cfg, {"mail": "mails_dir", "calendar": "calendars_dir", "upload": "uploads_dir", "workflow": "workflows_dir", "patch": "proposals_dir"}[gate.kind])
    artifact = directory / f"{gate.pid}.json"
    before = artifact.read_bytes()
    fdopen = os.fdopen

    class InterruptedWriter:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.handle.close()

        def write(self, text):
            self.handle.write(text[:1])
            raise OSError("synthetic disk full")

    def raw_write(path, text, **kwargs):
        path.write_bytes(text[:1].encode())
        raise OSError("synthetic disk full")

    monkeypatch.setattr(Path, "write_text", raw_write)
    monkeypatch.setattr(os, "fdopen", lambda *a, **k: InterruptedWriter(fdopen(*a, **k)))
    with pytest.raises(OSError):
        gate.module._save(gate.cfg, gate.proposal)
    assert artifact.read_bytes() == before


def test_proposal_artifact_is_private_before_confirmation(gate):
    import os
    import stat

    if os.name == "nt":
        pytest.skip("POSIX permission modes do not establish Windows ACLs")
    directory = getattr(gate.cfg, {"mail": "mails_dir", "calendar": "calendars_dir", "upload": "uploads_dir", "workflow": "workflows_dir", "patch": "proposals_dir"}[gate.kind])
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE((directory / f"{gate.pid}.json").stat().st_mode) == 0o600


def test_uncertain_attempt_is_not_presented_as_ready_to_approve(gate, monkeypatch, capsys):
    import argparse
    import nexgen_local.cli as cli

    save = gate.module._save

    def fail_final(cfg, proposal):
        if proposal.applied_at:
            raise OSError("synthetic storage failure")
        save(cfg, proposal)

    monkeypatch.setattr(gate.module, "_save", fail_final)
    with pytest.raises(gate.error):
        gate.run()
    if gate.kind == "upload":
        # Upload has no list command; its JSON state still distinguishes uncertainty.
        stored = gate.module.load_proposal(gate.cfg, gate.pid)
        assert stored.attempted_at and not stored.applied_at
        with pytest.raises(gate.error, match="verifica l'esito"):
            gate.run()
        assert len(gate.calls) == 1
        return
    monkeypatch.setattr(cli, "_config", lambda args: gate.cfg)
    command = {"mail": cli.cmd_mails, "calendar": cli.cmd_cals, "workflow": cli.cmd_wfs, "patch": cli.cmd_proposals}[gate.kind]
    assert command(argparse.Namespace(json=False)) == 0
    assert "esito da verificare" in capsys.readouterr().out
