"""Shared proposal identity and exclusive, durable mutation attempts.

Approval, destination checks and receipts belong to each domain gate.
This owner prevents simultaneous use and replay of an uncertain attempt.
An attempt is not evidence that the provider completed the operation.
"""
from __future__ import annotations

import re
import hashlib
import json
import secrets
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Protocol

from nexgen_core.files import secure_artifact
from nexgen_core.lock import HostLock, LockTimeoutError


PROPOSAL_ID_RE = re.compile(r"\d{8}-\d{6}-[0-9a-f]{8}")


def valid_proposal_id(proposal_id: str) -> bool:
    return bool(PROPOSAL_ID_RE.fullmatch(str(proposal_id or "")))


def new_proposal_id() -> str:
    """Sortable ids with randomness; creation still refuses collisions."""
    return f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(4)}"


class AttemptedProposal(Protocol):
    attempted_at: str
    applied_at: str


def migrate_attempt(data: dict) -> dict:
    """Read the previous sending marker without reopening uncertain work."""
    legacy = data.pop("sending_at", "")
    if legacy:
        data["attempted_at"] = data.get("attempted_at") or legacy
    return data


def read_proposal_data(path: Path, proposal_id: str, error: type[Exception]) -> dict:
    """The locked filename and stored id must name the same artifact."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("id") != proposal_id:
        raise error("identita della proposta incoerente con il file: riproponi")
    return migrate_attempt(data)


def content_sha(*parts: str) -> str:
    """Bind approval fields using an unambiguous, versioned encoding."""
    encoded = json.dumps(parts, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    return "v1:" + hashlib.sha256(encoded).hexdigest()


def validate_content(saved: str, error: type[Exception], *parts: str) -> None:
    if not isinstance(saved, str) or not saved:
        raise error("proposta senza vincolo di integrita': riproponi e riapprova")
    expected = content_sha(*parts)
    if not saved.startswith("v1:") and all(isinstance(p, str) and "\x00" not in p for p in parts):
        # Read the previous NUL-delimited encoding only where its boundaries
        # are unambiguous. New proposals always use the versioned format.
        expected = hashlib.sha256(b"".join(b"\x00" + p.encode("utf-8") for p in parts)).hexdigest()
    if saved != expected:
        raise error("contenuto cambiato dopo la proposta: riproponi e riapprova")


def refuse_prior_attempt(proposal: AttemptedProposal, error: type[Exception]) -> None:
    if proposal.attempted_at:
        raise error("tentativo gia' avviato: verifica l'esito prima di preparare una nuova proposta")


def proposal_status(proposal: AttemptedProposal, completed: str, pending: str = "da approvare") -> str:
    if proposal.applied_at:
        return completed
    return "esito da verificare" if proposal.attempted_at else pending


@contextmanager
def proposal_lock(directory: Path, proposal_id: str, error: type[Exception]):
    """Hold ownership before loading the proposal and until outcome storage."""
    if not valid_proposal_id(proposal_id):
        raise error(f"id proposta non valido: {proposal_id}")
    lock = HostLock(directory / f"{proposal_id}.lock", timeout=0, command_name="nexgen-local approve")
    try:
        secure_artifact(directory)
        lock.acquire()
    except LockTimeoutError as exc:
        raise error("proposta in uso: attendi la fine dell'altra esecuzione") from exc
    except OSError as exc:
        raise error(f"lock della proposta non accessibile ({type(exc).__name__})") from exc
    try:
        secure_artifact(directory, lock.lock_path)
        yield
    finally:
        lock.release()
    # The inode remains stable even after completion or interruption.


def record_attempt(proposal: AttemptedProposal, save: Callable[[], None], error: type[Exception]) -> None:
    """Write before mutation; a saved attempt permanently prevents replay.

    Call under proposal_lock, after domain validation and the intent receipt.
    If final storage or transport fails, an empty applied_at cannot prove the
    operation did not happen. The operator must verify the outcome before
    preparing another proposal; repeating this id is never safe.
    """
    refuse_prior_attempt(proposal, error)
    proposal.attempted_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    try:
        save()
    except OSError as exc:
        raise error(f"tentativo non registrato, operazione non avviata ({type(exc).__name__})") from exc
