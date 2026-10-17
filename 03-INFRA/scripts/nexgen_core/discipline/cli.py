"""Command line: check, score, gate, review, receipt, audit, report, smoke, init, ci."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from . import gates, review
from .config import COMMAND, Config, ConfigError, load
from .health import report, run_smoke
from .gitutil import STAGED, Change, GitError, fork_point, git, own_changes, parents, repo_root, resolve_base
from .score import is_complex
from .shellgate import check_shell
from .sizes import check_sizes
from .scaffold import init_project
from .violations import CODE_EXT, SHELL_EXT

COUNCIL = ("council code-review .nexgen-discipline/review.diff --context .nexgen-discipline/brief.md "
           "--author-vendor anthropic")  # change the vendor if another vendor wrote the code


def _staged_base(root: Path) -> str:
    """HEAD, or the empty tree before the first commit."""
    has_head = git(root, "rev-parse", "--verify", "-q", "HEAD", check=False).strip()
    return "HEAD" if has_head else resolve_base(root)


def _scope(args: argparse.Namespace) -> tuple[Path, Config, str, str | None]:
    root = repo_root(Path.cwd())
    if args.staged:
        base, head = _staged_base(root), STAGED
    else:
        base, head = resolve_base(root, args.base), args.head
    return root, load(root, base), base, head


def cmd_check(args: argparse.Namespace, need_review: bool = False) -> int:
    root, cfg, base, head = _scope(args)
    out = gates.run(root, cfg, base, head, need_review)
    print(gates.render(out))
    return 0 if out.ok else 1


def cmd_gate(args: argparse.Namespace) -> int:
    return cmd_check(args, need_review=True)


def cmd_score(args: argparse.Namespace) -> int:
    root, cfg, base, head = _scope(args)
    flagged, total, reasons = is_complex(own_changes(root, base, head), cfg)
    print(f"score {total} (threshold {cfg.complexity_threshold}): "
          f"{'COMPLEX, independent review required' if flagged else 'simple'}")
    print("\n".join(reasons))
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    root, cfg, base, head = _scope(args)
    _, total, reasons = is_complex(own_changes(root, base, head), cfg)
    diff_path, brief_path, digest = review.write_brief(root, base, head, total, reasons)
    print(f"Wrote {diff_path.relative_to(root)} and {brief_path.relative_to(root)} (review id {digest}).\n"
          "Do NOT pick the reviewer yourself. Run `council propose`, show the owner the options in plain "
          "words, and wait for their go-ahead. Then run, piping the result into the receipt:\n"
          f"  {COUNCIL} | {COMMAND} receipt --reviewer council\n"
          "Any change to the code after this point invalidates the review: run `review` again.")
    strays = git(root, "ls-files", "--others", "--exclude-standard", "--", ".", ":(exclude).nexgen-discipline").split()
    if strays:
        print(f"Untracked files included in this review: {', '.join(strays)}. Commit them or delete them before pushing.")
    return 0


def cmd_receipt(args: argparse.Namespace) -> int:
    root, _, base, head = _scope(args)
    text = Path(args.from_file).read_text(encoding="utf-8") if args.from_file else sys.stdin.read()
    verdict = review.parse_verdict(text)
    if verdict is None:
        print("FAIL the reviewer output must end with a `VERDICT: APPROVE|REVISE|REJECT` line", file=sys.stderr)
        return 1
    digest = review.diff_hash(root, base, head)
    if (refusal := review.receipt_refusal(root, digest, text)):
        print(f"FAIL {refusal}", file=sys.stderr)
        return 1
    review.record_receipt(root, digest, verdict, args.reviewer, text)
    print(f"Verdict for the owner: {review.OWNER_WORDS[verdict]} ({verdict}). Commit .nexgen-discipline/receipts/.")
    print(text)
    return 0 if verdict == "APPROVE" else 2


def cmd_approve(args: argparse.Namespace) -> int:
    """The owner's own decision, recorded against the exact work. Agents must never run this by themselves."""
    root, _, base, head = _scope(args)
    if len(args.reason.strip()) < 10:
        print("FAIL give the owner's reason in words: --reason \"...\"", file=sys.stderr)
        return 1
    digest = review.diff_hash(root, base, head)
    review.record_receipt(root, digest, "APPROVE", "owner", review.owner_output(args.reason.strip()))
    print(f"Owner approval recorded for {digest}. Commit .nexgen-discipline/receipts/.")
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    root = repo_root(Path.cwd())
    cfg = load(root, "HEAD")
    files = [Change(p, "A", None, (root / p).read_text(encoding="utf-8", errors="replace"))
             for p in git(root, "ls-files").splitlines()
             if p.lower().endswith(CODE_EXT + SHELL_EXT) and (root / p).is_file()]
    found = check_sizes(files, cfg) + check_shell(files, cfg)
    print("\n".join(f"DEBT {v}" for v in found) or "No debt found.")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    print(report(repo_root(Path.cwd())))
    return 0


