"""Facts read from the git clone and the deterministic post-audit actions.

Separated from `audit.py` (which decides WHETHER to promote) to keep each
file under ~300 lines: this is where the git reads used to judge the clone
live (cleanliness, linearity, what it touched) along with the actions that
happen only AFTER a clean audit -- backlog line, commit, publish,
quarantine.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

from nexgen_core.vault.coverage import BACKLOG_NOTE
from nexgen_core.vault.git_utils import git

QUARANTINE_MARKER_NAME = ".GROOM_QUARANTINE.json"
PUBLISH_TIMEOUT_SECONDS = 120


def clone_is_clean(clone: Path) -> bool:
    return git(clone, "status", "--porcelain").strip() == ""


def clone_head(clone: Path) -> str:
    return git(clone, "rev-parse", "HEAD").strip()


def history_is_linear(clone: Path, base: str, head: str) -> bool:
    """True if base..head is a linear first-parent chain (zero merges)."""
    if base == head:
        return True
    out = git(clone, "rev-list", "--min-parents=2", f"{base}..{head}")
    return out.strip() == ""


def collect_clone_facts(clone: Path, base: str, head: str) -> tuple[list[dict], list[str], set[str]]:
    """(commits, files_touched, added_paths) for base..head in the clone.

    `--no-renames`: a rename is broken down into a raw delete+add so the
    archive-move exception in check_coverage sees an honest ADDED path
    under the archive root, not a collapsed rename.
    """
    if base == head:
        return [], [], set()
    log_out = git(clone, "log", "--first-parent", "--format=%H|%s", f"{base}..{head}")
    commits = []
    for line in log_out.splitlines():
        if not line.strip():
            continue
        commit_hash, _, subject = line.partition("|")
        commits.append({"hash": commit_hash, "subject": subject})

    status_out = git(clone, "diff", "--no-renames", "--name-status", f"{base}..{head}")
    touched: set[str] = set()
    added: set[str] = set()
    for line in status_out.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        status, path = parts[0], parts[-1]
        touched.add(path)
        if status.startswith("A"):
            added.add(path)
    return commits, sorted(touched), added


def append_backlog_line(vault: Path, record: dict) -> tuple[str, bool]:
    """Appends the summary line, idempotent on a repeated timestamp."""
    unaddressed = record.get("unaddressed_targets") or []
    out_of_scope = record.get("out_of_scope_targets") or []
    line = (
        f"- {record['timestamp']}: runner={record['runner']} "
        f"commits={len(record['commits'])} files={len(record['files_touched'])} "
        f"coverage={record.get('coverage_status', 'clean')} "
        f"tranche={record['tranche_sha256'][:12]}"
        + (f" UNADDRESSED={','.join(unaddressed)}" if unaddressed else "")
        + (f" UNPLANNED={','.join(out_of_scope)}" if out_of_scope else "")
        + "\n"
    )
    note_path = Path(vault) / BACKLOG_NOTE
    existing = note_path.read_text(encoding="utf-8") if note_path.exists() else ""
    if f"- {record['timestamp']}:" in existing:
        return line, False

    if existing and not existing.endswith("\n"):
        existing += "\n"
    note_path.parent.mkdir(parents=True, exist_ok=True)
    note_path.write_text(existing + line, encoding="utf-8")
    return line, True


def commit_backlog(vault: Path, timestamp: str) -> str | None:
    git(vault, "add", BACKLOG_NOTE)
    status = git(vault, "status", "--porcelain", "--", BACKLOG_NOTE)
    if not status.strip():
        return None
    git(vault, "commit", "-m", f"chore(groom): record run {timestamp}")
    return git(vault, "rev-parse", "HEAD").strip()


def run_publish(vault: Path, engine_scripts: Path) -> subprocess.CompletedProcess:
    """Invokes the engine's `agent_sync.py publish`, with a timeout."""
    env = dict(os.environ)
    env["KNOWLEDGE_VAULT_PATH"] = str(vault)
    exe = sys.executable or shutil.which("python3") or shutil.which("python") or "python3"
    return subprocess.run(
        [exe, str(Path(engine_scripts) / "agent_sync.py"), "publish"],
        cwd=str(vault),
        env=env,
        capture_output=True,
        text=True,
        timeout=PUBLISH_TIMEOUT_SECONDS,
        check=False,
    )


def write_quarantine_marker(clone: Path, reason: str, timestamp: str) -> None:
    clone_path = Path(clone)
    if not clone_path.is_dir():
        return
    marker = clone_path / QUARANTINE_MARKER_NAME
    marker.write_text(
        json.dumps({"quarantined_at": timestamp, "reason": reason}, indent=2) + "\n",
        encoding="utf-8",
    )


def print_quarantine_summary(clone: Path, reason: str, *, output=print) -> None:
    output("=" * 70)
    output(f"vault-groom: AUDIT BLOCKED -- {reason}")
    output("vault-groom: your vault is UNTOUCHED -- nothing was promoted or pushed.")
    output("vault-groom: the quarantined clone (everything the write pass did) is kept at:")
    output(f"  {clone}")
    output("vault-groom: inspect it by hand, then delete it once you're done with it.")
    output("=" * 70)


def _on_rm_error(func, path, _exc_info) -> None:
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        pass


def remove_promoted_clone(clone: Path) -> None:
    """Removes the promoted clone -- otherwise one accumulates per run.

    Quarantined clones (failed audit) never pass through here.
    `GROOM_KEEP_CLONE=1` skips the removal (debug/inspection).
    """
    if os.environ.get("GROOM_KEEP_CLONE") == "1":
        return
    try:
        shutil.rmtree(clone, onerror=_on_rm_error)
    except OSError as exc:
        print(f"vault-groom: could not remove the promoted clone ({exc}) -- safe to delete by hand: {clone}")
