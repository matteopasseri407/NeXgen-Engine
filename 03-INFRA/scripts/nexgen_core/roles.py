"""Three roles for NeXgen Engine machine initialization.

One job: configure a machine into one of three distinct roles:
- server: always-on OpenClaw gateway on headless Linux (loopback + Tailscale Serve).
- node: PC connecting to an existing gateway, registering its agent via RPC.
- standalone: local PC running its own gateway.

Idempotent by contract: re-running any role on an already configured machine
causes no damage and maintains configuration alignment.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from nexgen_core.first_run import (
    align_now as _default_align,
    commit_setup,
    remaining_problems as _default_remaining_problems,
    seed_skill_manifest,
    write_remotes,
    write_user_profile,
)
from nexgen_core.i18n import t
from nexgen_core.paths import resolve_home


def _install_launchers_step(root: Path, out: Callable[[str], None]) -> None:
    scripts_dir = root / "03-INFRA" / "scripts"
    if (scripts_dir / "nexgen_core").is_dir():
        try:
            from nexgen_core.shims import install_shims

            installed = install_shims(scripts_dir=scripts_dir, home=resolve_home())
            out(t("{count} commands installed in ~/.local/bin", count=len(installed)))
        except Exception as exc:  # noqa: BLE001
            out(t("commands not installed ({error})", error=exc))


def _default_openclaw_setup(**kwargs: Any) -> int:
    from nexgen_core.openclaw.setup import run_setup

    return run_setup(**kwargs)


def run_standalone_role(
    root: Path,
    home: Path | None = None,
    setup_fn: Callable[..., int] | None = None,
    align_fn: Callable[[], tuple[bool, str]] | None = None,
    problems_fn: Callable[[], tuple[int, list[str]]] | None = None,
    out: Callable[[str], None] = print,
) -> int:
    """Standalone role: local PC with its own gateway."""
    out(t("NeXgen Engine · standalone role (local PC with own gateway)"))
    home_dir = resolve_home(home)
    profile = "MINIMAL"

    write_user_profile(
        root,
        profile=profile,
        clis=["claude", "codex", "opencode", "antigravity"],
        machines="primary (this one)",
    )
    write_remotes(root, profile=profile)
    seed_skill_manifest(root)
    _install_launchers_step(root, out)

    commit_setup(root, [
        root / "99-INDEX" / "USER-PROFILE.md",
        root / "03-INFRA" / "agent-universal-layer" / "sync" / "remotes.yaml",
        root / "03-INFRA" / "agent-universal-layer" / "skills" / "skills.manifest.yaml",
    ])

    run_setup = setup_fn or _default_openclaw_setup
    out(t("Configuring OpenClaw standalone gateway..."))
    setup_code = run_setup(yes=True, server=False, home=home_dir)
    if setup_code != 0:
        out(t("OpenClaw setup returned non-zero code {code}", code=setup_code))

    aligner = align_fn or _default_align
    aligned, message = aligner()
    out(t("Alignment: {msg}", msg=message))

    checker = problems_fn or _default_remaining_problems
    count, problems = checker()
    if count > 0:
        out(t("{count} problem(s) remain: run 'nexgen doctor'", count=count))
        return 1
    out(t("Standalone machine ready."))
    return 0


def run_server_role(
    root: Path,
    home: Path | None = None,
    yes_i_know: bool = False,
    setup_fn: Callable[..., int] | None = None,
    align_fn: Callable[[], tuple[bool, str]] | None = None,
    problems_fn: Callable[[], tuple[int, list[str]]] | None = None,
    out: Callable[[str], None] = print,
) -> int:
    """Server role: always-on OpenClaw gateway on headless Linux."""
    out(t("NeXgen Engine · server role (always-on headless Linux gateway)"))
    home_dir = resolve_home(home)
    profile = "MULTI"

    write_user_profile(root, profile=profile, clis=["openclaw"], machines="primary (server)")
    write_remotes(root, profile=profile)
    seed_skill_manifest(root)
    _install_launchers_step(root, out)

    commit_setup(root, [
        root / "99-INDEX" / "USER-PROFILE.md",
        root / "03-INFRA" / "agent-universal-layer" / "sync" / "remotes.yaml",
        root / "03-INFRA" / "agent-universal-layer" / "skills" / "skills.manifest.yaml",
    ])

    run_setup = setup_fn or _default_openclaw_setup
    out(t("Configuring OpenClaw server gateway (loopback + Tailscale Serve)..."))
    setup_code = run_setup(yes=True, server=True, yes_i_know=yes_i_know, home=home_dir)
    if setup_code != 0:
        out(t("OpenClaw server setup failed (exit code {code})", code=setup_code))
        return setup_code

    aligner = align_fn or _default_align
    aligned, message = aligner()
    out(t("Alignment: {msg}", msg=message))

    checker = problems_fn or _default_remaining_problems
    count, _ = checker()
    if count > 0:
        out(t("{count} problem(s) remain: run 'nexgen doctor'", count=count))
        return 1
    out(t("Server gateway ready."))
    return 0


def run_node_role(
    root: Path,
    home: Path | None = None,
    gateway_url: str | None = None,
    gateway_token: str | None = None,
    node_name: str | None = None,
    align_fn: Callable[[], tuple[bool, str]] | None = None,
    rpc_client_factory: Any | None = None,
    out: Callable[[str], None] = print,
) -> int:
    """Node role: connect to an existing gateway and register agent via RPC."""
    out(t("NeXgen Engine · node role (satellite PC connecting to an existing gateway)"))
    profile = "MINIMAL"

    write_user_profile(
        root,
        profile=profile,
        clis=["claude", "codex", "opencode", "antigravity"],
        machines="secondary (node)",
    )
    write_remotes(root, profile=profile)
    seed_skill_manifest(root)
    _install_launchers_step(root, out)

    aligner = align_fn or _default_align
    aligned, message = aligner()
    out(t("Synchronized CLI, skills and MCP from engine: {msg}", msg=message))

    name = node_name or "node"
    if gateway_url:
        out(t("Registering node agent '{name}' on gateway via protocol...", name=name))
        try:
            if rpc_client_factory:
                client = rpc_client_factory(url=gateway_url, token=gateway_token)
            else:
                from nexgen_core.openclaw.gateway_rpc import GatewayRpcClient

                client = GatewayRpcClient(url=gateway_url, token=gateway_token)
            patch = {
                "agents": {
                    "entries": {
                        name: {
                            "name": name,
                            "identity": {"name": name},
                            "tools": {"exec": {"host": "node", "node": name}},
                        },
                    },
                },
            }
            client.config_patch(patch=patch)
            out(t("Agent '{name}' registered on gateway via RPC.", name=name))
        except Exception as exc:  # noqa: BLE001
            out(t("Could not register agent on gateway via RPC ({error}); proceed with manual join.", error=exc))
    else:
        out(t("No gateway URL passed; use 'openclaw connect <url> --service --session-host' to attach."))

    out(t("Node machine ready."))
    return 0
