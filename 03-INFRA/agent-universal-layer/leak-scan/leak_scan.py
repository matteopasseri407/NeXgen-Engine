#!/usr/bin/env python3
"""The engine's anti-leak gate: one policy file, four modes, gitleaks as the scanner.

gitleaks (MIT, https://github.com/gitleaks/gitleaks) is the maintained engine behind the commit
gate and owns its matching, its allowlists, its baseline and its modes. This module keeps what is
this engine's own: the command line its hooks and CI already call, the private deny-list, the
output shape (one redacted line per finding, silent when clean) and the fail-closed behaviour
when the scanner is not installed.

gitleaks is a maintainer tool, and only the commit gate runs on the engine's own repository.
The text of a person's session, the brief about to reach a seat and the output a seat produced,
is scanned by `scan_text` with the built-in matcher and the forms in `leak_patterns.yaml`: a
Council that stopped working because a maintainer's binary is not installed would be a product
regression, and requiring nobody's secret to leave the process is the point of the gate.

Where each mode lands:
  --tree PATH...           `gitleaks dir PATH` (fixtures, a working tree)
  --staged                 `gitleaks git --staged --pre-commit REPO` (the pre-commit lane)
  --message FILE           `gitleaks stdin` reading the file (the commit-msg lane)
  --commit-range BASE..HEAD
                           `gitleaks git --log-opts BASE..HEAD REPO`, plus the commit messages
                           of the same range, which gitleaks attaches to a finding without
                           scanning them.
`--baseline` is handed to gitleaks as `--baseline-path` (the commit-range lanes only, exactly
as the retired exception file was): a finding pinned there is already public, anything else
blocks.

Two pattern sources, NEVER mixed:
  --config FILE    the public rules (`gitleaks.toml`): forms only, zero real values.
  --denylist FILE  literal real values, one per line, read only on the maintainer's machine.
                   gitleaks has no secret-free way to match a real value, so they become a
                   temporary copy of the config with one literal rule per value, deleted when
                   the scan ends.

Exit codes: 0 clean (or only soft findings), 1 blocking findings, and also 1 with a clear
message when gitleaks is missing or the scan could not be trusted: the gate never passes
because the tool was absent.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import NamedTuple

import yaml

SCAN_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = SCAN_DIR / "gitleaks.toml"
DEFAULT_BASELINE = SCAN_DIR / "gitleaks-baseline.json"
#: The forms the built-in matcher uses, for the gates that must work with no binary installed.
DEFAULT_PATTERNS = SCAN_DIR / "leak_patterns.yaml"
#: Rule ids with this prefix warn; every other rule, including the private deny-list, blocks.
SOFT_RULE_PREFIX = "nexgen-soft-"
GITLEAKS_MISSING = """[leak-scan] ABORT: gitleaks is not on PATH and it is this gate's scanner.
  Windows   winget install gitleaks.gitleaks   (or: scoop install gitleaks)
  macOS     brew install gitleaks
  Linux     brew install gitleaks, or the release zip below
  any OS    https://github.com/gitleaks/gitleaks/releases (single binary, MIT)
