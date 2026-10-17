# nexgen: one-job reason="apply one approved patch to one file without leaving it half-changed"
"""The pen, gated: patch proposals whose approval screen is machine facts only.

The model never writes. It returns a snippet replacement as JSON; the engine
checks that the snippet occurs exactly once, computes the unified diff, dry-runs
it against the repository and stores the proposal as an artifact. The approval
screen shows only what the machine verified: canonical path, original hash,
diff, dry-run result. The model's prose is stored, labelled as unverified, and
is never evidence. Applying is a separate command that re-checks the original
hash first, refuses a stale proposal, and is bound to the root the proposal was
dry-run against. The intent is recorded in the audit before the patch and the
outcome after; a failed verification is a distinct state, not a success.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import shlex
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import NoReturn

from nexgen_core.files import atomic_write_bytes, secure_artifact, write_private_text

from .config import LaneConfig
from .llm import LLM
from .proposals import (
    read_proposal_data,
    PROPOSAL_ID_RE as PROPOSAL_ID_RE,
    new_proposal_id as new_proposal_id,
    valid_proposal_id as valid_proposal_id,
    proposal_lock,
    record_attempt,
)
from .tools import ToolError, audit_event

MAX_FILE_CHARS = 20_000
MAX_NEW_CHARS = 20_000
APPLY_TIMEOUT = 300

PROPOSE_PROMPT = (
    "Sei un operatore locale. Devi proporre una modifica minima a un file. "
    'Rispondi SOLO con un oggetto JSON: {"old":"testo esatto da sostituire","new":"testo nuovo","why":"una riga"}. '
    'Regole: "old" deve comparire UNA sola volta nel file, copiato carattere per carattere; '
    '"new" diverso da "old"; nessun altro campo. Non inventare contenuto non presente.'
)


class PatchError(RuntimeError):
    """The proposal or the application was refused."""


@dataclass
class Proposal:
    id: str
    file: str
    repo_root: str
    instruction: str
    original_hash: str
    new_hash: str
    patch: str
    dry_run: bool
    dry_run_output: str = ""
    model_text: str = ""
    created_at: str = ""
    applied_at: str = ""
    verify_output: str = ""
    verify_rc: int | None = None
    verified: bool | None = None
    attempted_at: str = ""


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _hash_bytes(data: bytes) -> str:
    """The identity of a file: a hash of its bytes, never of a decoded view.

    Decoding with `errors="replace"` collapses different invalid sequences
    onto the same text, which would let the state check call someone else's
    edit "still ours" and overwrite it. For a valid UTF-8 file this is the
    same value the text-based proposal hashes produce.
    """
    return hashlib.sha256(data).hexdigest()


def _read_text_bytes(path: Path) -> str:
    """Decode the exact bytes: ``Path.read_text`` normalises CRLF to LF, which
    would hash differently from the proposal computed over the raw bytes."""
    return path.read_bytes().decode("utf-8", errors="replace")


def _run_git(repo_root: Path, args: list[str], timeout: int = 120) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_root), *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout
        )
        return proc.returncode, (proc.stdout + proc.stderr)
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, f"git non eseguibile: {exc}"


def _repo_file(cfg: LaneConfig, file_rel: str) -> tuple[Path, str, Path] | None:
    """Resolve a text file inside the declared repository roots, never outside."""
    cleaned = str(file_rel or "").strip().strip("`'\"")
    if not cleaned:
        return None
    candidates = [Path(cleaned)] if Path(cleaned).is_absolute() else [root / cleaned for root in cfg.repo_roots]
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if any(part in cfg.excluded_parts for part in resolved.parts):
            continue
        for root in cfg.repo_roots:
            try:
                rel = resolved.relative_to(root.resolve())
            except (OSError, ValueError):
                continue
            if resolved.is_file():
                return resolved, rel.as_posix(), root.resolve()
            break
    return None


def _approved_target(cfg: LaneConfig, proposal: Proposal) -> tuple[Path, Path]:
    """Resolve the destination exactly as approved: the proposal's own root.

    The proposal is bound to the canonical root it was dry-run against; a
    configuration that does not declare that root cannot apply it, and the
    relative path is never re-resolved against a different root.
    """
    approved = Path(proposal.repo_root).expanduser().resolve()
    if not any(root.expanduser().resolve() == approved for root in cfg.repo_roots):
        raise PatchError(
            f"proposta vincolata a un altro root ({approved}): "
            "dichiara quel root con --repo per applicarla"
        )
    try:
        candidate = (approved / proposal.file).resolve()
    except OSError as exc:
        raise PatchError(f"destinazione non risolvibile: {exc}") from exc
    if any(part in cfg.excluded_parts for part in candidate.parts):
        raise PatchError("destinazione in una parte esclusa")
    try:
        candidate.relative_to(approved)
    except (OSError, ValueError):
        raise PatchError("destinazione fuori dal root approvato") from None
    if not candidate.is_file():
        raise PatchError("file di destinazione non piu' raggiungibile")
    return candidate, approved


def _dry_run(repo_root: Path, patch_text: str) -> tuple[bool, str]:
    # Byte-exact: in modalita' testo Windows convertirebbe \n in \r\n e
    # gli hunk CRLF diventerebbero \r\r\n (git apply fallisce).
    with tempfile.NamedTemporaryFile("wb", suffix=".patch", delete=False) as handle:
        handle.write(patch_text.encode("utf-8"))
        path = Path(handle.name)
    try:
        code, output = _run_git(repo_root, ["-c", "core.autocrlf=false", "-c", "core.safecrlf=false",
                                            "apply", "--check", "--whitespace=nowarn", str(path)])
    finally:
        path.unlink(missing_ok=True)
    return code == 0, output.strip()


def _save(cfg: LaneConfig, proposal: Proposal) -> None:
    target = cfg.proposals_dir / f"{proposal.id}.json"
    write_private_text(target, json.dumps(asdict(proposal), ensure_ascii=False, indent=1))


def _create(cfg: LaneConfig, proposal: Proposal) -> None:
    """Store a new proposal without ever overwriting an existing one.

    Updates after apply reuse ``_save`` (overwrite is correct there: same
    id, same artifact). Creation refuses on collision so a confirmation can
    never apply a different proposal than the one shown; callers regenerate
    the id and retry.
    """
    target = cfg.proposals_dir / f"{proposal.id}.json"
    try:
        write_private_text(target, json.dumps(asdict(proposal), ensure_ascii=False, indent=1), exclusive=True)
    except FileExistsError as exc:
        raise PatchError(f"collisione id proposta, riprova: {proposal.id}") from exc


# Legacy imports remain aliases. New consumers use the shared owner.
_PROPOSAL_ID_RE = PROPOSAL_ID_RE


def load_proposal(cfg: LaneConfig, proposal_id: str) -> Proposal:
    if not _PROPOSAL_ID_RE.fullmatch(str(proposal_id or "")):
        raise PatchError(f"id proposta non valido: {proposal_id}")
    target = cfg.proposals_dir / f"{proposal_id}.json"
    if not target.is_file():
        raise PatchError(f"proposta inesistente: {proposal_id}")
    return Proposal(**read_proposal_data(target, proposal_id, PatchError))


def list_proposals(cfg: LaneConfig) -> list[Proposal]:
    if not cfg.proposals_dir.is_dir():
        return []
    proposals = []
    for path in sorted(cfg.proposals_dir.glob("*.json"), reverse=True):
        try:
            proposals.append(Proposal(**read_proposal_data(path, path.stem, PatchError)))
        except (OSError, TypeError, ValueError, PatchError):
            continue
    return proposals


def _build_proposal(
    cfg: LaneConfig,
    rel: str,
    root: Path,
    instruction: str,
    data: bytes,
    text: str,
    new_text: str,
    patch: str,
    dry_ok: bool,
    dry_output: str,
    why: str,
) -> Proposal:
    """Costruisce, memorizza e registra la proposta senza cambiarne il contenuto."""
    proposal = Proposal(
        id="",
        file=rel,
        repo_root=str(root),
        instruction=instruction,
        original_hash=_hash_bytes(data),
        new_hash=_sha(new_text),
        patch=patch,
        dry_run=dry_ok,
        dry_run_output=dry_output,
        model_text=why,
        created_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    )
    for _ in range(5):
        proposal.id = new_proposal_id()
        try:
            _create(cfg, proposal)
            break
        except PatchError as exc:
            if "collisione" not in str(exc):
                raise
    else:
        raise PatchError("collisione id proposta, riprova")
    audit_event(
        cfg,
        "propose_patch",
        {"file": rel, "original_hash": proposal.original_hash, "dry_run": dry_ok},
        ok=dry_ok,
        chars=len(patch),
    )
    return proposal


def propose_patch(llm: LLM, cfg: LaneConfig, file_rel: str, instruction: str) -> Proposal:
    """Ask the model for a snippet replacement; validate, diff and dry-run it."""
    target = _repo_file(cfg, file_rel)
    if target is None:
        raise PatchError("file fuori dai root del repository o inesistente")
    path, rel, root = target
    if path.suffix.casefold() == ".pdf":
        raise PatchError("i PDF non si modificano")
    data = path.read_bytes()
    if b"\x00" in data[:4096]:
        raise PatchError("file binario, non modificabile")
    text = data.decode("utf-8", errors="replace")
    if len(text) > MAX_FILE_CHARS:
        raise PatchError(f"file troppo grande per una proposta ({len(text)} caratteri)")

    raw = llm.json(PROPOSE_PROMPT, f"Istruzione: {instruction}\n\nFile {rel}:\n---\n{text}\n---")
    if not isinstance(raw, dict):
        raise PatchError("il modello non ha prodotto un JSON valido")
    old = str(raw.get("old") or "")
    new = str(raw.get("new") or "")
    why = str(raw.get("why") or "")
    if not old or old == new:
        raise PatchError("snippet 'old' vuoto o identico a 'new'")
    if text.count(old) != 1:
        raise PatchError("lo snippet 'old' non compare esattamente una volta nel file")
    if len(new) > MAX_NEW_CHARS:
        raise PatchError("sostituzione troppo grande")

    new_text = text.replace(old, new, 1)
    patch = "".join(
        difflib.unified_diff(
            text.splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            fromfile=f"a/{rel}",
            tofile=f"b/{rel}",
        )
    )
    if not patch.strip():
        raise PatchError("diff vuoto")
    dry_ok, dry_output = _dry_run(root, patch)
    return _build_proposal(cfg, rel, root, instruction, data, text, new_text, patch, dry_ok, dry_output, why)


def apply_proposal(
    cfg: LaneConfig, proposal_id: str, *, yes: bool, verify: str | None = None
) -> dict:
    """Apply exactly the artifact that was shown, after re-verifying the file.

    Order of operations: every refusal first, then the write-ahead receipt,
    then the patch, then the outcome receipt. An audit that cannot record the
    intent refuses the application before the file is touched. Verification is
    a distinct state: ``verified`` is None when not requested, False when the
    command failed or could not run, and the caller must propagate that.
    """
    if not yes:
        raise PatchError("applicazione rifiutata: serve --yes esplicito")
    with proposal_lock(cfg.proposals_dir, proposal_id, PatchError, serialize_directory=True):
        return _apply_proposal(cfg, proposal_id, verify=verify)


def _check_approval(cfg: LaneConfig, proposal: Proposal) -> tuple[Path, Path]:
    """Ricontrolla approvazione, root vincolato e freschezza hash senza scrivere."""
    if proposal.applied_at:
        raise PatchError("proposta gia' applicata")
    if not proposal.dry_run:
        raise PatchError("il dry-run della proposta era fallito: non si applica")
    path, root = _approved_target(cfg, proposal)
    if _hash_bytes(path.read_bytes()) != proposal.original_hash:
        raise PatchError("proposta stantia: il file e' cambiato dopo la proposta")
    return path, root


def _file_state(path: Path, expected: str) -> str:
    """`ours` while the file holds what `git apply` produced, else `changed`,
    or `unreadable` when it cannot be read at all: a file a scanner is
    holding open for a moment is not a change, and the log should not say it
    was one. Any failure counts as unreadable."""
    try:
        return "ours" if _hash_bytes(path.read_bytes()) == expected else "changed"
    except (OSError, ValueError, PatchError):  # nexgen: allow reason="unreadable is left alone"
        return "unreadable"


def _spare_path(cfg: LaneConfig, proposal: Proposal) -> Path:
    """Where the original bytes go whenever they cannot go back.

    The lane's own directory, not the repository: a `.pre-patch` beside the
    file would be an untracked file a commit could pick up, and each proposal
    gets its own copy instead of one shared name. The id comes from a JSON
    record, so a tampered one must not escape that directory.
    """
    if not proposal.id or ".." in proposal.id or any(sep in proposal.id for sep in "\\/:"):
        raise PatchError(f"identificativo di proposta non valido: {proposal.id!r}")
    return cfg.proposals_dir / f"{proposal.id}.pre-patch"


def _keep_original(before: bytes, spare: Path) -> str:
    """Saves the pre-patch bytes for a human and says how it went.

    `written` when this call saved them, `exists` when a copy from an earlier
    failure was already there, `failed` when even that write could not land
    (a full disk, a locked directory): a refusal must never point at a file
    that is not there. Any failure counts as failed.
    """
    try:
        if spare.exists():
            return "exists"
        secure_artifact(spare.parent)
        atomic_write_bytes(spare, before, preserve_mode=False)
    except (OSError, ValueError, PatchError):  # nexgen: allow reason="the outcome carries the failure"
        return "failed"
    return "written"


def _restore_outcome(
    path: Path, before: bytes, expected: str | None, spare: Path,
) -> tuple[str, str]:
    """Puts the pre-patch bytes back when they are ours to put back.

    Returns (restore, spare). `restore` is `restored`, `changed` (someone
    else edited the file meanwhile), `unreadable` (it could not be read to
    check) or `failed` (even the atomic write could not land); `spare` is
    what `_keep_original` reports whenever the bytes did not go back. The
    original bytes are kept in both non-restoring cases: the file may carry
    edits nobody committed, and only a copy can bring them back. `expected`
    is the content hash read right after `git apply`, or None when that read
    failed: with the applied output unknown, the known-good bytes go back on
    purpose. Two applies on the same file are not serialised: this can only
    check that the file still holds what the patch produced, not that the
    patch worked on the bytes read before it, so a parallel apply that lands
    during `git apply` can still be undone (Should Fix 24, plan 2.16).
    """
    if expected is not None:
        state = _file_state(path, expected)
        if state != "ours":
            return state, _keep_original(before, spare)
    try:
        atomic_write_bytes(path, before)
    except OSError:
        return "failed", _keep_original(before, spare)
    return "restored", "not-needed"


def _refuse_apply(cfg: LaneConfig, proposal: Proposal, rc: str, reason: str) -> NoReturn:
    """Records the refusal, then raises it.

    A refusal before the pen moves used to leave only the intent line in the
    log: seen later, it is indistinguishable from a crash in the middle of
    the application.
    """
    try:
        audit_event(cfg, "apply_patch",
                    {"file": proposal.file, "proposal": proposal.id, "phase": "result", "rc": rc},
                    ok=False, chars=len(proposal.patch))
    except (OSError, ValueError, PatchError):  # nexgen: allow reason="the refusal below is what the person needs"
        pass
    raise PatchError(reason)


def _approved_bytes_or_refuse(cfg: LaneConfig, path: Path, proposal: Proposal) -> bytes:
    """The file's bytes, refusing when they are not the approved ones.

    A second apply on the same file can slip in between the approval check
    and this read: with bytes nobody approved, a rollback would erase that
    other change. The read itself is guarded too: a file that cannot be read
    is refused before anything is applied.
    """
    try:
        before = path.read_bytes()
    except OSError as exc:
        _refuse_apply(cfg, proposal, "unreadable",
                      f"il file non e' leggibile ({type(exc).__name__}): non applico")
    if _hash_bytes(before) != proposal.original_hash:
        _refuse_apply(cfg, proposal, "stale",
                      "il file e' cambiato dopo l'approvazione: non applico, riprova la proposta")
    return before


def _applied_hash(path: Path) -> str | None:
    """The hash of what `git apply` produced, or None when it cannot be read.

    The caller treats an unreadable result as a mismatch: it knows the file
    is not what the proposal promised, and the pre-patch bytes can go back.
    Any failure counts: I/O, decoding, or a refusal raised by the reader.
    """
    try:
        return _hash_bytes(path.read_bytes())
    except (OSError, ValueError, PatchError):  # nexgen: allow reason="unreadable means mismatch"
        return None


def _settle_mismatch(
    cfg: LaneConfig, proposal: Proposal, path: Path, before: bytes, applied: str | None,
) -> NoReturn:
    """The applied pen stroke does not match the proposal: put the file back.

    The outcome reaches the log in every case, restored or not: the restore
    runs first and the result line is written whatever it did, because the
    cases where the file stays wrong are the ones where the record matters
    most. The line also carries whether a copy of the original bytes exists,
    so the refusal cannot promise one that is not there.
    """
    outcome, spare = "failed", "failed"
    try:
        outcome, spare = _restore_outcome(path, before, applied, _spare_path(cfg, proposal))
    finally:
        try:
            audit_event(cfg, "apply_patch",
                        {"file": proposal.file, "proposal": proposal.id, "phase": "result",
                         "rc": "hash", "restore": outcome, "spare": spare},
                        ok=False, chars=len(proposal.patch))
        except (OSError, ValueError, PatchError):  # nexgen: allow reason="the refusal below is what the person needs"
            pass
    if outcome == "restored":
        raise PatchError("il file applicato non corrisponde alla proposta; il file e' tornato com'era")
    if spare in ("written", "exists"):
        raise PatchError(
            f"il file applicato non corrisponde alla proposta (ripristino: {outcome}); "
            f"i byte originali sono in {_spare_path(cfg, proposal)}"
        )
    raise PatchError(
        f"il file applicato non corrisponde alla proposta (ripristino: {outcome}) e la copia "
        "dei byte originali non e' riuscita: il file va guardato a mano"
    )


def _run_apply(
    cfg: LaneConfig, proposal: Proposal, path: Path, root: Path, *, verify: str | None
) -> dict:
    """Applica la patch gia' approvata: ricevuta d'intento, penna, esito."""
    rel = proposal.file

    # Write-ahead receipt: the intent is recorded before the pen moves.
    audit_event(cfg, "apply_patch", {"file": rel, "proposal": proposal.id, "phase": "intent"},
                ok=True, chars=len(proposal.patch))

    record_attempt(proposal, lambda: _save(cfg, proposal), PatchError)
    before = _approved_bytes_or_refuse(cfg, path, proposal)  # a failed check puts these back
    with tempfile.NamedTemporaryFile("wb", suffix=".patch", delete=False) as handle:
        handle.write(proposal.patch.encode("utf-8"))
        patch_path = Path(handle.name)
    try:
        code, output = _run_git(root, ["-c", "core.autocrlf=false", "-c", "core.safecrlf=false",
                                       "apply", "--whitespace=nowarn", str(patch_path)])
    finally:
        patch_path.unlink(missing_ok=True)
    if code != 0:
        audit_event(cfg, "apply_patch",
                    {"file": rel, "proposal": proposal.id, "phase": "result", "rc": code},
                    ok=False, chars=len(proposal.patch))
        raise PatchError(f"git apply fallito: {output.strip()}")
    applied = _applied_hash(path)
    if applied != proposal.new_hash:
        _settle_mismatch(cfg, proposal, path, before, applied)

    verify_output = ""
    verify_rc: int | None = None
    if verify:
        try:
            proc = subprocess.run(
                shlex.split(verify), cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=APPLY_TIMEOUT
            )
            verify_rc = proc.returncode
            verify_output = f"rc={proc.returncode}\n{proc.stdout[-2000:]}{proc.stderr[-1000:]}"
        except (OSError, subprocess.SubprocessError) as exc:
            verify_output = f"verifica non eseguita: {exc}"
    verified: bool | None = None if not verify else verify_rc == 0

    proposal.applied_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    proposal.verify_output = verify_output
    proposal.verify_rc = verify_rc
    proposal.verified = verified
    try:
        _save(cfg, proposal)
        audit_event(
            cfg,
            "apply_patch",
            {"file": rel, "proposal": proposal.id, "phase": "result", "verify_rc": verify_rc},
            ok=verified is not False,
            chars=len(proposal.patch),
        )
    except (OSError, ToolError) as exc:
        raise PatchError(f"patch applicata, ma stato o ricevuta finale non salvati: {exc}") from exc
    return {
        "id": proposal.id,
        "file": rel,
        "applied": True,
        "verified": verified,
        "verify_rc": verify_rc,
        "verify": verify_output,
    }


def _apply_proposal(cfg: LaneConfig, proposal_id: str, *, verify: str | None) -> dict:
    proposal = load_proposal(cfg, proposal_id)
    path, root = _check_approval(cfg, proposal)
    return _run_apply(cfg, proposal, path, root, verify=verify)


def format_gate(proposal: Proposal) -> str:
    """The approval screen: machine facts first, model prose clearly labelled."""
    lines = [
        f"Proposta {proposal.id} — fatti macchina",
        f"  file: {proposal.file}",
        f"  hash originale: sha256:{proposal.original_hash[:16]}",
        f"  dry-run: {'OK' if proposal.dry_run else 'FALLITO'}",
        "  diff:",
    ]
    lines.extend(f"    {line.rstrip()}" for line in proposal.patch.splitlines())
    lines.append(f"  testo del modello (non verificato, non e' prova): {proposal.model_text}")
    if proposal.dry_run:
        lines.append(f"  Applica con: nexgen-local apply {proposal.id} --yes")
    return "\n".join(lines)
