"""Three roles for NeXgen Engine machine initialization.

One job: configure a machine into one of three distinct roles:
- server: always-on OpenClaw gateway on headless Linux (loopback + Tailscale Serve).
- node: PC connecting to an existing gateway, registering its agent via RPC.
- standalone: local PC running its own gateway.

Idempotent by contract: re-running any role on an already configured machine
causes no damage and maintains configuration alignment.

The role is also recorded on the machine (`declare_role`), because the rest of
the engine has to know whether this machine is the always-on one: on a server
the vault's clone is read-only, and a marker kept only inside the vault would be
reverted by the next pull that makes it read-only in the first place.
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from nexgen_core.files import atomic_write_text
from nexgen_core.first_run import (
    align_now as _default_align,
    commit_setup,
    remaining_problems as _default_remaining_problems,
    seed_skill_manifest,
    write_remotes,
    write_user_profile,
)
from nexgen_core.i18n import t
from nexgen_core.openclaw.node_attach import (
    DEFAULT_NODE_ATTACH_TIMEOUT_S,
    FULL_EXEC_POLICY,
    apply_exec_policy,
)
from nexgen_core.paths import resolve_home, resolve_state_dir

#: The three roles, as the words the installer accepts and this module records.
SERVER_ROLE = "server"
NODE_ROLE = "node"
STANDALONE_ROLE = "standalone"
ROLES = (SERVER_ROLE, NODE_ROLE, STANDALONE_ROLE)

#: The `machines` value the server role fills into the user profile. It is the
#: older half of the record: machines configured before the role file existed
#: carry only this, and it is still read back as a fallback.
SERVER_MACHINES = "primary (server)"
_SERVER_PROFILE_MARKER = re.compile(
    rf"^- \*\*machines\*\*: `{re.escape(SERVER_MACHINES)}`", re.MULTILINE
)


def role_record_path(home: Path | None = None) -> Path:
    """Where this machine's role is recorded: machine-local state, never synced."""
    return resolve_state_dir(home) / "nexgen" / "role.json"


def declare_role(role: str, home: Path | None = None) -> None:
    """Records the role that configured this machine.

    Failing to write it must not fail the install: the role still ran, and the
    profile carries the older marker for the server one.
    """
    if role not in ROLES:
        raise ValueError(f"unknown role {role!r}; expected one of {ROLES}")
    path = role_record_path(home)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, json.dumps({"role": role}, indent=2) + "\n")
    except OSError as exc:  # nexgen: allow reason="a machine that cannot record its role still finished the role; the profile is the fallback"
        raise OSError(f"cannot record the role in {path} ({type(exc).__name__})") from exc


def declared_role(home: Path | None = None, vault_root: Path | None = None) -> str | None:
    """The role that configured this machine, or None when nothing says.

    The role file wins, because it is the machine's own answer and survives a
    vault whose files are replaced by a pull. The profile's `machines` marker
    is the fallback for machines installed before that file existed.
    """
    try:
        raw = json.loads(role_record_path(home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = None
    role = raw.get("role") if isinstance(raw, dict) else None
    if isinstance(role, str) and role in ROLES:
        return role
    if vault_root is not None:
        profile = Path(vault_root) / "99-INDEX" / "USER-PROFILE.md"
        try:
            text = profile.read_text(encoding="utf-8")
        except OSError:  # nexgen: allow reason="no readable profile means this older marker says nothing, and the role file already answered"
            return None
        if _SERVER_PROFILE_MARKER.search(text):
            return SERVER_ROLE
    return None


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

    declare_role(STANDALONE_ROLE, home_dir)
    write_user_profile(
        root,
        profile=profile,
        clis=["claude", "codex", "opencode", "antigravity", "openclaw"],
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

    declare_role(SERVER_ROLE, home_dir)
    write_user_profile(root, profile=profile, clis=["openclaw"], machines=SERVER_MACHINES)
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
    target: str | None = None,
    gateway_url: str | None = None,
    node_name: str | None = None,
    align_fn: Callable[[], tuple[bool, str]] | None = None,
    runner: Callable[[list[str]], Any] | None = None,
    wait_probe_fn: Callable[[], bool] | None = None,
    timeout_s: float = DEFAULT_NODE_ATTACH_TIMEOUT_S,
    exec_policy: str | None = None,
    exec_runner: Callable[[list[str], str], Any] | None = None,
    out: Callable[[str], None] = print,
) -> int:
    """Node role: connect to an existing gateway as a headless node host.

    `exec_policy` is the explicit "and this PC never stops on an approval" answer:
    without it the node's own exec approvals are not touched, because they are the
    person's. With it, the documented never-prompt body is written locally once the
    node is attached, and the summary says so.
    """
    out(t("NeXgen Engine · node role (satellite PC connecting to an existing gateway)"))
    if exec_policy and exec_policy != FULL_EXEC_POLICY:
        out(t("Unknown exec policy '{policy}': this role applies only '{only}'.",
              policy=exec_policy, only=FULL_EXEC_POLICY))
        return 2
    profile = "MINIMAL"

    declare_role(NODE_ROLE, home)
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
    join_target = target or gateway_url
    if join_target:
        joined = _join_gateway(join_target, name, home, runner, wait_probe_fn, timeout_s, out)
        if not joined:
            return 1
    else:
        _join_instructions(out)

    _squad_hint(name, out)
    if exec_policy and not apply_exec_policy(runner=exec_runner, out=out):
        out(t("The node is attached, but its exec approvals still stop on a prompt nobody answers."))
        return 1
    out(t("Node machine ready."))
    return 0


def _join_gateway(
    join_target: str,
    name: str,
    home: Path | None,
    runner: Callable[[list[str]], Any] | None,
    wait_probe_fn: Callable[[], bool] | None,
    timeout_s: float,
    out: Callable[[str], None],
) -> bool:
    """Connects this PC as a service of the gateway, and waits for it to answer."""
    from nexgen_core.openclaw.node_attach import (
        check_node_connected,
        connect_node_service,
        wait_for_node_connection,
    )

    if not connect_node_service(join_target, home=home, runner=runner, out=out):
        return False
    probe = wait_probe_fn or (lambda: check_node_connected(runner=runner))
    connected, _ = wait_for_node_connection(probe, node_name=name, timeout_s=timeout_s, emit=out)
    return connected


def _join_instructions(out: Callable[[str], None]) -> None:
    """No join code: what to run on the gateway, and what to run here once there is one."""
    out(t("No join code passed. Mint a single-use join code on the gateway host:"))
    out(t("  openclaw devices join-code"))
    out(t("Then connect this node as a service with:"))
    out(t("  openclaw connect <join-url> --service --session-host"))


def _squad_hint(name: str, out: Callable[[str], None]) -> None:
    """The entry the server's squad needs, so work can be assigned to this PC."""
    out(t("To assign work to this node in your squad, add this entry to squad.yaml on the server:"))
    out(f"  agents:\n    {name}:\n      tools:\n        exec:\n          host: node\n          node: {name}")
