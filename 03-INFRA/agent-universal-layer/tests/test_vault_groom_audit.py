"""Behavioral tests for vault_groom_audit.py against a REAL git repo.

vault-groom.sh/.ps1 call this script after a write pass returns, to build
the structured audit record the 2026-07-13 review found missing entirely
(the old GROOM_LOG was raw stdout in /tmp, not a durable, structured trace).
These tests exercise the real git plumbing (log/diff/add/commit/push), not
mocks -- the whole point of this script is to report what git actually did,
not what the LLM claims it did.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
REAL_UL = TESTS_DIR.parent
REAL_VAULT = REAL_UL.parent.parent
AUDIT_SCRIPT = REAL_VAULT / "03-INFRA" / "scripts" / "vault_groom_audit.py"


def _git(vault, *args):
    return subprocess.run(
        ["git", "-C", str(vault), *args],
        capture_output=True, text=True, check=True,
    )


def _init_vault(tmp_path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    _git(vault, "init", "-q", "-b", "main")
    _git(vault, "config", "user.email", "nexgen-tests.invalid")
    _git(vault, "config", "user.name", "Test")
    (vault / "README.md").write_text("seed\n", encoding="utf-8")
    _git(vault, "add", "README.md")
    _git(vault, "commit", "-q", "-m", "seed")
    return vault


def _run_audit(vault, state_dir, **overrides):
    args = {
        "vault": str(vault),
        "state_dir": str(state_dir),
        "timestamp": "test-run-0001",
        "runner": "claude",
        "model": "claude-sonnet-5",
        "tranche_sha256": "a" * 64,
        "plan_record": str(state_dir / "test-run-0001-plan.txt"),
        "head_before": _git(vault, "rev-parse", "HEAD").stdout.strip(),
        "head_after": _git(vault, "rev-parse", "HEAD").stdout.strip(),
        "pushed": "false",
        "propose_log": "/tmp/propose.log",
        "write_log": "/tmp/write.log",
    }
    args.update(overrides)
    cli_args = []
    for key, value in args.items():
        cli_args += [f"--{key.replace('_', '-')}", value]
    return subprocess.run(
        ["python3", str(AUDIT_SCRIPT), *cli_args],
        capture_output=True, text=True, timeout=30,
    )


def test_record_built_from_real_git_history_between_before_and_after(tmp_path):
    vault = _init_vault(tmp_path)
    head_before = _git(vault, "rev-parse", "HEAD").stdout.strip()
    (vault / "groomed.md").write_text("archived content\n", encoding="utf-8")
    _git(vault, "add", "groomed.md")
    _git(vault, "commit", "-q", "-m", "archive: fold groomed.md")
    head_after = _git(vault, "rev-parse", "HEAD").stdout.strip()

    state_dir = tmp_path / "state"
    proc = _run_audit(vault, state_dir, head_before=head_before, head_after=head_after)
    assert proc.returncode == 0, proc.stdout + proc.stderr

    record_path = state_dir / "test-run-0001.json"
    assert record_path.exists()
    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert record["head_before"] == head_before
    assert record["head_after"] == head_after
    assert len(record["commits"]) == 1
    assert record["commits"][0]["subject"] == "archive: fold groomed.md"
    assert record["files_touched"] == ["groomed.md"]
    assert record["pushed"] is False


def test_record_when_head_unchanged_reports_zero_commits(tmp_path):
    vault = _init_vault(tmp_path)
    state_dir = tmp_path / "state"
    proc = _run_audit(vault, state_dir)
    assert proc.returncode == 0, proc.stdout + proc.stderr

    record = json.loads((state_dir / "test-run-0001.json").read_text(encoding="utf-8"))
    assert record["commits"] == []
    assert record["files_touched"] == []


def test_backlog_line_appended_and_committed(tmp_path):
    vault = _init_vault(tmp_path)
    (vault / "99-INDEX").mkdir()
    (vault / "99-INDEX" / "vault-cleanup-backlog.md").write_text("# Backlog\n", encoding="utf-8")
    _git(vault, "add", "99-INDEX/vault-cleanup-backlog.md")
    _git(vault, "commit", "-q", "-m", "seed backlog")

    state_dir = tmp_path / "state"
    proc = _run_audit(vault, state_dir, runner="codex", tranche_sha256="b" * 64)
    assert proc.returncode == 0, proc.stdout + proc.stderr

    backlog = (vault / "99-INDEX" / "vault-cleanup-backlog.md").read_text(encoding="utf-8")
    assert "runner=codex" in backlog
    assert "commits=0" in backlog
    assert ("b" * 12) in backlog

    log = _git(vault, "log", "-1", "--format=%s").stdout.strip()
    assert log == "chore(groom): record run test-run-0001"


def test_backlog_created_when_missing(tmp_path):
    vault = _init_vault(tmp_path)
    assert not (vault / "99-INDEX").exists()

    state_dir = tmp_path / "state"
    proc = _run_audit(vault, state_dir)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (vault / "99-INDEX" / "vault-cleanup-backlog.md").exists()


def test_push_true_reaches_the_real_remote(tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)

    vault = _init_vault(tmp_path)
    _git(vault, "remote", "add", "origin", str(remote))
    _git(vault, "push", "-q", "-u", "origin", "main")

    (vault / "99-INDEX").mkdir()
    (vault / "99-INDEX" / "vault-cleanup-backlog.md").write_text("# Backlog\n", encoding="utf-8")
    _git(vault, "add", "99-INDEX/vault-cleanup-backlog.md")
    _git(vault, "commit", "-q", "-m", "seed backlog")
    _git(vault, "push", "-q")

    state_dir = tmp_path / "state"
    proc = _run_audit(vault, state_dir, pushed="true")
    assert proc.returncode == 0, proc.stdout + proc.stderr

    local_head = _git(vault, "rev-parse", "HEAD").stdout.strip()
    # A bare remote's own ref only updates locally-tracked after a fetch;
    # check the bare repo's ref directly instead of `origin/main` here.
    remote_ref = subprocess.run(
        ["git", "-C", str(remote), "rev-parse", "main"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert remote_ref == local_head


def test_push_false_does_not_touch_the_remote(tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)

    vault = _init_vault(tmp_path)
    _git(vault, "remote", "add", "origin", str(remote))
    _git(vault, "push", "-q", "-u", "origin", "main")
    remote_head_before = subprocess.run(
        ["git", "-C", str(remote), "rev-parse", "main"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    state_dir = tmp_path / "state"
    proc = _run_audit(vault, state_dir, pushed="false")
    assert proc.returncode == 0, proc.stdout + proc.stderr

    remote_head_after = subprocess.run(
        ["git", "-C", str(remote), "rev-parse", "main"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert remote_head_after == remote_head_before


def test_rerun_with_identical_backlog_line_is_a_harmless_noop(tmp_path):
    vault = _init_vault(tmp_path)
    state_dir = tmp_path / "state"

    first = _run_audit(vault, state_dir)
    assert first.returncode == 0, first.stdout + first.stderr
    head_after_first = _git(vault, "rev-parse", "HEAD").stdout.strip()

    # Re-run with the exact same args (same timestamp/hash/runner) -- the
    # line append_backlog_line would produce is byte-identical, so nothing
    # actually changed for git to commit.
    second = _run_audit(vault, state_dir)
    assert second.returncode == 0, second.stdout + second.stderr
    assert "nothing to commit" in second.stdout

    head_after_second = _git(vault, "rev-parse", "HEAD").stdout.strip()
    assert head_after_second == head_after_first


# --- Coverage check: did the write pass actually do what it approved? ---
#
# Found for real on the gardener's first live run (2026-07-13): the write
# pass finished, self-reported success, and pushed -- while having silently
# left 4 of its own approved tranche's file fixes undone. Nothing caught it;
# a human had to grep the vault by hand afterward. These tests pin the
# mechanical check that replaces that manual grep.

def _write_plan(state_dir: Path, timestamp: str, text: str) -> Path:
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / f"{timestamp}-plan.txt"
    path.write_text(text, encoding="utf-8")
    return path


def test_coverage_flags_a_planned_file_that_was_never_touched(tmp_path):
    vault = _init_vault(tmp_path)
    state_dir = tmp_path / "state"
    plan = _write_plan(
        state_dir, "test-run-0001",
        "| Nota | Azione | Perche' |\n"
        "|---|---|---|\n"
        "| `touched.md` | **archive** | ok |\n"
        "| `forgotten.md` | **fix-frontmatter** | ok |\n",
    )
    head_before = _git(vault, "rev-parse", "HEAD").stdout.strip()
    (vault / "touched.md").write_text("x\n", encoding="utf-8")
    _git(vault, "add", "touched.md")
    _git(vault, "commit", "-q", "-m", "archive: touched.md")
    head_after = _git(vault, "rev-parse", "HEAD").stdout.strip()

    proc = _run_audit(vault, state_dir, plan_record=str(plan), head_before=head_before, head_after=head_after)
    assert proc.returncode == 0, proc.stdout + proc.stderr

    record = json.loads((state_dir / "test-run-0001.json").read_text(encoding="utf-8"))
    assert record["unaddressed_targets"] == ["forgotten.md"]
    assert "WARNING" in proc.stderr
    assert "forgotten.md" in proc.stderr

    backlog = (vault / "99-INDEX" / "vault-cleanup-backlog.md").read_text(encoding="utf-8")
    assert "UNADDRESSED=forgotten.md" in backlog


def test_coverage_ignores_rows_flagged_nessuna_azione(tmp_path):
    vault = _init_vault(tmp_path)
    state_dir = tmp_path / "state"
    plan = _write_plan(
        state_dir, "test-run-0001",
        "| Nota | Azione | Perche' |\n"
        "|---|---|---|\n"
        "| `untouched-on-purpose.md` | **nessuna azione** | in dubbio, si lascia |\n",
    )
    proc = _run_audit(vault, state_dir, plan_record=str(plan))
    assert proc.returncode == 0, proc.stdout + proc.stderr

    record = json.loads((state_dir / "test-run-0001.json").read_text(encoding="utf-8"))
    assert record["unaddressed_targets"] == []
    assert "WARNING" not in proc.stderr


def test_coverage_matches_by_basename_across_an_archive_move(tmp_path):
    vault = _init_vault(tmp_path)
    state_dir = tmp_path / "state"
    plan = _write_plan(
        state_dir, "test-run-0001",
        "| Nota | Azione | Perche' |\n"
        "|---|---|---|\n"
        "| `old-note.md` | **archive** | chiuso |\n",
    )
    (vault / "old-note.md").write_text("content\n", encoding="utf-8")
    _git(vault, "add", "old-note.md")
    _git(vault, "commit", "-q", "-m", "seed old-note")
    head_before = _git(vault, "rev-parse", "HEAD").stdout.strip()

    (vault / "archive").mkdir()
    _git(vault, "mv", "old-note.md", "archive/old-note.md")
    _git(vault, "commit", "-q", "-m", "archive(nexgen): sposta old-note.md in archive/")
    head_after = _git(vault, "rev-parse", "HEAD").stdout.strip()

    proc = _run_audit(vault, state_dir, plan_record=str(plan), head_before=head_before, head_after=head_after)
    assert proc.returncode == 0, proc.stdout + proc.stderr

    record = json.loads((state_dir / "test-run-0001.json").read_text(encoding="utf-8"))
    assert record["unaddressed_targets"] == [], "a same-basename move to archive/ must not false-positive"


def test_coverage_handles_a_multi_file_merge_row(tmp_path):
    vault = _init_vault(tmp_path)
    state_dir = tmp_path / "state"
    plan = _write_plan(
        state_dir, "test-run-0001",
        "| Nota | Azione | Perche' |\n"
        "|---|---|---|\n"
        "| `a.md` + `b.md` + `c.md` | **merge** in una nota | duplicati |\n",
    )
    for name in ("a.md", "b.md", "c.md"):
        (vault / name).write_text("x\n", encoding="utf-8")
    _git(vault, "add", "a.md", "b.md", "c.md")
    _git(vault, "commit", "-q", "-m", "seed a/b/c")
    head_before = _git(vault, "rev-parse", "HEAD").stdout.strip()

    _git(vault, "rm", "-q", "a.md", "b.md", "c.md")
    (vault / "merged.md").write_text("merged\n", encoding="utf-8")
    _git(vault, "add", "merged.md")
    _git(vault, "commit", "-q", "-m", "merge: a+b+c into merged.md")
    head_after = _git(vault, "rev-parse", "HEAD").stdout.strip()

    proc = _run_audit(vault, state_dir, plan_record=str(plan), head_before=head_before, head_after=head_after)
    assert proc.returncode == 0, proc.stdout + proc.stderr

    record = json.loads((state_dir / "test-run-0001.json").read_text(encoding="utf-8"))
    assert record["unaddressed_targets"] == []


def test_coverage_missing_plan_record_file_is_harmless(tmp_path):
    vault = _init_vault(tmp_path)
    state_dir = tmp_path / "state"
    proc = _run_audit(vault, state_dir, plan_record=str(state_dir / "does-not-exist-plan.txt"))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    record = json.loads((state_dir / "test-run-0001.json").read_text(encoding="utf-8"))
    assert record["unaddressed_targets"] == []
