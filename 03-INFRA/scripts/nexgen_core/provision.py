"""Provisioning of MCP server dependencies declared in the manifest.

A server in ``mcp/manifest.yaml`` may declare a ``deps:`` block. That block
names the external dependency the server needs to run: a pinned npm package
(``npx``), or a pinned git repository checked out at a commit (``git``),
optionally under a ``subdir`` and optionally built with ``build`` commands.

Rules (the same posture the rest of the layer lives by):

- pins are mandatory: ``npx`` requires ``pkg@version`` (the same regex
  ``depwatch`` enforces upstream), ``git`` requires a commit ``rev``. A dep
  without a pin is refused, never guessed, never moved.
- provisioning is lazy by default: ``ensure_deps(install=True)`` runs at the
  waiter's first spawn of the server. ``ensure_deps(install=False)`` is the
  offline-safe verification used by the doctor and preflight: it reads the
  local marker only, never touches the network.
- the workspace lives under the machine-local state dir (``deps/``), keyed
  by a hash of repo+rev: deterministic, recreatable, never synced.
- a build failure is a provisioning failure: the server is not started and
  the error names the step that failed.
- nothing here ever upgrades anything: ``depwatch`` stays the reporter,
  this module installs exactly the declared pin.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

try:
    from nexgen_core.depwatch import NPM_SPEC_RE
except ImportError:  # pragma: no cover - the waiter imports with a fallback
    NPM_SPEC_RE = __import__("re").compile(r"^(?P<name>(?:@[\w.-]+/)?[\w.-]+)@(?P<version>\d[\w.+-]*)$")

from nexgen_core.config import load_mcp_manifest
from nexgen_core.i18n import t
from nexgen_core.paths import mcp_manifest, resolve_state_dir

#: Subfolder of the machine-local state dir that holds provisioned workspaces.
DEPS_DIRNAME = "deps"

#: Marker file written inside a provisioned clone, holding repo+rev in JSON.
MARKER = ".nexgen-provisioned"

#: Lock wait: another process may be provisioning the same dep right now.
PROVISION_LOCK_TIMEOUT_S = 120

#: Node.js is required for `npx`-kind deps; the error must say so.
NODE_REQUIRED_MSG = (
    "the '{kind}' dependency of server '{server}' needs {bin} on the machine, "
    "which is missing. Install Node.js (or fix PATH), then call again."
)


class ProvisionError(Exception):
    """A dependency could not be verified or provisioned. Message is user-facing."""


def _workspace_root(state_dir: Path) -> Path:
    root = Path(state_dir) / DEPS_DIRNAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def workspace_path(deps: dict[str, Any], state_dir: Path) -> Path:
    """Deterministic workspace path for a git dep: state_dir/deps/<hash>.

    Hash covers repo and rev, so a pin change provisions a fresh workspace
    instead of mutating an existing one in place.
    """
    repo = str(deps.get("repo") or "").strip()
    rev = str(deps.get("rev") or "").strip()
    digest = hashlib.sha256(f"{repo}|{rev}".encode()).hexdigest()[:16]
    return _workspace_root(state_dir) / digest


def _validate_kind(deps: dict[str, Any], server: str) -> str:
    kind = str(deps.get("kind") or "").strip()
    if kind not in ("npx", "git"):
        raise ProvisionError(
            t("server '{server}' declares deps.kind '{kind}', which is not one of the supported kinds (npx, git).", server=server, kind=kind or "<empty>")
        )
    return kind


def _validate_pins(deps: dict[str, Any], server: str) -> None:
    kind = str(deps.get("kind") or "").strip()
    if kind == "npx":
        spec = str(deps.get("spec") or "").strip()
        if not NPM_SPEC_RE.match(spec):
            raise ProvisionError(
                t("server '{server}' declares an unpinned npx dependency '{spec}'. Pin rule: a version is mandatory ('pkg@1.2.3'), never '@latest' or a range.", server=server, spec=spec)
            )
    elif kind == "git":
        repo = str(deps.get("repo") or "").strip()
        rev = str(deps.get("rev") or "").strip()
        if not repo or not rev:
            raise ProvisionError(
                t("server '{server}' declares a git dependency without both 'repo' and 'rev'. Pin rule: a commit is mandatory.", server=server)
            )
        if len(rev) < 7 or "/" in rev or ".." in rev:
            raise ProvisionError(
                t("server '{server}' declares git rev '{rev}', which is not a commit. Pin rule: an immutable commit hash is mandatory.", server=server, rev=rev)
            )


def _check_node(server: str) -> None:
    if shutil.which("node"):
        return
    raise ProvisionError(t(NODE_REQUIRED_MSG, server=server, kind="npx", bin="node"))


def _read_marker(workspace: Path) -> dict[str, Any] | None:
    marker = workspace / MARKER
    if not marker.is_file():
        return None
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _run_workspace(clone: Path, deps: dict[str, Any]) -> Path:
    """The directory servers run from: the clone root, or its declared subdir."""
    subdir = str(deps.get("subdir") or "").strip().strip("/")
    return (clone / subdir) if subdir else clone


def _verified(deps: dict[str, Any], state_dir: Path) -> dict[str, str] | None:
    """Cheap, offline-safe verification of a git dep: marker with matching rev."""
    clone = workspace_path(deps, state_dir)
    data = _read_marker(clone)
    if data and data.get("repo") == deps.get("repo") and data.get("rev") == deps.get("rev"):
        return {"DEPS_WORKSPACE": str(_run_workspace(clone, deps))}
    return None


def _run_build(build: list[Any], workspace: Path, server: str) -> None:
    for step in build:
        argv = list(step) if isinstance(step, list) else str(step).split()
        if not argv:
            continue
        if not shutil.which(argv[0]):
            raise ProvisionError(
                t("build step '{cmd}' for server '{server}' needs '{bin}' on the machine.", cmd=" ".join(argv), server=server, bin=argv[0])
            )
        proc = subprocess.run(argv, cwd=workspace, capture_output=True, text=True, timeout=600)
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout).strip().splitlines()[-5:]
            raise ProvisionError(
                t("build for server '{server}' failed in '{cmd}': {tail}", server=server, cmd=" ".join(argv), tail=" | ".join(tail) or "exit {code}".format(code=proc.returncode))
            )


class _ProvisionLock:
    """Cross-platform advisory lock on a lock file, with timeout."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._fh = None

    def __enter__(self) -> "_ProvisionLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "a+", encoding="utf-8")
        deadline = time.monotonic() + PROVISION_LOCK_TIMEOUT_S
        if os.name == "posix":
            import fcntl
            while True:
                try:
                    fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    return self
                except OSError:
                    if time.monotonic() >= deadline:
                        raise ProvisionError(t("Timed out waiting for another provisioning run to finish (lock {path}).", path=self.path))
                    time.sleep(0.25)
        else:
            import msvcrt
            while True:
                try:
                    msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, 1)
                    self._fh.seek(0)
                    return self
                except OSError:
                    if time.monotonic() >= deadline:
                        raise ProvisionError(t("Timed out waiting for another provisioning run to finish (lock {path}).", path=self.path))
                    time.sleep(0.25)

    def __exit__(self, *exc: Any) -> None:
        if self._fh is None:
            return
        try:
            if os.name == "posix":
                import fcntl
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            else:
                import msvcrt
                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        self._fh.close()
        self._fh = None


