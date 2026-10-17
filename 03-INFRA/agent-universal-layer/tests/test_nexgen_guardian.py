"""Unit tests for the deterministic third-party guardian: static rules only,
no models, offline repos built on the fly. A wrong verdict must always
land on HOLD, never on AUTO."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

# The guardian builds real git repos on the fly; under a fully loaded parallel
# run two of them miss their time budget, so the module shares one worker.
pytestmark = pytest.mark.xdist_group("serial")

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.depwatch import PinFinding  # noqa: E402
from nexgen_core.thirdparty_guard import (  # noqa: E402
    AUTO,
    BATCH,
    HOLD,
    judge_finding,
    judge_github_skill,
    judge_npm_package,
    run_guardian,
)


def _git(cwd: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "guardian test",
        "GIT_AUTHOR_EMAIL": "guardian@localhost",
        "GIT_COMMITTER_NAME": "guardian test",
        "GIT_COMMITTER_EMAIL": "guardian@localhost",
    }
    proc = subprocess.run(["git", *args], cwd=cwd, check=True,
                          capture_output=True, text=True, env=env)
    return proc.stdout.strip()


def _repo(tmp_path: Path, name: str) -> Path:
    repo = tmp_path / name
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    return repo


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def test_identical_vendored_bytes_is_auto(tmp_path: Path):
    repo = _repo(tmp_path, "skill")
    (repo / "skills" / "demo").mkdir(parents=True)
    (repo / "skills" / "demo" / "SKILL.md").write_text("# demo\n", encoding="utf-8")
    pin = _commit(repo, "skill as pinned")
    (repo / "README.md").write_text("docs only, outside scope\n", encoding="utf-8")
    head = _commit(repo, "docs outside scope")

    verdict = judge_github_skill("skill 'demo'", str(repo), pin, head, "skills/demo")
    assert verdict.verdict == AUTO


def test_docs_only_in_scope_batches(tmp_path: Path):
    repo = _repo(tmp_path, "skill")
    (repo / "SKILL.md").write_text("# demo\n", encoding="utf-8")
    pin = _commit(repo, "skill as pinned")
    (repo / "SKILL.md").write_text("# demo\n\nA clearer sentence.\n", encoding="utf-8")
    head = _commit(repo, "prose touch-up")

    verdict = judge_github_skill("skill 'demo'", str(repo), pin, head, ".")
    assert verdict.verdict == BATCH


def test_new_script_is_held(tmp_path: Path):
    repo = _repo(tmp_path, "skill")
    (repo / "SKILL.md").write_text("# demo\n", encoding="utf-8")
    pin = _commit(repo, "skill as pinned")
    (repo / "scripts").mkdir()
    (repo / "scripts" / "validate.py").write_text("print('hi')\n", encoding="utf-8")
    head = _commit(repo, "new helper script")

    verdict = judge_github_skill("skill 'demo'", str(repo), pin, head, ".")
    assert verdict.verdict == HOLD
    assert any("script" in reason for reason in verdict.reasons)


def test_rewritten_history_is_held(tmp_path: Path):
    repo = _repo(tmp_path, "skill")
    (repo / "SKILL.md").write_text("# demo\n", encoding="utf-8")
    pin = _commit(repo, "skill as pinned")
    _git(repo, "checkout", "--orphan", "hijack")
    (repo / "SKILL.md").write_text("# demo replaced\n", encoding="utf-8")
    head = _commit(repo, "unrelated history")

    verdict = judge_github_skill("skill 'demo'", str(repo), pin, head, ".")
    assert verdict.verdict == HOLD
    assert any("rewritten" in reason for reason in verdict.reasons)


def test_missing_skill_md_is_held(tmp_path: Path):
    repo = _repo(tmp_path, "skill")
    (repo / "SKILL.md").write_text("# demo\n", encoding="utf-8")
    pin = _commit(repo, "skill as pinned")
    (repo / "SKILL.md").unlink()
    (repo / "notes.md").write_text("moved docs\n", encoding="utf-8")
    head = _commit(repo, "drop skill body")

    verdict = judge_github_skill("skill 'demo'", str(repo), pin, head, ".")
    assert verdict.verdict == HOLD


def test_hook_wiring_is_held(tmp_path: Path):
    repo = _repo(tmp_path, "skill")
    (repo / "SKILL.md").write_text("# demo\n", encoding="utf-8")
    pin = _commit(repo, "skill as pinned")
    (repo / "hooks").mkdir()
    (repo / "hooks" / "hooks.json").write_text("{}\n", encoding="utf-8")
    head = _commit(repo, "native hooks")

    verdict = judge_github_skill("skill 'demo'", str(repo), pin, head, ".")
    assert verdict.verdict == HOLD


def test_npm_patch_batches_minor_and_major_hold():
    assert judge_npm_package("mcp 'x'", "3.25.1", "3.25.3").verdict == BATCH
    assert judge_npm_package("mcp 'x'", "3.24.0", "3.25.3").verdict == HOLD
    assert judge_npm_package("mcp 'x'", "4.0.2", "4.1.0").verdict == HOLD
    assert judge_npm_package("mcp 'x'", "1.0.13", "2.2.2").verdict == HOLD
    assert judge_npm_package("mcp 'x'", "2026.7.10", "2026.8.31").verdict == HOLD


def test_run_guardian_writes_verdict_file(tmp_path: Path):
    state = tmp_path / "state"
    findings = [
        PinFinding(kind="npm-version", what="MCP server 'x'", pinned="1.2.3",
                   upstream="1.2.4", stale=True),
        PinFinding(kind="npm-version", what="MCP server 'y'", pinned="1.2.3",
                   upstream="2.0.0", stale=True),
        PinFinding(kind="npm-version", what="MCP server 'z'", pinned="1.2.3",
                   upstream=None, stale=False),
    ]
    result = run_guardian(findings, state)
    assert result == {"ok": True, "auto": 0, "batch": 1, "hold": 1}
    sidecar = state / "nexgen" / "third-party-guard.json"
    assert sidecar.is_file()


def test_judge_finding_routes_github_and_holds_without_repo():
    finding = PinFinding(kind="git-commit", what="skill 'demo' (github o/r)",
                         pinned="a" * 40, upstream="b" * 40, stale=True)
    verdict = judge_finding(finding, skill_scopes={"demo": "."})
    # Local clone of a fake repo fails: held, never auto.
    assert verdict.verdict == HOLD

    mysterious = PinFinding(kind="git-commit", what="skill 'demo'",
                            pinned="a" * 40, upstream="b" * 40, stale=True)
    assert judge_finding(mysterious).verdict == HOLD

    current = PinFinding(kind="npm-version", what="MCP server 'x'",
                         pinned="1.2.3", upstream="1.2.3", stale=False)
    assert judge_finding(current).verdict == HOLD


def test_failed_git_inspection_holds_instead_of_auto(tmp_path: Path, monkeypatch):
    """Sol's case: a diff that errors out must never read as empty (AUTO)."""
    import subprocess

    import nexgen_core.thirdparty_guard as guard_mod

    repo = _repo(tmp_path, "skill")
    (repo / "SKILL.md").write_text("# demo\n", encoding="utf-8")
    pin = _commit(repo, "skill as pinned")
    (repo / "SKILL.md").write_text("# demo\n\nmore\n", encoding="utf-8")
    head = _commit(repo, "touch-up")

    real_run = guard_mod._run_git

    def _flaky(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        if args[:2] == ["diff", "--name-status"]:
            return subprocess.CompletedProcess(args, returncode=128, stdout="", stderr="fatal")
        return real_run(args, cwd)

    monkeypatch.setattr(guard_mod, "_run_git", _flaky)
    verdict = guard_mod.judge_github_skill("skill 'demo'", str(repo), pin, head, ".")
    assert verdict.verdict == HOLD


def test_quoted_non_ascii_path_never_reads_as_empty_diff(tmp_path: Path):
    """Sol's case: git C-quotes non-ASCII names without -z, which used to
    miss the scope check and verdict AUTO on a new script."""
    repo = _repo(tmp_path, "skill")
    (repo / "skills" / "demo").mkdir(parents=True)
    (repo / "skills" / "demo" / "SKILL.md").write_text("# demo\n", encoding="utf-8")
    pin = _commit(repo, "skill as pinned")
    (repo / "skills" / "demo" / "évìl.py").write_text("print('hi')\n", encoding="utf-8")
    head = _commit(repo, "sneaky script")

    verdict = judge_github_skill("skill 'demo'", str(repo), pin, head, "skills/demo")
    assert verdict.verdict == HOLD
    assert any("script" in reason for reason in verdict.reasons)


def test_a_scope_written_with_windows_backslashes_is_not_a_free_pass(tmp_path: Path):
    """MUST-10: `skills\\demo` non trova nulla, il diff leggeva come vuoto e
    il codice di terzi veniva promosso con un AUTO mai guadagnato."""
    repo = _repo(tmp_path, "skill")
    target = repo / "skills" / "demo"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text("# demo\n", encoding="utf-8")
    pin = _commit(repo, "skill as pinned")
    (target / "validate.py").write_text("print('hi')\n", encoding="utf-8")
    head = _commit(repo, "new script inside the skill")

    verdict = judge_github_skill("skill 'demo'", str(repo), pin, head, "skills\\demo")
    assert verdict.verdict == HOLD
    assert any("script" in reason for reason in verdict.reasons)


def test_files_listed_outside_the_scope_hold_instead_of_auto(tmp_path: Path, monkeypatch):
    """Il confronto dello scope è l'unica cosa fra un aggiornamento di terzi
    e un AUTO: se il diff elenca file e nessuno cade nello scope, il verdetto
    è HOLD, mai 'byte identici'."""
    import nexgen_core.thirdparty_guard as guard_mod

    repo = _repo(tmp_path, "skill")
    target = repo / "skills" / "demo"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text("# demo\n", encoding="utf-8")
    pin = _commit(repo, "skill as pinned")
    (target / "validate.py").write_text("print('hi')\n", encoding="utf-8")
    head = _commit(repo, "new script inside the skill")

    monkeypatch.setattr(guard_mod, "_in_scope", lambda path, scope: False)
    verdict = guard_mod.judge_github_skill("skill 'demo'", str(repo), pin, head, "skills/demo")
    assert verdict.verdict == HOLD
    assert any("scope" in reason for reason in verdict.reasons)


def test_a_symlink_added_to_a_skill_is_held(tmp_path: Path):
    """MUST-13: il modo 120000 non è un documento, è un rimando a un file
    fuori dalla skill: leggeva come prosa e finiva in BATCH."""
    repo = _repo(tmp_path, "skill")
    (repo / "SKILL.md").write_text("# demo\n", encoding="utf-8")
    pin = _commit(repo, "skill as pinned")
    # Un symlink si registra anche dove il sistema non sa crearne uno:
    # git lo tiene in indice con il modo 120000.
    blob = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"], cwd=repo,
        input="/etc/passwd\n", text=True, capture_output=True, check=True,
    )
    _git(repo, "update-index", "--add", "--cacheinfo", f"120000,{blob.stdout.strip()},notes.md")
    _git(repo, "commit", "-m", "docs pointer")
    head = _git(repo, "rev-parse", "HEAD")

    verdict = judge_github_skill("skill 'demo'", str(repo), pin, head, ".")
    assert verdict.verdict == HOLD
    assert any("symlink" in reason for reason in verdict.reasons)