Nothing was scanned. The gate fails closed: install it, then run the same command again."""
SCAN_TIMEOUT_S = 900


class Finding(NamedTuple):
    label: str
    lineno: int
    kind: str
    redacted: str
    blocking: bool  # True blocks; False is a soft rule, printed as a warning only


class Unit(NamedTuple):
    """One line of text to match: what the finding can point a person at."""

    label: str  # "brief", "generated output", a file path
    lineno: int
    text: str


def load_patterns(path: Path) -> tuple[list[tuple[str, str, bool]], list[str]]:
    """The built-in forms: blocking, warning, and the values they agree to ignore.

    `leak_hard` blocks, `leak_soft` warns, `leak_allow` exempts a token a pattern
    accounts for whole. An empty `leak_hard` is refused rather than read as
    "nothing is a secret".
    """
    if not path.is_file():
        sys.exit(f"[leak-scan] ABORT: pattern file not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    hard = data.get("leak_hard", [])
    if not hard:
        sys.exit("[leak-scan] ABORT: leak_hard is empty in the pattern file")
    soft = data.get("leak_soft", [])
    named = [(f"h{index}", pattern, True) for index, pattern in enumerate(hard)]
    named += [(f"s{index}", pattern, False) for index, pattern in enumerate(soft)]
    return named, data.get("leak_allow", [])


def _allowlisted(token: str, allowed: list[re.Pattern[str]]) -> bool:
    """Whether an allow pattern accounts for the WHOLE flagged token.

    `any(a.search(tok))` used to answer "an allow pattern appears somewhere inside
    the token", so a real value that merely contained an allowlisted one was waved
    through with it. `fullmatch` keeps `^`/`$` in the allow entries meaning "this
    value stands alone" while still judging the true extent of what was flagged.
    """
    return any(pattern.fullmatch(token) for pattern in allowed)


def scan_units(units: Iterable[Unit], patterns: list[tuple[str, str, bool]], allowed: list[str],
               denylist: list[str]) -> list[Finding]:
    """Every match in `units`, redacted, with the private values matched literally."""
    allow_re = [re.compile(pattern) for pattern in allowed]
    prepared = [(name, re.compile(pattern), blocking) for name, pattern, blocking in patterns]
    findings: list[Finding] = []
    for unit in units:
        for name, regex, blocking in prepared:
            for match in regex.finditer(unit.text):
                token = match.group(0)
                if _allowlisted(token, allow_re):
                    continue
                findings.append(Finding(unit.label, unit.lineno, f"pattern:{name}", redact(token), blocking))
        for literal in denylist:
            if literal and literal in unit.text:
                findings.append(Finding(unit.label, unit.lineno, "denylist", redact(literal), True))
    return findings


def redact(token: str) -> str:
    """Enough of a value to recognise it, never enough to use it."""
    if len(token) <= 6:
        return "*" * len(token)
    return f"{token[:2]}…{token[-2:]} ({len(token)} chars)"


def load_denylist(path: Path | None) -> list[str]:
    """The private literal values, one per line; blank and `#` lines are ignored."""
    if path is None:
        return []
    if not path.is_file():
        sys.exit(f"[leak-scan] ABORT: denylist given but not found: {path}")
    values = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            values.append(line)
    return values


def gitleaks_binary() -> str:
    """The installed gitleaks, or a refusal that says how to install it."""
    binary = shutil.which("gitleaks")
    if binary is None:
        sys.exit(GITLEAKS_MISSING)
    return binary


def _tail(text: str, limit: int = 800) -> str:
    return " | ".join(line.strip() for line in text.splitlines() if line.strip())[-limit:]


def _run_report(binary: str, command: list[str], config: Path, baseline: Path | None,
                stdin_text: str | None = None) -> list[dict]:
    """Run one gitleaks command and return its JSON findings.

    A report that cannot be read, or a non-zero exit that is not "leaks found", is a scan
    nobody can trust: it aborts instead of reporting a clean tree.
    """
    argv = [binary, *command, "--config", str(config), "--report-format", "json",
            "--report-path", "-", "--no-banner", "--log-level", "warn"]
    if baseline is not None:
        argv += ["--baseline-path", str(baseline)]
    done = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          input=stdin_text, timeout=SCAN_TIMEOUT_S, check=False)
    if done.returncode not in (0, 1):
        sys.exit(f"[leak-scan] ABORT: gitleaks failed with {done.returncode}: {_tail(done.stderr)}")
    try:
        entries = json.loads(done.stdout or "[]")
    except json.JSONDecodeError:
        sys.exit(f"[leak-scan] ABORT: gitleaks returned no readable report: {_tail(done.stdout)}")
    if not isinstance(entries, list):
        sys.exit("[leak-scan] ABORT: gitleaks returned a report that is not a list of findings")
    if done.returncode == 1 and not entries:
        # gitleaks exits 1 for a partial scan too; an empty report there is "unknown", not clean.
        sys.exit(f"[leak-scan] ABORT: gitleaks could not finish the scan: {_tail(done.stderr)}")
    return entries


def _finding(entry: dict, label: str | None = None) -> Finding:
    """One gitleaks finding in the engine's shape: where, which rule, redacted value."""
    rule = str(entry.get("RuleID") or "unknown")
    secret = str(entry.get("Secret") or entry.get("Match") or "")
    return Finding(
        label=label or str(entry.get("File") or "(stdin)"),
        lineno=int(entry.get("StartLine") or 0),
        kind=f"rule:{rule}",
        redacted=redact(secret) if secret else "(empty)",
        blocking=not rule.startswith(SOFT_RULE_PREFIX),
    )


