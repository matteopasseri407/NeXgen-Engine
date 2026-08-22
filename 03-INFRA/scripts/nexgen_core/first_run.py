#!/usr/bin/env python3
"""Finishing the install without an assistant in the loop.

The entry paradox this removes: to install NeXgen you needed a working AI
assistant, because the last step of the install said to paste `INIT.md` into
a CLI that can edit files. Someone who has not set one up yet stopped at the
first move, and a company does not paste documents into agents — it wants an
installer.

Reading `INIT.md`'s seven steps against what the code already does, only one
of them genuinely needs a conversation: offering to ingest the user's own
documents, which is declared optional. The other six are mechanical — ask
three questions, fill a profile, name the authoritative remote, create the
folders, check the prerequisites, run the alignment — and mechanical work
belongs in code. `INIT.md` stays, as the richer guided path for whoever
wants it.

Everything here is idempotent and additive. A profile someone has already
filled in is never overwritten: the installer fills placeholders, and where
it finds an answer it leaves it alone and says so.
"""
from __future__ import annotations

import os
import platform
import re
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.i18n import t

#: The line in the shipped template that exists only to instruct an agent.
#: Once the installer has filled the profile itself, it is a leftover.
_AGENT_NOTE_RE = re.compile(
    r"^> \*\*NOTE FOR THE INSTALLER AGENT\*\*.*?\n\n", re.MULTILINE | re.DOTALL
)

#: A placeholder is a bracketed span inside backticks. Matching only that
#: shape means prose containing brackets is never touched by accident.
_PLACEHOLDER = r"`\[[^`]*\]`"


def _section_bounds(text: str, heading: str) -> tuple[int, int]:
    """Where a `## heading` section starts and ends.

    Field names repeat across sections and mean different things in each:
    "Primary workstation" asks for hardware under Host Awareness and for an
    absolute path under Knowledge Vault. Filling by name alone wrote the
    machine's specs into the field that wanted the vault's path.
    """
    marker = f"\n## {heading}"
    start = text.find(marker)
    if start < 0:
        return -1, -1
    nxt = text.find("\n## ", start + len(marker))
    return start, nxt if nxt > 0 else len(text)


def _fill(text: str, field: str, value: str, *, section: str | None = None) -> tuple[str, bool]:
    """Replaces the placeholder on the line that declares `field`.

    The explanation after the em dash is kept: it tells the reader what the
    value means, and it is as useful filled in as it was empty. Every
    occurrence within scope is filled, so running the installer twice finds
    nothing left to do — which is what makes "already filled, left untouched"
    a promise rather than a coincidence.
    """
    pattern = re.compile(rf"^(- \*\*{re.escape(field)}\*\*: ){_PLACEHOLDER}", re.MULTILINE)
    if section is None:
        new_text, count = pattern.subn(lambda m: f"{m.group(1)}`{value}`", text)
        return new_text, bool(count)

    start, end = _section_bounds(text, section)
    if start < 0:
        return text, False
    body, count = pattern.subn(lambda m: f"{m.group(1)}`{value}`", text[start:end])
    return text[:start] + body + text[end:], bool(count)


def describe_host() -> str:
    """This machine, in the terms the profile asks for.

    Deliberately shallow: operating system, processors, memory. Anything
    finer would be a guess presented as a fact, and the file is read by
    assistants that will act on it.
    """
    system = {"Linux": "Linux", "Darwin": "macOS", "Windows": "Windows"}.get(
        platform.system(), platform.system()
    )
    release = ""
    if system == "Linux":
        os_release = Path("/etc/os-release")
        if os_release.is_file():
            for line in os_release.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("PRETTY_NAME="):
                    release = line.split("=", 1)[1].strip().strip('"')
                    break
    elif system == "Windows":
        release = platform.release()
    elif system == "macOS":
        release = platform.mac_ver()[0]

    parts = [release or system]
    cores = os.cpu_count()
    if cores:
        parts.append(f"{cores} core")
    memory = _memory_gb()
    if memory:
        parts.append(f"{memory} GB RAM")
    return ", ".join(parts)


def _memory_gb() -> int | None:
    """Total memory in whole gigabytes, where the system will say."""
    meminfo = Path("/proc/meminfo")
    if meminfo.is_file():
        for line in meminfo.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("MemTotal:"):
                try:
                    return round(int(line.split()[1]) / 1024 / 1024)
                except (ValueError, IndexError):
                    return None
    try:  # POSIX systems that are not Linux
        pages = os.sysconf("SC_PHYS_PAGES")
        size = os.sysconf("SC_PAGE_SIZE")
        return round(pages * size / 1024**3)
    except (ValueError, OSError, AttributeError):
        return None


