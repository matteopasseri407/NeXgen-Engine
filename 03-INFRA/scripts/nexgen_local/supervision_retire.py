"""Retiring a finished unit: free its servers, remove its copy, delete its branch.

One job: put the machine back the way it was once a unit's work has arrived
somewhere permanent. The commits are verified before anything is deleted: a
published unit is checked against the commits its publication wrote, and an
approved unit that is never published gets an archive ref first, so its branch
can go without losing the history. The servers a harness opened for the copy are
evicted through that harness's own adapter, because deleting the folder under a
live `opencode serve` is what made `git worktree remove` fail with "Device or
resource busy". The task folder stays: it is the record, not the workspace.
"""
from __future__ import annotations

from pathlib import Path

from nexgen_core.discipline.gitutil import LONG_PATHS, GitError, git, is_ancestor

from . import supervision_adapters as adapters
from . import supervision_state as state
from .supervision_state import SupervisionError

#: Where an unpublished unit's history is kept after its branch goes.
ARCHIVE_PREFIX = "refs/nexgen/archive"


class RetireError(SupervisionError):
    """The commits did not arrive, or the copy could not be freed, so nothing was deleted."""


def retire(
    task_id: str, unit: str, *, why: str, commits: list[str] | None = None,
    archive: bool = False,
) -> dict:
    """Free the unit's servers, remove its copy and branch, and journal what was kept.

    `commits` is what the caller already saw arrive on the owner's branch: a
    publication passes the commits it wrote, and cleanup reads the journal's own
    publication row. `archive` writes a ref that keeps the head before the branch
    is deleted, and is the only honest way to retire an approved unit that is not
    published: the work stays reachable, the workspace does not.
    """
    record = state.unit_record(task_id, unit)
    name = record["unit"]
    copy = Path(record.get("worktree") or "")
    repo = Path(record.get("repo") or "")
    branch = str(record.get("branch") or "")
    if record.get("retired") and not copy.exists() and not _branch_exists(repo, branch):
        return {"unit": name, "retired": False, "reason": "unità già ritirata, niente è rimasto sul disco"}
    kept = _keep_history(task_id, name, record, repo, commits, archive)
    notes = _close_tab(task_id, name)
    notes += _release_local(task_id, name, record)
    notes += _dispose(record, copy)
    _remove_copy(repo, copy, name)
    _delete_branch(repo, branch, notes)
    state.append_event(
        task_id, "unit_retired", unit=name, why=why, copy=str(copy), branch=branch,
        archive=kept["ref"], kept_head=kept["head"], notes=notes,
    )
    return {
        "unit": name, "retired": True, "copy": str(copy), "branch": branch,
        "archive": kept["ref"], "head": kept["head"], "notes": notes,
    }


def _keep_history(
    task_id: str, name: str, record: dict, repo: Path, commits: list[str] | None, archive: bool,
) -> dict:
    """Make sure the work is somewhere before the branch goes, or refuse to delete anything."""
    if archive:
        head = _unit_head(repo, str(record.get("branch") or ""))
        ref = f"{ARCHIVE_PREFIX}/{state.slug(task_id)}/{name}"
        _update_ref(repo, ref, head, name)
        return {"ref": ref, "head": head}
    published = [str(item) for item in (commits or record.get("published") or [])]
    if not published:
        kept = str(record.get("kept_head") or "")
        if record.get("retired") and kept and _has_commit(repo, kept):
            return {"ref": str(record.get("archive") or ""), "head": kept}
        raise RetireError(
            f"unità {name} non pubblicata e nessun archivio chiesto: i suoi commit sarebbero persi. "
            "Pubblica l'unità, oppure chiudila con `sup review ... --approve \"<motivo>\" --close`"
        )
    publication = _publication(task_id, name)
    place = Path(str(publication.get("repo") or ""))
    branch = str(publication.get("branch") or "")
    if not place.is_dir() or not branch:
        raise RetireError(
            f"il diario non dice su quale ramo di quale repository {name} è stata pubblicata: "
            "niente è stato cancellato"
        )
    missing = [sha for sha in published if not is_ancestor(place, sha, branch)]
    if missing:
        raise RetireError(
            f"i commit pubblicati di {name} non sono sul ramo {branch} di {place}: "
            + ", ".join(sha[:12] for sha in missing)
            + ". Niente è stato cancellato"
        )
    return {"ref": "", "head": published[-1]}


def _publication(task_id: str, unit: str) -> dict:
    """The last `unit_published` row: the only record of which branch actually received the work."""
    name = state.slug(unit)
    found: dict = {}
    for row in state.read_events(task_id):
        if row.get("event") == "unit_published" and row.get("unit") == name:
            found = row
    return found


def _unit_head(repo: Path, branch: str) -> str:
    """The commit the unit's branch points at, refused when the repo cannot say."""
    if not repo.is_dir():
        raise RetireError(f"repository dell'unità sparito: {repo}")
    sha = git(repo, "rev-parse", "--verify", f"refs/heads/{branch}", check=False).strip()
    if not sha:
        raise RetireError(f"ramo dell'unità sparito: {branch}: niente da archiviare")
    return sha


