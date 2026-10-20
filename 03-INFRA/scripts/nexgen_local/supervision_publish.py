"""Publishing one approved unit: cherry-pick in order, forward only, never forced.

The engine is the only thing that pushes, and it pushes nothing it did not
approve at a commit it can still see. This module replays the unit's commits one
by one onto the owner's branch with `git cherry-pick`, which also means the
signature follows the owner's own `commit.gpgsign` instead of being invented
here; it refuses a version bump, a tag and a rewrite of published history by
name, and its only push is a plain fast-forward. A conflict takes the checkout
back to where it was and nothing is published.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from nexgen_core.discipline.gitutil import git, is_ancestor
from nexgen_core.processes import no_window_kwargs, split_command_line, windows_command_argv

from . import supervision_gates as gates
from . import supervision_state as state
from .supervision_state import SupervisionError

#: Files that only a release may move, refused even when the unit's boundary named them.
RELEASE_FILES = ("VERSION", "CHANGELOG.md")
#: The words that mean "rewrite published history", refused in a declared push command.
FORCE_WORDS = ("--force", "-f", "--force-with-lease", "--force-if-includes", "--mirror", "--delete")
#: What the engine says instead of forcing, word for word, so it can be recognised.
FORCE_REFUSAL = "servirebbe un force-push: il motore non lo fa mai"
#: Budgets: a fetch, a push, and the owner's own publishing command.
FETCH_TIMEOUT_S = 120.0
PUSH_TIMEOUT_S = 600.0
PUBLISH_TIMEOUT_S = 1800.0


class PublishError(SupervisionError):
    """A unit that is not approved, a checkout that cannot take it, or a push that would need force."""


def publish(
    task_id: str, unit: str, *, repo: str | Path, branch: str, remote: str = "origin",
    push: bool = False, push_with: str = "",
) -> dict:
    """Replay an approved unit onto the owner's branch, then push it forward if asked.

    Everything that can refuse does so before the checkout is touched: the
    approval, the moved head, the dirty tree, the wrong branch, the release
    files and the forward-only question all come first. The gates run again on
    the interval before the first cherry-pick, and their FAIL stops everything.
    """
    record = state.unit_record(task_id, unit)
    name = record["unit"]
    approved = _approval(record)
    copy = Path(record.get("worktree") or "")
    if not copy.is_dir():
        raise PublishError(f"copia dell'unità sparita: {copy}")
    unit_head = gates.head_of(copy, str(record.get("branch") or ""))
    if unit_head != approved:
        raise PublishError(
            f"la head dell'unità è cambiata dopo l'approvazione: approvata {approved[:12]}, "
            f"ora {unit_head[:12]}: raccogli e rivedi di nuovo"
        )
    root, start = _owner_checkout(repo, branch, copy)
    _refuse_republished(name, record, unit_head)
    commits = _commits(copy, str(record.get("base") or ""), unit_head)
    if not commits:
        raise PublishError(f"nessun commit nell'intervallo dell'unità {name}: non c'è niente da pubblicare")
    _refuse_release_files(commits)
    _require_forward(root, remote, branch)
    recheck = gates.collect(task_id, name)
    if recheck["verdict"] != "PASS":
        failed = [f"{item.name}: {item.reason}" for item in recheck["gates"] if not item.ok]
        raise PublishError(
            "i cancelli del motore, rieseguiti ora, danno FAIL e nulla è stato pubblicato: "
            + " | ".join(failed)
        )
    applied = _cherry_pick(root, commits, start)
    result = {
        "unit": name, "repo": str(root), "branch": branch, "remote": remote,
        "head": unit_head, "start": start, "commits": applied, "signing": _signing(root, applied),
        "pushed": False, "command": "", "output": "",
    }
    try:
        result["pushed"], result["command"], result["output"] = _deliver(
            root, remote, branch, push, push_with,
        )
    except PublishError as exc:
        # The commits are on the branch even when the push could not start: say so.
        result["command"] = push_with
        result["output"] = str(exc)
        _record(task_id, result)
        raise
    _record(task_id, result)
    return result


def _record(task_id: str, result: dict) -> None:
    """One journal row per publication that reached the owner's checkout."""
    state.append_event(
        task_id, "unit_published", unit=result["unit"], branch=result["branch"],
        repo=result["repo"], remote=result["remote"], head=result["head"],
        start=result["start"], commits=[item["published"] for item in result["commits"]],
        sources=[item["source"] for item in result["commits"]], pushed=result["pushed"],
        command=result["command"], output=_tail(result["output"]), signing=result["signing"],
    )


