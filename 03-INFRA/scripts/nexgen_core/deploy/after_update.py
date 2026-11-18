"""Rebuild the stack a server's own update changed, right after the engine moved.

nexgen: one-job reason="decide whether an engine update moved the deployed stack and redeploy it there, without ever undoing an update that worked"
One job: answer two questions and act on them. Did this update touch a stack
under `03-INFRA/deploy/`, and is this the machine that runs it? Both yes means
the container is now the only half of the install on old code.

The failure policy is the point of the module: a rebuild that fails is raised,
so the update is undone whole. A stack this engine does not hold (another user
started it from its own copy, so there is no compose file or token here) is not
a failure: it is named in one line and left alone, and the doctor measures the
gap it leaves.
"""
from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

from nexgen_core.deploy import vault_mcp
from nexgen_core.i18n import t
from nexgen_core.processes import no_window_kwargs

#: The deployed stacks an update can move, by name, and the folder that owns them.
STACKS: dict[str, str] = {"vault-mcp": "03-INFRA/deploy/vault-mcp"}

GIT_TIMEOUT_SECONDS = 120.0


def _git(repo: Path, args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    """One git call, on the repository the update just moved."""
    try:
        return subprocess.run(
            ["git", "-C", str(repo), *args], capture_output=True, text=True,
            encoding="utf-8", errors="replace", check=False,
            timeout=GIT_TIMEOUT_SECONDS, **no_window_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise vault_mcp.DeployError(t("git could not answer ({error})", error=exc)) from exc


def moved_stacks(
    engine_repo: Path,
    previous_head: str,
    target_rev: str,
    *,
    run: Callable[[Path, Sequence[str]], subprocess.CompletedProcess[str]] = _git,
) -> list[str]:
    """The deployed stacks whose own files changed between two commits.

    Narrow on purpose: an update that touches the engine but not `deploy/` has
    nothing to rebuild, and rebuilding anyway would restart the vault write door
    on every release for no reason.
    """
    listing = run(engine_repo, ["diff", "--name-only", previous_head, target_rev, "--", *STACKS.values()])
    if listing.returncode != 0:
        detail = (listing.stderr or listing.stdout or f"exit {listing.returncode}").strip()
        raise vault_mcp.DeployError(t("the update could not tell which stacks moved ({detail})", detail=detail))
    changed = [line.strip() for line in listing.stdout.splitlines() if line.strip()]
    return [name for name, folder in STACKS.items() if any(path.startswith(f"{folder}/") for path in changed)]


def deploy_after_update(
    *,
    engine_repo: Path,
    vault_data: Path,
    state_dir: Path,
    previous_head: str,
    target_rev: str,
    out: Callable[[str], None] = print,
    runner: Callable[..., subprocess.CompletedProcess[str]] = vault_mcp.run_command,
    run_git: Callable[[Path, Sequence[str]], subprocess.CompletedProcess[str]] = _git,
) -> list[str]:
    """Redeploy the stacks this update moved, on the machine that runs them.

    Nothing happens on a workstation, which neither serves the containers nor
    should restart them: its doctor reads the server's answer instead.
    """
    from nexgen_core import roles
    from nexgen_core.paths import resolve_home

    if roles.declared_role(home=resolve_home(), vault_root=vault_data) != roles.SERVER_ROLE:
        return []
    stacks = moved_stacks(engine_repo, previous_head, target_rev, run=run_git)
    if not stacks:
        return []
    done = list(stacks)
    for name in stacks:
        out(t("\n{stack} changed in this update: redeploying it here.", stack=name))
        # The stack's own source moves with the engine, which is already merged:
        # `deploy` reads the new tree and backs up whatever it replaces.
        try:
            vault_mcp.deploy(
                engine_repo / "03-INFRA",
                state_dir=state_dir, out=out, runner=runner,
            )
        except vault_mcp.NotDeployedHere as exc:
            # Another user runs this container from its own copy: a rebuild from
            # here could only fail, and undoing the whole update for it left the
            # VPS on the old release (2026-11-18). The doctor measures the gap.
            out(t("{stack} is not deployed from this engine, so it was left as it is: {reason}", stack=name, reason=exc))
            out(t("Redeploy it where it runs: nexgen deploy vault-mcp --source <the folder with its compose file>"))
            done.remove(name)
    return done
