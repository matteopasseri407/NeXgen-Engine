"""Soft gate for shell and PowerShell: fine as a thin launcher, not as a place for logic.

Over the limits a file needs `nexgen: allow reason="..."` in its first lines,
so the reason is visible in review. Existing files may not grow past their old size.
"""
from __future__ import annotations

import re

from .config import Config
from .gitutil import Change
from .violations import SHELL_EXT, Violation, line_count, selected

CONTROL = re.compile(r"^\s*(if|elif|elseif|for|foreach|while|until|case|switch|function|try)\b", re.I)
PARSERS = re.compile(r"\b(sed|awk|jq|eval|Invoke-Expression|ConvertFrom-Json|ConvertTo-Json|Select-String)\b", re.I)
MARKER = re.compile(r"nexgen:\s*allow\b.*reason\s*=\s*[\"']?(\S[^\"']{3,})", re.I)
MARKER_WINDOW = 15
MARKED_CAP_FACTOR = 4
SMALL_EDIT = 10  # lines a script that already exceeds the cap may still grow by


def logic_signals(text: str | None) -> int:
    lines = (text or "").splitlines()
    return sum(1 for line in lines if CONTROL.match(line)) + sum(len(PARSERS.findall(line)) for line in lines)


def has_reason(text: str) -> bool:
    return any(MARKER.search(line) for line in text.splitlines()[:MARKER_WINDOW])


def _grows(change: Change, lines: int, logic: int) -> bool:
    return change.old is None or lines > line_count(change.old) or logic > logic_signals(change.old)


def check_shell(changes: list[Change], cfg: Config) -> list[Violation]:
    found = []
    for change in changes:
        if not selected(change, cfg, SHELL_EXT):
            continue
        lines, logic = line_count(change.new), logic_signals(change.new)
        if not _grows(change, lines, logic):
            continue
        over = lines > cfg.shell_max_lines or logic > cfg.shell_max_logic
        if over and not has_reason(change.new):
            found.append(Violation(change.path, f"{lines} lines and {logic} logic signals "
                                   f"(limits {cfg.shell_max_lines}/{cfg.shell_max_logic}). Put the logic in the "
                                   "project's main language and keep this a thin launcher, or add "
                                   '`nexgen: allow reason="why shell is the right tool"` at the top.', "shell"))
        elif over and lines > cfg.shell_max_lines * MARKED_CAP_FACTOR and (
                change.old is None or lines - line_count(change.old) > SMALL_EDIT):
            found.append(Violation(change.path, f"{lines} lines exceeds the hard cap "
                                   f"{cfg.shell_max_lines * MARKED_CAP_FACTOR} even with a reason. Split it.", "shell"))
    return found