def _update_ref(repo: Path, ref: str, head: str, name: str) -> None:
    """Write the archive ref; a repository that refuses it keeps the branch instead."""
    try:
        git(repo, "update-ref", ref, head)
    except GitError as exc:
        raise RetireError(f"archivio di {name} non scritto in {ref}: {exc}. Niente è stato cancellato") from exc


def _has_commit(repo: Path, sha: str) -> bool:
    """Whether this repository still has the commit, so deleting a branch keeps it reachable."""
    if not repo.is_dir():
        return False
    return bool(git(repo, "rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}", check=False).strip())


def _close_tab(task_id: str, unit: str) -> list[str]:
    """Close the tab that ran this unit, before its copy goes.

    A published or retired unit must not leave a window of its own behind: the
    supervisor found the tab of a unit published in the morning still open hours
    later. Only a tab the journal says was opened and never closed is looked for,
    so a unit that never had one costs no process-table read.
    """
    from . import supervision_windows as windows  # noqa: PLC0415  the window layer reads this module's state

    if not windows.unit_tab_open(task_id, unit):
        return []
    return [windows.close_unit_tab(task_id, unit)["line"]]


def _release_local(task_id: str, unit: str, record: dict) -> list[str]:
    """Stop the lane's local server when this unit was the one it served."""
    from . import supervision_local_model as local  # noqa: PLC0415  the lifecycle module imports this one's state

    return local.release_for_unit(task_id, unit, record)


def _dispose(record: dict, copy: Path) -> list[str]:
    """Ask the unit's harness to drop what it holds for the copy; a refusal is a note, not a stop."""
    harness = str(record.get("harness") or "opencode")
    try:
        adapter = adapters.get(harness)
    except adapters.base.AdapterError as exc:
        return [str(exc)]
    if not adapter.capabilities.has("dispose_location"):
        return [f"{harness} non sa liberare una cartella: nessun server da smaltire"]
    try:
        ok, reason = adapter.dispose_location(copy)
    except adapters.base.AdapterError as exc:
        return [f"istanze di {harness} non liberate ({exc})"]
    return [reason] if ok else [f"istanze di {harness} non liberate: {reason}"]


def _remove_copy(repo: Path, copy: Path, name: str) -> None:
    """`git worktree remove`, with Windows long paths; a copy that will not go stops the retirement."""
    if not copy.exists():
        return
    if not repo.is_dir():
        raise RetireError(f"copia di {name} rimasta ma il repository {repo} non c'è: non la tocco")
    done = git(repo, *LONG_PATHS, "worktree", "remove", "--force", str(copy), check=False)
    if copy.exists():
        raise RetireError(
            f"copia di {name} non rimossa ({copy}); un processo la tiene aperta. "
            f"{_last_line(done)} Il ramo {name} non è stato cancellato"
        )
    git(repo, *LONG_PATHS, "worktree", "prune", check=False)


def _delete_branch(repo: Path, branch: str, notes: list[str]) -> None:
    """Delete the unit's local branch; a branch another worktree holds is named, not forced."""
    if not branch or not repo.is_dir():
        return
    done = git(repo, *LONG_PATHS, "branch", "-D", branch, check=False)
    if git(repo, "rev-parse", "--verify", f"refs/heads/{branch}", check=False).strip():
        notes.append(f"ramo {branch} non cancellato: {_last_line(done)}")


def _last_line(done: str) -> str:
    """The last non-empty line of git's output, where the reason lives."""
    lines = [line.strip() for line in (done or "").splitlines() if line.strip()]
    return lines[-1][:300] if lines else ""


def leftover(task_id: str, record: dict) -> dict:
    """What is still on disk for a finished unit: its copy and its branch, as a listing row.

    `sup pulizia` asks this of units the journal already closed. A row with both
    flags false is not a leftover and the caller drops it; asking git is the only
    honest way to know, because the copy can be gone while the branch stays.
    """
    repo = Path(record.get("repo") or "")
    copy = Path(record.get("worktree") or "")
    branch = str(record.get("branch") or "")
    return {
        "task": task_id, "unit": record["unit"], "copy": str(copy) if copy.exists() else "",
        "branch": branch if _branch_exists(repo, branch) else "",
        "repo": str(repo), "state": str(record.get("state") or ""), "retired": bool(record.get("retired")),
    }


def _branch_exists(repo: Path, branch: str) -> bool:
    if not branch or not repo.is_dir():
        return False
    return bool(git(repo, "rev-parse", "--verify", f"refs/heads/{branch}", check=False).strip())


def verified_commits(task_id: str, unit: str) -> list[str]:
    """The commits a publication wrote, from the journal's last `unit_published`, or none."""
    name = state.slug(unit)
    found: list[str] = []
    for row in state.read_events(task_id):
        if row.get("event") == "unit_published" and row.get("unit") == name:
            found = [str(item) for item in row.get("commits") or []]
    return found


def owner_repo(task_id: str, unit: str) -> Path:
    """The owner's checkout the publication wrote to, or an empty path when it never published."""
    found = str(_publication(task_id, unit).get("repo") or "")
    return Path(found) if found else Path("")
