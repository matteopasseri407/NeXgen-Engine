#!/usr/bin/env python3
# nexgen: one-job reason="preflight prerequisites, interactive configuration, and role bootstrap"
"""First run: what's missing, what's there, and what's the next step.

This logic used to exist twice, in `install.sh` and `install.ps1`, kept in
step by hand. They had already drifted: one of them warned that jq is also
needed by the health scripts, the other didn't. Now it exists exactly once,
and the two installers are shells that find Python and hand off here.

It goes all the way: prerequisites, vault structure, three questions, the
profile written from the answers, the authoritative remote named, and the
first alignment run — ending on one outcome, ready or what is missing and
how to fix it. `INIT.md` stays as the richer guided path for whoever wants
it, and as the only step that genuinely needs a conversation: offering to
ingest the user's own documents.

`--check` remains exactly what it says: it writes nothing at all.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import bootstrap_runtime  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.bootstrap_secrets import _ask, wire_absorbed_secrets  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.first_run import (  # noqa: E402 - sys.path shim for cloned checkout
    align_now,
    commit_setup,
    remaining_problems,
    seed_skill_manifest,
    write_remotes,
    write_user_profile,
)
from nexgen_core.i18n import t  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.marks import safe_text  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.paths import installed_as_package, resolve_home  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.platforms import installed_desktop_apps  # noqa: E402 - sys.path shim for cloned checkout

#: The Python version below which the engine won't start.
MINIMUM_PYTHON = (3, 11)

#: The folders a vault must have. If missing, they get created.
SCAFFOLD_DIRS = ("01-NOTES", "02-PROJECTS", "04-NOW", "99-INDEX", "99-SECRETS")

#: The files without which the clone isn't complete. If missing, we just say so.
SCAFFOLD_FILES = (
    "INIT.md",
    "00-START-HERE.md",
    "99-INDEX/USER-PROFILE.md",
    "03-INFRA/agent-universal-layer/instructions/AGENTS.md",
)


@dataclass(frozen=True)
class Finding:
    """A preflight outcome: what was checked, how it went, and the remedy."""

    label: str
    ok: bool
    required: bool = True
    remedy: str = ""


def _colour(stream) -> dict[str, str]:
    """Colors only if someone can actually see them."""
    if not stream.isatty() or os.environ.get("NO_COLOR"):
        return dict.fromkeys(("bold", "dim", "reset", "green", "red", "yellow", "cyan"), "")
    return {
        "bold": "\033[1m", "dim": "\033[2m", "reset": "\033[0m",
        "green": "\033[32m", "red": "\033[31m", "yellow": "\033[33m", "cyan": "\033[36m",
    }


def package_hint() -> str:
    """How to install a package on this system."""
    system = platform.system()
    if system == "Darwin":
        if shutil.which("brew"):
            return "brew install"
        return t("install Homebrew (https://brew.sh), then: brew install")
    if system == "Windows":
        return "winget install"
    for manager, command in (("apt", "sudo apt install"), ("dnf", "sudo dnf install"),
                             ("pacman", "sudo pacman -S"), ("zypper", "sudo zypper install")):
        if shutil.which(manager):
            return command
    return t("your system's package manager")


def preflight(check: bool = False) -> list[Finding]:
    """What's needed, and what's actually there.

    `check` decides the severity of the one requirement the installer answers for
    itself: with `--check` nothing is created, so a missing PyYAML says what the
    real run does and never blocks a read-only pass.
    """
    hint = package_hint()
    found: list[Finding] = []

    found.append(Finding("git", shutil.which("git") is not None, True, f"{hint} git"))

    version_ok = sys.version_info >= MINIMUM_PYTHON
    wanted = ".".join(str(n) for n in MINIMUM_PYTHON)
    found.append(Finding(
        f"Python {platform.python_version()}", version_ok, True,
        t("needs Python {wanted} or later → {hint} python3", wanted=wanted, hint=hint),
    ))

    # Not `pip install pyyaml`: on Ubuntu 24.04 that is refused (PEP 668) and
    # never worked. The engine builds its own environment instead.
    found.append(Finding(
        "PyYAML", bootstrap_runtime.yaml_available(), required=not check,
        remedy=t("the installer creates it in the engine's own Python environment "
                 "(on Ubuntu this needs 'sudo apt install python3-venv')"),
    ))

    # The rest is useful but not blocking: reporting it as blocking would
    # make people reinstall things they don't need.
    found.append(Finding(
        "node/npx", shutil.which("npx") is not None, False,
        t("only needed to mount MCP connectors or skills installed via npx"),
    ))
    found.append(Finding(
        "gpg", shutil.which("gpg") is not None, False,
        t("only needed if you keep encrypted secrets in 99-SECRETS/"),
    ))
    found.append(Finding(
        "docker", shutil.which("docker") is not None, False,
        t("only needed for the full install on this machine (nexgen stack up)"),
    ))
    return found


TEMPLATE_URL = "https://github.com/matteopasseri407/NeXgen-Engine.git"


def scaffold(root: Path, write: bool) -> list[Finding]:
    """The folders and files a vault must have.

    A folder that lacks the template files is not a vault yet, and nothing is created in it: the folders would only
    make it non-empty and block the `git clone` of the template that it needs next.
    """
    found: list[Finding] = []
    template_missing = [name for name in SCAFFOLD_FILES if not (root / name).is_file()]
    write = write and not template_missing
    for name in SCAFFOLD_DIRS:
        target = root / name
        if target.is_dir():
            found.append(Finding(f"{name}/", True))
            continue
        if write:
            target.mkdir(parents=True, exist_ok=True)
            (target / ".gitkeep").touch()
            found.append(Finding(t("{name}/ (created)", name=name), True))
        else:
            found.append(Finding(f"{name}/", False, True, t("rerun without --check to create it")))

    nothing_there = len(template_missing) == len(SCAFFOLD_FILES)
    for name in SCAFFOLD_FILES:
        found.append(Finding(
            name, (root / name).is_file(), True,
            t("this folder is not a vault yet: clone the template into it first: git clone {url} {root}", url=TEMPLATE_URL, root=root)
            if nothing_there
            else t("the clone looks incomplete: double-check you cloned the whole repository"),
        ))
    return found


def detect_clis(home: Path | None = None) -> list[str]:
    """Which command-line assistants are on this machine.

    We check for the binary, not a folder: inferring "installed" from a
    folder the layer itself creates is a defect that's already bitten us
    twice.
    """
    home_dir = resolve_home(home)
    found = [name for name in ("claude", "codex", "opencode") if shutil.which(name)]
    if shutil.which("agy") or (home_dir / ".gemini" / "settings.json").is_file():
        found.append("antigravity")
    # A desktop app shares its config with a CLI target: ChatGPT with Codex
    # (~/.codex), Antigravity IDE with Antigravity (~/.gemini). Its presence
    # alone is enough to configure that target, so an app-only machine is not
    # skipped. The target is listed once, however many of its surfaces exist.
    for app in installed_desktop_apps():
        for target in app.targets:
            if target not in found:
                found.append(target)
    return found


def _display_name(name: str) -> str:
    """A live server name safe to print: JSON keys can carry anything,
    including ANSI escapes left by a third-party extension installer."""
    import re

    return re.sub(r"[^\x20-\x7e]", "?", name)


def _read_desktop_servers(home: Path | None = None) -> dict[str, Any] | None:
    """The app's live server table, or None when absent or unreadable.

    The one reader for the install path: detection and stubs share it, so
    they can never disagree about which file they read.
    """
    try:
        from nexgen_core.paths import claude_desktop_config

        data = json.loads(claude_desktop_config(resolve_home(home)).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return {}
    live = data.get("mcpServers", {})
    return dict(live) if isinstance(live, dict) else {}


def desktop_app_orphans(root: Path, home: Path | None = None) -> list[str]:
    """Live Claude Desktop app servers missing from the vault manifest.

    Read-only: an absent, malformed, or non-object app config means none,
    never an error. An unreadable manifest means every live server counts,
    because claiming "already absorbed" without reading the manifest would
    be the quiet wrong.
    """
    live = _read_desktop_servers(home)
    if not live:
        return []
    names = set(live)
    manifest = root / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml"
    try:
        from nexgen_core.config import load_mcp_manifest

        declared = set(load_mcp_manifest(manifest).get("servers", {}))
    except Exception:  # noqa: BLE001 - unreadable manifest: everything live is new
        return sorted(names)
    return sorted(names - declared)


def print_desktop_stubs(names: list[str], home: Path | None = None) -> dict[str, dict[str, Any]]:
    """Review stubs for live Desktop-app servers, printed, never applied.

    Applying during the install would write manifest entries the user has
    not reviewed; the stubs below go under 'servers:' by hand, or with
    'nexgen import --from claude-desktop --apply'. A stub targets the
    Claude CLI (the Desktop renderer resolves from there); opting it into
    the app itself is a separate manual 'desktop_allow: true', only for
    read-only servers.
    """
    from nexgen_core import renderer_cli

    live = _read_desktop_servers(home)
    if not live:
        print("  " + t("the Desktop config is gone: nothing to absorb."))
        return {}
    adopted = _adopted_entries(names, live)
    skipped_names = [n for n in names if n in live and n not in adopted]
    for name in skipped_names:
        print("  " + t("skipping {name}: refused by validation.", name=_display_name(name)))
    stubs = []
    for name in sorted(adopted):
        stubs.append(renderer_cli._emit_manifest_stub(name, adopted[name]))
    if not stubs:
        print("  " + t("nothing left to absorb."))
        return adopted
    print("  " + t("manifest.yaml STUB below -- review it, adjust it, then place it under 'servers:'."))
    print("  " + t("Opting a server into the Desktop app itself is a separate manual 'desktop_allow: true', only for read-only servers."))
    print("servers:")
    for stub in stubs:
        print(stub)
    print("  " + t("Or run 'nexgen import --from claude-desktop --apply' to add them (backup + re-validation)."))
    return adopted


#: A bare ${NAME} reference in an absorbed entry: the value must come from
#: the store. A ${NAME:-default} carries its own fallback and needs nothing.
_EXACT_REF_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _referenced_secret_names(live: dict[str, Any]) -> dict[str, list[str]]:
    """${NAME} references per adopted server, scanning env, args, url and
    command. Only exact references count: defaulted ones resolve alone.

    `live` must be adopted entries (`_adopt_entry`), not the raw Desktop
    config: the app stores literal values, and the ${NAME}s only exist
    after adoption scrubs them. Scanning raw values would find nothing in
    exactly the typical case.
    """
    refs: dict[str, set[str]] = {}
    for server, spec in live.items():
        if not isinstance(spec, dict):
            continue
        texts: list[str] = []
        for key in ("command", "url"):
            value = spec.get(key)
            if isinstance(value, str):
                texts.append(value)
            elif isinstance(value, list):
                texts.extend(v for v in value if isinstance(v, str))
        raw_args = spec.get("args")
        if isinstance(raw_args, str):
            texts.append(raw_args)
        elif isinstance(raw_args, list):
            texts.extend(v for v in raw_args if isinstance(v, str))
        env = spec.get("env")
        if isinstance(env, dict):
            texts.extend(v for v in env.values() if isinstance(v, str))
        for text in texts:
            for name in _EXACT_REF_RE.findall(text):
                refs.setdefault(name, set()).add(server)
    return {name: sorted(servers) for name, servers in refs.items()}


def _adopted_entries(names: list[str], live: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Adopted stubs for the named live servers, skipping invalid ones.

    Shared by the printed stubs and the wiring step so both work on the
    same transformation and the referenced names coincide by construction.
    """
    from nexgen_core import renderer_cli

    adopted: dict[str, dict[str, Any]] = {}
    for name in names:
        if name not in live:
            continue
        entry = renderer_cli._adopt_entry("claude-desktop", live[name])
        if renderer_cli._validate_stub_entry(name, entry) is not None:
            continue
        adopted[name] = entry
    return adopted