def test_a_tracked_file_replaced_by_a_symlink_is_held(tmp_path: Path):
    repo = _repo(tmp_path, "skill")
    (repo / "SKILL.md").write_text("# demo\n", encoding="utf-8")
    pin = _commit(repo, "skill as pinned")
    blob = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"], cwd=repo,
        input="../../notes.md\n", text=True, capture_output=True, check=True,
    )
    _git(repo, "update-index", "--cacheinfo", f"120000,{blob.stdout.strip()},SKILL.md")
    _git(repo, "commit", "-m", "the skill file becomes a pointer")
    head = _git(repo, "rev-parse", "HEAD")

    verdict = judge_github_skill("skill 'demo'", str(repo), pin, head, ".")
    assert verdict.verdict == HOLD
    assert any("symlink" in reason for reason in verdict.reasons)


def test_a_symlink_retargeted_in_place_is_held(tmp_path: Path):
    """MUST-13, il caso che la prima correzione lasciava aperto: un symlink
    già presente al pin e ridiretto non cambia modo, quindi nessun controllo
    di modo lo vedeva e il file passava come prosa."""
    repo = _repo(tmp_path, "skill")
    (repo / "SKILL.md").write_text("# demo\n", encoding="utf-8")
    blob = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"], cwd=repo,
        input="../../notes.md\n", text=True, capture_output=True, check=True,
    )
    _git(repo, "update-index", "--add", "--cacheinfo", f"120000,{blob.stdout.strip()},notes.md")
    _git(repo, "commit", "-m", "the skill already carries a pointer")
    pin = _git(repo, "rev-parse", "HEAD")

    redirected = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"], cwd=repo,
        input="../../../.ssh/id_rsa\n", text=True, capture_output=True, check=True,
    )
    _git(repo, "update-index", "--cacheinfo", f"120000,{redirected.stdout.strip()},notes.md")
    _git(repo, "commit", "-m", "the pointer is redirected")
    head = _git(repo, "rev-parse", "HEAD")

    verdict = judge_github_skill("skill 'demo'", str(repo), pin, head, ".")
    assert verdict.verdict == HOLD
    assert any("symlink" in reason for reason in verdict.reasons)