def _provision_git(deps: dict[str, Any], state_dir: Path, server: str) -> dict[str, str]:
    """Clone repo at the pinned rev into a fresh temp dir, build, atomically move in."""
    final = workspace_path(deps, state_dir)
    with _ProvisionLock(final.parent / ".lock"):
        verified = _verified(deps, state_dir)
        if verified:
            return verified
        repo = str(deps.get("repo")).strip()
        rev = str(deps.get("rev")).strip()
        with tempfile.TemporaryDirectory(prefix=f".{final.name}.tmp-", dir=str(final.parent)) as tmp:
            clone = Path(tmp)
            git = ["git"]
            if not shutil.which("git"):
                raise ProvisionError(t("server '{server}' declares a git dependency but git is not on the machine.", server=server))
            proc = subprocess.run(
                git + ["init", "-q"], cwd=clone, capture_output=True, text=True,
            )
            if proc.returncode != 0:
                raise ProvisionError(t("git init failed for server '{server}': {err}", server=server, err=(proc.stderr or "").strip()))
            proc = subprocess.run(
                git + ["remote", "add", "origin", repo], cwd=clone, capture_output=True, text=True,
            )
            if proc.returncode != 0:
                raise ProvisionError(t("git remote add failed for server '{server}': {err}", server=server, err=(proc.stderr or "").strip()))
            proc = subprocess.run(
                git + ["fetch", "--depth", "1", "origin", rev], cwd=clone, capture_output=True, text=True, timeout=600,
            )
            if proc.returncode != 0:
                raise ProvisionError(
                    t("could not fetch rev '{rev}' of '{repo}' for server '{server}': {err}", rev=rev, repo=repo, server=server, err=(proc.stderr or "").strip().splitlines()[-1])
                )
            proc = subprocess.run(git + ["checkout", "-q", "FETCH_HEAD"], cwd=clone, capture_output=True, text=True)
            if proc.returncode != 0:
                raise ProvisionError(t("git checkout failed for server '{server}': {err}", server=server, err=(proc.stderr or "").strip()))
            workspace = _run_workspace(clone, deps)
            if workspace != clone and not workspace.is_dir():
                raise ProvisionError(
                    t("subdir '{subdir}' does not exist in '{repo}' at rev '{rev}' (server '{server}').", subdir=deps.get("subdir"), repo=repo, rev=rev, server=server)
                )
            build = deps.get("build") or []
            if build:
                _run_build(build, workspace, server)
            (clone / MARKER).write_text(json.dumps({"repo": repo, "rev": rev}), encoding="utf-8")
            if final.exists():
                shutil.rmtree(final, ignore_errors=True)
            os.replace(clone, final)
        return {"DEPS_WORKSPACE": str(_run_workspace(final, deps))}


