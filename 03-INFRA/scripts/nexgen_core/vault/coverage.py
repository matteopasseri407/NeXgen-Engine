"""Coverage of the approved tranche: what was supposed to be touched, what wasn't.

Extracted from vault_groom_audit.py (release), unchanged in logic:
best-effort parsing of the `| Note | Action | Why |` table that
PROPOSE_PROMPT requires, then a bidirectional check against the files
actually touched by the write pass.
"""
from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

FILENAME_RE = re.compile(r"`([\w./-]+\.md)`")
NO_ACTION_RE = re.compile(r"nessuna azione|no action", re.IGNORECASE)
# Deliberately broad (matches "archive", "archivia", "archiviare", ...): the
# Action column is free prose, not an enum -- this only decides which
# targets are ELIGIBLE for the archive-move exception below, never coverage
# itself.
ARCHIVE_ACTION_RE = re.compile(r"archivi|archive", re.IGNORECASE)

BACKLOG_NOTE = "99-INDEX/vault-cleanup-backlog.md"


def _under_archive_root(path: str, archive_root: str) -> bool:
    root = PurePosixPath(archive_root.strip("/"))
    p = PurePosixPath(path.strip("/"))
    return p == root or root in p.parents


def extract_action_targets(tranche_text: str) -> tuple[set[str], set[str], bool]:
    """Extracts the files the approved tranche commits to touching.

    Rows whose Action column says "no action" don't produce a target (never
    expected in files_touched) but still count as a correctly parsed row
    (third return value), so a tranche that's all "no action" isn't
    confused with one that doesn't parse as a table at all.

    Returns (targets, archive_targets, found_any_row). archive_targets is
    the subset of targets whose row reads as an archive-type action -- the
    only case where the archive-move exception below applies.
    """
    targets: set[str] = set()
    archive_targets: set[str] = set()
    found_any_row = False
    for line in tranche_text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cols = [c.strip() for c in line.strip("|").split("|")]
        if len(cols) < 2:
            continue
        note_col, action_col = cols[0], cols[1]
        row_targets = FILENAME_RE.findall(note_col)
        is_no_action = bool(NO_ACTION_RE.search(action_col))
        if row_targets or is_no_action:
            found_any_row = True
        if is_no_action:
            continue
        targets.update(row_targets)
        if row_targets and ARCHIVE_ACTION_RE.search(action_col):
            archive_targets.update(row_targets)
    return targets, archive_targets, found_any_row


def check_coverage(
    plan_record: str, files_touched: list[str], added_paths: set[str], archive_root: str
) -> dict:
    """Bidirectional check: every approved target was touched, and no
    out-of-plan file was touched.

    Path-exact on the "the target was touched" side: a target either
    appears verbatim in files_touched or is unaddressed, with no basename
    fallback. The only exception lives on the other side: an ADDED path is
    excused from out_of_scope if it's under `archive_root` AND its
    basename matches that (or the renamed form `<stem>-archive-<date>.md`,
    the playbook's pattern) of a target whose row is archive-type.
    """
    plan_path = Path(plan_record)
    if not plan_path.is_file():
        return {
            "coverage_status": "clean",
            "unaddressed_targets": [],
            "matched_by_archive_move": [],
            "out_of_scope_targets": [],
        }

    tranche_text = plan_path.read_text(encoding="utf-8")
    targets, archive_targets, found_any_row = extract_action_targets(tranche_text)

    if tranche_text.strip() and not found_any_row:
        return {
            "coverage_status": "unparseable",
            "unaddressed_targets": [],
            "matched_by_archive_move": [],
            "out_of_scope_targets": [],
        }

    touched_set = set(files_touched)
    unaddressed = sorted(target for target in targets if target not in touched_set)

    archive_target_basenames = {Path(target).name for target in archive_targets}
    archive_target_stems = {Path(target).stem for target in archive_targets}

    def _is_archived_shape(name: str) -> bool:
        if name in archive_target_basenames:
            return True
        return any(name.startswith(stem + "-") for stem in archive_target_stems)

    matched_by_archive_move = sorted(
        path for path in added_paths
        if _under_archive_root(path, archive_root) and _is_archived_shape(Path(path).name)
    )
    sanctioned = set(matched_by_archive_move)
    out_of_scope = sorted(
        touched for touched in files_touched
        if touched not in targets and touched != BACKLOG_NOTE and touched not in sanctioned
    )

    status = "dirty" if (unaddressed or out_of_scope) else "clean"
    return {
        "coverage_status": status,
        "unaddressed_targets": unaddressed,
        "matched_by_archive_move": matched_by_archive_move,
        "out_of_scope_targets": out_of_scope,
    }