def _masked_servers(adopted: dict[str, dict[str, Any]]) -> list[str]:
    """Adopted servers carrying a <hidden> placeholder: secrets were cut
    out of args or URLs, so no ${NAME} will ever wire them. They need a
    manual fix, and the closing summary must say so instead of letting a
    runtime 401 be the first signal.
    """
    masked = []

    def carries(value: Any) -> bool:
        if isinstance(value, str):
            return "<hidden>" in value
        if isinstance(value, list):
            return any(carries(v) for v in value)
        if isinstance(value, dict):
            return any(carries(v) for v in value.values())
        return False

    for name, entry in adopted.items():
        if carries(entry):
            masked.append(name)
    return sorted(masked)


def install_launchers(root: Path, home: Path | None = None) -> str:
    """Generates the commands, if the engine lives in this clone.

    The home is asked for, never assumed. Without this the installer wrote
    into the real `~/.local/bin` even under a sandbox home — which is how a
    throwaway test left this machine's commands pointing at a directory in
    /tmp that was then deleted. Three separate incidents, one cause.
    """
    scripts_dir = root / "03-INFRA" / "scripts"
    if not (scripts_dir / "nexgen_core").is_dir():
        return ""
    sys.path.insert(0, str(scripts_dir))
    try:
        from nexgen_core.shims import install_shims

        installed = install_shims(scripts_dir=scripts_dir, home=resolve_home(home))
        return t("{count} commands installed in ~/.local/bin", count=len(installed))
    except Exception as exc:  # noqa: BLE001 - bootstrap reports, never raises
        return t("commands not installed ({error})", error=exc)


