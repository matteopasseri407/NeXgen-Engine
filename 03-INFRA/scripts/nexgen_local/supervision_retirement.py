"""What a unit may authorise for itself, and what its commits retired.

The owner authorises a test removal by naming the file in `test_removal_ok` in
`.nexgen-discipline.toml` on the base: `nexgen_core/discipline/integrity.py`
says so in `HOW_TO_ALLOW`, and every gate has always read the base's copy. The
one exception is a unit the supervisor let carry that file itself, by writing
its path inside the unit's boundary at creation: the record's `allow` is in the
unit's journal, so the unit cannot hand itself the permission. When the
boundary holds the file, the two test gates judge the interval the way the
project's own hook judges it, commit by commit against the configuration each
commit inherited from its parent, so a commit that authorises a removal and
makes it at once is refused. Every other unit is judged against the base,
unchanged. This module only supplies the walk and the facts; the verdicts stay
in the gates that own them.
"""
from __future__ import annotations

from fnmatch import fnmatch
from pathlib import Path

from nexgen_core.discipline import config
from nexgen_core.discipline.config import ConfigError
from nexgen_core.discipline.gitutil import Change, changes as changed_files, git
from nexgen_core.discipline.integrity import check_test_integrity, test_stats
from nexgen_core.discipline.violations import TEST_PATH

#: The file whose rules a unit may carry only when the supervisor allowed it.
CONFIG_PATH = config.CONFIG_NAME


def may_carry(scope: list[str] | tuple[str, ...]) -> bool:
    """Whether the unit's boundary holds the discipline configuration.

    The scope is what the supervisor wrote at creation, so a unit that names
    that path there is the only one allowed to bring its own `test_removal_ok`.
    Every other unit is judged against the base's copy, as it always was.
    """
    return any(fnmatch(CONFIG_PATH, pattern) for pattern in scope)


def commits(folder: Path, base: str, head: str) -> list[str]:
    """The unit's own commits, oldest first: what no remote has, the list the per-commit proof judges.

    Published history was judged when it was pushed, and a unit that merged an
    advanced base would otherwise be judged for someone else's commits.
    """
    return git(folder, "rev-list", "--reverse", "--no-merges", f"{base}..{head}", "--not", "--remotes").split()


def parent_of(folder: Path, commit: str) -> str:
    """The commit's first parent, or an empty string for a root commit."""
    return git(folder, "rev-parse", "--verify", "-q", f"{commit}^", check=False).strip()


def rules_before(folder: Path, commit: str) -> config.Config:
    """The configuration the commit inherited: its parent's copy, never its own.

    A commit is judged by the rules its parent committed, which is what makes
    "add the authorisation, then use it" legal and "add it in the same commit,
    then use it" refused.
    """
    parent = parent_of(folder, commit)
    return config.load(folder, parent) if parent else config.Config()


def changes_of(folder: Path, commit: str) -> list[Change]:
    """What the commit itself changed, old and new text included; a root commit changed nothing to judge."""
    parent = parent_of(folder, commit)
    return changed_files(folder, parent, commit) if parent else []


def retired_cases(changes: list[Change], rules: config.Config) -> dict[str, int]:
    """How many test cases each authorised file gives up in one commit, counted as `integrity.py` counts.

    Only files the inherited rules already authorise are counted: an
    unauthorised shrink is the integrity gate's failure, not a retirement.
    """
    found: dict[str, int] = {}
    for change in changes:
        path = change.old_path or change.path
        if not (TEST_PATH.search(path) and any(fnmatch(path, glob) for glob in rules.test_removal_ok)):
            continue
        lost = test_stats(change.old)[0] - test_stats(change.new)[0]
        if lost > 0:
            found[path] = found.get(path, 0) + lost
    return found


def integrity_by_commit(folder: Path, base: str, head: str) -> tuple[list[str], dict[str, int]]:
    """The test-integrity failures of the unit's own commits, and the cases its authorised files gave up.

    Each commit is judged with the configuration of its parent, the way the
    project's hook judges it: adding the authorisation and removing the test in
    the same commit is a failure, and a removal is a retirement only when the
    commit before it already allowed the file.
    """
    problems: list[str] = []
    retired: dict[str, int] = {}
    for commit in commits(folder, base, head):
        changes = changes_of(folder, commit)
        if not changes:
            continue
        inherited = rules_before(folder, commit)
        problems += [
            f"commit {commit[:8]}: {violation.path}: {violation.message}"
            for violation in check_test_integrity(changes, inherited)
        ]
        for path, cases in retired_cases(changes, inherited).items():
            retired[path] = retired.get(path, 0) + cases
    return problems, retired


def retirement_reason(retired: dict[str, int]) -> str:
    """The PASS reason: which files the unit retired and how many test cases each gave up."""
    if not retired:
        return "nessun test cancellato, ridotto o saltato: configurazione dell'unità giudicata commit per commit"
    total = sum(retired.values())
    files = ", ".join(f"{path} ({cases})" for path, cases in sorted(retired.items()))
    return (
        f"nessun test cancellato senza autorizzazione: {total} casi ritirati da {files}, "
        "autorizzati dal commit precedente"
    )


def added_entries(folder: Path, head: str, base_rules: config.Config) -> tuple[list[str], str]:
    """The `test_removal_ok` entries the unit added, and why the head's copy could not be read.

    The supervisor must see every authorisation a unit brought before saying
    yes, because the gates themselves only say whether the removal it allowed
    happened, not that someone agreed to it.
    """
    try:
        entries = config.load(folder, head).test_removal_ok
    except (ConfigError, OSError) as exc:
        return [], f"la configurazione della head non si legge, quindi le voci aggiunte non si sanno ({exc})"
    return [entry for entry in entries if entry not in base_rules.test_removal_ok], ""
