"""Pinning del comportamento attuale di vault/audit.py prima dello split.

Solo vault finti in tmp_path, mai vault vero, mai publish reale.
Fissa: run_audit su vault finto ritorna esito, clone finto stabile,
firme pubbliche invariate.
"""
from __future__ import annotations

import inspect
import subprocess
import sys
from dataclasses import fields as dc_fields
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.vault import audit as audit_module  # noqa: E402
from nexgen_core.vault.audit import AuditRequest, run_audit  # noqa: E402

TRANCHE = "| Note | Action | Why |\n| --- | --- | --- |\n| `notes/example.md` | compress | too verbose |\n"


def _run_git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, timeout=30)


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True, capture_output=True, timeout=30)
    _run_git(path, "config", "user.email", "test@example.com")
    _run_git(path, "config", "user.name", "Test")
    _run_git(path, "config", "commit.gpgsign", "false")
    return path


def _commit_file(repo: Path, relpath: str, content: str, message: str) -> None:
    _run_git(repo, "config", "user.email", "test@example.com")
    _run_git(repo, "config", "user.name", "Test")
    _run_git(repo, "config", "commit.gpgsign", "false")
    target = repo / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    _run_git(repo, "add", relpath)
    _run_git(repo, "commit", "-q", "-m", message)


def _head(repo: Path) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True, timeout=30,
    )
    return proc.stdout.strip()


def _make_request(tmp_path: Path, name: str, *, write_exit_code: int = 0) -> tuple[AuditRequest, Path, str]:
    """Vault finto + clone finto (origin-less) con una modifica lineare pulita."""
    vault = _init_repo(tmp_path / f"{name}-vault")
    _commit_file(vault, "notes/example.md", "verbose content\n", "seed")
    base = _head(vault)
    clone = tmp_path / f"{name}-clone"
    subprocess.run(["git", "clone", "-q", str(vault), str(clone)], check=True, capture_output=True, timeout=30)
    _run_git(clone, "config", "user.email", "test@example.com")
    _run_git(clone, "config", "user.name", "Test")
    _run_git(clone, "config", "commit.gpgsign", "false")
    _run_git(clone, "remote", "remove", "origin")
    _commit_file(clone, "notes/example.md", "compressed content\n", "compress note")
    state_dir = tmp_path / f"{name}-state"
    state_dir.mkdir(parents=True, exist_ok=True)
    plan_record = tmp_path / f"{name}-plan.txt"
    plan_record.write_text(TRANCHE, encoding="utf-8")
    propose_log = tmp_path / f"{name}-propose.log"
    propose_log.write_text(TRANCHE, encoding="utf-8")
    write_log = tmp_path / f"{name}-write.log"
    write_log.write_text("write pass done\n", encoding="utf-8")
    request = AuditRequest(
        vault=vault,
        clone=clone,
        branch="main",
        base=base,
        archive_root="99-ARCHIVE",
        state_dir=state_dir,
        timestamp=f"20260101-{name}",
        runner="fake",
        model="fake-model",
        tranche_sha256="ab" * 32,
        plan_record=plan_record,
        propose_log=propose_log,
        write_log=write_log,
        write_exit_code=write_exit_code,
        push_if_clean=False,
        engine_scripts=None,
    )
    return request, vault, base


def test_run_audit_on_fake_vault_returns_outcome(tmp_path: Path, monkeypatch) -> None:
    import nexgen_core.vault.promote as promote_module

    monkeypatch.setattr(
        promote_module, "run_publish",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("publish reale/finto non deve partire")),
    )
    request, vault, base = _make_request(tmp_path, "ok")
    head_before_clone = _head(request.clone)
    record, exit_code = run_audit(request, output=lambda *_: None)
    assert isinstance(record, dict)
    assert isinstance(exit_code, int)
    assert exit_code == 0
    assert record["promoted"] is True
    assert record["promoted_oid"] == head_before_clone
    assert record["pushed"] is False
    # La promozione fa fetch+ff del clone e poi committa la backlog line:
    # la HEAD del vault avanza oltre il base e contiene l'OID auditato.
    assert _head(vault) != base
    subprocess.run(
        ["git", "-C", str(vault), "merge-base", "--is-ancestor", head_before_clone, "HEAD"],
        check=True, capture_output=True, timeout=30,
    )
    assert "20260101-ok" in (vault / "99-INDEX" / "vault-cleanup-backlog.md").read_text(encoding="utf-8")
    assert not request.clone.exists()