def guided_profile() -> tuple[str, str]:
    """Two questions, and the resulting profile. Writes nothing."""
    clis = _ask(t("How many CLIs will you use?"), "1 / 2+")
    machines = _ask(t("How many machines do you want kept in sync?"), "1 / 2+")
    arch = _ask(t("Where do the services live?"), t("N=none / H=here / S=on a server"))

    profile = "MULTI" if ("2" in clis or "2" in machines) else "MINIMAL"
    mode = {"h": "LOCAL-FULL", "s": "CLOUD-SERVER"}.get(arch[:1], "LOCAL-ONLY")
    return profile, mode


def _sym(char: str, fallback: str, stream: object) -> str:
    try:
        char.encode(getattr(stream, "encoding", None) or "utf-8")
        return char
    except (UnicodeEncodeError, TypeError):
        return fallback


def render(findings: list[Finding], title: str, stream=None) -> int:
    """Prints a block of outcomes and returns how many requirements are missing.

    The default stream is looked up when called: bound at import it kept pointing at whatever
    `sys.stdout` was then, and failed with "I/O operation on closed file" once that was replaced.
    """
    stream = stream if stream is not None else sys.stdout
    c = _colour(stream)
    ok_sym = _sym("✓", "[OK]", stream)
    err_sym = _sym("✗", "[X]", stream)
    warn_sym = _sym("○", "[o]", stream)
    print(safe_text(f"\n{c['bold']}{c['cyan']}{title}{c['reset']}", stream), file=stream)
    missing = 0
    for f in findings:
        if f.ok:
            print(safe_text(f"  {c['green']}{ok_sym}{c['reset']} {f.label}", stream), file=stream)
        elif f.required:
            print(safe_text(f"  {c['red']}{err_sym}{c['reset']} {f.label} — {f.remedy}", stream), file=stream)
            missing += 1
        else:
            print(safe_text(f"  {c['yellow']}{warn_sym}{c['reset']} {f.label} — {f.remedy}", stream), file=stream)
    return missing


