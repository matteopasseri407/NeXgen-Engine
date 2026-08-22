"""Test delle regole di sicurezza di vault-groom (non del giudizio dell'LLM).

Nessun runner LLM vero: ogni test che deve passare per una "passata"
inietta un FakeRunner. Nessun test tocca il vault reale -- solo repo git
finti sotto tmp_path.
"""
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.vault import gate, groom
from nexgen_core.vault.runner import (
    RunnerNotFoundError,
    RunnerUnknownError,
    RunnerUnsupportedError,
    RunResult,
    get_runner,
)

# --- fixtures & helpers ------------------------------------------------


def _run_git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, timeout=30)


def init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True, capture_output=True, timeout=30)
    _run_git(path, "config", "user.email", "test@example.com")
    _run_git(path, "config", "user.name", "Test")
    return path


def commit_file(repo: Path, relpath: str, content: str, message: str) -> None:
    # A freshly cloned repo has no local identity (clone does not copy it
    # from the source) -- set it every time, cheap and idempotent.
    _run_git(repo, "config", "user.email", "test@example.com")
    _run_git(repo, "config", "user.name", "Test")
    target = repo / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    _run_git(repo, "add", relpath)
    _run_git(repo, "commit", "-q", "-m", message)


@dataclass
class FakeRunner:
    """Un Runner finto: mai un processo, mai un LLM."""

    propose_text: str
    write_effect: object = None  # callable(workdir: Path) -> None
    name: str = "fake"
    model: str = "fake-model"

    def run_readonly(self, prompt: str) -> RunResult:
        return RunResult(text=self.propose_text, exit_code=0)

    def run_write(self, prompt: str, workdir: Path) -> RunResult:
        if self.write_effect is not None:
            self.write_effect(workdir)
        return RunResult(text="write pass done", exit_code=0)


TRANCHE = "| Note | Action | Why |\n| --- | --- | --- |\n| `notes/example.md` | compress | too verbose |\n"


def make_write_effect(relpath: str, new_content: str):
    def _effect(workdir: Path) -> None:
        commit_file(workdir, relpath, new_content, "compress note")
    return _effect


# --- rule 1: preview never writes ---------------------------------------


