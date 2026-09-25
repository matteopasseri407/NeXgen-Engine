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
import re
import shlex
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .config import LaneConfig
from .llm import LLM
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


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _run_git(repo_root: Path, args: list[str], timeout: int = 120) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_root), *args], capture_output=True, text=True, timeout=timeout
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
                return resolved, str(rel), root.resolve()
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
    with tempfile.NamedTemporaryFile("w", suffix=".patch", delete=False, encoding="utf-8") as handle:
        handle.write(patch_text)
        path = Path(handle.name)
    try:
        code, output = _run_git(repo_root, ["apply", "--check", "--whitespace=nowarn", str(path)])
    finally:
        path.unlink(missing_ok=True)
    return code == 0, output.strip()


def _save(cfg: LaneConfig, proposal: Proposal) -> None:
    cfg.proposals_dir.mkdir(parents=True, exist_ok=True)
    target = cfg.proposals_dir / f"{proposal.id}.json"
    target.write_text(json.dumps(asdict(proposal), ensure_ascii=False, indent=1), encoding="utf-8")


#: Proposal ids are generated by this module; anything else is refused before
#: it can be joined into a path (no ../, no absolute ids).
_PROPOSAL_ID_RE = re.compile(r"\d{8}-\d{6}-[0-9a-f]{8}")


def load_proposal(cfg: LaneConfig, proposal_id: str) -> Proposal:
    if not _PROPOSAL_ID_RE.fullmatch(str(proposal_id or "")):
        raise PatchError(f"id proposta non valido: {proposal_id}")
    target = cfg.proposals_dir / f"{proposal_id}.json"
    if not target.is_file():
        raise PatchError(f"proposta inesistente: {proposal_id}")
    return Proposal(**json.loads(target.read_text(encoding="utf-8")))


def list_proposals(cfg: LaneConfig) -> list[Proposal]:
    if not cfg.proposals_dir.is_dir():
        return []
    proposals = []
    for path in sorted(cfg.proposals_dir.glob("*.json"), reverse=True):
        try:
            proposals.append(Proposal(**json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, TypeError, ValueError):
            continue
    return proposals


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
    proposal = Proposal(
        id=f"{time.strftime('%Y%m%d-%H%M%S')}-{_sha(patch)[:8]}",
        file=rel,
        repo_root=str(root),
        instruction=instruction,
        original_hash=_sha(text),
        new_hash=_sha(new_text),
        patch=patch,
        dry_run=dry_ok,
        dry_run_output=dry_output,
        model_text=why,
        created_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    )
    _save(cfg, proposal)
    audit_event(
        cfg,
        "propose_patch",
        {"file": rel, "original_hash": proposal.original_hash, "dry_run": dry_ok},
        ok=dry_ok,
        chars=len(patch),
    )
    return proposal


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
    proposal = load_proposal(cfg, proposal_id)
    if proposal.applied_at:
        raise PatchError("proposta gia' applicata")
    if not proposal.dry_run:
        raise PatchError("il dry-run della proposta era fallito: non si applica")
    path, root = _approved_target(cfg, proposal)
    rel = proposal.file
    if _sha(path.read_text(errors="replace")) != proposal.original_hash:
        raise PatchError("proposta stantia: il file e' cambiato dopo la proposta")

    # Write-ahead receipt: the intent is recorded before the pen moves.
    audit_event(
        cfg,
        "apply_patch",
        {"file": rel, "proposal": proposal.id, "phase": "intent"},
        ok=True,
        chars=len(proposal.patch),
    )

    with tempfile.NamedTemporaryFile("w", suffix=".patch", delete=False, encoding="utf-8") as handle:
        handle.write(proposal.patch)
        patch_path = Path(handle.name)
    try:
        code, output = _run_git(root, ["apply", "--whitespace=nowarn", str(patch_path)])
    finally:
        patch_path.unlink(missing_ok=True)
    if code != 0:
        audit_event(
            cfg,
            "apply_patch",
            {"file": rel, "proposal": proposal.id, "phase": "result", "rc": code},
            ok=False,
            chars=len(proposal.patch),
        )
        raise PatchError(f"git apply fallito: {output.strip()}")
    if _sha(path.read_text(errors="replace")) != proposal.new_hash:
        audit_event(
            cfg,
            "apply_patch",
            {"file": rel, "proposal": proposal.id, "phase": "result", "rc": "hash"},
            ok=False,
            chars=len(proposal.patch),
        )
        raise PatchError("il file applicato non corrisponde alla proposta")

    verify_output = ""
    verify_rc: int | None = None
    if verify:
        try:
            proc = subprocess.run(
                shlex.split(verify), cwd=root, capture_output=True, text=True, timeout=APPLY_TIMEOUT
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