def test_a_gitlink_planted_in_a_skill_is_held(tmp_path: Path):
    """MUST-13: anche il modo 160000 punta a byte che nessuno ha venduto, e
    con la sola regola del 120000 finiva in BATCH come prosa."""
    repo = _repo(tmp_path, "skill")
    (repo / "SKILL.md").write_text("# demo\n", encoding="utf-8")
    pin = _commit(repo, "skill as pinned")
    _git(repo, "update-index", "--add", "--cacheinfo", f"160000,{pin},docs.md")
    _git(repo, "commit", "-m", "a submodule dressed as a document")
    head = _git(repo, "rev-parse", "HEAD")

    verdict = judge_github_skill("skill 'demo'", str(repo), pin, head, ".")
    assert verdict.verdict == HOLD
    assert any("gitlink" in reason for reason in verdict.reasons)


def test_a_scope_written_with_a_dot_slash_still_finds_the_diff(tmp_path: Path):
    """MUST-10: `./skills/demo` è lo stesso di `skills/demo`. Prima il diff
    veniva letto e lo scope no, quindi ogni aggiornamento restava HOLD per
    sempre pur essendo prosa innocua."""
    repo = _repo(tmp_path, "skill")
    target = repo / "skills" / "demo"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text("# demo\n", encoding="utf-8")
    pin = _commit(repo, "skill as pinned")
    (target / "SKILL.md").write_text("# demo\n\nA clearer sentence.\n", encoding="utf-8")
    head = _commit(repo, "prose touch-up")

    verdict = judge_github_skill("skill 'demo'", str(repo), pin, head, "./skills/demo")
    assert verdict.verdict == BATCH


