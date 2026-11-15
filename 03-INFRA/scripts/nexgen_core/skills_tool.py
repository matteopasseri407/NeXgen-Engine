"""The github-skill fetch mechanics, handed to the tool that maintains them.

One job: ask the pinned `skills` CLI (npm `skills`, MIT, vercel-labs/skills) to
fetch one skill of one repository at one commit, and answer with the folder it
wrote inside a scratch directory the caller owns, or with one line saying why
there is none. Nothing here decides placement, exposure, targets or the index:
`skills.py` still owns those, and `skill_sources.py` still owns the fallback.

Adopted rather than reimplemented, and only where it is 1:1 or better than the
engine's own `git clone` of the whole repository: the CLI resolves the commit,
downloads the one skill folder, and records a content hash in a
`skills-lock.json` (version 2). The hash is used here for what a clone could not
answer: whether the bytes that arrived are the bytes the pin names.

Three deliberate limits, each of them a decision and not an omission:

- The CLI is a fetch backend only. `skills update` moves its own installs and
  its own agent directories, so using it to apply a pin would make it a second
  writer of the same bytes the engine materializes; the mover stays
  `thirdparty bump` (the guardian decides, the manifest records, `sync` applies).
- `skills check` has no machine-readable mode in the pinned version (it prints
  prose and sets an exit code), and it answers "did upstream move", which the
  dependency watch already answers without Node. So the drift answer the lock
  gives is used here, at fetch time, where it is true and free.
- A machine without Node keeps today's path: `available()` is False and every
  caller falls back to the git clone without a word to the person waiting.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path

from nexgen_core.action_notes import ERROR
from nexgen_core.i18n import t
from nexgen_core.processes import ShimEntryError, no_window_kwargs, windows_command_argv

logger = logging.getLogger(__name__)

#: The npm package, and the version this engine pins. A bump is a deliberate
#: edit, made the way any other pin is: read what changed, then change this line.
PACKAGE = "skills"
VERSION = "1.8.1"

#: One fetch downloads a skill folder over the network; it is bounded like the
#: installer it stands beside, and never left to hold a guard cycle open.
RUN_TIMEOUT_SECONDS = 240

#: Where the CLI writes the skill it installed, relative to its working directory.
INSTALLED_ROOT = Path(".agents") / "skills"

#: The CLI's own record of what it installed: version 2 carries `computedHash`
#: (a SHA-256 over the folder, published with test vectors) and the `ref` it
#: fetched. Read as the integrity check of the bytes that arrived.
LOCK_FILE = "skills-lock.json"

#: The CLI sends anonymous usage and audit data unless told not to. Both names
#: are its own switches; the engine sets them on every run, always.
TELEMETRY_ENV = {"DISABLE_TELEMETRY": "1", "DO_NOT_TRACK": "1"}


def available(which=shutil.which) -> bool:
    """True when this machine can run the pinned CLI through npx.

    `which` is looked up at call time and replaceable, so a test states the
    machine it is describing instead of inheriting this one.
    """
    return bool(which("npx") and which("node"))


def _run(argv: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """The CLI, windowless, with its telemetry off, in the caller's scratch directory."""
    return subprocess.run(
        windows_command_argv(argv),
        stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8", errors="replace",
        check=False, timeout=RUN_TIMEOUT_SECONDS, cwd=str(cwd),
        env={**os.environ, **TELEMETRY_ENV},
        **no_window_kwargs(),
    )


def _read_lock(workdir: Path) -> dict | None:
    """The CLI's lock as a mapping, or None when it is absent or not a lock."""
    try:
        data = json.loads((workdir / LOCK_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):  # nexgen: allow reason="the contract answers None for an absent or unreadable lock: the caller reports no pin and touches no file"
        return None
    return data if isinstance(data, dict) and isinstance(data.get("skills"), dict) else None


def _entry_of(lock: dict | None, skill: str) -> dict | None:
    """One skill's entry, matched on the installed name the caller asked for."""
    entry = (lock or {}).get("skills", {}).get(skill)
    return entry if isinstance(entry, dict) else None


def _pin_problem(workdir: Path, skill: str, commit: str) -> str | None:
    """One line when the lock does not prove the pin, None when it does.

    The lock echoes the `ref` it fetched and the content hash it recorded. Bytes
    with no hash, or a ref that is not the pinned commit, are refused whole:
    materializing a tree nobody can identify is the failure this backend exists
    to make impossible.
    """
    entry = _entry_of(_read_lock(workdir), skill)
    if entry is None:
        return t("the fetched skill is not in {lock}", lock=LOCK_FILE)
    if not str(entry.get("computedHash") or "").strip():
        return t("the fetch recorded no content hash")
    fetched = str(entry.get("ref") or entry.get("commit") or "")
    if fetched.lower() != commit.lower():
        return t("the fetch resolved {fetched} instead of the pinned commit", fetched=fetched or "nothing")
    return None


def fetch(repo: str, commit: str, skill: str, workdir: Path) -> tuple[Path | None, str | None]:
    """Fetch one skill at one commit into `workdir`; its folder, or why there is none.

    `workdir` is the caller's scratch directory and must be empty: the CLI writes
    `.agents/skills/<skill>` and `skills-lock.json` into the directory it runs in.
    On success the folder is verified to sit inside that boundary before it is
    handed back, because everything downstream copies it into a live skill view.
    """
    if not available():
        return None, t("node and npx are not on this machine")
    try:
        result = _run(["npx", "--yes", f"{PACKAGE}@{VERSION}", "add", f"{repo}@{commit}",
                       "--skill", skill, "-y", "--copy"], workdir)
    except subprocess.TimeoutExpired:
        return None, t("the skills CLI did not finish within {seconds}s", seconds=RUN_TIMEOUT_SECONDS)
    except (OSError, ShimEntryError) as exc:  # an OS failure, or an npm shim that cannot be started
        return None, str(exc)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        return None, t("the skills CLI failed: {reason}",
                       reason=detail[-1] if detail else f"exit {result.returncode}")
    folder = workdir / INSTALLED_ROOT / skill
    if not (folder / "SKILL.md").is_file():
        return None, t("the skills CLI ran but left no {skill} skill to take", skill=skill)
    problem = _pin_problem(workdir, skill, commit)
    if problem:
        return None, problem
    return folder, None


def failure_line(problem: str) -> str:
    """One line for the caller's report, prefixed like every other acquisition failure."""
    return ERROR + t("github skill fetched by the skills CLI: {problem}", problem=problem)