def test_preview_never_writes(tmp_path):
    vault = init_repo(tmp_path / "vault")
    commit_file(vault, "notes/example.md", "verbose content\n", "seed")
    head_before = subprocess.run(
        ["git", "-C", str(vault), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()

    runner = FakeRunner(propose_text=TRANCHE)
    log_path = tmp_path / "preview.log"
    exit_code = groom.preview(vault=vault, runner=runner, log_path=log_path, output=lambda *_: None)

    assert exit_code == 0
    status = subprocess.run(
        ["git", "-C", str(vault), "status", "--porcelain"], capture_output=True, text=True, check=True
    ).stdout
    assert status.strip() == ""
    head_after = subprocess.run(
        ["git", "-C", str(vault), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    assert head_after == head_before
    assert log_path.read_text(encoding="utf-8") == TRANCHE


# --- rule 2: the temp-clone gate -----------------------------------------


def test_prepare_clone_has_no_origin_remote(tmp_path):
    vault = init_repo(tmp_path / "vault")
    commit_file(vault, "notes/example.md", "content\n", "seed")
    state_dir = tmp_path / "state"

    clone_dir = gate.prepare_clone(vault, state_dir, "20260101-000000")

    remotes = subprocess.run(
        ["git", "-C", str(clone_dir), "remote"], capture_output=True, text=True, check=True
    ).stdout
    assert remotes.strip() == ""


# --- rule 3: the human confirmation ---------------------------------------


def test_confirm_tranche_requires_exact_yes():
    for bad_answer in ("y", "Yes", "YES", "yes ", " yes", ""):
        assert gate.confirm_tranche(
            "tranche", "deadbeef", input_func=lambda _, ans=bad_answer: ans, output=lambda *_: None
        ) is False

    assert gate.confirm_tranche(
        "tranche", "deadbeef", input_func=lambda _: "yes", output=lambda *_: None
    ) is True


# --- rule 4: the anti-TOCTOU guard -----------------------------------------


def test_verify_hash_unchanged_detects_tampering(tmp_path):
    state_dir = tmp_path / "state"
    plan_record = gate.write_plan_record(state_dir, "20260101-000000", TRANCHE)
    approved_hash = gate.hash_plan_record(plan_record)

    # No tampering: passes silently.
    gate.verify_hash_unchanged(plan_record, approved_hash)

    # Tampered after approval: must abort.
    plan_record.write_text("something else entirely\n", encoding="utf-8")
    with pytest.raises(gate.GateError):
        gate.verify_hash_unchanged(plan_record, approved_hash)


def test_hash_bytes_normalizes_crlf():
    assert gate.hash_bytes(b"line one\r\nline two\r\n") == gate.hash_bytes(b"line one\nline two\n")


# --- rule 5: a dirty tree refuses to start ---------------------------------


def test_require_clean_tree_blocks_dirty_vault(tmp_path):
    vault = init_repo(tmp_path / "vault")
    commit_file(vault, "notes/example.md", "content\n", "seed")
    (vault / "notes" / "example.md").write_text("uncommitted change\n", encoding="utf-8")

    with pytest.raises(gate.GateError):
        gate.require_clean_tree(vault)


def test_require_clean_tree_passes_on_clean_vault(tmp_path):
    vault = init_repo(tmp_path / "vault")
    commit_file(vault, "notes/example.md", "content\n", "seed")
    gate.require_clean_tree(vault)  # must not raise


# --- apply(): the full guarded flow ----------------------------------------


def test_apply_cancelled_leaves_vault_untouched(tmp_path):
    vault = init_repo(tmp_path / "vault")
    commit_file(vault, "notes/example.md", "verbose content\n", "seed")
    head_before = subprocess.run(
        ["git", "-C", str(vault), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    state_dir = tmp_path / "state"

    runner = FakeRunner(propose_text=TRANCHE)
    exit_code = groom.apply(
        vault=vault,
        runner=runner,
        state_dir=state_dir,
        timestamp="20260101-000000",
        engine_scripts=None,
        push_if_clean=False,
        input_func=lambda _: "nope",
        output=lambda *_: None,
    )

    assert exit_code == 0
    head_after = subprocess.run(
        ["git", "-C", str(vault), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    assert head_after == head_before
    # A declined confirmation must never even reach the clone step.
    clones = [p for p in state_dir.glob("*") if p.is_dir() and "clone" in p.name]
    assert clones == []


def test_apply_dirty_tree_aborts_before_clone(tmp_path):
    vault = init_repo(tmp_path / "vault")
    commit_file(vault, "notes/example.md", "verbose content\n", "seed")
    (vault / "notes" / "example.md").write_text("dirty\n", encoding="utf-8")
    state_dir = tmp_path / "state"

    runner = FakeRunner(propose_text=TRANCHE)
    exit_code = groom.apply(
        vault=vault,
        runner=runner,
        state_dir=state_dir,
        timestamp="20260101-000001",
        engine_scripts=None,
        push_if_clean=False,
        input_func=lambda _: "yes",
        output=lambda *_: None,
    )

    assert exit_code == 1
    # Dirty tree caught before any clone was ever created.
    clones = [p for p in state_dir.glob("*") if p.is_dir() and "clone" in p.name]
    assert clones == []


def test_apply_hash_tampered_between_confirm_and_write_aborts(tmp_path):
    vault = init_repo(tmp_path / "vault")
    commit_file(vault, "notes/example.md", "verbose content\n", "seed")
    state_dir = tmp_path / "state"

    def tamper_then_confirm(_prompt: str) -> str:
        # Simulates the plan record changing underneath the approval,
        # right at the point where the human would type "yes".
        for plan_file in state_dir.glob("*-plan.txt"):
            plan_file.write_text("tampered tranche\n", encoding="utf-8")
        return "yes"

    runner = FakeRunner(propose_text=TRANCHE)
    exit_code = groom.apply(
        vault=vault,
        runner=runner,
        state_dir=state_dir,
        timestamp="20260101-000002",
        engine_scripts=None,
        push_if_clean=False,
        input_func=tamper_then_confirm,
        output=lambda *_: None,
    )

    assert exit_code == 1
    clones = [p for p in state_dir.glob("*") if p.is_dir() and "clone" in p.name]
    assert clones == []


def test_apply_promotes_a_clean_tranche(tmp_path):
    vault = init_repo(tmp_path / "vault")
    commit_file(vault, "notes/example.md", "verbose content\n", "seed")
    head_before = subprocess.run(
        ["git", "-C", str(vault), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    state_dir = tmp_path / "state"

    runner = FakeRunner(
        propose_text=TRANCHE,
        write_effect=make_write_effect("notes/example.md", "compressed content\n"),
    )
    exit_code = groom.apply(
        vault=vault,
        runner=runner,
        state_dir=state_dir,
        timestamp="20260101-000003",
        engine_scripts=None,
        push_if_clean=False,
        input_func=lambda _: "yes",
        output=lambda *_: None,
    )

    assert exit_code == 0
    head_after = subprocess.run(
        ["git", "-C", str(vault), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    assert head_after != head_before
    assert (vault / "notes" / "example.md").read_text(encoding="utf-8") == "compressed content\n"
    backlog = vault / "99-INDEX" / "vault-cleanup-backlog.md"
    assert backlog.is_file()
    assert "20260101-000003" in backlog.read_text(encoding="utf-8")
    # The promoted clone is cleaned up, not left behind.
    clones = [p for p in state_dir.glob("*") if p.is_dir() and "clone" in p.name]
    assert clones == []


def test_apply_out_of_scope_write_is_quarantined(tmp_path):
    """The write pass touching a file the tranche never approved must block
    promotion -- the vault stays exactly as it was."""
    vault = init_repo(tmp_path / "vault")
    commit_file(vault, "notes/example.md", "verbose content\n", "seed")
    head_before = subprocess.run(
        ["git", "-C", str(vault), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    state_dir = tmp_path / "state"

    runner = FakeRunner(
        propose_text=TRANCHE,
        write_effect=make_write_effect("notes/unrelated.md", "surprise\n"),
    )
    exit_code = groom.apply(
        vault=vault,
        runner=runner,
        state_dir=state_dir,
        timestamp="20260101-000004",
        engine_scripts=None,
        push_if_clean=False,
        input_func=lambda _: "yes",
        output=lambda *_: None,
    )

    assert exit_code == 4  # EXIT_AUDIT_BLOCKED
    head_after = subprocess.run(
        ["git", "-C", str(vault), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    assert head_after == head_before
    # The clone is quarantined, not removed.
    clones = [p for p in state_dir.glob("*") if p.is_dir() and "clone" in p.name]
    assert len(clones) == 1
    assert (clones[0] / ".GROOM_QUARANTINE.json").is_file()


# --- runner resolution & the opencode refusal ------------------------------


def test_get_runner_rejects_opencode(tmp_path):
    with pytest.raises(RunnerUnsupportedError):
        get_runner("opencode", "model", tmp_path)


def test_get_runner_rejects_unknown_name(tmp_path):
    with pytest.raises(RunnerUnknownError):
        get_runner("some-random-cli", "model", tmp_path)


def test_get_runner_raises_when_command_missing(tmp_path, monkeypatch):
    import nexgen_core.vault.runner as runner_module

    monkeypatch.setattr(runner_module.shutil, "which", lambda _name: None)
    with pytest.raises(RunnerNotFoundError):
        get_runner("claude", "model", tmp_path)


def test_claude_runner_never_allows_push(tmp_path, monkeypatch):
    import nexgen_core.vault.runner as runner_module

    captured = {}

    def fake_streaming(cmd, *, cwd, input_text, timeout):
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        return RunResult(text="ok", exit_code=0)

    monkeypatch.setattr(runner_module, "_run_streaming", fake_streaming)
    active = get_runner("claude", "some-model", tmp_path)
    active.run_write("prompt", tmp_path / "clone")

    assert "--disallowedTools" in captured["cmd"]
    idx = captured["cmd"].index("--disallowedTools")
    assert captured["cmd"][idx + 1] == "Bash(git push:*)"
    assert captured["cwd"] == tmp_path / "clone"


# --- the timeout kills the whole process group, not just the parent -------


def test_run_streaming_kills_on_timeout():
    import sys
    import time

    from nexgen_core.vault.runner import _run_streaming

    start = time.monotonic()
    result = _run_streaming(
        [sys.executable, "-c", "import time; time.sleep(30)"], cwd=None, input_text=None, timeout=1
    )
    elapsed = time.monotonic() - start

    assert result.exit_code == 124
    assert elapsed < 10  # killed promptly, not left to run the full 30s


# --- retired names & --help -------------------------------------------------


@pytest.mark.parametrize("retired", ["plan", "run", "guarded"])
def test_retired_mode_names_are_rejected(retired, capsys):
    exit_code = groom.main([retired])
    assert exit_code == 2
    captured = capsys.readouterr()
    assert "retired" in captured.err


def test_help_exits_zero_and_is_readable(capsys):
    exit_code = groom.main(["--help"])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "preview" in captured.out
    assert "apply" in captured.out


def test_vault_groom_cli_help_works_end_to_end():
    """`vault-groom --help` (the actual installed entry point) must work
    and be understandable without reading code."""
    script = SCRIPTS_DIR / "nexgen_core" / "tools" / "vault_groom.py"
    proc = subprocess.run(
        [sys.executable, str(script), "--help"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0
    assert "preview" in proc.stdout
    assert "apply" in proc.stdout