def write_user_profile(
    vault_root: Path,
    *,
    profile: str,
    clis: list[str],
    machines: str,
) -> tuple[bool, str]:
    """Fills the installation profile from the answers just given.

    Returns whether anything was written, and a line to show the user. A
    profile that already carries answers is left exactly as it is: someone
    put them there, and an installer that overwrites them is worse than one
    that does nothing.
    """
    path = vault_root / "99-INDEX" / "USER-PROFILE.md"
    if not path.is_file():
        return False, t("{file} is not in this vault: nothing to fill in.", file=path.name)

    original = path.read_text(encoding="utf-8")
    text = original
    filled: list[str] = []

    for field, value in (
        ("profile", profile),
        ("clis", ", ".join(clis) if clis else t("none detected yet")),
        ("machines", machines),
        ("sync_method", "agent-sync" if profile == "MULTI" else "manual"),
    ):
        text, done = _fill(text, field, value)
        if done:
            filled.append(field)

    # Same field name, two sections, two different meanings.
    host_pattern = re.compile(rf"^(- \*\*Primary workstation\*\*: ){_PLACEHOLDER}.*$", re.MULTILINE)
    start, end = _section_bounds(text, "Host Awareness")
    if start >= 0:
        body, host_count = host_pattern.subn(
            lambda m: f"{m.group(1)}`{describe_host()}`.", text[start:end]
        )
        text = text[:start] + body + text[end:]
        if host_count:
            filled.append("host")

    vault_pattern = re.compile(rf"^(- \*\*Primary workstation\*\*: ){_PLACEHOLDER}", re.MULTILINE)
    start, end = _section_bounds(text, "Knowledge Vault")
    if start >= 0:
        body, path_count = vault_pattern.subn(
            lambda m: f"{m.group(1)}`{vault_root}`", text[start:end]
        )
        text = text[:start] + body + text[end:]
        if path_count:
            filled.append("vault path")

    if not filled:
        return False, t("Profile already filled in: left untouched.")

    # The note only ever addressed an agent doing this by hand.
    text = _AGENT_NOTE_RE.sub("", text, count=1)

    path.write_text(text, encoding="utf-8")
    return True, t("Profile filled in: {fields}", fields=", ".join(filled))


def write_remotes(vault_root: Path, *, profile: str) -> tuple[bool, str]:
    """Names the authoritative remote, so a single-machine install stays quiet.

    Without this, someone who installs on one machine keeps the public
    project repo as `origin`, and every check then asks them to publish
    private notes to it. Two red failures, on a correct install, with no way
    to tell them from real ones.

    It follows the machines question, not the services one. A remote is what
    carries the vault between machines; running the connectors locally or on
    a server says nothing about whether there is a second machine to reach.

    And it describes what exists, not what is planned. Someone who answers
    "2+ machines" before setting up a private remote would otherwise get a
    file naming `origin` and an alignment that refuses to start, on the first
    run, with nothing they did wrong. A remote gets named once it is there.
    """
    sync_dir = vault_root / "03-INFRA" / "agent-universal-layer" / "sync"
    target = sync_dir / "remotes.yaml"
    if target.is_file():
        return False, t("Remotes already declared: left untouched.")

    remote = "local"
    later = ""
    if profile != "MINIMAL":
        configured = _git_remotes(vault_root)
        if "origin" in configured:
            remote = "origin"
        elif configured:
            remote = configured[0]
        else:
            later = t(
                " No remote is set up yet, so it stays local for now; "
                "'nexgen config authoritative_remote <name>' names one later."
            )
    sync_dir.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "# Written by the installer. `nexgen config authoritative_remote <name>`\n"
        "# changes it; see docs/sync-contract.md for what the two fields mean.\n"
        "schema_version: 1\n"
        f"authoritative_remote: {remote}\n"
        "mirrors: []\n",
        encoding="utf-8",
    )
    return True, t("Authoritative remote set to '{remote}'.", remote=remote) + later


