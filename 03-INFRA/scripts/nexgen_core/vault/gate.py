"""vault-groom's safety gate.

Every function here enforces a structural guarantee, not a hoped-for
convention:

- `require_clean_tree`: with uncommitted changes, nothing starts -- the
  gate needs a clean HEAD to clone from.
- `prepare_clone`: clones the vault and IMMEDIATELY removes the `origin`
  remote from the clone, so a `git push` from the write pass has nowhere
  to go, not just a prohibition written into the prompt.
- `confirm_tranche`: requires the word "yes" spelled out in full,
  case-sensitive comparison; any other answer cancels without writing
  anything.
- `hash_bytes` / `verify_hash_unchanged`: the anti-TOCTOU guard. The
  approved plan is hashed right after approval and re-hashed right before
  the write pass -- if it changed in between, it aborts.

`hash_bytes` normalizes CRLF -> LF before computing the sha256: the same
text written and reread on Windows would otherwise produce a different
hash than the one computed elsewhere, for a file that's text for a
human/LLM, not a binary blob.
"""
from __future__ import annotations

import hashlib
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

from nexgen_core.git_ops import run_git
from nexgen_core.i18n import t
from nexgen_core.vault.git_utils import git

CLONE_TIMEOUT_SECONDS = 300


class GateError(RuntimeError):
    """A non-negotiable safety rule was not satisfied."""


def working_tree_is_clean(repo: Path) -> bool:
    """True if `git status --porcelain` reports nothing."""
    result = run_git(repo, "status", "--porcelain")
    return result.returncode == 0 and result.stdout.strip() == ""


def require_clean_tree(repo: Path) -> None:
    """Rule 5: a dirty tree must never reach the clone."""
    if not working_tree_is_clean(repo):
        raise GateError(t(
            "vault-groom: the vault's working tree is not clean ({repo}) -- "
            "commit or stash them first. Aborting: the temp-clone gate needs "
            "a clean HEAD to clone from, zero writes made.",
            repo=repo,
        ))


def hash_bytes(data: bytes) -> str:
    """sha256 of the bytes, after CRLF -> LF normalization."""
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def write_plan_record(state_dir: Path, timestamp: str, tranche_text: str) -> Path:
    """Saves the approved tranche's text as the single source of truth.

    The hash the confirmation shows and the one the TOCTOU guard
    recomputes is always that of this file's BYTES, never of the in-memory
    string -- so the two checks and the write pass agree on what they're
    hashing.
    """
    state_dir.mkdir(parents=True, exist_ok=True)
    plan_record = state_dir / f"{timestamp}-plan.txt"
    text = tranche_text if tranche_text.endswith("\n") else tranche_text + "\n"
    plan_record.write_text(text, encoding="utf-8", newline="\n")
    return plan_record


def hash_plan_record(plan_record: Path) -> str:
    """The hash of the file on disk, not of the string that generated it."""
    return hash_bytes(plan_record.read_bytes())


def verify_hash_unchanged(plan_record: Path, expected_hash: str) -> None:
    """Rule 4: the TOCTOU guard, re-run right before the write pass."""
    current = hash_plan_record(plan_record)
    if current != expected_hash:
        raise GateError(t(
            "vault-groom: plan record changed after approval (expected "
            "{expected}, got {current}) -- aborting, zero writes. "
            "Re-run and re-approve.",
            expected=expected_hash, current=current,
        ))


def confirm_tranche(
    tranche_text: str,
    tranche_hash: str,
    *,
    input_func: Callable[[str], str] = input,
    output: Callable[[str], object] = print,
) -> bool:
    """Rule 3: shows the tranche and requires an exact, spelled-out 'yes'."""
    output("=" * 70)
    output(t(" Proposed tranche (sha256 {short_hash}...) -- read it before confirming", short_hash=tranche_hash[:12]))
    output("=" * 70)
    output(tranche_text)
    output("=" * 70)
    output(t("Type exactly 'yes' to execute THIS tranche as-is."))
    output(t("Any other answer cancels: no changes to the vault."))
    try:
        answer = input_func(t("Proceed? > "))
    except EOFError:
        answer = ""
    return answer == "yes"


def prepare_clone(vault: Path, state_dir: Path, timestamp: str) -> Path:
    """Rule 2: the temp-clone gate.

    Clones the vault into a temporary directory under `state_dir` and
    IMMEDIATELY removes the `origin` remote from the clone, before the
    write pass runs a single command -- it's this, not the prompt's
    wording, that makes a `git push` from the write pass mechanically
    impossible.
    """
    state_dir.mkdir(parents=True, exist_ok=True)
    clone_dir = Path(tempfile.mkdtemp(prefix=f"{timestamp}-clone-", dir=str(state_dir)))
    subprocess.run(
        ["git", "clone", "-q", str(vault), str(clone_dir)],
        check=True,
        timeout=CLONE_TIMEOUT_SECONDS,
    )
    git(clone_dir, "remote", "remove", "origin")
    return clone_dir