def test_a_scope_of_spaces_is_read_like_the_whole_repo(tmp_path: Path):
    """Le due letture dello scope devono dire la stessa cosa: prima il
    pathspec rifiutava la stringa vuota e `_in_scope` la leggeva come
    "tutto il repo", quindi lo stesso manifest dava due verdetti diversi."""
    repo = _repo(tmp_path, "skill")
    (repo / "SKILL.md").write_text("# demo\n", encoding="utf-8")
    pin = _commit(repo, "skill as pinned")
    (repo / "SKILL.md").write_text("# demo\n\nA clearer sentence.\n", encoding="utf-8")
    head = _commit(repo, "prose touch-up")

    verdict = judge_github_skill("skill 'demo'", str(repo), pin, head, "   ")
    assert verdict.verdict == BATCH


def test_a_scope_that_does_not_exist_is_never_identical_bytes(tmp_path: Path):
    """MUST-10: con uno scope scritto male git non lista niente, e "nessuna
    riga" leggeva come "byte identici": AUTO per un refuso di percorso."""
    repo = _repo(tmp_path, "skill")
    target = repo / "skills" / "demo"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text("# demo\n", encoding="utf-8")
    pin = _commit(repo, "skill as pinned")
    (repo / "README.md").write_text("docs\n", encoding="utf-8")
    head = _commit(repo, "docs outside the skill")

    verdict = judge_github_skill("skill 'demo'", str(repo), pin, head, "skills/typo")
    assert verdict.verdict == HOLD
    assert any("scope" in reason for reason in verdict.reasons)


def test_a_scope_with_a_trailing_slash_is_the_same_scope(tmp_path: Path):
    """La normalizzazione copre anche lo slash finale; questo test lo fissa
    perché un manifest scritto così è normale, non un caso di scuola."""
    repo = _repo(tmp_path, "skill")
    target = repo / "skills" / "demo"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text("# demo\n", encoding="utf-8")
    pin = _commit(repo, "skill as pinned")
    (target / "SKILL.md").write_text("# demo\n\nA clearer sentence.\n", encoding="utf-8")
    head = _commit(repo, "prose touch-up")

    verdict = judge_github_skill("skill 'demo'", str(repo), pin, head, "skills/demo/")
    assert verdict.verdict == BATCH


def test_a_rename_that_gains_the_executable_bit_is_held(tmp_path: Path):
    """Il parser delle rinomine deve restituire il percorso nuovo: se desse
    quello vecchio, il modo non verrebbe letto e un file rinominato con +x
    passerebbe come prosa."""
    repo = _repo(tmp_path, "skill")
    (repo / "notes.md").write_text("# demo\n", encoding="utf-8")
    pin = _commit(repo, "skill as pinned")
    _git(repo, "mv", "notes.md", "run.md")
    _git(repo, "update-index", "--chmod=+x", "run.md")
    _git(repo, "commit", "-m", "the note is renamed and can run")
    head = _git(repo, "rev-parse", "HEAD")

    verdict = judge_github_skill("skill 'demo'", str(repo), pin, head, ".")
    assert verdict.verdict == HOLD
    assert any("executable" in reason for reason in verdict.reasons)