def _role_args(parser: argparse.ArgumentParser) -> None:
    """The three roles, and the four answers only the node role reads."""
    parser.add_argument("--role", choices=["server", "node", "standalone"], default=None,
                        help=t("Role for this machine: server, node, or standalone"))
    parser.add_argument("--target", default=None,
                        help=t("Gateway join URL or setup code (for node role)"))
    parser.add_argument("--join", default=None,
                        help=t("Gateway join URL or setup code (for node role)"))
    parser.add_argument("--force", action="store_true",
                        help=t("Force role transition, e.g. switching gateway on an enrolled node"))
    parser.add_argument("--gateway-url", default=None,
                        help=t("Gateway WebSocket or join URL (for node role)"))
    parser.add_argument("--gateway-token", default=None,
                        help=t("Gateway authentication token (for node role)"))
    parser.add_argument("--node-name", default=None,
                        help=t("Display name for this node (for node role)"))
    parser.add_argument("--exec-policy", dest="exec_policy", default=None, choices=["full"],
                        help=t("For the node role: also align this PC's exec approvals with 'full' (no prompt, "
                               "fallback full), so the gateway's commands are not stopped on a machine where nobody "
                               "can answer an approval"))


def _main_impl(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="nexgen-bootstrap",
        description=t("Checks prerequisites, prepares the vault, and says what the next step is."),
    )
    parser.add_argument("--check", action="store_true",
                        help=t("Checks only: no questions and no writes"))
    _role_args(parser)
    parser.add_argument("--local", action="store_true",
                        help=t("Single machine: no questions, no secrets, no remote required. "
                               "Profile MINIMAL, services LOCAL-ONLY, then alignment and doctor"))
    parser.add_argument("--root", default=None, help=t("Vault root (default: the repository folder)"))
    args = parser.parse_args(argv)

    if args.root:
        root = Path(args.root)
    elif installed_as_package():
        # The default root is the repository folder. An installed package has none: the
        # path computed from this file points inside the tool's virtual environment, and
        # the scaffold used to be written there (even with --check, which did not exist).
        print(t(
            "This engine is installed as a package, so there is no repository folder to use as the vault. "
            "Pass --root PATH (for example ~/KnowledgeVault)."
        ), file=sys.stderr)
        return 2
    else:
        root = Path(__file__).resolve().parents[3]
    c = _colour(sys.stdout)

    print(f"{c['bold']}{c['cyan']}" + t("NeXgen Engine · first run") + c['reset'])
    print(f"{c['dim']}" + t("A Git vault for your assistants' configuration and memory.") + c['reset'])

    # A clean Ubuntu has python3 without PyYAML, and its pip refuses to install
    # one: build the engine's own environment first, then run this same bootstrap
    # inside it. A --check pass writes nothing, so there it only reports.
    stopped = bootstrap_runtime.provision_before_prerequisites(
        list(sys.argv[1:] if argv is None else argv), check=args.check,
    )
    if stopped is not None:
        return stopped

    missing = render(preflight(check=args.check), "1 · " + t("Prerequisites"))
    missing += render(scaffold(root, write=not args.check), "2 · " + t("Vault structure"))

    clis = detect_clis()
    # Not a missing requirement any more. The engine's job is to configure
    # assistants; installing it before any of them exist is a legitimate
    # order, and the next alignment picks up whatever appears later. Making
    # this fatal was part of the same assumption as "paste INIT.md into an
    # agent": that you already had one.
    found = [Finding(name, True) for name in clis] or [Finding(
        t("no assistant found yet"), False, required=False,
        remedy=t(
            "install Claude Code, Codex, OpenCode or Antigravity when you want one: "
            "the engine will configure it on its own from then on"
        ),
    )]
    desktop_new = desktop_app_orphans(root)
    if desktop_new:
        found.append(Finding(
            t("Claude Desktop app holds {count} MCP server(s) outside the manifest: {names}",
              count=len(desktop_new), names=", ".join(_display_name(n) for n in desktop_new)),
            False, required=False,
            remedy=t("absorb them with 'nexgen import --from claude-desktop'"),
        ))
    missing += render(found, "3 · " + t("Assistants found on this machine"))

    if missing:
        print(f"\n{c['red']}" + t("Something required is missing.") + f"{c['reset']} " + t("Fix it and rerun."))
        return 1

    if args.role and bootstrap_runtime.role_plan_ready(args.check):
        from nexgen_core import roles

        if args.role == "server":
            return roles.run_server_role(root, check=args.check, out=print)
        if args.role == "node":
            return roles.run_node_role(
                root,
                target=args.target,
                join=args.join,
                gateway_url=args.gateway_url,
                node_name=args.node_name,
                exec_policy=args.exec_policy,
                check=args.check,
                force=args.force,
                out=print,
            )
        if args.role == "standalone":
            return roles.run_standalone_role(root, check=args.check, out=print)

    if args.check:
        print(f"\n{c['bold']}{c['cyan']}4 · " + t("Next step") + c['reset'])
        print("  " + t("Everything required is in place. Run the installer without --check."))
        return 0

    if args.local:
        # The no-conversation install: one machine, no remote, no secrets,
        # no questions. Everything downstream is the same code the guided
        # path runs, with the answers already decided — which is why the
        # alignment and the final doctor verdict below are identical.
        profile, mode = "MINIMAL", "LOCAL-ONLY"
        print(f"\n{c['bold']}{c['cyan']}4 · " + t("Local install") + c['reset'])
        print("  " + t("Profile:") + f" {c['bold']}{profile}{c['reset']} · "
              + t("Services:") + f" {c['bold']}{mode}{c['reset']}")
    else:
        if not sys.stdin.isatty():
            # Nobody to answer the questions. Do the part that needs no answers
            # and say plainly what was skipped, rather than assuming defaults for
            # someone who is not there.
            note = install_launchers(root)
            if note:
                ok_sym = _sym("✓", "[OK]", sys.stdout)
                print(f"\n{c['green']}{ok_sym}{c['reset']} {note}")
            print("\n" + t("Not a terminal: the questions were skipped. "
                           "Run 'nexgen sync apply' when you are ready."))
            return 0

        print(f"\n{c['bold']}{c['cyan']}4 · " + t("Three questions") + c['reset'])
        profile, mode = guided_profile()
        print("\n  " + t("Profile:") + f" {c['bold']}{profile}{c['reset']} · "
              + t("Services:") + f" {c['bold']}{mode}{c['reset']}")

        if desktop_new:
            # Guided terminal only: --local promises no questions, and the
            # non-tty path returned earlier. Read-only either way. Both the
            # detection above and the stubs below resolve the effective home
            # (main never passes an override), so they always read one file.
            answer = _ask(
                t("Absorb the {count} Claude Desktop MCP server(s) into the manifest now (review stub, nothing applied)?",
                  count=len(desktop_new)),
                "y / N",
            )
            if answer.startswith("y"):
                adopted = print_desktop_stubs(desktop_new)
                refs = _referenced_secret_names(adopted)
                masked = _masked_servers(adopted)
                if refs or masked:
                    wire_absorbed_secrets(root, refs, masked)

    print(f"\n{c['bold']}{c['cyan']}5 · " + t("Setting it up") + c['reset'])
    steps: list[Finding] = []

    _written, message = write_user_profile(
        root, profile=profile, clis=clis,
        machines="primary (this one)" if profile == "MINIMAL" else "primary (this one), secondary",
    )
    steps.append(Finding(message, True, required=False))

    _written, message = write_remotes(root, profile=profile)
    steps.append(Finding(message, True, required=False))

    _written, message = seed_skill_manifest(root)
    steps.append(Finding(message, True, required=False))

    note = install_launchers(root)
    if note:
        steps.append(Finding(note, True, required=False))

    # The alignment refuses to work on a vault with uncommitted changes, and
    # it is right to. These files are the vault's content, so they belong in
    # its history before the alignment looks at it.
    _written, message = commit_setup(root, [
        root / "99-INDEX" / "USER-PROFILE.md",
        root / "03-INFRA" / "agent-universal-layer" / "sync" / "remotes.yaml",
        root / "03-INFRA" / "agent-universal-layer" / "skills" / "skills.manifest.yaml",
    ])
    steps.append(Finding(message, True, required=False))

    ok_sym = _sym("✓", "[OK]", sys.stdout)
    err_sym = _sym("✗", "[X]", sys.stdout)
    for step in steps:
        print(safe_text(f"  {c['green']}{ok_sym}{c['reset']} {step.label}"))

    aligned, message = align_now()
    print(safe_text(("  " + f"{c['green']}{ok_sym}{c['reset']} " if aligned else "  " + f"{c['red']}{err_sym}{c['reset']} ") + message))

    print(safe_text(f"\n{c['bold']}{c['cyan']}6 · " + t("Where you are") + c['reset']))

    # Not "did the command return zero", but "is this machine actually set
    # up". They came apart during development, and the install said Ready
    # over an alignment that had produced nothing at all.
    count, problems = remaining_problems()

    if aligned and count == 0:
        print(safe_text(f"  {c['green']}" + t("Ready.") + f"{c['reset']} " + t("Nothing else is required.")))
        if mode == "LOCAL-FULL":
            print(safe_text(f"  {c['dim']}→ " + t("the local services run here: 'nexgen stack up' starts them.") + c['reset']))
        elif mode == "CLOUD-SERVER":
            print(safe_text(f"  {c['dim']}→ " + t("the services live on a server: see 03-INFRA/deploy/.") + c['reset']))
        print(safe_text("\n  " + t("From now on: 'nexgen doctor' tells you if anything is wrong, "
                                "and 'nexgen update' brings in a new version.")))
        print(safe_text(f"  {c['dim']}"
              + t("Want the guided path too, to bring your own documents in? Open INIT.md.")
              + c['reset']))
        return 0

    print(safe_text(f"  {c['yellow']}" + t("Installed. {count} thing(s) still need attention:", count=max(count, 0))
          + f"{c['reset']}"))
    for line in problems[:5]:
        print(safe_text(f"    - {line}"))
    if count > 5:
        print(safe_text(f"    {c['dim']}" + t("...and {more} more: run 'nexgen doctor'.", more=count - 5) + c['reset']))
    if not aligned:
        print(safe_text("\n  " + t("The alignment stopped first: {reason}", reason=message)))
    print(safe_text("  " + t("Run 'nexgen doctor' for the whole list; most of it clears "
                   "with 'nexgen sync apply'.")))
    return 1


def main(argv: list[str] | None = None) -> int:
    """Checks prerequisites, prepares the vault, and says what the next step is."""
    return _main_impl(argv)


if __name__ == "__main__":
    sys.exit(main())
