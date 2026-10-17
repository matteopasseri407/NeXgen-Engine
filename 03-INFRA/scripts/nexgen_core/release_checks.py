"""Which checks a commit had to pass, and whether it did.

One rule, read from here by both callers: `release ship` waits for the checks
main's branch protection requires before it merges and before it tags, and
release.yml refuses a tag whose commit does not carry them. A tag `ship` cuts
names them in its annotation (REQUIRED_LINE): the workflow's own token cannot
read main's branch protection, and the annotation travels signed, with the tag.
A tag cut by hand has no such line, and then the rule this gate started with
stands: one successful `ci.yml` run.

The `Runner` seam is what makes all of it testable without a network: pass a
callable that answers like `gh` and the whole rule runs on a scripted GitHub.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from collections.abc import Callable, Sequence
from pathlib import Path

#: The line a release tag carries in its annotation: the checks the release was verified against.
REQUIRED_LINE = "Required checks:"
#: How often `ci-passed` asks again for a required check that has not reported yet.
CHECK_POLL_SECONDS = 30.0
CHECK_GREEN = ("SUCCESS", "SKIPPED", "NEUTRAL")
CHECK_PENDING = ("QUEUED", "IN_PROGRESS", "PENDING", "WAITING", "REQUESTED", "EXPECTED")

Runner = Callable[[Sequence[str], Path], "subprocess.CompletedProcess[str]"]


def run_command(argv: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """One command, resolved through PATH(EXT) so `gh.exe` works on Windows too."""
    exe = shutil.which(argv[0]) or argv[0]
    return subprocess.run([exe, *argv[1:]], cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", check=False, timeout=300)


def _answer(text: str):
    """A JSON answer as it came, None when it cannot be read (the caller treats that as no answer)."""
    try:
        return json.loads(text or "null")
    except json.JSONDecodeError:  # nexgen: allow reason="an unreadable answer is treated as no answer by the caller"
        return None


def required_checks(repo: Path, run: Runner = run_command) -> set[str] | None:
    """The checks main's protection requires, from both places GitHub keeps them.

    Classic branch protection is what this repository uses, and reading it needs
    admin rights a workflow token does not have; the rules endpoint is readable
    without them. Both are asked and their names united. None when neither
    answers: the caller then keeps its strict reading.
    """
    names: set[str] = set()
    protection = run(["gh", "api", "repos/{owner}/{repo}/branches/main/protection/required_status_checks"], repo)
    if protection.returncode == 0:
        data = _answer(protection.stdout) or {}
        names |= {str(name) for name in data.get("contexts") or []}
        names |= {str(check.get("context")) for check in data.get("checks") or [] if check.get("context")}
    rules = run(["gh", "api", "repos/{owner}/{repo}/rules/branches/main"], repo)
    if rules.returncode == 0:
        for rule in _answer(rules.stdout) or []:
            if rule.get("type") == "required_status_checks":
                parameters = (rule.get("parameters") or {}).get("required_status_checks") or []
                names |= {str(check["context"]) for check in parameters if check.get("context")}
    return names or None


def check_state(check: dict | None) -> bool | None:
    """True when the check passed, False when it failed, None while it is missing or running."""
    if not check:
        return None
    status = str(check.get("status") or "").upper()
    state = str(check.get("conclusion") or check.get("state") or "").upper()
    if status in CHECK_PENDING or (status and status != "COMPLETED") or state in CHECK_PENDING or not state:
        return None
    return state in CHECK_GREEN


def verdict(checks: dict[str, dict], required: set[str]) -> tuple[str, list[str]]:
    """('green' | 'failed' | 'missing', the required names that are not green).

    'missing' is the answer a caller may wait on: a required check that has not
    reported yet is not a failure, it is an answer that has not arrived.
    """
    states = {name: check_state(checks.get(name)) for name in required}
    failed = sorted(name for name, state in states.items() if state is False)
    if failed:
        return "failed", failed
    waiting = sorted(name for name, state in states.items() if state is None)
    return ("missing", waiting) if waiting else ("green", [])


def commit_checks(repo: Path, sha: str, run: Runner = run_command) -> dict[str, dict]:
    """Every check GitHub reports for one commit, by name; empty when it cannot be read."""
    result = run(["gh", "api", f"repos/{{owner}}/{{repo}}/commits/{sha}/check-runs", "-F", "per_page=100"], repo)
    if result.returncode != 0:
        return {}
    return {str(check.get("name") or "?"): check for check in (_answer(result.stdout) or {}).get("check_runs") or []}


def ci_run_passed(repo: Path, sha: str, run: Runner = run_command, workflow: str = "ci.yml") -> bool:
    """Whether a successful run of `workflow` exists for this commit: the rule before the required list."""
    result = run(["gh", "api",
                  f"repos/{{owner}}/{{repo}}/actions/workflows/{workflow}/runs?head_sha={sha}&status=success",
                  "--jq", ".total_count"], repo)
    if result.returncode != 0:
        return False
    return (_answer(result.stdout.strip() or "0") or 0) > 0


def declared_checks(tag_message: str) -> set[str] | None:
    """The checks a release tag was verified against, as `release ship` wrote them into its annotation."""
    for line in tag_message.splitlines():
        if line.strip().startswith(REQUIRED_LINE):
            names = {name.strip() for name in line.split(REQUIRED_LINE, 1)[1].split(",") if name.strip()}
            return names or None
    return None


def required_annotation(required: set[str] | None) -> list[str]:
    """The `git tag` arguments that put the verified checks into the tag's annotation."""
    return ["-m", f"{REQUIRED_LINE} {', '.join(sorted(required))}"] if required else []


def ci_passed(repo: Path, tag: str, run: Runner = run_command, wait_seconds: float = 900.0,
              sleep: Callable[[float], None] = time.sleep) -> tuple[bool, str]:
    """Whether the tagged commit carries the checks the release was verified against.

    A tag `release ship` cut carries that list in its annotation, signed with the
    tag itself: the commit must carry those checks green, and a required one that
    has not reported yet is waited for. A tag cut by hand has no such line, and
    then the rule this gate started with stands: one successful `ci.yml` run.
    """
    message = run(["git", "-C", str(repo), "tag", "-l", "--format=%(contents:body)", tag], repo)
    required = declared_checks(message.stdout) if message.returncode == 0 else None
    sha = run(["git", "-C", str(repo), "rev-parse", f"{tag}^{{commit}}"], repo).stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", sha):  # git echoes a missing tag's name instead of failing on stdout
        return False, f"{tag} is not a tag in this checkout"
    if not required:
        if ci_run_passed(repo, sha, run):
            return True, f"ci.yml has a successful run for {sha[:8]}"
        return False, f"CI has no successful run for {sha[:8]}"
    waited = 0.0
    while True:
        state, detail = verdict(commit_checks(repo, sha, run), required)
        if state == "green":
            return True, f"the checks this release was verified against are green on {sha[:8]}: {', '.join(sorted(required))}"
        if state == "failed":
            return False, f"failing on {sha[:8]}: {', '.join(detail)}"
        if waited >= wait_seconds:
            return False, f"not reported on {sha[:8]} after {int(waited)}s: {', '.join(detail)}"
        sleep(CHECK_POLL_SECONDS)
        waited += CHECK_POLL_SECONDS
