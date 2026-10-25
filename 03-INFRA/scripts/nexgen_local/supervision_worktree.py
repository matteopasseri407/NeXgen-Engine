"""The working copy of one unit: its own local branch, its scope, and a push that cannot work.

The executor works in a git worktree created from the unit's base, on a branch
that is never pushed, with the push blocked inside that copy alone. This module
creates it, writes down what the engine will judge, and says in the unit folder
how to remove it; publication stays a later phase's decision.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from nexgen_core.discipline.gitutil import LONG_PATHS, GitError, git

from . import supervision_gitconfig as gitconfig
from . import supervision_state as state
from .supervision_state import SupervisionError

#: Prefix of the unit's folder inside the task folder.
UNITS_DIR = "23-unita"
#: What a push is rewritten to: a scheme no git transport implements, so it always fails.
BLOCKED_PUSH_URL = "nexgen-sup-disabled://push-blocked"
#: A remote that does not answer within this budget is reported as unreadable, never as unchanged.
REMOTE_TIMEOUT_S = 30.0


class WorktreeError(SupervisionError):
    """A base that does not resolve, a copy that already exists, a push block that would not stick."""


def unit_branch(task_id: str, unit: str) -> str:
    """The local branch a unit commits on: never pushed, named after task and unit."""
    return f"sup/{task_id}/{state.slug(unit)}"


def worktree_path(task_id: str, unit: str) -> Path:
    """Where the unit's copy lives: inside the task folder, so one removal cleans it all."""
    return state.open_task(task_id) / f"{UNITS_DIR}-{state.slug(unit)}"


def remote_urls(root: Path) -> dict:
    """Every remote's fetch URL as git reports it now; one without a URL is empty, not absent.

    The URL is read from the config directly: after a `git remote remove` in a
    worktree whose per-worktree `pushurl` still exists, `git remote get-url`
    prints the remote's own name instead of failing, which would turn a removed
    remote into a remote that looks present.
    """
    return {
        name: git(root, "config", "--get", f"remote.{name}.url", check=False).strip()
        for name in git(root, "remote").split()
    }


def push_urls(root: Path) -> dict:
    """Every remote's push URL as git sees it now, the one a push really uses.

    `remote.<name>.pushurl` wins over the fetch URL for every push. Reading only
    `url` let a unit re-point its pushes without touching the value the gates
    compared, so the gate looked clean while the commits went elsewhere.
    """
    return {
        name: git(root, "config", "--get", f"remote.{name}.pushurl", check=False).strip()
        for name in git(root, "remote").split()
    }


def refs_at(url: str, *, timeout: float = REMOTE_TIMEOUT_S) -> dict:
    """Every ref the remote at `url` advertises now; an unreachable one says why, not nothing."""
    try:
        done = subprocess.run(
            ["git", "ls-remote", url],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            stdin=subprocess.DEVNULL, timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"error": type(exc).__name__}
    if done.returncode:
        reason = (done.stderr or "").strip().splitlines()
        return {"error": (reason[-1] if reason else f"exit {done.returncode}")[:200]}
    return {
        line.split("\t")[1]: line.split("\t")[0]
        for line in done.stdout.splitlines() if "\t" in line
    }


def remote_refs(root: Path, *, timeout: float = REMOTE_TIMEOUT_S) -> dict:
    """Every remote's refs as git reports them now, read through its own fetch URL."""
    return {
        name: refs_at(url, timeout=timeout)
        for name, url in remote_urls(root).items()
    }


def create(
    task_id: str,
    unit: str,
    *,
    repo: str | Path,
    base: str,
    brief: str | Path,
    allow: list[str] | tuple[str, ...],
    verify: str = "",
    mcp: list[str] | tuple[str, ...] | None = None,
) -> dict:
    """Prepare the unit's copy, its branch, its push block and its written environment."""
    folder = worktree_path(task_id, unit)
    if folder.exists():
        raise WorktreeError(f"la copia dell'unità esiste già: {folder}")
    scope = [str(pattern).strip() for pattern in allow if str(pattern).strip()]
    if not scope:
        raise WorktreeError("confine vuoto: indica almeno un `--allow <glob>`")
    root = _repo_root(repo)
    owner = repo_identity(root)
    base_sha = _base_commit(root, base)
    brief_text = _brief_text(brief)
    branch = unit_branch(task_id, unit)
    _add_worktree(root, folder, branch, base_sha)
    try:
        _block_push(root, folder)
        _set_identity(folder, owner)
    except (GitError, OSError) as exc:
        _discard(root, folder, branch)
        raise WorktreeError(f"configurazione della copia non riuscita in {folder}: copia rimossa ({exc})") from exc
    snapshot = remote_refs(root)
    (Path(folder).parent / f"20-brief-{state.slug(unit)}.md").write_text(brief_text, encoding="utf-8")
    environment = _environment_text(task_id, unit, folder, branch, base, base_sha, scope, verify, identity=owner)
    (Path(folder).parent / f"22-ambiente-{state.slug(unit)}.md").write_text(environment, encoding="utf-8")
    mcp_assigned = list(dict.fromkeys(
        str(item).strip() for item in (mcp or []) if str(item).strip()
    ))
    record = {
        "unit": state.slug(unit),
        "repo": str(root),
        "base_ref": base,
        "base": base_sha,
        "branch": branch,
        "worktree": str(folder),
        "allow": scope,
        "verify": verify,
        "identity": owner,
        "mcp": mcp_assigned,
        "remotes": snapshot,
        "remote_urls": remote_urls(root),
        "remote_pushurls": push_urls(Path(folder)),
        "config_hashes": gitconfig.fingerprint(Path(folder)),
        "url_override_keys": gitconfig.url_override_keys(Path(folder)),
    }
    state.append_event(task_id, "unit_created", **record)
    return record


