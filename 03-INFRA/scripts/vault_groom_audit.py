#!/usr/bin/env python3
"""Structured audit trail for one vault-groom run.

Called by vault-groom.sh/.ps1 AFTER the write pass returns. Builds a
structured JSON record from the real git history the write pass produced
(commits, files touched -- not the LLM's self-report), writes it to a
durable local state dir, then appends one deterministic summary line to
the vault's own backlog note and commits (+ pushes) that.

Kept in Python, not duplicated in bash and PowerShell, on purpose: this
project's own history is full of bugs from the same logic drifting between
its .sh and .ps1 twins (see CHANGELOG 0.4.0/Unreleased). One implementation,
called the same way from both wrappers.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

BACKLOG_NOTE = "99-INDEX/vault-cleanup-backlog.md"

FILENAME_RE = re.compile(r"`([\w./-]+\.md)`")
NO_ACTION_RE = re.compile(r"nessuna azione|no action", re.IGNORECASE)


def git(vault: str, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", vault, *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def extract_action_targets(tranche_text: str) -> set[str]:
    """Best-effort extraction of the files the approved tranche commits to touching.

    Parses the markdown table the playbook's own format always produces:
    "| `note.md` ... | **action** | reason |". Rows whose action column says
    "nessuna azione" (flag-only, no work planned) are excluded on purpose --
    those are legitimately never expected to show up in files_touched.

    This is a heuristic, not a strict parser: it exists to catch a silently
    dropped item (found for real on the gardener's first live run,
    2026-07-13: the write pass finished and reported success while having
    silently left 4 of the tranche's own planned file fixes undone), not to
    be a hard contract on tranche formatting.
    """
    targets: set[str] = set()
    for line in tranche_text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cols = [c.strip() for c in line.strip("|").split("|")]
        if len(cols) < 2:
            continue
        note_col, action_col = cols[0], cols[1]
        if NO_ACTION_RE.search(action_col):
            continue
        targets.update(FILENAME_RE.findall(note_col))
    return targets


def check_coverage(plan_record: str, files_touched: list[str]) -> list[str]:
    """Files the approved tranche planned to act on but never shows up touched.

    Compared by basename, not full path: an archive/merge action legitimately
    moves a file to a different directory (or deletes it into a merged note),
    and git diff --name-only reports the old path as touched (deleted)
    either way -- basename comparison survives that without false positives.
    """
    plan_path = Path(plan_record)
    if not plan_path.is_file():
        return []
    targets = extract_action_targets(plan_path.read_text(encoding="utf-8"))
    touched_basenames = {Path(f).name for f in files_touched}
    return sorted(targets - touched_basenames)


def build_record(args: argparse.Namespace) -> dict:
    if args.head_before != args.head_after:
        log_out = git(args.vault, "log", "--format=%H|%s", f"{args.head_before}..{args.head_after}")
        commits = []
        for line in log_out.splitlines():
            if not line.strip():
                continue
            commit_hash, _, subject = line.partition("|")
            commits.append({"hash": commit_hash, "subject": subject})
        diff_out = git(args.vault, "diff", "--name-only", args.head_before, args.head_after)
        files_touched = [line for line in diff_out.splitlines() if line.strip()]
    else:
        commits = []
        files_touched = []

    return {
        "timestamp": args.timestamp,
        "runner": args.runner,
        "model": args.model,
        "vault": args.vault,
        "tranche_sha256": args.tranche_sha256,
        "plan_record": args.plan_record,
        "head_before": args.head_before,
        "head_after": args.head_after,
        "pushed": args.pushed == "true",
        "commits": commits,
        "files_touched": files_touched,
        "unaddressed_targets": check_coverage(args.plan_record, files_touched),
        "propose_log": args.propose_log,
        "write_log": args.write_log,
    }


def append_backlog_line(vault: str, record: dict) -> tuple[str, bool]:
    """Appends the summary line unless this timestamp is already recorded.

    Returns (line, was_appended). The timestamp-marker check makes this
    idempotent: if the audit call for one run is ever invoked twice (a
    retried step, a re-run after a partial failure), it does not duplicate
    the backlog entry.
    """
    unaddressed = record.get("unaddressed_targets") or []
    line = (
        f"- {record['timestamp']}: runner={record['runner']} "
        f"commits={len(record['commits'])} files={len(record['files_touched'])} "
        f"pushed={str(record['pushed']).lower()} "
        f"tranche={record['tranche_sha256'][:12]}"
        + (f" UNADDRESSED={','.join(unaddressed)}" if unaddressed else "")
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


def commit_backlog(vault: str, timestamp: str, push: bool) -> str | None:
    git(vault, "add", BACKLOG_NOTE)
    status = git(vault, "status", "--porcelain", "--", BACKLOG_NOTE)
    if not status.strip():
        # Defensive double-check: append_backlog_line's own timestamp guard
        # is what normally prevents this, but nothing changed either way is
        # not an error.
        return None
    git(vault, "commit", "-m", f"chore(groom): record run {timestamp}")
    commit_hash = git(vault, "rev-parse", "HEAD").strip()
    if push:
        git(vault, "push")
    return commit_hash


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault", required=True)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--timestamp", required=True)
    parser.add_argument("--runner", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--tranche-sha256", required=True)
    parser.add_argument("--plan-record", required=True)
    parser.add_argument("--head-before", required=True)
    parser.add_argument("--head-after", required=True)
    parser.add_argument("--pushed", choices=["true", "false"], required=True)
    parser.add_argument("--propose-log", required=True)
    parser.add_argument("--write-log", required=True)
    args = parser.parse_args()

    record = build_record(args)

    state_dir = Path(args.state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    record_path = state_dir / f"{args.timestamp}.json"
    record_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")

    line, appended = append_backlog_line(args.vault, record)
    backlog_commit = (
        commit_backlog(args.vault, args.timestamp, push=record["pushed"])
        if appended
        else None
    )

    print(f"audit record: {record_path}")
    print(f"backlog line: {line.rstrip()}")
    print(f"backlog commit: {backlog_commit or '(nothing to commit)'}")

    if record["unaddressed_targets"]:
        print(
            "WARNING: the approved tranche named these files for action, but "
            "none of them appear among the commits' changed files -- the "
            "write pass may have silently left them undone, review by hand: "
            + ", ".join(record["unaddressed_targets"]),
            file=sys.stderr,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
