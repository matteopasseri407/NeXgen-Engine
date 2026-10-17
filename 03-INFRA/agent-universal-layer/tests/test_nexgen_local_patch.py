"""Test della penna: proposta, cancello a fatti macchina, applicazione, staleness.

Il modello non scrive mai: propone uno snippet, il motore valida, calcola il
diff e fa il dry-run. L'applicazione e' un comando separato che riverifica
l'hash del file prima di toccarlo.
"""
from __future__ import annotations

import argparse
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest

from nexgen_local.config import LaneConfig
from nexgen_local.patch import PatchError, apply_proposal, format_gate, load_proposal, propose_patch
from nexgen_local.tools import ToolError


def _git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    return repo


def _git_repo_at(path: Path) -> Path:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    return path


def _cfg(tmp_path: Path, repo: Path) -> LaneConfig:
    return LaneConfig(
        vault_root=tmp_path / "vault",
        repo_roots=(repo,),
        model="fake-model",
        audit_path=tmp_path / "audit.jsonl",
        proposals_dir=tmp_path / "proposals",
    )


class PatchLLM:
    """Fake: risponde sempre lo stesso JSON, come un router prescritto."""

    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def json(self, system: str, user: str) -> dict:
        return self.payload

    def text(self, system: str, user: str) -> str:
        return ""


def test_distinct_proposals_cannot_mutate_the_same_file_concurrently(tmp_path: Path, monkeypatch) -> None:
    from nexgen_local import patch

    repo = _git_repo(tmp_path)
    cfg = _cfg(tmp_path, repo)
    before = "".join(f"line {i:02d}\n" for i in range(30))
    target = repo / "target.txt"
    target.write_text(before, encoding="utf-8")
    first = propose_patch(PatchLLM({"old": "line 01", "new": "FIRST edit"}), cfg, "target.txt", "first")
    second = propose_patch(PatchLLM({"old": "line 28", "new": "SECOND edit"}), cfg, "target.txt", "second")
    entered, release = Event(), Event()
    run_git = patch._run_git

    def pause_first(root, args):
        if "apply" in args and "--check" not in args and "FIRST edit" in Path(args[-1]).read_text():
            entered.set()
            assert release.wait(10)
        return run_git(root, args)

    monkeypatch.setattr(patch, "_run_git", pause_first)
    with ThreadPoolExecutor(max_workers=1) as pool:
        active = pool.submit(apply_proposal, cfg, first.id, yes=True)
        try:
            assert entered.wait(10)
            with pytest.raises(PatchError, match="in uso"):
                apply_proposal(cfg, second.id, yes=True)
            assert not load_proposal(cfg, second.id).attempted_at
        finally:
            release.set()
        assert active.result(timeout=10)["applied"] is True
    assert target.read_text(encoding="utf-8") == before.replace("line 01", "FIRST edit")
    with pytest.raises(PatchError, match="stantia"):
        apply_proposal(cfg, second.id, yes=True)
    assert not load_proposal(cfg, second.id).attempted_at


def test_busy_proposal_releases_the_patch_directory_lock(tmp_path: Path) -> None:
    from nexgen_local.proposals import proposal_lock

    repo = _git_repo(tmp_path)
    cfg = _cfg(tmp_path, repo)
    (repo / "target.txt").write_text("old\n", encoding="utf-8")
    llm = PatchLLM({"old": "old", "new": "new"})
    first = propose_patch(llm, cfg, "target.txt", "first")
    second = propose_patch(llm, cfg, "target.txt", "second")
    with proposal_lock(cfg.proposals_dir, first.id, PatchError):
        with pytest.raises(PatchError, match="in uso"):
            apply_proposal(cfg, first.id, yes=True)
        assert apply_proposal(cfg, second.id, yes=True)["applied"] is True