def cmd_smoke(args: argparse.Namespace) -> int:
    return run_smoke(repo_root(Path.cwd()))


def cmd_init(args: argparse.Namespace) -> int:
    lines, protected = init_project(repo_root(Path.cwd()), args.smoke)
    print("\n".join(lines))
    return 0 if protected else 1


def cmd_ci(args: argparse.Namespace) -> int:
    """What the CI job runs: picks base, head and strictness from the event, so the YAML holds no logic."""
    root = repo_root(Path.cwd())
    pull_request = args.event == "pull_request"
    # A pull request is judged on its own branch tip, not on GitHub's synthetic merge commit.
    head = "HEAD^2" if pull_request and len(parents(root, "HEAD")) > 1 else "HEAD"
    base_ref = f"origin/{args.base_ref}" if pull_request else "origin/main"
    usable_before = not pull_request and args.before.strip("0") and _object_exists(root, args.before)
    pushed_before = args.before if usable_before else None
    base = pushed_before or fork_point(root, base_ref, head) or resolve_base(root)
    release = (pull_request and args.head_ref == "developer" and args.base_ref == "main") or \
        (not pull_request and args.head_ref == "main")
    out = gates.run(root, load(root, base), base, head, need_review=not release, release=release)
    print(gates.render(out))
    return 0 if out.ok else 1


def _object_exists(root: Path, sha: str) -> bool:
    return subprocess.run(["git", "-C", str(root), "cat-file", "-e", f"{sha}^{{commit}}"], capture_output=True).returncode == 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="discipline")
    sub = parser.add_subparsers(dest="command", required=True)

    def scoped(name: str, func, help_: str) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_)
        p.add_argument("--base", help="compare against this revision (default: fork point)")
        p.add_argument("--head", help="compare this revision (default: working tree)")
        p.add_argument("--staged", action="store_true", help="check the index against HEAD")
        p.set_defaults(func=func)
        return p

    scoped("check", cmd_check, "size ratchet and shell/PowerShell gate")
    scoped("score", cmd_score, "complexity score of the change")
    scoped("gate", cmd_gate, "check plus review receipt for complex changes")
    scoped("review", cmd_review, "prepare the brief for an independent reviewer")
    receipt = scoped("receipt", cmd_receipt, "record the reviewer's verdict for this exact work")
    receipt.add_argument("--reviewer", default="unknown")
    receipt.add_argument("--from-file", help="reviewer output file (default: stdin)")
    approve = scoped("approve", cmd_approve, "record the OWNER's decision to accept this work without a paid review")
    approve.add_argument("--reason", required=True, help="why the owner accepts it, in the owner's words")
    sub.add_parser("audit", help="list existing debt across the repo").set_defaults(func=cmd_audit)
    sub.add_parser("report", help="plain-Italian health report").set_defaults(func=cmd_report)
    sub.add_parser("smoke", help="run the project's real-flow command").set_defaults(func=cmd_smoke)
    init = sub.add_parser("init", help="set up this project: config, decision log, git hooks")
    init.add_argument("--smoke", default="", help="the command that proves the program really works")
    init.set_defaults(func=cmd_init)
    ci = sub.add_parser("ci", help="CI entry point")
    for flag in ("event", "before", "base-ref", "head-ref"):
        ci.add_argument(f"--{flag}", default="")
    ci.set_defaults(func=cmd_ci)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (GitError, ConfigError) as err:
        print(f"discipline: {err}", file=sys.stderr)
        return 1
