#!/usr/bin/env python3
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
import importlib.util
import os
import platform
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.first_run import (
    align_now,
    commit_setup,
    remaining_problems,
    seed_skill_manifest,
    write_remotes,
    write_user_profile,
)
from nexgen_core.i18n import t
from nexgen_core.paths import resolve_home

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


def preflight() -> list[Finding]:
    """What's needed, and what's actually there."""
    hint = package_hint()
    found: list[Finding] = []

    found.append(Finding("git", shutil.which("git") is not None, True, f"{hint} git"))

    version_ok = sys.version_info >= MINIMUM_PYTHON
    wanted = ".".join(str(n) for n in MINIMUM_PYTHON)
    found.append(Finding(
        f"Python {platform.python_version()}", version_ok, True,
        t("needs Python {wanted} or later → {hint} python3", wanted=wanted, hint=hint),
    ))

    has_yaml = importlib.util.find_spec("yaml") is not None
    found.append(Finding("PyYAML", has_yaml, True, "pip install pyyaml"))

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


def scaffold(root: Path, write: bool) -> list[Finding]:
    """The folders and files a vault must have."""
    found: list[Finding] = []
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

    for name in SCAFFOLD_FILES:
        found.append(Finding(
            name, (root / name).is_file(), True,
            t("the clone looks incomplete: double-check you cloned the whole repository"),
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
    return found


def install_launchers(root: Path) -> str:
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

        installed = install_shims(scripts_dir=scripts_dir, home=resolve_home())
        return t("{count} commands installed in ~/.local/bin", count=len(installed))
    except Exception as exc:
        return t("commands not installed ({error})", error=exc)


def _ask(prompt: str, options: str) -> str:
    try:
        return input(f"  {prompt} [{options}]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return ""


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


def render(findings: list[Finding], title: str, stream=sys.stdout) -> int:
    """Prints a block of outcomes and returns how many requirements are missing."""
    c = _colour(stream)
    ok_sym = _sym("✓", "[OK]", stream)
    err_sym = _sym("✗", "[X]", stream)
    warn_sym = _sym("○", "[o]", stream)
    print(f"\n{c['bold']}{c['cyan']}{title}{c['reset']}", file=stream)
    missing = 0
    for f in findings:
        if f.ok:
            print(f"  {c['green']}{ok_sym}{c['reset']} {f.label}", file=stream)
        elif f.required:
            print(f"  {c['red']}{err_sym}{c['reset']} {f.label} — {f.remedy}", file=stream)
            missing += 1
        else:
            print(f"  {c['yellow']}{warn_sym}{c['reset']} {f.label} — {f.remedy}", file=stream)
    return missing


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="nexgen-bootstrap",
        description=t("Checks prerequisites, prepares the vault, and says what the next step is."),
    )
    parser.add_argument("--check", action="store_true",
                        help=t("Checks only: no questions and no writes"))
    parser.add_argument("--root", default=None, help=t("Vault root (default: the repository folder)"))
    args = parser.parse_args(argv)

    root = Path(args.root) if args.root else Path(__file__).resolve().parents[3]
    c = _colour(sys.stdout)

    print(f"{c['bold']}{c['cyan']}" + t("NeXgen Engine · first run") + c['reset'])
    print(f"{c['dim']}" + t("A Git vault for your assistants' configuration and memory.") + c['reset'])

    missing = render(preflight(), "1 · " + t("Prerequisites"))
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
    missing += render(found, "3 · " + t("Assistants found on this machine"))

    if missing:
        print(f"\n{c['red']}" + t("Something required is missing.") + f"{c['reset']} " + t("Fix it and rerun."))
        return 1

    if args.check:
        print(f"\n{c['bold']}{c['cyan']}4 · " + t("Next step") + c['reset'])
        print("  " + t("Everything required is in place. Run the installer without --check."))
        return 0

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
        print(f"  {c['green']}{ok_sym}{c['reset']} {step.label}")

    aligned, message = align_now()
    print(("  " + f"{c['green']}{ok_sym}{c['reset']} " if aligned else "  " + f"{c['red']}{err_sym}{c['reset']} ") + message)

    print(f"\n{c['bold']}{c['cyan']}6 · " + t("Where you are") + c['reset'])

    # Not "did the command return zero", but "is this machine actually set
    # up". They came apart during development, and the install said Ready
    # over an alignment that had produced nothing at all.
    count, problems = remaining_problems()

    if aligned and count == 0:
        print(f"  {c['green']}" + t("Ready.") + f"{c['reset']} " + t("Nothing else is required."))
        if mode == "LOCAL-FULL":
            print(f"  {c['dim']}→ " + t("the five connectors run here: 'nexgen stack up' starts them.") + c['reset'])
        elif mode == "CLOUD-SERVER":
            print(f"  {c['dim']}→ " + t("the services live on a server: see 03-INFRA/deploy/.") + c['reset'])
        print("\n  " + t("From now on: 'nexgen doctor' tells you if anything is wrong, "
                         "and 'nexgen update' brings in a new version."))
        print(f"  {c['dim']}"
              + t("Want the guided path too, to bring your own documents in? Open INIT.md.")
              + c['reset'])
        return 0

    print(f"  {c['yellow']}" + t("Installed. {count} thing(s) still need attention:", count=max(count, 0))
          + f"{c['reset']}")
    for line in problems[:5]:
        print(f"    - {line}")
    if count > 5:
        print(f"    {c['dim']}" + t("...and {more} more: run 'nexgen doctor'.", more=count - 5) + c['reset'])
    if not aligned:
        print("\n  " + t("The alignment stopped first: {reason}", reason=message))
    print("  " + t("Run 'nexgen doctor' for the whole list; most of it clears "
                   "with 'nexgen sync apply'."))
    return 1


if __name__ == "__main__":
    sys.exit(main())