def _config_with_denylist(config: Path, denylist: list[str]) -> Path | None:
    """A temporary copy of the public config with one literal rule per private value.

    gitleaks matches forms, so a real value cannot live in the public file. Writing the values
    into a temporary config keeps them where they already are (a private file, never printed)
    and lets the same scan cover them in every mode. Returns None when there is no deny-list.
    """
    if not denylist:
        return None
    rules = "\n".join(
        f'[[rules]]\nid = "nexgen-private-denylist-{index}"\n'
        f'description = "a literal value from the private deny-list"\n'
        f"regex = {json.dumps(re.escape(value))}\n"
        for index, value in enumerate(denylist, 1)
    )
    handle, name = tempfile.mkstemp(prefix="nexgen-denylist-", suffix=".toml")
    with os.fdopen(handle, "w", encoding="utf-8") as stream:
        stream.write(config.read_text(encoding="utf-8") + "\n" + rules)
    return Path(name)


def _git(repo: str, *args: str) -> str:
    done = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=False,
                          check=False, timeout=120)
    if done.returncode != 0:
        err = done.stderr.decode("utf-8", errors="replace")
        sys.exit(f"[leak-scan] git command failed: git {' '.join(args)}\n{err}")
    return done.stdout.decode("utf-8", errors="replace")


def _message_findings(binary: str, repo: str, range_spec: str, config: Path,
                      baseline: Path | None) -> list[Finding]:
    """The commit messages of the range: gitleaks carries one on a finding, never scans it."""
    findings: list[Finding] = []
    for sha in (line for line in _git(repo, "rev-list", "--reverse", range_spec).splitlines() if line):
        message = _git(repo, "log", "-1", "--format=%B", sha)
        entries = _run_report(binary, ["stdin"], config, baseline, stdin_text=message)
        findings += [_finding(entry, label=f"commit {sha[:9]} (message)") for entry in entries]
    return findings


def _findings_for(args: argparse.Namespace, binary: str, config: Path,
                  baseline: Path | None) -> list[Finding]:
    """Collect the findings of the one mode the caller asked for."""
    if args.tree:
        findings: list[Finding] = []
        for path in args.tree:
            findings += [_finding(entry) for entry in _run_report(binary, ["dir", path], config, None)]
        return findings
    if args.staged:
        entries = _run_report(binary, ["git", "--staged", "--pre-commit", args.repo], config, None)
        return [_finding(entry) for entry in entries]
    if args.message:
        text = Path(args.message).read_text(encoding="utf-8")
        entries = _run_report(binary, ["stdin"], config, None, stdin_text=text)
        return [_finding(entry, label="(commit message)") for entry in entries]
    entries = _run_report(binary, ["git", "--log-opts", args.commit_range, args.repo], config, baseline)
    return [_finding(entry) for entry in entries] + _message_findings(
        binary, args.repo, args.commit_range, config, baseline)


def scan_text(text: str, label: str = "text") -> list[Finding]:
    """Scan a string that is not a file on disk: a Council brief, a seat's generated output.

    The built-in matcher, never gitleaks: these two gates run inside a person's own
    session and must keep working on a machine where no maintainer binary is
    installed. The text is matched line by line, so a finding still names the line
    the caller has to redact.
    """
    patterns, allowed = load_patterns(DEFAULT_PATTERNS)
    units = [Unit(label, number, line) for number, line in enumerate(text.splitlines(), 1)]
    return scan_units(units, patterns, allowed, [])


