"""Adversarial-review plumbing: the brief for the reviewer and the receipt the gate checks.

The receipt binds a verdict to one exact set of file contents and keeps the reviewer's
full answer for the owner to read. It stops the careless case (unreviewed or changed-
after-review work). It does not stop an agent that forges the file by hand: that is
an honesty check, not a security boundary.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from ..files import atomic_write_text
from .brief import BRIEF
from .gitutil import diff_text, own_changes, show

WORK_DIR = ".nexgen-discipline"
RECEIPT_DIR = f"{WORK_DIR}/receipts"
PENDING = f"{WORK_DIR}/pending"
VERDICT_RE = re.compile(r"verdict\s*:\s*(APPROVE|REVISE|REJECT)", re.I)
OWNER_WORDS = {"APPROVE": "PROCEDI", "REVISE": "RIDUCI", "REJECT": "FERMA"}


def _digest(text: str | None) -> str:
    return "-" if text is None else hashlib.sha256(text.replace("\r\n", "\n").encode()).hexdigest()[:16]


def diff_hash(root: Path, base: str, head: str | None) -> str:
    """Hash of (path, old content, new content) per changed file: independent of git settings."""
    rows = sorted(f"{c.path}\t{_digest(c.old)}\t{_digest(c.new)}" for c in own_changes(root, base, head, renames=False))
    return hashlib.sha256("\n".join(rows).encode()).hexdigest()[:16]


def write_brief(root: Path, base: str, head: str | None, total: int, reasons: list[str]) -> tuple[Path, Path, str]:
    work = root / WORK_DIR
    diff_path, brief_path = work / "review.diff", work / "brief.md"
    digest = diff_hash(root, base, head)
    atomic_write_text(diff_path, diff_text(root, base, head))
    atomic_write_text(root / PENDING, digest)
    brief = BRIEF.replace("{score}", str(total)).replace("{reasons}", "\n".join(f"- {r}" for r in reasons))
    atomic_write_text(brief_path, brief.replace("{id}", digest))
    return diff_path, brief_path, digest


def parse_verdict(text: str) -> str | None:
    """The verdict is the last non-empty line, as the brief demands."""
    lines = [line.strip(" *`_") for line in text.splitlines() if line.strip()]
    match = VERDICT_RE.fullmatch(lines[-1]) if lines else None
    return match.group(1).upper() if match else None


def receipt_refusal(root: Path, digest: str, text: str) -> str | None:
    """Why this reviewer output cannot become a receipt for the current work, or None."""
    pending = (root / PENDING).read_text(encoding="utf-8").strip() if (root / PENDING).is_file() else None
    if pending is None:
        return "no review was prepared: run `review` first"
    if pending != digest:
        return "the code changed after `review` prepared the brief: run `review` again"
    if digest not in text:
        return f"the reviewer output does not repeat the review id {digest}"
    return None


def record_receipt(root: Path, digest: str, verdict: str, reviewer: str, output: str) -> Path:
    path = root / RECEIPT_DIR / f"{digest}.json"
    atomic_write_text(path, json.dumps({
        "diff_hash": digest, "verdict": verdict, "reviewer": reviewer, "output": output,
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }, indent=2) + "\n")
    return path


def receipt_problem(root: Path, digest: str, head: str | None) -> str | None:
    """None when a committed APPROVE receipt exists for exactly this work, else why not."""
    path = f"{RECEIPT_DIR}/{digest}.json"
    if head is None:
        file = root / path
        text = file.read_text(encoding="utf-8") if file.is_file() else None
    else:
        text = show(root, f":{path}" if head == ":" else f"{head}:{path}")
    if text is None:
        return "no review receipt for this exact work"
    try:
        verdict = json.loads(text).get("verdict")
    except json.JSONDecodeError:
        return "review receipt is not valid JSON"
    return None if verdict == "APPROVE" else f"the review verdict is {verdict} ({OWNER_WORDS.get(verdict, '?')}), not APPROVE"