def test_fake_clone_stable_blocked_twice_same_outcome(tmp_path: Path, monkeypatch) -> None:
    import nexgen_core.vault.promote as promote_module

    monkeypatch.setattr(
        promote_module, "run_publish",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("publish reale/finto non deve partire")),
    )
    vault = _init_repo(tmp_path / "stable-vault")
    _commit_file(vault, "notes/example.md", "verbose content\n", "seed")
    base = _head(vault)
    head_before = base
    clone = tmp_path / "stable-clone"
    subprocess.run(["git", "clone", "-q", str(vault), str(clone)], check=True, capture_output=True, timeout=30)
    _run_git(clone, "config", "user.email", "test@example.com")
    _run_git(clone, "config", "user.name", "Test")
    _run_git(clone, "config", "commit.gpgsign", "false")
    _run_git(clone, "remote", "remove", "origin")
    # Tocco fuori piano: coverage dirty, audit bloccato, vault intatto.
    _commit_file(clone, "notes/unrelated.md", "surprise\n", "unplanned write")
    clone_head = _head(clone)
    state_dir = tmp_path / "stable-state"
    state_dir.mkdir(parents=True, exist_ok=True)
    plan_record = tmp_path / "stable-plan.txt"
    plan_record.write_text(TRANCHE, encoding="utf-8")
    propose_log = tmp_path / "stable-propose.log"
    propose_log.write_text(TRANCHE, encoding="utf-8")
    write_log = tmp_path / "stable-write.log"
    write_log.write_text("write pass done\n", encoding="utf-8")
    request = AuditRequest(
        vault=vault, clone=clone, branch="main", base=base, archive_root="99-ARCHIVE",
        state_dir=state_dir, timestamp="20260101-stable", runner="fake", model="fake-model",
        tranche_sha256="cd" * 32, plan_record=plan_record, propose_log=propose_log,
        write_log=write_log, write_exit_code=0, push_if_clean=False, engine_scripts=None,
    )
    record1, exit1 = run_audit(request, output=lambda *_: None)
    assert exit1 == 4
    assert _head(vault) == head_before
    assert clone.is_dir()
    assert (clone / ".GROOM_QUARANTINE.json").is_file()
    assert _head(clone) == clone_head
    # Seconda corsa sullo stesso clone finto: stesso esito bloccato, HEAD stabile.
    # Nota comportamento attuale: la prima corsa scrive il marker di quarantena
    # nel clone, quindi la seconda corsa blocca per working tree sporco invece
    # che per coverage; l'esito resta 4, la HEAD e il vault restano fermi.
    record2, exit2 = run_audit(request, output=lambda *_: None)
    assert exit2 == exit1 == 4
    assert _head(clone) == clone_head
    assert _head(vault) == head_before


def test_run_audit_write_exit_nonzero_blocks_without_touching_vault(tmp_path: Path) -> None:
    request, vault, _base = _make_request(tmp_path, "blocked", write_exit_code=3)
    head_before = _head(vault)
    record, exit_code = run_audit(request, output=lambda *_: None)
    assert exit_code == 4
    assert "write-pass" in record["blocked_reason"]
    assert _head(vault) == head_before
    assert (request.clone / ".GROOM_QUARANTINE.json").is_file()


def test_coverage_reads_a_note_whose_name_has_spaces(tmp_path: Path):
    """Una nota Obsidian con uno spazio nel nome è una nota, non una riga illeggibile.

    `FILENAME_RE` ammetteva solo `[\\w./-]`, quindi quel target non entrava in
    `targets`: la tranche veniva bocciata come out_of_scope (o unparseable) pur
    essendo approvata e corretta.
    """
    from nexgen_core.vault.coverage import extract_action_targets

    text = (
        "| Note | Action | Why |\n| --- | --- | --- |\n"
        "| `02-PROJECTS/nota con spazi.md` | compress | troppo lunga |\n"
    )

    targets, archive_targets, found_any_row = extract_action_targets(text)

    assert targets == {"02-PROJECTS/nota con spazi.md"}
    assert archive_targets == set()
    assert found_any_row is True


