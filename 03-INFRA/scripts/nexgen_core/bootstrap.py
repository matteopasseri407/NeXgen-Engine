#!/usr/bin/env python3
"""First run: what's missing, what's there, and what's the next step.

This logic used to exist twice, in `install.sh` and `install.ps1`, kept in
step by hand. They had already drifted: one of them warned that jq is also
needed by the health scripts, the other didn't. Now it exists exactly once,
and the two installers are shells that find Python and hand off here.

It doesn't replace the agent-guided install: it does the mechanical part.
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
    """Generates the commands, if the engine lives in this clone."""
    scripts_dir = root / "03-INFRA" / "scripts"
    if not (scripts_dir / "nexgen_core").is_dir():
        return ""
    sys.path.insert(0, str(scripts_dir))
    try:
        from nexgen_core.shims import install_shims

        installed = install_shims(scripts_dir=scripts_dir)
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


def render(findings: list[Finding], title: str, stream=sys.stdout) -> int:
    """Prints a block of outcomes and returns how many requirements are missing."""
    c = _colour(stream)
    print(f"\n{c['bold']}{c['cyan']}{title}{c['reset']}", file=stream)
    missing = 0
    for f in findings:
        if f.ok:
            print(f"  {c['green']}✓{c['reset']} {f.label}", file=stream)
        elif f.required:
            print(f"  {c['red']}✗{c['reset']} {f.label} — {f.remedy}", file=stream)
            missing += 1
        else:
            print(f"  {c['yellow']}○{c['reset']} {f.label} — {f.remedy}", file=stream)
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
    found = [Finding(name, True) for name in clis] or [Finding(
        t("no CLI found"), False, True,
        t(
            "you need an assistant that can write files (Claude Code, Codex, OpenCode, Antigravity): "
            "a web chat can't do it"
        ),
    )]
    missing += render(found, "3 · " + t("Assistants found on this machine"))

    if missing:
        print(f"\n{c['red']}" + t("Something required is missing.") + f"{c['reset']} " + t("Fix it and rerun."))
        return 1

    if not args.check and sys.stdin.isatty():
        print(f"\n{c['bold']}{c['cyan']}4 · " + t("Which install do you want") + c['reset'])
        print(f"  {c['dim']}" + t("No file gets written: this is just a recommendation.") + c['reset'])
        profile, mode = guided_profile()
        print("\n  " + t("Profile:") + f" {c['bold']}{profile}{c['reset']} · " + t("Services:") + f" {c['bold']}{mode}{c['reset']}")
        if mode == "LOCAL-FULL":
            print(f"  {c['dim']}→ " + t("the five connectors run here: 'nexgen stack up' starts them.") + c['reset'])
        elif mode == "CLOUD-SERVER":
            print(f"  {c['dim']}→ " + t("the services live on a server: see 03-INFRA/deploy/.") + c['reset'])
        else:
            print(f"  {c['dim']}→ " + t("no services: native search, no remote automation.") + c['reset'])

    print(f"\n{c['bold']}{c['cyan']}5 · " + t("Next step") + c['reset'])
    # `--check` promises to write nothing, so it writes nothing. The
    # previous version installed the commands from here regardless, which
    # made its own promise false and, on a development clone, hijacked the
    # machine's commands toward that clone.
    if not args.check:
        note = install_launchers(root)
        if note:
            print(f"  {c['green']}✓{c['reset']} {note}")
    print(
        "\n  "
        + t("Open {file} and paste its contents into a command-line assistant\n"
            "  opened in this folder. It will ask you a few questions and mount the\n"
            "  connectors and skills.", file=f"{c['bold']}INIT.md{c['reset']}")
        + f"\n\n  {c['dim']}"
        + t("Then, to verify at any time: nexgen doctor")
        + c['reset']
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
