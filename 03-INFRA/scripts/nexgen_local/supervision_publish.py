# nexgen: one-job reason="publish one approved unit forward on the owner's branch, then retire its copy"
"""Publishing one approved unit: cherry-pick in order, forward only, never forced.

The engine is the only thing that pushes, and it pushes nothing it did not
approve at a commit it can still see. This module replays the unit's commits one
by one onto the owner's branch with `git cherry-pick`, which also means the
signature follows the owner's own `commit.gpgsign` instead of being invented
here; it refuses a version bump and a rewrite of published history by name, and
its only push is checked after it ran: the remote branch must be exactly the
published head, the value read before its ancestor, and the tag list unchanged.
A conflict takes the checkout back to where it was and nothing is published.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from nexgen_core.discipline.gitutil import git, is_ancestor
from nexgen_core.processes import no_window_kwargs, split_command_line, windows_command_argv

from . import supervision_gates as gates
from . import supervision_retire as retire_module
from . import supervision_state as state
from .supervision_state import SupervisionError

#: Files that only a release may move, refused even when the unit's boundary named them.
RELEASE_FILES = ("VERSION", "CHANGELOG.md")
#: The words that mean "rewrite published history", refused in a declared push command.
FORCE_WORDS = ("--force", "-f", "--force-with-lease", "--force-if-includes", "--mirror", "--delete")
#: A push option that deletes every remote branch the local repository does not have.
PRUNE_PREFIX = "--prune"
#: A refspec that starts with one of these forces (`+branch`) or deletes (`:branch`) a remote ref.
REFSPEC_FORCE_PREFIXES = ("+", ":")
#: What the engine says instead of forcing, word for word, so it can be recognised.
FORCE_REFUSAL = "servirebbe un force-push: il motore non lo fa mai"
#: What the engine says when the push landed somewhere other than the head it approved.
PUSH_REWRITE = "il comando di pubblicazione ha forzato la storia"
#: What the engine says when a push created or moved a tag.
PUSH_TAGS = "il comando di pubblicazione ha creato o spinto tag"
#: What the engine says when a push deleted, created or moved another remote branch.
PUSH_BRANCHES = "il comando di pubblicazione ha toccato altri rami del remoto"
#: Budgets: a fetch, a push, and the owner's own publishing command.
FETCH_TIMEOUT_S = 120.0
PUSH_TIMEOUT_S = 600.0
PUBLISH_TIMEOUT_S = 1800.0
#: Windows refuses a path over MAX_PATH unless each git command is told to allow it.
LONG_PATHS = ("-c", "core.longpaths=true")


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
    record, name, copy, unit_head = _unit_to_publish(task_id, unit)
    root, start = _owner_checkout(repo, branch, copy)
    published = [str(item) for item in record.get("published") or []]
    if published and record.get("published_head") == unit_head:
        return _push_local_publication(
            task_id, record, name, root, branch, remote, unit_head, push, push_with, published,
        )
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
    published_head = git(root, "rev-parse", "HEAD").strip()
    result = {
        "unit": name, "repo": str(root), "branch": branch, "remote": remote,
        "head": unit_head, "start": start, "commits": applied, "signing": _signing(root, applied),
        "pushed": False, "command": "", "output": "", "replayed": True,
    }
    try:
        result["pushed"], result["command"], result["output"] = _deliver(
            root, remote, branch, push, push_with, published_head,
        )
    except PublishError as exc:
        # The commits are on the branch even when the push could not start: say so.
        result["command"] = push_with
        result["output"] = str(exc)
        _record(task_id, result)
        _retire_after(task_id, result)
        _raise_with_retirement(exc, result)
    _record(task_id, result)
    _retire_after(task_id, result)
    return result


def _push_local_publication(
    task_id: str, record: dict, name: str, root: Path, branch: str, remote: str,
    unit_head: str, push: bool, push_with: str, published: list[str],
) -> dict:
    """Push a unit already applied locally: the cherry-pick is never replayed, the commits stay checked.

    A publication that failed to push kept its commits on the owner's branch and
    recorded `pushed: false`; the CLI tells the owner to push it later. Replaying
    it would double the commits, so this path checks that the published commits
    are still in the branch's history and pushes exactly the published head: a
    commit the owner added afterwards stays local, because the engine never
    publishes what it did not review.
    """
    if record.get("published_pushed"):
        _refuse_republished(name, record, unit_head)
    if not push and not push_with:
        raise PublishError(
            f"unità {name} è già applicata sul ramo locale a {unit_head[:12]} ma non è stata spinta: "
            "`sup publish ... --push` (o `--push-with \"<comando>\"`) spinge solo, senza rifare il cherry-pick"
        )
    head = git(root, "rev-parse", "HEAD").strip()
    missing = [sha for sha in published if not is_ancestor(root, sha, "HEAD")]
    if missing:
        raise PublishError(
            f"il ramo locale non contiene più i commit pubblicati di {name}: "
            + ", ".join(sha[:12] for sha in missing)
            + ". Il cherry-pick non si rifà: ripristina il ramo o riparti da una nuova approvazione"
        )
    # The commits on the owner's branch are the cherry-picked ones, not the unit's
    # own commits: the published tip is the last of the journal's published list.
    published_tip = published[-1]
    applied = [
        {"source": sha, "published": sha, "subject": git(root, "log", "-1", "--format=%s", sha).strip()}
        for sha in published
    ]
    result = {
        "unit": name, "repo": str(root), "branch": branch, "remote": remote,
        "head": unit_head, "start": head, "commits": applied, "signing": _signing(root, applied),
        "pushed": False, "command": "", "output": "", "replayed": False,
    }
    try:
        result["pushed"], result["command"], result["output"] = _deliver(
            root, remote, branch, push, push_with, published_tip, push_source=published_tip,
        )
    except PublishError as exc:
        result["command"] = push_with
        result["output"] = str(exc)
        _record(task_id, result)
        _retire_after(task_id, result)
        _raise_with_retirement(exc, result)
    _record(task_id, result)
    _retire_after(task_id, result)
    return result


def _unit_to_publish(task_id: str, unit: str) -> tuple[dict, str, Path, str]:
    """The unit's record, name, copy and the head the supervisor approved, refused if they moved."""
    record = state.unit_record(task_id, unit)
    name = record["unit"]
    approved = _approval(record)
    copy = Path(record.get("worktree") or "")
    published = [str(item) for item in record.get("published") or []]
    if published and record.get("published_head") == approved:
        # The copy left with the publication: its head cannot be read any more, and
        # the commits that already arrived are the answer, not the folder that went.
        return record, name, copy, approved
    if not copy.is_dir():
        raise PublishError(f"copia dell'unità sparita: {copy}")
    unit_head = gates.head_of(copy, str(record.get("branch") or ""))
    if unit_head != approved:
        raise PublishError(
            f"la head dell'unità è cambiata dopo l'approvazione: approvata {approved[:12]}, "
            f"ora {unit_head[:12]}: raccogli e rivedi di nuovo"
        )
    return record, name, copy, unit_head