def commit_setup(vault_root: Path, paths: list[Path]) -> tuple[bool, str]:
    """Puts the installer's own writes into the vault's history, before aligning.

    Without this the install sabotages itself: it writes the profile, the
    remotes and the skill manifest, and then the alignment refuses to run
    because the vault has uncommitted changes — its own. The gate is right
    and must not be loosened; what was missing is that these files are the
    vault's content, and a vault is a git repository precisely so that its
    content is committed.

    Only the files just written are staged. Whatever else the user already
    had in progress is theirs, and stays untouched. Nothing is ever pushed:
    publishing is a separate decision, made by a separate command.
    """
    tracked = [p for p in paths if p.is_file()]
    if not tracked or not (vault_root / ".git").exists():
        return False, t("Nothing to record.")

    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", str(vault_root), *args],
            capture_output=True, text=True, timeout=60, check=False,
        )

    relative = [str(p.relative_to(vault_root)) for p in tracked]
    if git("add", "--", *relative).returncode != 0:
        return False, t("Could not stage the setup files.")

    result = git("commit", "-m", "setup: first run", "--", *relative)
    if result.returncode == 0:
        return True, t("Setup recorded in the vault's history.")

    detail = (result.stderr or result.stdout or "").lower()
    if "nothing to commit" in detail or "nothing added" in detail:
        return False, t("Nothing to record.")
    if "author identity" in detail or "user.email" in detail:
        return False, t(
            "Git does not know who you are yet, so the setup was not recorded. "
            "Run: git config --global user.email \"you@example.com\" "
            "and git config --global user.name \"Your Name\""
        )
    return False, t("Setup could not be recorded: {detail}", detail=detail.strip().splitlines()[-1] if detail.strip() else "")


def align_now(timeout: float = 900.0) -> tuple[bool, str]:
    """Runs the first alignment, so the install ends ready instead of ready-to-start.

    In a subprocess on purpose: the alignment is a long, chatty command with
    its own locking, and a failure there must be reportable as an outcome
    rather than an exception unwinding through the installer.
    """
    entry = SCRIPTS_DIR / "nexgen_core" / "cli" / "__init__.py"
    try:
        result = subprocess.run(
            [sys.executable, str(entry), "sync", "apply"],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return False, t("Alignment could not be run: {error}", error=exc)

    if result.returncode == 0:
        return True, t("Machine aligned.")
    detail = (result.stderr or result.stdout or "").strip().splitlines()
    return False, t(
        "Alignment did not finish: {detail}",
        detail=detail[-1] if detail else t("no reason given"),
    )


def _git_remotes(vault_root: Path) -> list[str]:
    """The remotes this repository actually has, in the order git lists them."""
    if not (vault_root / ".git").exists():
        return []
    result = subprocess.run(
        ["git", "-C", str(vault_root), "remote"],
        capture_output=True, text=True, timeout=30, check=False,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def seed_skill_manifest(vault_root: Path) -> tuple[bool, str]:
    """Puts the example manifest in place, once, if there is none.

    The engine deliberately never creates or rewrites this file on its own —
    it is the user's declaration of which skills they want, and a sync that
    recreated it would overwrite a deliberate `skills: {}`. But it has to
    exist once to begin with, and until now the only thing that put it there
    was a step in `INIT.md`, which is to say: an assistant.
    """
    skills_dir = vault_root / "03-INFRA" / "agent-universal-layer" / "skills"
    target = skills_dir / "skills.manifest.yaml"
    example = skills_dir / "skills.manifest.yaml.example"
    if target.is_file():
        return False, t("Skill manifest already there: left untouched.")
    if not example.is_file():
        return False, t("No example skill manifest in this vault.")
    skills_dir.mkdir(parents=True, exist_ok=True)
    target.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
    return True, t("Skill manifest seeded from the example; it is yours to edit.")


def remaining_problems() -> tuple[int, list[str]]:
    """What the doctor still finds, after the install has done its part.

    The installer must not call itself finished on the strength of an exit
    code. During development the alignment returned success while having
    produced none of the pointers, none of the configs and no skill index,
    and the install cheerfully said "Ready." — which is the one thing an
    installer must never get wrong. So the last word belongs to the same
    check the user would run themselves.

    Returns how many problems there are and the first few, each already
    carrying its own next step.
    """
    from nexgen_core.doctor import Doctor

    try:
        report = Doctor().run_diagnostics(apply_remedies=False)
    except Exception as exc:  # noqa: BLE001 - an installer must not end on a traceback
        return -1, [t("The check itself could not run: {error}", error=exc)]

    lines = []
    for outcome in report.broken:
        line = outcome.message
        if getattr(outcome, "action", ""):
            line += " " + t("→ {action}", action=outcome.action)
        lines.append(line)
    return len(lines), lines