def test_coverage_still_reads_the_other_rows(tmp_path: Path):
    from nexgen_core.vault.coverage import extract_action_targets

    text = (
        "| Note | Action | Why |\n| --- | --- | --- |\n"
        "| `01-NOTES/senza-spazi.md` | archive | vecchia |\n"
        "| `99-ARCHIVE/roba/` | no action | gia' a posto |\n"
    )
    targets, archive_targets, found_any_row = extract_action_targets(text)

    assert targets == {"01-NOTES/senza-spazi.md"}
    assert archive_targets == {"01-NOTES/senza-spazi.md"}
    assert found_any_row is True


def test_a_non_ascii_name_is_read_back_as_it_is(tmp_path: Path) -> None:
    """`core.quotepath` cita i nomi non ASCII: la tranche va letta com'è.

    Con i nomi citati (`caff\\303\\250.md`) `collect_clone_facts` salvava
    l'escape ottale e `check_coverage` bocciava la tranche, out_of_scope e
    non indirizzata, pur essendo corretta.
    """
    from nexgen_core.vault.promote import collect_clone_facts

    repo = _init_repo(tmp_path / "utf8")
    _commit_file(repo, "01-NOTES/caffè.md", "v1\n", "seed")
    base = _head(repo)
    _commit_file(repo, "01-NOTES/caffè.md", "v2\n", "accento")
    head = _head(repo)

    _commits, touched, _added = collect_clone_facts(repo, base, head)

    assert "01-NOTES/caffè.md" in touched
    assert not any("\\" in path for path in touched), touched


def test_a_failed_backlog_commit_after_promotion_is_reported_not_raised(tmp_path: Path, monkeypatch) -> None:
    """Il commit della backlog puo' fallire dopo la promozione: si riporta, non si esplode.

    GitCommandError non e' un OSError: sfuggiva al `except (OSError, LockTimeoutError)`
    con la promozione gia' avvenuta, quindi il record JSON non veniva scritto e il
    comando moriva con un traceback al posto di un esito.
    """
    import nexgen_core.vault.promote as promote_module
    from nexgen_core.vault.git_utils import GitCommandError

    monkeypatch.setattr(
        promote_module, "run_publish",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("publish non deve partire")),
    )
    monkeypatch.setattr(
        promote_module, "commit_backlog",
        lambda *a, **k: (_ for _ in ()).throw(GitCommandError("fatal: cannot commit")),
    )
    request, vault, base = _make_request(tmp_path, "commitfail")
    head_before_clone = _head(request.clone)

    record, exit_code = run_audit(request, output=lambda *_: None)

    assert exit_code == 1
    assert record["promoted"] is True  # la promozione era gia' avvenuta
    assert "cannot commit" in record["blocked_reason"]
    assert _head(vault) != base, "la promozione resta dov'era"
    subprocess.run(
        ["git", "-C", str(vault), "merge-base", "--is-ancestor", head_before_clone, "HEAD"],
        check=True, capture_output=True, timeout=30,
    )


def test_public_signatures_unchanged() -> None:
    assert list(inspect.signature(run_audit).parameters) == ["request", "output"]
    assert [f.name for f in dc_fields(AuditRequest)] == [
        "vault", "clone", "branch", "base", "archive_root", "state_dir",
        "timestamp", "runner", "model", "tranche_sha256", "plan_record",
        "propose_log", "write_log", "write_exit_code", "push_if_clean", "engine_scripts",
    ]
    assert (audit_module.EXIT_OK, audit_module.EXIT_INTERNAL_OR_PUBLISH_FAILED,
            audit_module.EXIT_AUDIT_BLOCKED, audit_module.EXIT_STALE) == (0, 1, 4, 5)
    public_callables = {n for n, v in vars(audit_module).items()
                        if callable(v) and not n.startswith("_") and getattr(v, "__module__", "") == audit_module.__name__}
    assert public_callables == {"AuditRequest", "run_audit"}
