"""Github-origin skill bytes: the pin, the fetch, and the boundary they must stay inside.

One job: turn a manifest entry (`repo`, `commit`, `path`) into a cache directory
holding exactly the tree that commit names, or one line saying why it could not
be brought here. It was the top half of `skill_sources.py`; it moved out when
that file reached its size ceiling and the fetch gained a second backend.

Two backends, one outcome. The pinned `skills` CLI (see `skills_tool.py`) is
tried first where Node is installed, because it resolves the commit and
downloads the one skill folder instead of the whole repository; a clone and a
detached checkout follow when it is missing or fails, which is what every machine
without Node does. Both end at the same place, so `github_skill_source()` and the
materializer do not know which one ran.

Nothing here decides what is visible: placement, exposure, the index and the
recorded pin stay with `skills.py`.
"""
from __future__ import annotations

import errno
import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from nexgen_core.action_notes import ERROR, WARN
from nexgen_core.files import retry_transient_lock
from nexgen_core.i18n import t
from nexgen_core.paths import resolve_state_dir
from nexgen_core.skill_backups import next_backup_path

logger = logging.getLogger(__name__)

#: A pin that isn't a full commit isn't a pin: a branch or tag moves under
#: your feet and the skill changes without anyone having chosen that.
COMMIT_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")

#: A clone that doesn't respond must not stall the guard cycle.
GIT_CLONE_TIMEOUT_SECONDS = 120

#: Git must never stop to ask for credentials inside a timer.
GIT_NONINTERACTIVE_ENV = {
    "GIT_TERMINAL_PROMPT": "0",
    "GCM_INTERACTIVE": "Never",
}

#: `owner/name`, the shorthand a manifest declares a GitHub skill with.
GITHUB_SHORTHAND_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")


def clone_url(repo: str) -> str:
    """The URL to clone from, given what the manifest says.

    A manifest names a GitHub skill the way people write it — `owner/name` —
    and the rewrite handed that straight to `git clone`, which read it as a
    local path and reported that the repository does not exist. Three skills
    on this machine failed on every alignment for that reason.

    A value that already carries a scheme, or looks like an SSH remote, is
    passed through: somebody who wrote a full URL meant it.
    """
    repo = repo.strip()
    if GITHUB_SHORTHAND_RE.match(repo):
        return f"https://github.com/{repo}.git"
    return repo


def github_skill_source(cache_dir: Path, entry) -> Path:
    """Resolve a declared skill within its verified repository boundary."""
    root = cache_dir.resolve()
    source = (cache_dir / (entry.path or "")).resolve()
    skill = source / "SKILL.md"
    if not source.is_relative_to(root) or not skill.resolve().is_relative_to(root):
        raise ValueError(t("github skill '{name}': source escapes the cloned repository", name=entry.name))
    if not skill.is_file():
        raise ValueError(t("github skill '{name}': missing SKILL.md at the declared path", name=entry.name))
    return source


def _git_runner():
    """`git <args> [-C cwd]`, offline-safe: no prompt, bounded, never raising."""
    env = {**os.environ, **GIT_NONINTERACTIVE_ENV}

    def git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        base = ["git"] + (["-C", str(cwd)] if cwd else [])
        return subprocess.run(
            base + list(args),
            stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
            timeout=GIT_CLONE_TIMEOUT_SECONDS, env=env,
        )

    return git


def _tool_checkout(entry, incoming: Path, home: Path) -> str | None:
    """Fill `incoming` with the pinned skill using the `skills` CLI; why not, or None.

    The CLI writes the skill folder itself, so it is put back at the relative
    path a clone would have carried it in: the boundary check and the views then
    find the tree exactly where they always have.
    """
    from nexgen_core import skills_tool

    if not skills_tool.available():
        return t("node and npx are not on this machine")
    repo = (entry.repo or "").strip()
    if not GITHUB_SHORTHAND_RE.match(repo):
        return t("the repository is not written as owner/name")
    work_root = resolve_state_dir(home) / "skills-tool"
    try:
        work_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=f"{entry.name}.fetch-", dir=work_root) as work:
            folder, problem = skills_tool.fetch(repo, entry.commit or "",
                                                Path(entry.path or entry.name).name, Path(work))
            if problem:
                return problem
            target = incoming / (entry.path or "")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(folder), str(target))
    except OSError as exc:
        return t("the scratch directory is not usable: {error}", error=exc)
    return None