def ensure_deps(deps: dict[str, Any], state_dir: Path, install: bool = False, server: str = "<server>") -> tuple[dict[str, str], str | None]:
    """Make sure the dependency declared by ``deps`` is satisfiable.

    Returns ``(context, error)``: ``context`` carries ``DEPS_WORKSPACE`` when
    a git workspace exists (so the caller can substitute it in server args);
    ``error`` is None on success. With ``install=False`` this is a local,
    offline-safe check (markers and tool presence only).
    """
    if not isinstance(deps, dict) or not deps:
        return {}, t("server '{server}' declares an empty 'deps' block.", server=server)
    try:
        kind = _validate_kind(deps, server)
        _validate_pins(deps, server)
        if kind == "npx":
            _check_node(server)
            return {}, None
        verified = _verified(deps, state_dir)
        if verified:
            return verified, None
        if not install:
            return {}, t(
                "server '{server}' depends on '{repo}' at rev '{rev}', which is not provisioned yet on this machine. The first lazy load will provision it; to check that git can reach it, run agent-sync apply.",
                server=server, repo=deps.get("repo", "?"), rev=deps.get("rev", "?"),
            )
        return _provision_git(deps, state_dir, server), None
    except ProvisionError as exc:
        return {}, str(exc)


def report_unsatisfied_deps(vault_data: Path, state_dir: Path | None = None) -> list[str]:
    """Doctor-side, offline-safe report of servers whose declared deps are missing."""
    state = Path(state_dir) if state_dir is not None else resolve_state_dir()
    path = mcp_manifest(vault_data)
    try:
        data = load_mcp_manifest(path)
    except Exception:
        return []
    problems: list[str] = []
    for name, srv in (data.get("servers") or {}).items():
        deps = srv.get("deps")
        if not isinstance(deps, dict):
            continue
        _, error = ensure_deps(deps, state, install=False, server=name)
        if error:
            problems.append(f"{name}: {error}")
    return problems
