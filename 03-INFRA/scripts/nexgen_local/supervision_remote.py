"""The unit's remotes: the creation's own, and whether any of them already carries its commits.

A push is the one thing the gates must never miss, and it hides in two ways: a
remote that disappeared, was added or was re-pointed, and a remote branch that
already holds a commit of the unit. This module asks both questions of the URLs
written at creation, which a unit cannot edit without being seen, and answers
with the PASS/FAIL pair the `remoto intatto` gate prints.
"""
from __future__ import annotations

from pathlib import Path

from nexgen_core.discipline.gitutil import git

from .supervision_worktree import BLOCKED_PUSH_URL, push_urls, refs_at, remote_urls


def check(folder: Path, record: dict, base: str, head: str) -> tuple[bool, str]:
    """The gate's verdict: the creation's remotes unchanged, and no remote ref carrying a commit of the interval.

    A remote branch that advanced for another reason (another unit was
    published) is not a failure: the question is whether *this unit's* commits
    reached a remote, and it is asked of the URLs written at creation.
    """
    problem, saved = _unchanged(record, folder)
    if problem:
        return problem
    if not saved:
        return True, "nessun remoto configurato alla creazione"
    mine = set(git(folder, "rev-list", f"{base}..{head}").split())
    pushed: list[str] = []
    unreadable: list[str] = []
    for name in sorted(saved):
        refs = refs_at(saved[name])
        if "error" in refs:
            unreadable.append(f"{name}: {refs['error']}")
            continue
        pushed += [
            f"{name}/{ref.removeprefix('refs/heads/')} → {sha[:12]}"
            for ref, sha in refs.items() if sha in mine
        ]
    if unreadable:
        return False, "non verificabile, remoto non leggibile: " + "; ".join(unreadable)
    if pushed:
        return False, "l'unità ha spinto: " + ", ".join(sorted(pushed))
    return True, f"{len(saved)} remoti, nessun ref remoto porta un commit dell'unità"


def _unchanged(record: dict, folder: Path) -> tuple[tuple[bool, str] | None, dict]:
    """The remote configuration against the creation's, and the fetch URLs to ask.

    A remote gone, added, re-pointed for fetch or for push is a failure. The
    returned dict is the creation's fetch URLs, or today's when the journal
    predates the field: the refs are then asked of the only URLs there are.
    """
    saved = {str(name): str(url) for name, url in (record.get("remote_urls") or {}).items()}
    saved_push = {str(name): str(url) for name, url in (record.get("remote_pushurls") or {}).items()}
    now = remote_urls(folder)
    now_push = push_urls(folder)
    if not saved:
        return None, now
    missing = sorted(name for name in saved if not now.get(name))
    if missing:
        return (False, "remoti spariti dalla copia: " + ", ".join(missing)), saved
    added = sorted(name for name in now if name not in saved)
    if added:
        return (False, "remoti aggiunti alla copia: " + ", ".join(added)), saved
    changed = sorted(name for name in saved if now.get(name) and now[name] != saved[name])
    if changed:
        detail = ", ".join(f"{name} ({saved[name]} → {now[name]})" for name in changed)
        return (False, "URL di fetch cambiato: " + detail), saved
    moved = sorted(name for name in saved if now_push.get(name, "") != saved_push.get(name, BLOCKED_PUSH_URL))
    if moved:
        detail = ", ".join(
            f"{name} ({saved_push.get(name, BLOCKED_PUSH_URL)} → {now_push.get(name, '')})" for name in moved
        )
        return (False, "pushurl cambiata: " + detail), saved
    return None, saved
