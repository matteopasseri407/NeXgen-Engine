"""The promotion gate: the only road into the real vault and its remotes.

Called AFTER the write pass has returned (successfully or not -- see
`write_exit_code`). The write pass never runs against the real vault: it
runs inside the `origin`-less clone that `gate.prepare_clone` prepared.
This module decides whether that clone deserves to become the real vault:

1. Audits the CLONE, not the vault: clean working tree, history since
   `base` a linear first-parent chain (zero merge commits), clean coverage
   (see `coverage.py`, read through `promote.py`).
2. On a clean audit, checks freshness (the real vault's HEAD must still be
   exactly `base`), then PROMOTES: fetches the clone's exact OID into the
   real vault and fast-forwards onto it. No re-execution: the audited OID
   is moved, never re-derived.
3. Only after promotion (deterministic code, no LLM involved) does it
   append the backlog line and, if requested, publish (`promote.py`).

On ANY audit failure the real vault is never touched: the clone stays in
place with a quarantine marker.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from nexgen_core.vault import promote
from nexgen_core.vault.coverage import check_coverage
from nexgen_core.vault.git_utils import GitCommandError, git

EXIT_OK = 0
EXIT_INTERNAL_OR_PUBLISH_FAILED = 1
EXIT_AUDIT_BLOCKED = 4
EXIT_STALE = 5


@dataclass
class AuditRequest:
    vault: Path
    clone: Path
    branch: str
    base: str
    archive_root: str
    state_dir: Path
    timestamp: str
    runner: str
    model: str
    tranche_sha256: str
    plan_record: Path
    propose_log: Path
    write_log: Path
    write_exit_code: int
    push_if_clean: bool = False
    engine_scripts: Path | None = None


def _new_record(request: AuditRequest) -> dict:
    return {
        "timestamp": request.timestamp,
        "runner": request.runner,
        "model": request.model,
        "vault": str(request.vault),
        "clone": str(request.clone),
        "base": request.base,
        "tranche_sha256": request.tranche_sha256,
        "plan_record": str(request.plan_record),
        "propose_log": str(request.propose_log),
        "write_log": str(request.write_log),
        "write_exit_code": request.write_exit_code,
        "commits": [],
        "files_touched": [],
        "promoted": False,
        "pushed": False,
    }


def _block(record: dict, request: AuditRequest, reason: str, exit_code: int, *, output) -> tuple[dict, int]:
    record["blocked_reason"] = reason
    promote.write_quarantine_marker(request.clone, reason, request.timestamp)
    promote.print_quarantine_summary(request.clone, reason, output=output)
    return record, exit_code


@dataclass
class _CloneAudit:
    head: str = ""
    blocked: tuple[dict, int] | None = None


def _audit_clone(request: AuditRequest, record: dict, *, output) -> _CloneAudit:
    """The three non-negotiable conditions on the clone."""
    clean = promote.clone_is_clean(request.clone)
    head = promote.clone_head(request.clone)
    linear = promote.history_is_linear(request.clone, request.base, head)
    commits, files_touched, added_paths = promote.collect_clone_facts(request.clone, request.base, head)
    coverage = check_coverage(str(request.plan_record), files_touched, added_paths, request.archive_root)

    record["clone_head"] = head
    record["clone_clean"] = clean
    record["history_linear"] = linear
    record["commits"] = commits
    record["files_touched"] = files_touched
    record.update(coverage)

    blocked_reason = None
    if not clean:
        blocked_reason = "clone working tree not clean after the write pass"
    elif not linear:
        blocked_reason = "clone history is not a linear first-parent chain (merge commit found)"
    elif coverage["coverage_status"] != "clean":
        blocked_reason = f"coverage {coverage['coverage_status']}"

    if blocked_reason:
        blocked = _block(record, request, blocked_reason, EXIT_AUDIT_BLOCKED, output=output)
        return _CloneAudit(blocked=blocked)
    return _CloneAudit(head=head)


def _promote(request: AuditRequest, record: dict, head: str, *, output) -> tuple[dict, int] | None:
    """Fetch + fast-forward of the exact audited OID. None if promoted."""
    try:
        git(request.vault, "fetch", str(request.clone), request.branch)
        fetched_tip = git(request.vault, "rev-parse", "FETCH_HEAD").strip()
        if fetched_tip != head:
            raise GitCommandError(f"fetched tip {fetched_tip} does not match the audited OID {head}")
        git(request.vault, "merge-base", "--is-ancestor", request.base, fetched_tip)
        git(request.vault, "merge", "--ff-only", fetched_tip)
    except GitCommandError as exc:
        return _block(record, request, f"promotion failed: {exc}", EXIT_INTERNAL_OR_PUBLISH_FAILED, output=output)
    record["promoted"] = True
    record["promoted_oid"] = head
    return None


def _publish_if_requested(request: AuditRequest, record: dict, *, output) -> int:
    if not request.push_if_clean:
        return EXIT_OK
    if not request.engine_scripts:
        output("vault-groom: push_if_clean requires engine_scripts, skipping publish.")
        return EXIT_INTERNAL_OR_PUBLISH_FAILED
    proc = promote.run_publish(request.vault, request.engine_scripts)
    sys.stdout.write(proc.stdout)
    sys.stderr.write(proc.stderr)
    if proc.returncode == 0:
        record["pushed"] = True
        return EXIT_OK
    output(
        f"vault-groom: publish failed (agent_sync.py exited {proc.returncode}) "
        "-- the promotion already landed locally, inspect or revert by hand."
    )
    return EXIT_INTERNAL_OR_PUBLISH_FAILED


def run_audit(request: AuditRequest, *, output=print) -> tuple[dict, int]:
    """The promotion gate. Returns (record, exit_code); it doesn't write
    the JSON record to disk itself -- that's the caller's job (CLI or
    groom.py), which knows where to put it."""
    record = _new_record(request)

    if request.write_exit_code != 0:
        return _block(
            record, request, f"the write-pass runner exited {request.write_exit_code}",
            EXIT_AUDIT_BLOCKED, output=output,
        )

    if not request.clone.is_dir():
        reason = f"expected clone directory is missing: {request.clone}"
        record["blocked_reason"] = reason
        output(f"vault-groom: {reason}")
        return record, EXIT_INTERNAL_OR_PUBLISH_FAILED

    audited = _audit_clone(request, record, output=output)
    if audited.blocked is not None:
        return audited.blocked
    head = audited.head

    try:
        real_head = git(request.vault, "rev-parse", "HEAD").strip()
    except GitCommandError as exc:
        return _block(
            record, request, f"could not read the real vault's HEAD: {exc}",
            EXIT_INTERNAL_OR_PUBLISH_FAILED, output=output,
        )

    record["vault_head_at_audit"] = real_head
    if real_head != request.base:
        reason = "vault moved during grooming"
        record["blocked_reason"] = reason
        promote.write_quarantine_marker(request.clone, reason, request.timestamp)
        output(
            f"vault-groom: {reason} (expected HEAD {request.base}, found {real_head}) -- "
            f"nothing promoted, vault untouched. Clone kept for inspection at {request.clone}"
        )
        return record, EXIT_STALE

    blocked = _promote(request, record, head, output=output)
    if blocked is not None:
        return blocked

    line, appended = promote.append_backlog_line(request.vault, record)
    backlog_commit = promote.commit_backlog(request.vault, request.timestamp) if appended else None

    exit_code = _publish_if_requested(request, record, output=output)
    promote.remove_promoted_clone(request.clone)

    output(f"promoted OID: {head}")
    output(f"backlog line: {line.rstrip()}")
    output(f"backlog commit: {backlog_commit or '(nothing to commit)'}")
    output(f"pushed: {str(record['pushed']).lower()}")

    return record, exit_code