def _clone_at_pin(git, incoming: Path, entry) -> str | None:
    """Clone the repository and check out the pin; one line when it could not."""
    res = git("clone", "--quiet", "--", clone_url(entry.repo or ""), str(incoming))
    if res.returncode != 0:
        return ERROR + t(
            "github skill '{name}': cloning {repo} failed: {error}",
            name=entry.name, repo=entry.repo, error=res.stderr.strip(),
        )
    res = git("checkout", "--quiet", "--detach", entry.commit or "", cwd=incoming)
    if res.returncode:
        git("fetch", "--quiet", "origin", entry.commit or "", cwd=incoming)
        res = git("checkout", "--quiet", "--detach", entry.commit or "", cwd=incoming)
    if res.returncode != 0:
        return ERROR + t(
            "github skill '{name}': commit {commit} is not reachable in the repository: {error}",
            name=entry.name, commit=entry.commit, error=res.stderr.strip(),
        )
    return None


def _publish(incoming: Path, cache_dir: Path, clean_cache: bool, name: str) -> tuple[bool, str | None]:
    """Swap the verified bytes in, keeping whatever was there as a set-aside copy."""
    backup = next_backup_path(cache_dir) if cache_dir.exists() else None
    if backup is not None:
        try:
            retry_transient_lock(lambda: cache_dir.rename(backup))
        except OSError as exc:
            # Transient lock (a CLI holding the skill open, a scanner, a concurrent
            # cycle): nothing was touched, old bytes stay valid, retry next cycle.
            # Reported as not materialized so the caller records no pin.
            if exc.errno in (errno.EACCES, errno.EBUSY):
                return False, WARN + t(
                    "github skill '{name}': cache locked by another process, keeping the previous pin and retrying next cycle",
                    name=name,
                )
            raise
    try:
        retry_transient_lock(lambda: incoming.rename(cache_dir))
    except OSError:
        if backup is not None:
            retry_transient_lock(lambda: backup.rename(cache_dir))
        raise
    # A clean cache is reproducible from its pin. Preserve an unknown or corrupt
    # directory instead of deleting user bytes.
    if backup is not None and clean_cache:
        try:
            shutil.rmtree(backup)
        except OSError:
            return True, WARN + t(
                "github skill '{name}': updated; old cache cleanup failed at {path}", name=name, path=backup,
            )
    return True, None


def ensure_github_checkout(cache_dir: Path, entry, home: Path) -> tuple[bool, str | None]:
    """Verify a pin off to the side, then replace the usable cache.

    Acquisition failures leave the previous bytes and views intact. Local edits
    are preserved and reported, never certified by HEAD alone.
    """
    git = _git_runner()
    try:
        if not COMMIT_SHA_RE.fullmatch(entry.commit or ""):
            raise ValueError(t("github skill '{name}': a full commit pin is required", name=entry.name))
        if cache_dir.is_symlink():
            raise ValueError(t("github skill '{name}': cache is a foreign symlink; preserved", name=entry.name))
        clean_cache = False
        if cache_dir.is_dir():
            head = git("rev-parse", "HEAD", cwd=cache_dir)
            if head.returncode == 0:
                dirty = git("status", "--porcelain", "--untracked-files=all", cwd=cache_dir)
                if dirty.returncode or dirty.stdout.strip():
                    raise ValueError(t("github skill '{name}': cache has local changes; preserved", name=entry.name))
                clean_cache = True
                origin = git("remote", "get-url", "origin", cwd=cache_dir)
                if (head.stdout.strip().lower() == (entry.commit or "").lower()
                        and origin.returncode == 0 and origin.stdout.strip() == clone_url(entry.repo or "")):
                    github_skill_source(cache_dir, entry)
                    return True, None

        cache_dir.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=f"{cache_dir.name}.incoming-", dir=cache_dir.parent) as tmp:
            incoming = Path(tmp) / "checkout"
            why = _tool_checkout(entry, incoming, home)
            if why is not None:
                # The clone is the path this engine shipped with: a tool that is
                # absent, refused or answered with another commit costs nothing.
                logger.debug("skills CLI fetch skipped for '%s': %s", entry.name, why)
                problem = _clone_at_pin(git, incoming, entry)
                if problem is not None:
                    return False, problem
            github_skill_source(incoming, entry)
            return _publish(incoming, cache_dir, clean_cache, entry.name)
    except subprocess.TimeoutExpired:
        return False, ERROR + t(
            "github skill '{name}': {repo} did not respond within {timeout}s, retrying next cycle",
            name=entry.name, repo=entry.repo, timeout=GIT_CLONE_TIMEOUT_SECONDS,
        )
    except (OSError, ValueError) as exc:
        return False, ERROR + t("github skill '{name}': {error}", name=entry.name, error=exc)