def _approval(record: dict) -> str:
    """The commit the supervisor approved, or a refusal that says what is missing."""
    if not record.get("approved") or not record.get("approved_head"):
        raise PublishError(
            f"unità {record.get('unit', '')} non approvata: `sup review --approve` prima di pubblicare"
        )
    return str(record["approved_head"])


def _owner_checkout(repo: str | Path, branch: str, copy: Path) -> tuple[Path, str]:
    """The owner's checkout, refused when it is not one, not clean, or not on the branch asked for."""
    candidate = Path(repo).expanduser()
    if not candidate.is_dir():
        raise PublishError(f"checkout inesistente: {candidate}")
    top = git(candidate, "rev-parse", "--show-toplevel", check=False).strip()
    if not top:
        raise PublishError(f"non è un repository git: {candidate}")
    root = Path(top)
    if root.resolve() == copy.resolve():
        raise PublishError("il checkout indicato è la copia dell'unità: la pubblicazione va sul checkout del proprietario")
    current = git(root, "rev-parse", "--abbrev-ref", "HEAD", check=False).strip()
    if current != branch:
        where = "un ramo staccato" if current in ("", "HEAD") else current
        raise PublishError(f"il checkout è su {where}, non su {branch}")
    dirty = git(root, "status", "--porcelain", check=False).rstrip("\r\n")
    if dirty:
        listed = ", ".join(line[3:] for line in dirty.splitlines()[:10])
        raise PublishError(f"checkout sporco su {branch}: {listed} — committa o metti via prima di pubblicare")
    return root, git(root, "rev-parse", "HEAD").strip()


def _refuse_republished(name: str, record: dict, head: str) -> None:
    """The same unit at the same commit is published once: a second run would double the commits."""
    if record.get("published") and record.get("published_head") == head:
        raise PublishError(
            f"unità {name} già pubblicata a {head[:12]}: "
            + ", ".join(item[:12] for item in record["published"])
        )


def _commits(copy: Path, base: str, head: str) -> list[dict]:
    """The unit's commits oldest first, each with its subject and the files it touches."""
    if not base:
        raise PublishError("unità senza base nel diario: impossibile elencare i commit da pubblicare")
    shas = git(copy, "rev-list", "--reverse", f"{base}..{head}").split()
    return [
        {
            "sha": sha,
            "subject": git(copy, "log", "-1", "--format=%s", sha).strip(),
            "files": git(copy, "diff-tree", "--no-commit-id", "--name-only", "-r", sha).splitlines(),
        }
        for sha in shas
    ]


def _refuse_release_files(commits: list[dict]) -> None:
    """A bump is never published, not even inside the unit's own boundary."""
    for commit in commits:
        hit = [name for name in commit["files"] if name in RELEASE_FILES]
        if hit:
            raise PublishError(
                f"bump di versione: il commit {commit['sha'][:12]} tocca {', '.join(hit)}, "
                "anche se il confine lo permetteva. La release la fa solo `nexgen release ship`; "
                "il motore non pubblica un bump e non crea tag"
            )


def _require_forward(root: Path, remote: str, branch: str) -> str:
    """Fetch the remote and refuse, by name, when pushing would rewrite published history."""
    done = _run(["git", "-C", str(root), "fetch", "--quiet", remote], root, FETCH_TIMEOUT_S)
    if done.returncode:
        raise PublishError(f"remoto {remote} non leggibile: {_last_line(done.stderr or done.stdout)}")
    ref = f"refs/remotes/{remote}/{branch}"
    theirs = git(root, "rev-parse", "--verify", "--quiet", ref, check=False).strip()
    if not theirs:
        return ""
    ours = git(root, "rev-parse", "HEAD").strip()
    if not is_ancestor(root, theirs, ours):
        raise PublishError(
            f"{FORCE_REFUSAL}: {remote}/{branch} è a {theirs[:12]}, che non è antenato di "
            f"{ours[:12]}. Il ramo remoto è avanzato fuori dal motore"
        )
    return theirs


