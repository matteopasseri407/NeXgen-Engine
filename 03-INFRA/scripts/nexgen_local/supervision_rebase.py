"""Put a unit's branch back on the tip of its base, and absorb a deliberate config change.

One job: move the unit's own commits onto wherever its base points now, record
that new base in the journal so `sup collect` and `sup publish` look at the
right interval, and re-fingerprint the copy's git configuration so a change the
owner made on purpose stops failing the fourth gate. It is a local rebase: the
engine fetches nothing, so the base moves when the owner moves it. A conflict
stops the whole thing and leaves the unit exactly as it was, because a copy left
half-rebased is a trap for whoever collects it next.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from nexgen_core.discipline.gitutil import GitError, git

from . import supervision_gitconfig as gitconfig
from . import supervision_state as state
from .supervision_state import SupervisionError


class RebaseError(SupervisionError):
    """No copy to rebase, uncommitted work, a conflicting replay, a base that was rewritten."""


def rebase(task_id: str, unit: str) -> dict:
    """Realign the unit on its base and journal the new base, or say why it would not."""
    record = state.unit_record(task_id, unit)
    name = record["unit"]
    if record.get("published_head"):
        raise RebaseError(
            f"unità {name} già pubblicata: riallinearla riscriverebbe commit che il proprietario ha "
            "già; apri una unità nuova"
        )
    folder = Path(record.get("worktree") or "")
    if not folder.is_dir():
        raise RebaseError(f"copia dell'unità sparita: {folder}")
    branch = str(record.get("branch") or "")
    old_base = str(record.get("base") or "")
    if not branch or not old_base:
        raise RebaseError(f"unità {name} senza ramo o base nel diario: rifai `sup unit`")
    tip = _tip(Path(str(record.get("repo") or "")), str(record.get("base_ref") or ""))
    _refuse_dirty(folder, name)
    moved = _move(folder, name, old_base, tip, branch)
    hashes = gitconfig.fingerprint(folder)
    overrides = gitconfig.url_override_keys(folder)
    unchanged = (
        not moved
        and hashes == (record.get("config_hashes") or {})
        and overrides == (record.get("url_override_keys") or [])
        and tip == old_base
    )
    if unchanged:
        return {
            "unit": name, "changed": False, "branch": branch, "base": tip, "previous_base": old_base,
            "base_ref": str(record.get("base_ref") or ""), "head": _head(folder),
            "config_hashes": hashes, "reason": "base ferma e configurazione identica: niente da riallineare",
        }
    state.append_event(
        task_id, "unit_rebased", unit=name, base=tip, previous_base=old_base,
        base_ref=str(record.get("base_ref") or ""), head=_head(folder),
        config_hashes=hashes, url_override_keys=overrides, moved=moved,
    )
    return {
        "unit": name, "changed": True, "branch": branch, "base": tip, "previous_base": old_base,
        "base_ref": str(record.get("base_ref") or ""), "head": _head(folder),
        "config_hashes": hashes, "reason": "base spostata" if moved else "impronta della configurazione aggiornata",
    }


def _tip(repo: Path, base_ref: str) -> str:
    """Where the base points now, read from the owner's checkout and never fetched."""
    if not base_ref:
        raise RebaseError("la base dell'unità non è un ref: non c'è una punta da seguire")
    sha = git(repo, "rev-parse", "--verify", f"{base_ref}^{{commit}}", check=False).strip()
    if not sha:
        raise RebaseError(f"base {base_ref} non risolvibile in {repo}")
    return sha


def _refuse_dirty(folder: Path, name: str) -> None:
    """A rebase over uncommitted work either loses it or leaves a copy nobody can collect.

    Untracked files stay out of the question: `git rebase` leaves them alone, so
    an artefact in the copy is not a reason to refuse the realignment.
    """
    dirty = git(folder, "status", "--porcelain", "--untracked-files=no", check=False).strip()
    if dirty:
        first = ", ".join(line[3:] if len(line) > 3 else line for line in dirty.splitlines()[:5])
        raise RebaseError(
            f"unità {name}: ci sono modifiche non committate ({first}): committale o scartale, "
            "poi riallinea"
        )


def _move(folder: Path, name: str, old_base: str, tip: str, branch: str) -> bool:
    """Replay the unit's commits onto the new tip; False when the branch already contains it."""
    if _ancestor(folder, tip, branch):
        return False
    if not _ancestor(folder, old_base, tip):
        raise RebaseError(
            f"la base {old_base[:12]} non è più un antenato della sua punta {tip[:12]}: "
            "è stata riscritta, e il riallineamento automatico non è sicuro. Rifai l'unità sulla base nuova"
        )
    try:
        git(folder, "rebase", "--onto", tip, old_base, branch)
    except GitError as exc:
        conflicts = _conflicts(folder)
        git(folder, "rebase", "--abort", check=False)
        detail = ", ".join(conflicts) or str(exc)
        raise RebaseError(
            f"unità {name}: il riallineamento si ferma su un conflitto ({detail}); la copia è tornata "
            "com'era, risolvilo a mano o rifai l'unità"
        ) from exc
    return True


def _ancestor(folder: Path, older: str, newer: str) -> bool:
    """Whether `older` is an ancestor of `newer`, asked to git directly because the answer is the exit code."""
    done = subprocess.run(
        ["git", "-C", str(folder), "merge-base", "--is-ancestor", older, newer],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return done.returncode == 0


def _conflicts(folder: Path) -> list[str]:
    """The files git could not merge, as the rebase left them."""
    listed = git(folder, "diff", "--name-only", "--diff-filter=U", check=False)
    return [line.strip() for line in listed.splitlines() if line.strip()]


def _head(folder: Path) -> str:
    return git(folder, "rev-parse", "HEAD", check=False).strip()