def _repo_root(repo: str | Path) -> Path:
    """The repository the unit branches from, refused when the path is not one."""
    candidate = Path(repo).expanduser()
    if not candidate.is_dir():
        raise WorktreeError(f"checkout inesistente: {candidate}")
    top = git(candidate, "rev-parse", "--show-toplevel", check=False).strip()
    if not top:
        raise WorktreeError(f"non è un repository git: {candidate}")
    return Path(top)


def _base_commit(root: Path, base: str) -> str:
    """The base as a commit, so a moving branch cannot change what was reviewed."""
    sha = git(root, "rev-parse", "--verify", f"{base}^{{commit}}", check=False).strip()
    if not sha:
        raise WorktreeError(f"base sconosciuta nel repository: {base}")
    return sha


def _brief_text(brief: str | Path) -> str:
    """The brief as written, refused when it is missing or empty."""
    path = Path(brief).expanduser()
    if not path.is_file():
        raise WorktreeError(f"brief inesistente: {path}")
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        raise WorktreeError(f"brief vuoto: {path}")
    return text


def _add_worktree(root: Path, folder: Path, branch: str, base_sha: str) -> None:
    """Create the copy on its own new branch from the base commit."""
    try:
        git(root, *LONG_PATHS, "worktree", "add", "-b", branch, str(folder), base_sha)
    except GitError as exc:
        raise WorktreeError(f"copia di lavoro non creata: {exc}") from exc


def repo_identity(root: Path) -> dict[str, str]:
    """The owner's identity as configured in the repository, refused when either field is missing."""
    name = git(root, "config", "user.name", check=False).strip()
    email = git(root, "config", "user.email", check=False).strip()
    if not name or not email:
        missing = []
        if not name:
            missing.append("user.name")
        if not email:
            missing.append("user.email")
        raise WorktreeError(
            f"il repository non ha un'identità configurata ({', '.join(missing)} mancante in {root}): "
            "configura user.name e user.email prima di creare l'unità"
        )
    return {"name": name, "email": email}


def _set_identity(worktree: Path, identity: dict[str, str]) -> None:
    """Write the owner's identity into the copy's per-worktree configuration."""
    git(worktree, "config", "--worktree", "user.name", identity["name"])
    git(worktree, "config", "--worktree", "user.email", identity["email"])


def _block_push(root: Path, worktree: Path) -> None:
    """Make every push from this copy fail, without touching hooks or the shared push config.

    Per-worktree config is the only place git lets one worktree differ: enabling
    the extension is a repository-wide switch that changes nothing until a
    `config.worktree` file exists, and the pushurl lives only in this copy's.
    Hooks are shared with the owner's checkout and are left exactly as they are.
    """
    git(worktree, "config", "extensions.worktreeConfig", "true")
    for name in git(worktree, "remote").split():
        git(worktree, "config", "--worktree", f"remote.{name}.pushurl", BLOCKED_PUSH_URL)


def _discard(root: Path, folder: Path, branch: str) -> None:
    """Undo a half-made unit: the copy and its branch go, the repository stays."""
    git(root, *LONG_PATHS, "worktree", "remove", "--force", str(folder), check=False)
    git(root, "branch", "-D", branch, check=False)


def _environment_text(
    task_id: str, unit: str, folder: Path, branch: str, base: str, base_sha: str,
    scope: list[str], verify: str, identity: dict[str, str] | None = None,
) -> str:
    """The environment the executor has to know: where it is and what it may not do."""
    lines = [
        f"# Ambiente dell'unità {unit}",
        "",
        f"- compito: {task_id}",
        f"- copia di lavoro: {folder}",
        f"- ramo locale: {branch}",
        f"- base: {base} ({base_sha})",
        *( [f"- identità: {identity['name']} <{identity['email']}>"] if identity else [] ),
        f"- verifica: {verify or 'nessuna dichiarata'}",
        "",
        "## Confine",
        "",
        "Solo questi percorsi possono cambiare:",
        "",
        *[f"- {pattern}" for pattern in scope],
        "",
        "## Push",
        "",
        "La push da questa copia è bloccata dalla configurazione del worktree.",
        "Un tentativo fallisce; non aggirarlo, non è un errore da correggere.",
        "",
        "## Pulizia",
        "",
        f"Dal repository: `git worktree remove {folder}` poi `git branch -D {branch}`.",
        "",
    ]
    return "\n".join(lines)