def _cherry_pick(root: Path, commits: list[dict], start: str) -> list[dict]:
    """Replay each commit in order; a conflict puts the checkout back and publishes nothing."""
    applied: list[dict] = []
    for commit in commits:
        done = _run(["git", "-C", str(root), "cherry-pick", commit["sha"]], root, PUBLISH_TIMEOUT_S)
        if done.returncode:
            files = git(root, "diff", "--name-only", "--diff-filter=U", check=False).splitlines()
            _restore(root, start)
            left = git(root, "rev-parse", "HEAD", check=False).strip()
            stuck = "" if left == start else f" ATTENZIONE: il checkout è rimasto a {left[:12]}"
            raise PublishError(
                f"cherry-pick di {commit['sha'][:12]} in conflitto su: "
                f"{', '.join(files) or _last_line(done.stderr or done.stdout)}. "
                f"Annullato: il checkout è tornato a {start[:12]} e niente è stato pubblicato.{stuck}"
            )
        applied.append({
            "source": commit["sha"],
            "published": git(root, "rev-parse", "HEAD").strip(),
            "subject": commit["subject"],
        })
    return applied


def _restore(root: Path, start: str) -> None:
    """Undo the sequence we started: the checkout goes back to the commit we found."""
    git(root, "cherry-pick", "--abort", check=False)
    if git(root, "rev-parse", "HEAD", check=False).strip() != start:
        git(root, "reset", "--hard", start, check=False)
    git(root, "cherry-pick", "--quit", check=False)


def _signing(root: Path, applied: list[dict]) -> str:
    """What the owner's own `commit.gpgsign` produced, read from the commits themselves."""
    configured = git(root, "config", "--get", "commit.gpgsign", check=False).strip() or "non impostato"
    marks = [git(root, "log", "-1", "--format=%G?", item["published"], check=False).strip() for item in applied]
    return f"commit.gpgsign = {configured} · firma dei commit pubblicati: {', '.join(marks)}"


def _deliver(root: Path, remote: str, branch: str, push: bool, push_with: str) -> tuple[bool, str, str]:
    """The only push there is: plain and forward, or the owner's own declared command."""
    if push_with:
        _refuse_force(push_with)
        done = _run(push_with, root, PUSH_TIMEOUT_S, shell_command=True)
        return done.returncode == 0, push_with, (done.stdout or "") + (done.stderr or "")
    if not push:
        return False, "", ""
    done = _run(["git", "-C", str(root), "push", remote, branch], root, PUSH_TIMEOUT_S)
    return done.returncode == 0, f"git push {remote} {branch}", (done.stdout or "") + (done.stderr or "")


def _refuse_force(command: str) -> None:
    """The owner may publish with a script of theirs, never with a rewrite of history."""
    words = set(split_command_line(command)) & set(FORCE_WORDS)
    if words:
        raise PublishError(
            f"{FORCE_REFUSAL}: il comando dichiarato contiene {' '.join(sorted(words))}. "
            "Nessuna opzione di forza esiste"
        )


def _run(
    argv: list[str] | str, cwd: Path, timeout: float, *, shell_command: bool = False,
) -> subprocess.CompletedProcess:
    """One process with the engine's own launch rules; a command is split, never handed to a shell."""
    final = windows_command_argv(split_command_line(argv)) if shell_command else list(argv)
    try:
        return subprocess.run(
            final, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
            stdin=subprocess.DEVNULL, timeout=timeout, check=False, **no_window_kwargs(),
        )
    except subprocess.TimeoutExpired:
        raise PublishError(f"comando oltre il tempo massimo ({timeout:.0f}s): {' '.join(final)[:200]}")
    except OSError as exc:
        raise PublishError(f"comando non avviato ({type(exc).__name__}): {' '.join(final)[:200]}") from exc


def _last_line(text: str) -> str:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return (lines[-1] if lines else "")[:300]


def _tail(text: str, limit: int = 4000) -> str:
    return (text or "")[-limit:]
