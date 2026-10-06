#!/usr/bin/env python3
"""Where third-party skill bytes come from (github / installer / upstream).

`skills.py` decides WHAT gets materialized and WHERE the views land; this
module fetches the bytes for everything the engine doesn't own. A new
acquisition lane (a registry, a package manager) lands here as one method
on `SkillFetcher`, without touching placement, views, index or CLI -- that
split is what keeps a new origin a small change instead of a rewrite.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.action_notes import ERROR, WARN  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.i18n import t  # noqa: E402
from nexgen_core.files import atomic_write_text  # noqa: E402
from nexgen_core.lock import HostLock  # noqa: E402
from nexgen_core.paths import resolve_home, resolve_state_dir  # noqa: E402


@dataclass
class SkillEntry:
    name: str
    origin: str = "vault"
    exposure: str = "lazy"
    scope: str = "shared"
    owner: str | None = None
    targets: list[str] = field(default_factory=lambda: ["claude", "codex", "antigravity", "opencode"])
    repo: str | None = None
    commit: str | None = None
    version: str | None = None
    install: list[str] = field(default_factory=list)
    path: str | None = None
    description: str = ""
    deps: dict | None = None
    source_path: Path | None = None


#: A skill name is a path segment, not a path: no separators, no traversal.
#: Without this check `agent-skill show ../../something` would read a file
#: outside the library.
SKILL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

#: A pin that isn't a full commit isn't a pin: a branch or tag moves under
#: your feet and the skill changes without anyone having chosen that.
COMMIT_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")

#: A clone that doesn't respond must not stall the guard cycle.
GIT_CLONE_TIMEOUT_SECONDS = 120

#: A third-party installer that hangs must not hold the guard cycle open.
INSTALLER_TIMEOUT_SECONDS = 300

#: Git must never stop to ask for credentials inside a timer.
GIT_NONINTERACTIVE_ENV = {
    "GIT_TERMINAL_PROMPT": "0",
    "GCM_INTERACTIVE": "Never",
}


def is_safe_skill_name(name: str) -> bool:
    """True if the name can become a path segment with no surprises."""
    return bool(name) and name not in (".", "..") and bool(SKILL_NAME_RE.match(name))


def same_tree_content(src: Path, dst: Path) -> bool:
    """True if two trees contain exactly the same files, byte for byte."""
    if not src.is_dir() or not dst.is_dir():
        return False
    left = {p.relative_to(src): p for p in src.rglob("*") if p.is_file()}
    right = {p.relative_to(dst): p for p in dst.rglob("*") if p.is_file()}
    if left.keys() != right.keys():
        return False
    try:
        return all(left[k].read_bytes() == right[k].read_bytes() for k in left)
    except OSError:
        return False


def next_backup_path(path: Path) -> Path:
    """A free backup path next to `path`."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    candidate = path.with_name(f"{path.name}.bak-{stamp}")
    n = 2
    while candidate.exists():
        candidate = path.with_name(f"{path.name}.bak-{stamp}-{n}")
        n += 1
    return candidate