#: Set by GitHub on its own runners. The lock there is the repository's permissions, not this
#: machine's flag, and the private config a registration lives in does not exist in a fresh clone.
CI_ENV = "GITHUB_ACTIONS"


def _maintainer_refusal(repo: str) -> str | None:
    """The refusal when the engine's own commit gate is asked for on an unregistered machine.

    Only the commit lanes are that gate: `--staged` is its pre-commit step and `--commit-range`
    its pre-push one. `--tree` and `--message` scan whatever text a caller hands them, in any
    project, and stay open. Outside the engine's own checkout there is nothing to gate.
    """
    module = SCAN_DIR.parents[2] / "03-INFRA" / "scripts" / "nexgen_core" / "maintainer.py"
    if not module.is_file() or os.environ.get(CI_ENV):
        return None
    sys.path.insert(0, str(module.parent.parent))  # the scripts directory, where the package root sits
    from nexgen_core.maintainer import engine_repo_refusal

    return engine_repo_refusal(Path(repo))


def _baseline_for(args: argparse.Namespace) -> Path | None:
    """The published findings pinned for this run: named, or the one shipped beside the rules."""
    if args.baseline:
        path = Path(args.baseline)
        if not path.is_file():
            sys.exit(f"[leak-scan] ABORT: baseline not found: {path}")
        return path
    if args.commit_range and DEFAULT_BASELINE.is_file():
        return DEFAULT_BASELINE
    return None


def _parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(DEFAULT_CONFIG),
                    help="public gitleaks rules (default: the gitleaks.toml beside this script)")
    ap.add_argument("--denylist", help="private deny-list file (literal values), optional")
    ap.add_argument("--repo", default=".", help="git repo to operate on (default: cwd)")
    ap.add_argument("--baseline", metavar="FILE",
                    help="already-published findings to ignore (default: the shipped baseline, "
                         "commit-range only)")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--tree", nargs="+", metavar="PATH", help="scan literal file/dir content")
    mode.add_argument("--staged", action="store_true", help="scan the staged diff")
    mode.add_argument("--message", metavar="FILE", help="scan a commit-message file")
    mode.add_argument("--commit-range", metavar="BASE..HEAD", help="scan every commit in the range")
    return ap.parse_args()


def _print_and_exit(findings: list[Finding]) -> int:
    """The engine's own output contract: silent when clean, redacted findings, soft first."""
    soft = [finding for finding in findings if not finding.blocking]
    blocking = [finding for finding in findings if finding.blocking]
    if soft:
        print("[leak-scan] soft warnings (not blocking):")
        for finding in soft:
            print(f"  ? {finding.label}:{finding.lineno}  [{finding.kind}]  match={finding.redacted}")
    if not blocking:
        return 0
    print("[leak-scan] BLOCKING leaks found:")
    for finding in blocking:
        print(f"  ! {finding.label}:{finding.lineno}  [{finding.kind}]  match={finding.redacted}")
    print(f"[leak-scan] total: {len(blocking)} finding(s) — commit/push blocked.")
    return 1


def main() -> int:
    args = _parse_args()
    if args.staged or args.commit_range:
        refused = _maintainer_refusal(args.repo)
        if refused:
            sys.exit(refused)
    binary = gitleaks_binary()
    config = Path(args.config)
    if not config.is_file():
        sys.exit(f"[leak-scan] ABORT: rule file not found: {config}")
    denylist = load_denylist(Path(args.denylist)) if args.denylist else []
    temporary = _config_with_denylist(config, denylist)
    try:
        findings = _findings_for(args, binary, temporary or config, _baseline_for(args))
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return _print_and_exit(findings)


if __name__ == "__main__":
    raise SystemExit(main())