def test_propose_and_apply_happy_path(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    (repo / "notes.md").write_text("Questo contiente un refuso.\n", encoding="utf-8")
    cfg = _cfg(tmp_path, repo)
    llm = PatchLLM({"old": "contiente", "new": "contiene", "why": "refuso"})
    proposal = propose_patch(llm, cfg, "notes.md", "correggi il refuso")
    assert proposal.dry_run is True
    assert "-Questo contiente un refuso." in proposal.patch
    assert "+Questo contiene un refuso." in proposal.patch
    result = apply_proposal(cfg, proposal.id, yes=True)
    assert result["applied"] is True
    assert result["verified"] is None
    assert (repo / "notes.md").read_text(encoding="utf-8") == "Questo contiene un refuso.\n"
    # Una riga per la proposta, una d'intento prima della penna, una d'esito.
    assert len(cfg.audit_path.read_text(encoding="utf-8").strip().splitlines()) == 3


def test_crlf_file_propose_and_apply_roundtrip(tmp_path: Path) -> None:
    """CRLF files: hashes compare the same bytes at propose and apply time."""
    repo = _git_repo(tmp_path)
    (repo / "win.md").write_bytes("riga uno\r\nvecchia riga\r\nriga tre\r\n".encode("utf-8"))
    cfg = _cfg(tmp_path, repo)
    llm = PatchLLM({"old": "vecchia riga\r\n", "new": "nuova riga\r\n", "why": "refuso"})
    proposal = propose_patch(llm, cfg, "win.md", "correggi la riga")
    assert proposal.dry_run is True
    result = apply_proposal(cfg, proposal.id, yes=True)
    assert result["applied"] is True
    assert (repo / "win.md").read_bytes() == "riga uno\r\nnuova riga\r\nriga tre\r\n".encode("utf-8")


def test_apply_requires_explicit_yes(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    (repo / "notes.md").write_text("Questo contiente un refuso.\n", encoding="utf-8")
    cfg = _cfg(tmp_path, repo)
    proposal = propose_patch(PatchLLM({"old": "contiente", "new": "contiene"}), cfg, "notes.md", "fix")
    with pytest.raises(PatchError):
        apply_proposal(cfg, proposal.id, yes=False)
    assert (repo / "notes.md").read_text(encoding="utf-8") == "Questo contiente un refuso.\n"


def test_stale_proposal_is_refused(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    (repo / "notes.md").write_text("Questo contiente un refuso.\n", encoding="utf-8")
    cfg = _cfg(tmp_path, repo)
    proposal = propose_patch(PatchLLM({"old": "contiente", "new": "contiene"}), cfg, "notes.md", "fix")
    (repo / "notes.md").write_text("Il file e' cambiato nel frattempo.\n", encoding="utf-8")
    with pytest.raises(PatchError, match="stantia"):
        apply_proposal(cfg, proposal.id, yes=True)


def test_ambiguous_snippet_is_refused(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    (repo / "notes.md").write_text("alfa e ancora alfa.\n", encoding="utf-8")
    cfg = _cfg(tmp_path, repo)
    with pytest.raises(PatchError, match="esattamente una volta"):
        propose_patch(PatchLLM({"old": "alfa", "new": "beta"}), cfg, "notes.md", "cambia")


def test_file_outside_repo_is_refused(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "nota.md").write_text("contenuto\n", encoding="utf-8")
    cfg = _cfg(tmp_path, repo)
    with pytest.raises(PatchError, match="fuori dai root"):
        propose_patch(PatchLLM({"old": "contenuto", "new": "altro"}), cfg, "nota.md", "cambia")
    with pytest.raises(PatchError, match="fuori dai root"):
        propose_patch(PatchLLM({"old": "x", "new": "y"}), cfg, "../fuori.md", "cambia")


def test_model_prose_is_never_evidence(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    (repo / "notes.md").write_text("Questo contiente un refuso.\n", encoding="utf-8")
    cfg = _cfg(tmp_path, repo)
    liar = PatchLLM({"old": "contiente", "new": "contiene", "why": "ho gia' applicato e verificato tutto io"})
    proposal = propose_patch(liar, cfg, "notes.md", "fix")
    gate = format_gate(proposal)
    assert "non verificato" in gate
    assert (repo / "notes.md").read_text(encoding="utf-8") == "Questo contiente un refuso.\n"
    apply_proposal(cfg, proposal.id, yes=True)
    assert (repo / "notes.md").read_text(encoding="utf-8") == "Questo contiene un refuso.\n"


def test_proposal_id_traversal_is_refused(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    cfg = _cfg(tmp_path, repo)
    with pytest.raises(PatchError, match="non valido"):
        load_proposal(cfg, "../evil")
    with pytest.raises(PatchError, match="non valido"):
        load_proposal(cfg, "20260925-213501-ab12cd34/../../x")


def test_failed_dry_run_blocks_application(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import nexgen_local.patch as patch_module

    repo = _git_repo(tmp_path)
    (repo / "notes.md").write_text("Questo contiente un refuso.\n", encoding="utf-8")
    cfg = _cfg(tmp_path, repo)
    monkeypatch.setattr(patch_module, "_dry_run", lambda root, text: (False, "dry-run simulato fallito"))
    proposal = propose_patch(PatchLLM({"old": "contiente", "new": "contiene"}), cfg, "notes.md", "fix")
    assert proposal.dry_run is False
    with pytest.raises(PatchError, match="dry-run"):
        apply_proposal(cfg, proposal.id, yes=True)


def test_apply_is_bound_to_the_approved_root(tmp_path: Path) -> None:
    repo_a = _git_repo_at(tmp_path / "a")
    repo_b = _git_repo_at(tmp_path / "b")
    (repo_a / "notes.md").write_text("Questo contiente un refuso.\n", encoding="utf-8")
    (repo_b / "notes.md").write_text("Questo contiente un refuso.\n", encoding="utf-8")
    cfg_a = _cfg(tmp_path, repo_a)
    proposal = propose_patch(PatchLLM({"old": "contiente", "new": "contiene"}), cfg_a, "notes.md", "fix")

    cfg_b = _cfg(tmp_path, repo_b)
    with pytest.raises(PatchError, match="altro root"):
        apply_proposal(cfg_b, proposal.id, yes=True)
    assert (repo_a / "notes.md").read_text(encoding="utf-8") == "Questo contiente un refuso.\n"
    assert (repo_b / "notes.md").read_text(encoding="utf-8") == "Questo contiente un refuso.\n"

    result = apply_proposal(cfg_a, proposal.id, yes=True)
    assert result["applied"] is True
    assert (repo_a / "notes.md").read_text(encoding="utf-8") == "Questo contiene un refuso.\n"


def test_apply_refuses_before_writing_when_the_audit_is_unwritable(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    (repo / "notes.md").write_text("Questo contiente un refuso.\n", encoding="utf-8")
    cfg = _cfg(tmp_path, repo)
    proposal = propose_patch(PatchLLM({"old": "contiente", "new": "contiene"}), cfg, "notes.md", "fix")

    blocker = tmp_path / "blocker"
    blocker.write_text("non e' una directory", encoding="utf-8")
    blocked = LaneConfig(
        vault_root=tmp_path / "vault",
        repo_roots=(repo,),
        model="fake-model",
        audit_path=blocker / "audit.jsonl",
        proposals_dir=tmp_path / "proposals",
    )
    with pytest.raises(ToolError):
        apply_proposal(blocked, proposal.id, yes=True)
    # Nessuna ricevuta d'intento, nessuna penna: il file resta intatto.
    assert (repo / "notes.md").read_text(encoding="utf-8") == "Questo contiente un refuso.\n"


def test_verify_failure_is_a_distinct_state(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    (repo / "notes.md").write_text("Questo contiente un refuso.\n", encoding="utf-8")
    cfg = _cfg(tmp_path, repo)
    proposal = propose_patch(PatchLLM({"old": "contiente", "new": "contiene"}), cfg, "notes.md", "fix")
    result = apply_proposal(cfg, proposal.id, yes=True, verify="false")
    assert result["applied"] is True
    assert result["verified"] is False
    assert result["verify_rc"] != 0
    assert (repo / "notes.md").read_text(encoding="utf-8") == "Questo contiene un refuso.\n"


def test_apply_cli_propagates_verify_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _git_repo(tmp_path)
    (repo / "notes.md").write_text("Questo contiente un refuso.\n", encoding="utf-8")
    cfg = _cfg(tmp_path, repo)
    proposal = propose_patch(PatchLLM({"old": "contiente", "new": "contiene"}), cfg, "notes.md", "fix")
    from nexgen_local import cli

    monkeypatch.setattr(cli, "_config", lambda args: cfg)
    args = argparse.Namespace(proposal_id=proposal.id, yes=True, verify="false", json=False)
    assert cli.cmd_apply(args) == 3


def test_the_verify_command_uses_windows_parsing_and_the_shim_helper(tmp_path: Path, monkeypatch) -> None:
    """Ricognizione 4: il passo verify usava shlex.split in modo POSIX e non
    passava da windows_command_argv: su Windows `tests\\unit\\x.py` perdeva le
    barre rovesciate e gli shim .cmd fallivano con FileNotFoundError. La prova
    simula Windows sui nomi dei due moduli, cosi' gira anche nella CI Linux."""
    from types import SimpleNamespace

    from nexgen_core import processes
    from nexgen_local import patch as patch_mod

    repo = _git_repo(tmp_path)
    (repo / "notes.md").write_text("Questo contiente un refuso.\n", encoding="utf-8")
    cfg = _cfg(tmp_path, repo)
    proposal = propose_patch(PatchLLM({"old": "contiente", "new": "contiene"}), cfg, "notes.md", "fix")

    monkeypatch.setattr(patch_mod, "os", SimpleNamespace(name="nt"), raising=False)
    monkeypatch.setattr(processes, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(processes.shutil, "which", lambda name: str(tmp_path / f"{name}.cmd"))

    launched: list[list[str]] = []
    real_run = subprocess.run

    def observe(argv, **kwargs):
        if kwargs.get("timeout") == patch_mod.APPLY_TIMEOUT:
            launched.append(list(argv))
            raise OSError("stop: cattura e basta")
        return real_run(argv, **kwargs)

    monkeypatch.setattr(patch_mod.subprocess, "run", observe)

    result = apply_proposal(cfg, proposal.id, yes=True, verify=r"runner tests\unit\check.py")

    assert launched, "il passo verify non e' passato da subprocess.run"
    assert launched[0][:4] == ["cmd.exe", "/d", "/s", "/c"], launched[0]
    assert launched[0][4] == str(tmp_path / "runner.cmd"), launched[0]
    assert launched[0][5:] == [r"tests\unit\check.py"], launched[0]
    assert result["applied"] is True and result["verified"] is False


def test_a_quoted_windows_verify_argument_arrives_without_its_quotes(tmp_path: Path, monkeypatch) -> None:
    """MUST-21: in modo non POSIX shlex lascia le virgolette dentro il token e
    subprocess, con list2cmdline, le rimette con l'escape: un percorso con
    spazi tra virgolette arriverebbe al programma con le virgolette
    letterali. Una coppia esterna corrispondente va tolta, le barre restano."""
    from types import SimpleNamespace

    from nexgen_local import patch as patch_mod

    repo = _git_repo(tmp_path)
    (repo / "notes.md").write_text("Questo contiente un refuso.\n", encoding="utf-8")
    cfg = _cfg(tmp_path, repo)
    proposal = propose_patch(PatchLLM({"old": "contiente", "new": "contiene"}), cfg, "notes.md", "fix")

    monkeypatch.setattr(patch_mod, "os", SimpleNamespace(name="nt"), raising=False)
    launched: list[list[str]] = []
    real_run = subprocess.run

    def observe(argv, **kwargs):
        if kwargs.get("timeout") == patch_mod.APPLY_TIMEOUT:
            launched.append(list(argv))
            raise OSError("stop: cattura e basta")
        return real_run(argv, **kwargs)

    monkeypatch.setattr(patch_mod.subprocess, "run", observe)

    apply_proposal(cfg, proposal.id, yes=True, verify='runner "tests\\a b.py"')

    assert launched[0] == ["runner", r"tests\a b.py"], launched[0]


def test_same_patch_twice_gets_unique_ids(tmp_path: Path) -> None:
    """Same snippet in the same second: two artifacts, no overwrite."""
    repo = _git_repo(tmp_path)
    (repo / "notes.md").write_text("Questo contiente un refuso.\n", encoding="utf-8")
    cfg = _cfg(tmp_path, repo)
    llm = PatchLLM({"old": "contiente", "new": "contiene", "why": "refuso"})
    first = propose_patch(llm, cfg, "notes.md", "correggi il refuso")
    second = propose_patch(llm, cfg, "notes.md", "correggi il refuso")
    assert first.id != second.id
    assert load_proposal(cfg, first.id).patch == load_proposal(cfg, second.id).patch


def test_a_post_apply_mismatch_puts_the_file_back(tmp_path: Path) -> None:
    """MUST-16: se dopo l'applicazione il contenuto non corrisponde alla
    proposta (a-capo diversi sotto), il file torna esattamente com'era:
    mezze modifiche mai, e la proposta resta rifiutata."""
    from nexgen_local import patch as patch_mod

    repo = _git_repo(tmp_path)
    original = "Questo contiente un refuso.\n"
    (repo / "notes.md").write_text(original, encoding="utf-8")
    cfg = _cfg(tmp_path, repo)
    proposal = propose_patch(PatchLLM({"old": "contiente", "new": "contiene", "why": "refuso"}),
                             cfg, "notes.md", "correggi il refuso")
    tampered = load_proposal(cfg, proposal.id)
    tampered.new_hash = "0" * 64
    patch_mod._save(cfg, tampered)

    with pytest.raises(PatchError, match="corrisponde alla proposta"):
        apply_proposal(cfg, proposal.id, yes=True)

    assert (repo / "notes.md").read_text(encoding="utf-8") == original
    state = load_proposal(cfg, proposal.id)
    assert state.attempted_at and not state.applied_at, "la proposta resta rifiutata, non applicata"


def test_a_mismatch_is_undone_even_when_the_audit_cannot_write(tmp_path, monkeypatch):
    """MUST-16, revisione: un registro che non si puo' scrivere non deve
    impedire il ripristino ne' coprire il rifiuto. Su Windows il registro si
    blocca per davvero, ed era il percorso che lasciava la modifica a meta'."""
    import json

    from nexgen_local import patch as patch_mod

    repo = _git_repo(tmp_path)
    original = "Questo contiente un refuso.\n"
    (repo / "notes.md").write_text(original, encoding="utf-8")
    cfg = _cfg(tmp_path, repo)
    proposal = propose_patch(PatchLLM({"old": "contiente", "new": "contiene", "why": "refuso"}),
                             cfg, "notes.md", "correggi il refuso")
    tampered = load_proposal(cfg, proposal.id)
    tampered.new_hash = "0" * 64
    patch_mod._save(cfg, tampered)

    real_audit = patch_mod.audit_event

    def failing_audit(cfg_arg, kind, payload, **kwargs):
        if payload.get("phase") == "result":
            raise PermissionError("audit bloccato da un altro processo")
        return real_audit(cfg_arg, kind, payload, **kwargs)

    monkeypatch.setattr(patch_mod, "audit_event", failing_audit)
    with pytest.raises(PatchError, match="tornato com'era"):
        apply_proposal(cfg, proposal.id, yes=True)

    assert (repo / "notes.md").read_text(encoding="utf-8") == original
    lines = [json.loads(line) for line in cfg.audit_path.read_text(encoding="utf-8").strip().splitlines()]
    apply_phases = [line["args"].get("phase") for line in lines if line["tool"] == "apply_patch"]
    assert apply_phases == ["intent"], "il risultato non e' scrivibile, l'intento resta"


def test_a_failed_rollback_keeps_the_original_bytes_beside_the_file(tmp_path, monkeypatch):
    """Se il ripristino non riesce, i byte originali restano in un file
    dell'area della lane (non nel repository) e il registro dice sia l'esito
    sia che la copia c'e'."""
    import json

    from nexgen_local import patch as patch_mod

    repo = _git_repo(tmp_path)
    original = "Questo contiente un refuso.\n"
    target = repo / "notes.md"
    target.write_text(original, encoding="utf-8")
    cfg = _cfg(tmp_path, repo)
    proposal = propose_patch(PatchLLM({"old": "contiente", "new": "contiene", "why": "refuso"}),
                             cfg, "notes.md", "correggi il refuso")
    tampered = load_proposal(cfg, proposal.id)
    tampered.new_hash = "0" * 64
    patch_mod._save(cfg, tampered)

    real_write = patch_mod.atomic_write_bytes

    def failing_write(path_arg, data, **kwargs):
        if Path(path_arg) == target:
            raise OSError("disco pieno")
        return real_write(path_arg, data, **kwargs)

    monkeypatch.setattr(patch_mod, "atomic_write_bytes", failing_write)
    with pytest.raises(PatchError, match="ripristino: failed"):
        apply_proposal(cfg, proposal.id, yes=True)

    spare = cfg.proposals_dir / f"{proposal.id}.pre-patch"
    assert spare.read_text(encoding="utf-8") == original
    assert spare.parent == cfg.proposals_dir, "la copia non vive nel repository"
    lines = [json.loads(line) for line in cfg.audit_path.read_text(encoding="utf-8").strip().splitlines()]
    assert lines[-1]["args"]["restore"] == "failed"
    assert lines[-1]["args"]["spare"] == "written"


def test_a_file_changed_meanwhile_is_never_overwritten_by_the_rollback(tmp_path, monkeypatch):
    """Il ripristino controlla che il file sia ancora quello che la penna ha
    prodotto: se qualcuno l'ha toccato si rifiuta, lo lascia com'e' e ne
    conserva comunque i byte originali in una copia."""
    import json

    from nexgen_local import patch as patch_mod

    repo = _git_repo(tmp_path)
    original = "Questo contiente un refuso.\n"
    (repo / "notes.md").write_text(original, encoding="utf-8")
    cfg = _cfg(tmp_path, repo)
    proposal = propose_patch(PatchLLM({"old": "contiente", "new": "contiene", "why": "refuso"}),
                             cfg, "notes.md", "correggi il refuso")
    tampered = load_proposal(cfg, proposal.id)
    tampered.new_hash = "0" * 64
    patch_mod._save(cfg, tampered)

    monkeypatch.setattr(patch_mod, "_file_state", lambda path_arg, expected: "changed")
    with pytest.raises(PatchError, match="ripristino: changed"):
        apply_proposal(cfg, proposal.id, yes=True)

    assert (repo / "notes.md").read_text(encoding="utf-8") == "Questo contiene un refuso.\n"
    spare = cfg.proposals_dir / f"{proposal.id}.pre-patch"
    assert spare.read_text(encoding="utf-8") == original
    lines = [json.loads(line) for line in cfg.audit_path.read_text(encoding="utf-8").strip().splitlines()]
    assert lines[-1]["args"]["restore"] == "changed"
    assert lines[-1]["args"]["spare"] == "written"


def test_when_no_copy_can_be_saved_the_refusal_does_not_promise_one(tmp_path, monkeypatch):
    """Disco pieno fino in fondo: il file resta modificato e nessuna copia
    esiste, quindi il rifiuto non deve indicare un percorso che non c'e'."""
    import json

    from nexgen_local import patch as patch_mod

    repo = _git_repo(tmp_path)
    (repo / "notes.md").write_text("Questo contiente un refuso.\n", encoding="utf-8")
    cfg = _cfg(tmp_path, repo)
    proposal = propose_patch(PatchLLM({"old": "contiente", "new": "contiene", "why": "refuso"}),
                             cfg, "notes.md", "correggi il refuso")
    tampered = load_proposal(cfg, proposal.id)
    tampered.new_hash = "0" * 64
    patch_mod._save(cfg, tampered)

    def failing_write(path_arg, data, **kwargs):
        raise OSError("disco pieno")

    monkeypatch.setattr(patch_mod, "atomic_write_bytes", failing_write)
    with pytest.raises(PatchError) as refused:
        apply_proposal(cfg, proposal.id, yes=True)

    assert "copia" in str(refused.value) and ".pre-patch" not in str(refused.value)
    assert not (cfg.proposals_dir / f"{proposal.id}.pre-patch").exists()
    lines = [json.loads(line) for line in cfg.audit_path.read_text(encoding="utf-8").strip().splitlines()]
    assert lines[-1]["args"]["restore"] == "failed"
    assert lines[-1]["args"]["spare"] == "failed"


def test_a_refused_apply_still_writes_its_result_line(tmp_path, monkeypatch):
    """Un rifiuto prima della penna scrive comunque la riga di esito: senza,
    nel registro resta solo l'intento e a posteriori non si distingue da un
    crash a meta' applicazione."""
    import json

    from nexgen_local import patch as patch_mod

    repo = _git_repo(tmp_path)
    (repo / "notes.md").write_text("Questo contiente un refuso.\n", encoding="utf-8")
    cfg = _cfg(tmp_path, repo)
    proposal = propose_patch(PatchLLM({"old": "contiente", "new": "contiene", "why": "refuso"}),
                             cfg, "notes.md", "correggi il refuso")

    real_save = patch_mod._save

    def save_and_touch(cfg_arg, prop):
        real_save(cfg_arg, prop)
        (repo / "notes.md").write_text("scritto da qualcun altro nel frattempo.\n", encoding="utf-8")

    monkeypatch.setattr(patch_mod, "_save", save_and_touch)
    with pytest.raises(PatchError, match="dopo l'approvazione"):
        apply_proposal(cfg, proposal.id, yes=True)

    lines = [json.loads(line) for line in cfg.audit_path.read_text(encoding="utf-8").strip().splitlines()]
    results = [line for line in lines if line["tool"] == "apply_patch" and line["args"].get("phase") == "result"]
    assert results and results[-1]["args"]["rc"] == "stale"
    assert (repo / "notes.md").read_text(encoding="utf-8") == "scritto da qualcun altro nel frattempo.\n"


def test_the_file_identity_is_the_raw_bytes_not_a_decoded_view(tmp_path):
    """Due contenuti che differiscono solo per byte non validi non sono lo
    stesso file. Con l'hash calcolato sulla decodifica sostitutiva lo
    sarebbero, e il ripristino sovrascriverebbe la modifica di qualcun altro
    credendo che il file fosse ancora il suo."""
    from nexgen_local import patch as patch_mod

    first = tmp_path / "primo"
    second = tmp_path / "secondo"
    first.write_bytes(b"riga valida\n\xff finale\n")
    second.write_bytes(b"riga valida\n\xfe finale\n")

    decoded = patch_mod._sha(patch_mod._read_text_bytes(first))
    assert decoded == patch_mod._sha(patch_mod._read_text_bytes(second)), "la vista decodificata li confonde"

    identity = patch_mod._hash_bytes(first.read_bytes())
    assert identity != patch_mod._hash_bytes(second.read_bytes())
    assert patch_mod._file_state(second, identity) == "changed"


def test_a_file_that_is_not_valid_utf8_is_refused_at_the_proposal(tmp_path):
    """Un file cp1252 con un byte non ASCII lontano dal punto da correggere
    non e' testo UTF-8: l'identita' sui byte grezzi e il testo della proposta
    non possono coincidere, quindi si rifiuta alla proposta invece di
    applicare e poi disfare."""
    repo = _git_repo(tmp_path)
    original = "perch\xe8 un refuso contiente\n".encode("cp1252")
    (repo / "notes.md").write_bytes(original)

    cfg = _cfg(tmp_path, repo)
    with pytest.raises(PatchError, match="UTF-8"):
        propose_patch(PatchLLM({"old": "contiente", "new": "contiene", "why": "refuso"}),
                      cfg, "notes.md", "correggi il refuso")

    assert (repo / "notes.md").read_bytes() == original