def _record(task_id: str, result: dict) -> None:
    """One journal row per publication that reached the owner's checkout."""
    state.append_event(
        task_id, "unit_published", unit=result["unit"], branch=result["branch"],
        repo=result["repo"], remote=result["remote"], head=result["head"],
        start=result["start"], commits=[item["published"] for item in result["commits"]],
        sources=[item["source"] for item in result["commits"]], pushed=result["pushed"],
        command=result["command"], output=_tail(result["output"]), signing=result["signing"],
    )


def _retire_after(task_id: str, result: dict) -> None:
    """The owner's rule: a published unit leaves no copy and no branch behind.

    The commits are already on the owner's branch and in the journal, so a copy
    that will not go does not undo the publication: it is named, journalled and
    left for `sup pulizia` to try again.
    """
    try:
        result["retirement"] = retire_module.retire(
            task_id, result["unit"], why="pubblicata",
            commits=[str(item["published"]) for item in result["commits"]],
        )
    except SupervisionError as exc:
        result["retirement"] = {"retired": False, "error": str(exc)}
        state.append_event(
            task_id, "unit_retire_failed", unit=result["unit"], why="pubblicata", reason=str(exc),
        )


def _raise_with_retirement(exc: SupervisionError, result: dict) -> None:
    """Keep the push's own refusal, and add the one fact it did not know: the copy stayed."""
    problem = (result.get("retirement") or {}).get("error")
    if not problem:
        raise exc
    raise PublishError(f"{exc} · copia dell'unità non rimossa: {problem}") from exc


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
    """The unit's commits oldest first, each with its subject and the files it touches.

    Renames are not folded: a moved `VERSION` must appear as `VERSION` deleted
    plus something added, or the release-file check would only see the new name.
    """
    if not base:
        raise PublishError("unità senza base nel diario: impossibile elencare i commit da pubblicare")
    shas = git(copy, "rev-list", "--reverse", f"{base}..{head}").split()
    return [
        {
            "sha": sha,
            "subject": git(copy, "log", "-1", "--format=%s", sha).strip(),
            "files": git(copy, "diff-tree", "--no-commit-id", "--name-only", "--no-renames", "-r", sha).splitlines(),
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
        done = _run(
            ["git", *LONG_PATHS, "-C", str(root), "cherry-pick", commit["sha"]],
            root, PUBLISH_TIMEOUT_S,
        )
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
    git(root, *LONG_PATHS, "cherry-pick", "--abort", check=False)
    if git(root, "rev-parse", "HEAD", check=False).strip() != start:
        git(root, *LONG_PATHS, "reset", "--hard", start, check=False)
    git(root, *LONG_PATHS, "cherry-pick", "--quit", check=False)


def _signing(root: Path, applied: list[dict]) -> str:
    """What the owner's own `commit.gpgsign` produced, read from the commits themselves."""
    configured = git(root, "config", "--get", "commit.gpgsign", check=False).strip() or "non impostato"
    marks = [git(root, "log", "-1", "--format=%G?", item["published"], check=False).strip() for item in applied]
    return f"commit.gpgsign = {configured} · firma dei commit pubblicati: {', '.join(marks)}"


def _deliver(
    root: Path, remote: str, branch: str, push: bool, push_with: str, published_head: str,
    *, push_source: str = "",
) -> tuple[bool, str, str]:
    """The only push there is: plain and forward, or the owner's own declared command, checked after.

    `push_source` names the commit to send instead of the branch's own head: a
    local-only publication must send exactly the head it published, never a
    commit the owner added to the branch afterwards.
    """
    if push_with:
        _refuse_force(push_with)
        return _push_and_check(root, remote, branch, published_head, command=push_with)
    if not push:
        return False, "", ""
    target = f"{push_source}:refs/heads/{branch}" if push_source else branch
    return _push_and_check(
        root, remote, branch, published_head,
        argv=["git", "-C", str(root), "push", "--no-follow-tags", remote, target],
        command=f"git push --no-follow-tags {remote} {target}",
    )


def _push_and_check(
    root: Path, remote: str, branch: str, published_head: str, *,
    command: str = "", argv: list[str] | None = None,
) -> tuple[bool, str, str]:
    """Run one push, then prove what it did: the approved head, its ancestor, the other branches and the tags."""
    before = _remote_state(root, remote)
    done = _run(command or (argv or []), root, PUSH_TIMEOUT_S, shell_command=bool(command))
    after = _remote_state(root, remote)
    if done.returncode == 0 or after != before:
        _check_push(root, remote, branch, published_head, before, after)
    declared = command or " ".join(argv or [])
    return done.returncode == 0, declared, (done.stdout or "") + (done.stderr or "")


def _remote_state(root: Path, remote: str) -> dict:
    """Every remote branch and the whole tag list, read straight from the remote, not from a local cache."""
    heads = _run(["git", "-C", str(root), "ls-remote", "--heads", remote], root, FETCH_TIMEOUT_S)
    tags = _run(["git", "-C", str(root), "ls-remote", "--tags", remote], root, FETCH_TIMEOUT_S)
    if heads.returncode or tags.returncode:
        reason = _last_line(heads.stderr or heads.stdout or tags.stderr or tags.stdout)
        raise PublishError(f"remoto {remote} non leggibile durante la spinta: {reason}")
    return {"heads": _refs(heads.stdout), "tags": sorted(line for line in tags.stdout.splitlines() if line.strip())}


def _refs(text: str) -> dict[str, str]:
    """`sha<TAB>ref` lines as a ref-to-sha mapping."""
    return {
        parts[1]: parts[0]
        for line in text.splitlines()
        if len(parts := line.split()) >= 2
    }


def _check_push(
    root: Path, remote: str, branch: str, published_head: str, before: dict, after: dict,
) -> None:
    """What the push must have done; anything else is named instead of believed.

    A push is declared, not trusted: the branch must now point exactly at the head
    the engine published, the value read before must be an ancestor of it (so
    nothing was rewritten), no tag may have appeared or moved, and no other remote
    branch may have appeared, moved or disappeared: `--prune` deletes a branch the
    local repository does not have, and looking only at the published one hid it.
    """
    ref = f"refs/heads/{branch}"
    if after["heads"].get(ref, "") != published_head:
        raise PublishError(
            f"{PUSH_REWRITE}: {remote}/{branch} è a {after['heads'].get(ref, '')[:12] or 'niente'}, "
            f"non alla head pubblicata {published_head[:12]}"
        )
    was = before["heads"].get(ref, "")
    if was and not _is_ancestor_on_remote(root, remote, was, after["heads"][ref]):
        raise PublishError(
            f"{PUSH_REWRITE}: {remote}/{branch} era a {was[:12]} e ora è a "
            f"{after['heads'][ref][:12]}: il valore di prima non è antenato di quello nuovo"
        )
    others = _other_branches(before["heads"], after["heads"], ref)
    if others:
        raise PublishError(f"{PUSH_BRANCHES}: " + ", ".join(others[:5]) + ". Solo il ramo pubblicato può muoversi")
    if before["tags"] != after["tags"]:
        changed = sorted(set(after["tags"]) ^ set(before["tags"]))
        raise PublishError(
            f"{PUSH_TAGS}: " + ", ".join(changed[:5]) + ". Il motore non crea e non spinge tag"
        )


def _other_branches(before: dict[str, str], after: dict[str, str], published: str) -> list[str]:
    """Every remote branch that appeared, moved or disappeared besides the one published, with what happened."""
    found = [
        f"cancellato {name.removeprefix('refs/heads/')}"
        for name in sorted(set(before) - set(after))
    ]
    found += [
        f"spostato {name.removeprefix('refs/heads/')}"
        for name in sorted(set(before) & set(after))
        if name != published and before[name] != after[name]
    ]
    found += [
        f"creato {name.removeprefix('refs/heads/')}"
        for name in sorted(set(after) - set(before))
        if name != published
    ]
    return found


def _is_ancestor_on_remote(root: Path, remote: str, old: str, new: str) -> bool:
    """Whether `old` is in `new`'s history, fetching it once if the object is not local."""
    def present(sha: str) -> bool:
        return bool(git(root, "rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}", check=False).strip())

    if not present(old):
        _run(["git", "-C", str(root), "fetch", "--quiet", remote], root, FETCH_TIMEOUT_S)
    return present(old) and is_ancestor(root, old, new)


def _refuse_force(command: str) -> None:
    """The owner may publish with a script of theirs, never with a rewrite of history, a tag or a deletion."""
    refused = sorted({
        token for token in split_command_line(command)
        if token in FORCE_WORDS
        or token.startswith("--force")
        or token.startswith(PRUNE_PREFIX)
        or token.startswith(REFSPEC_FORCE_PREFIXES)
        or _short_flag_with_force(token)
    })
    if refused:
        raise PublishError(
            f"{FORCE_REFUSAL}: il comando dichiarato contiene {' '.join(refused)}. "
            "Nessuna opzione di forza, di potatura o di cancellazione esiste"
        )


def _short_flag_with_force(token: str) -> bool:
    """A short flag, or a bundle of short flags, that carries `f`: `-f`, `-vf`, `-fd`."""
    return (len(token) > 1 and token.startswith("-") and not token.startswith("--")
            and "f" in token[1:].casefold())


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