def make_link_or_copy(src: Path, dst: Path) -> bool:
    """Creates a symlink (or copies, on Windows if symlink privileges aren't active).

    A real folder found where the view should be is never deleted: it may
    hold work nobody entrusted to us. If the content already matches,
    nothing is touched; otherwise it's set aside with a backup before taking
    its place.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.is_symlink() or dst.is_file():
        try:
            if dst.is_symlink() and dst.resolve() == src.resolve():
                return False
            dst.unlink()
        except OSError:
            pass
    elif dst.is_dir():
        if same_tree_content(src, dst):
            return False
        dst.rename(next_backup_path(dst))

    try:
        dst.symlink_to(src, target_is_directory=src.is_dir())
        return True
    except OSError:
        # Copy fallback for Windows without symlink developer mode
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dst)
        return True


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


def github_skill_source(cache_dir: Path, entry: SkillEntry) -> Path:
    """Resolve a declared skill within its verified repository boundary."""
    root = cache_dir.resolve()
    source = (cache_dir / (entry.path or "")).resolve()
    skill = source / "SKILL.md"
    if not source.is_relative_to(root) or not skill.resolve().is_relative_to(root):
        raise ValueError(t("github skill '{name}': source escapes the cloned repository", name=entry.name))
    if not skill.is_file():
        raise ValueError(t("github skill '{name}': missing SKILL.md at the declared path", name=entry.name))
    return source


class SkillFetcher:
    """Brings third-party skill bytes to the library door. Placement
    (library links, native views, index) stays the materializer's job:
    this class never decides where a skill is visible, only what bytes
    arrive and which version is recorded."""

    def __init__(self, home: Path | None = None) -> None:
        self.home = resolve_home(home)

    def ensure_github_checkout(self, cache_dir: Path, entry: SkillEntry) -> tuple[bool, str | None]:
        """Verify a pin off to the side, then replace the usable cache.

        Acquisition failures leave the previous bytes and views intact.
        Local edits are preserved and reported, never certified by HEAD alone.
        """
        env = {**os.environ, **GIT_NONINTERACTIVE_ENV}

        def git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
            base = ["git"] + (["-C", str(cwd)] if cwd else [])
            return subprocess.run(
                base + list(args),
                capture_output=True, text=True, check=False,
                timeout=GIT_CLONE_TIMEOUT_SECONDS, env=env,
            )

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
                res = git("clone", "--quiet", "--", clone_url(entry.repo or ""), str(incoming))
                if res.returncode != 0:
                    return False, ERROR + t(
                        "github skill '{name}': cloning {repo} failed: {error}",
                        name=entry.name, repo=entry.repo, error=res.stderr.strip(),
                    )
                res = git("checkout", "--quiet", "--detach", entry.commit or "", cwd=incoming)
                if res.returncode:
                    git("fetch", "--quiet", "origin", entry.commit or "", cwd=incoming)
                    res = git("checkout", "--quiet", "--detach", entry.commit or "", cwd=incoming)
                if res.returncode != 0:
                    return False, ERROR + t(
                        "github skill '{name}': commit {commit} is not reachable in the repository: {error}",
                        name=entry.name, commit=entry.commit, error=res.stderr.strip(),
                    )
                github_skill_source(incoming, entry)
                backup = next_backup_path(cache_dir) if cache_dir.exists() else None
                if backup is not None:
                    cache_dir.rename(backup)
                try:
                    incoming.rename(cache_dir)
                except OSError:
                    if backup is not None:
                        backup.rename(cache_dir)
                    raise
                # Clean Git caches are reproducible from their pin. Preserve
                # an unknown/corrupt directory instead of deleting user bytes.
                if backup is not None and clean_cache:
                    try:
                        shutil.rmtree(backup)
                    except OSError:
                        return True, WARN + t(
                            "github skill '{name}': updated; old cache cleanup failed at {path}",
                            name=entry.name, path=backup,
                        )
            return True, None
        except subprocess.TimeoutExpired:
            return False, ERROR + t(
                "github skill '{name}': {repo} did not respond within {timeout}s, retrying next cycle",
                name=entry.name, repo=entry.repo, timeout=GIT_CLONE_TIMEOUT_SECONDS,
            )
        except (OSError, ValueError) as exc:
            return False, ERROR + t("github skill '{name}': {error}", name=entry.name, error=exc)

    def _installed_versions_file(self) -> Path:
        return resolve_state_dir(self.home) / "installed-skill-versions.json"

    def _installed_versions(self) -> dict[str, str]:
        """Which version of each installer-owned skill is materialized here."""
        path = self._installed_versions_file()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not all(isinstance(value, str) for value in data.values()):
                raise ValueError("skill version record must map names to version strings")
            return data
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            raise OSError(t("Cannot read skill version record at {path} ({error}).",
                            path=path, error=type(exc).__name__)) from exc

    def _record_installed_version(self, name: str, version: str) -> None:
        """Persist a pin or raise a diagnostic safe for sync and doctor."""
        try:
            path = self._installed_versions_file()
            # A distinct lock avoids nesting the global sync lock. Atomic
            # rename protects readers; serialization protects other writers.
            with HostLock(lock_path=path.with_suffix(".lock"), timeout=5, command_name="skill-pin-record"):
                current = self._installed_versions()
                current[name] = version
                atomic_write_text(path, json.dumps(current, indent=2, sort_keys=True) + "\n")
        except OSError as exc:
            raise OSError(t(
                "Skill '{name}' pin could not be recorded ({error}); update is incomplete.",
                name=name, error=type(exc).__name__,
            )) from exc

    def claim_from_discovery(
        self, name: str, lib_dest: Path, discovery_dirs: tuple[Path, ...]
    ) -> bool:
        """Moves an installer's copy out of wherever a runtime would find it.

        An installer with a global scope drops its skill straight into a
        discovery root, and from there every runtime loads it eagerly — the
        opposite of what the manifest asked for. Remembering to move it by
        hand is not a mechanism, so the engine moves it: into the library,
        which no runtime scans, and from there only the declared views are
        created.
        """
        for directory in discovery_dirs:
            candidate = directory / name
            if not candidate.is_dir() or candidate.is_symlink():
                continue
            if lib_dest.exists() or lib_dest.is_symlink():
                if same_tree_content(candidate, lib_dest):
                    shutil.rmtree(candidate, ignore_errors=True)
                    return True
                # New bytes arrived but the library holds different ones:
                # set the old aside instead of silently keeping stale bytes
                # while the version is recorded as new (false-green pin).
                try:
                    backup = next_backup_path(lib_dest)
                    # Rename the link itself, never its target. It is recoverable
                    # exactly like a directory if publishing the replacement fails.
                    lib_dest.rename(backup)
                except OSError:
                    return False
                try:
                    shutil.move(str(candidate), str(lib_dest))
                except (OSError, shutil.Error):
                    if lib_dest.is_symlink() or lib_dest.is_file():
                        lib_dest.unlink()
                    elif lib_dest.is_dir():
                        shutil.rmtree(lib_dest)
                    backup.rename(lib_dest)
                    return False
                return True
            lib_dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(candidate), str(lib_dest))
            return True
        return False

    def _tree_fingerprint(self, root: Path) -> dict[str, str] | None:
        """Content hash per file, or None when the tree doesn't exist."""
        import hashlib

        if not root.is_dir():
            return None
        out: dict[str, str] = {}
        try:
            for path in sorted(root.rglob("*")):
                if not path.is_file() or path.is_symlink():
                    continue
                try:
                    digest = hashlib.sha256(path.read_bytes()).hexdigest()
                except OSError:
                    return None
                out[str(path.relative_to(root))] = digest
        except OSError:
            return None
        return out

    def install_third_party(
        self, entry: SkillEntry, lib_dest: Path, discovery_dirs: tuple[Path, ...]
    ) -> tuple[bool, str | None]:
        """Runs a third-party installer, but only when the pin actually moved."""
        try:
            recorded = self._installed_versions().get(entry.name)
        except OSError as exc:
            return False, ERROR + t("Cannot verify the recorded pin for skill '{name}' ({error}).",
                                        name=entry.name, error=type(exc).__name__)
        if recorded == entry.version and lib_dest.is_dir():
            return True, None
        if not entry.install:
            return False, ERROR + t(
                "skill '{name}' is installed by its own installer but declares no install command",
                name=entry.name,
            )
        before = self._tree_fingerprint(lib_dest)
        if lib_dest.exists() and before is None:
            return False, ERROR + t("Cannot verify the existing skill library: {path}", path=lib_dest)
        try:
            result = subprocess.run(
                list(entry.install), capture_output=True, text=True, check=False,
                timeout=INSTALLER_TIMEOUT_SECONDS,
                env={**os.environ, **GIT_NONINTERACTIVE_ENV},
            )
        except subprocess.TimeoutExpired:
            return False, ERROR + t(
                "the installer for '{name}' did not finish within {seconds}s",
                name=entry.name, seconds=INSTALLER_TIMEOUT_SECONDS,
            )
        except OSError as exc:
            return False, f"{ERROR}{exc}"
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip().splitlines()
            return False, ERROR + t(
                "the installer for '{name}' failed: {reason}",
                name=entry.name, reason=detail[-1] if detail else "no detail",
            )

        claimed = self.claim_from_discovery(entry.name, lib_dest, discovery_dirs)
        if not lib_dest.is_dir():
            return False, ERROR + t(
                "the installer for '{name}' ran but left nothing the engine could find",
                name=entry.name,
            )
        after = self._tree_fingerprint(lib_dest)
        if claimed or (after is not None and (before is None or after != before)):
            # One completion path owns both claimed and in-place installs.
            # New bytes without a durable pin are an incomplete update.
            try:
                self._record_installed_version(entry.name, entry.version or "")
            except OSError as exc:
                return False, ERROR + str(exc)
            return True, t("Installed skill '{name}' at version {version}", name=entry.name, version=entry.version)
        # The installer exited 0 but left no observable new bytes, while
        # the library still holds the old version: recording the new
        # version would declare the pin current on stale bytes.
        return False, ERROR + t(
            "the installer for '{name}' ran but left no new copy to claim: keeping the previous version",
            name=entry.name,
        )
