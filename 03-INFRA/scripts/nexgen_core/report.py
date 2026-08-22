"""Reporting and outcomes system for NeXgen Engine v2.

Every check returns a structured object with four main fields (id, severity,
message, action) plus an optional automatic remedy.

Core rules:
1. If there's a remedy and the check is broken, it runs silently.
2. If there's no remedy and the state is broken, it's reported with a clear message and a single action.
3. If the state is undetermined, it's reported distinctly from broken.
4. If the state is ok, it's counted and kept silent (unless verbose mode).
5. Warnings are silent for the human report: they need no end-user action, an agent takes them in charge (they surface in verbose mode and in JSON).
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from nexgen_core.i18n import t


class Severity(str, Enum):
    """Severity levels for a check."""
    OK = "ok"
    WARN = "warn"
    BROKEN = "broken"
    UNDETERMINED = "undetermined"


@dataclass
class CheckOutcome:
    """Normalized outcome of a system or configuration check."""

    id: str
    severity: Severity | str
    message: str
    action: str | None = None
    remedy: Callable[[], bool | None] | None = None
    detail: str | None = None
    remedied: bool = False

    def __post_init__(self) -> None:
        if isinstance(self.severity, str):
            self.severity = Severity(self.severity.lower())

    def to_dict(self) -> dict[str, Any]:
        """Serializes to a dict for logging or JSON output."""
        return {
            "id": self.id,
            "severity": self.severity.value,
            "message": self.message,
            "action": self.action,
            "detail": self.detail,
            "remedied": self.remedied,
            "has_remedy": self.remedy is not None,
        }


@dataclass
class Report:
    """Collector and formatter for check outcomes."""

    outcomes: list[CheckOutcome] = field(default_factory=list)
    log_entries: list[str] = field(default_factory=list)

    def add(self, outcome: CheckOutcome, apply_remedy: bool = True) -> CheckOutcome:
        """Adds an outcome and, if applicable, applies its automatic remedy."""
        if apply_remedy and outcome.remedy and outcome.severity == Severity.BROKEN:
            try:
                res = outcome.remedy()
                # Treated as success unless it raises or explicitly returns False
                if res is not False:
                    outcome.remedied = True
                    outcome.severity = Severity.OK
                    log_msg = f"Automatic remedy applied successfully for [{outcome.id}]: {outcome.message}"
                    self.log_entries.append(log_msg)
            except Exception as exc:
                outcome.detail = f"{outcome.detail or ''} (Remedy failed: {exc})".strip()
                self.log_entries.append(f"Remedy failed for [{outcome.id}]: {exc}")

        self.outcomes.append(outcome)
        return outcome

    @property
    def ok_count(self) -> int:
        return sum(1 for o in self.outcomes if o.severity == Severity.OK)

    @property
    def broken(self) -> list[CheckOutcome]:
        return [o for o in self.outcomes if o.severity == Severity.BROKEN]

    @property
    def warnings(self) -> list[CheckOutcome]:
        return [o for o in self.outcomes if o.severity == Severity.WARN]

    @property
    def undetermined(self) -> list[CheckOutcome]:
        return [o for o in self.outcomes if o.severity == Severity.UNDETERMINED]

    @property
    def is_healthy(self) -> bool:
        return len(self.broken) == 0 and len(self.undetermined) == 0

    @property
    def has_failures(self) -> bool:
        return len(self.broken) > 0

    def exit_code(self, strict: bool = False) -> int:
        """Computes the process exit code: 0 if all ok/remedied, >0 if there are problems."""
        if self.has_failures:
            return 1
        if strict and len(self.undetermined) > 0:
            return 1
        return 0

    def format_human(self, verbose: bool = False) -> str:
        """Formats the report for human reading, following the anti-noise rules."""
        lines: list[str] = []

        # 1. Unremedied broken checks are shown first, with priority
        if self.broken:
            lines.append(t("Problems detected that need your attention:"))
            for item in self.broken:
                line = f"  - {item.message}"
                if item.action:
                    line += f"\n    {t('Suggested action:')} {item.action}"
                if item.detail and verbose:
                    line += f"\n    [Detail: {item.detail}]"
                lines.append(line)

        # 2. Warnings are silent for the human report (verbose only):
        #    they block nothing and an agent takes them in charge.
        if self.warnings and verbose:
            if lines:
                lines.append("")
            lines.append(t("Warnings (nothing is blocked):"))
            for item in self.warnings:
                line = f"  - {item.message}"
                if item.action:
                    line += f"\n    {t('Suggested action:')} {item.action}"
                if item.detail and verbose:
                    line += f"\n    [Detail: {item.detail}]"
                lines.append(line)

        # 3. Undetermined checks (e.g. no network, or service unreachable)
        if self.undetermined:
            if lines:
                lines.append("")
            lines.append(t("Checks that could not be verified right now:"))
            for item in self.undetermined:
                line = f"  - {item.message}"
                if item.action:
                    line += f"\n    {t('Suggested action:')} {item.action}"
                if item.detail and verbose:
                    line += f"\n    [Detail: {item.detail}]"
                lines.append(line)

        # 3. Summary, or verbose detail, of passed checks
        if verbose:
            if lines:
                lines.append("")
            lines.append(t("Checks passed ({count}):", count=self.ok_count))
            for item in self.outcomes:
                if item.severity == Severity.OK:
                    remedy_tag = f" ({t('auto-remedied')})" if item.remedied else ""
                    lines.append(f"  [OK] {item.id}: {item.message}{remedy_tag}")
        elif not self.broken and not self.undetermined:
            # Silent, or a single reassuring line
            lines.append(t("Everything is aligned and working ({count} checks passed).", count=self.ok_count))
        else:
            lines.append("\n" + t("({count} checks passed)", count=self.ok_count))

        return "\n".join(lines)

    def format_json(self) -> str:
        """Returns the full report in JSON format."""
        return json.dumps({
            "healthy": self.is_healthy,
            "ok_count": self.ok_count,
            "broken_count": len(self.broken),
            "warning_count": len(self.warnings),
            "undetermined_count": len(self.undetermined),
            "broken": [o.to_dict() for o in self.broken],
            "warnings": [o.to_dict() for o in self.warnings],
            "undetermined": [o.to_dict() for o in self.undetermined],
            "all": [o.to_dict() for o in self.outcomes],
            "logs": self.log_entries,
        }, indent=2)
