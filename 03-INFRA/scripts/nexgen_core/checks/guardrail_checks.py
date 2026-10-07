"""Whether each CLI really calls the guardrail that is registered for it."""
from __future__ import annotations

import json
import time
from pathlib import Path

from nexgen_core.i18n import t
from nexgen_core.report import CheckOutcome, Severity
from nexgen_core.runtimes import REGISTRY


def _read_record(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def check_guardrail_consulted(home: Path) -> list[CheckOutcome]:
    """One outcome per CLI that has a guardrail installed.

    A registration only proves the hook is written down. Whether the CLI ever reaches it is
    another question, and the one that matters: OpenCode's hook is only called for actions it
    is about to ask about, so a posture that allowed shell outright left the plugin registered
    and unreachable behind a green check. Each adapter records every consultation (see
    nexgen-guardrail-core.mjs), so "consulted at least once since install" is a fact the doctor
    can read. Never consulted is undetermined, not broken: it is also what a CLI nobody has used
    since the install looks like.
    """
    outcomes: list[CheckOutcome] = []
    for runtime in REGISTRY.values():
        if not runtime.is_installed(home):
            continue
        sidecar_path = runtime.guardrail_sidecar(home)
        if sidecar_path is None or not sidecar_path.is_file():
            continue
        sidecar = runtime.read_guardrail_sidecar(sidecar_path)
        if not sidecar.get("hooks"):
            continue
        audit = Path(sidecar.get("auditFile") or runtime.guardrail_audit_file(home, runtime.name))
        record = _read_record(audit)
        check_id = f"guardrail.consulted.{runtime.name}"
        if not record.get("count"):
            outcomes.append(CheckOutcome(
                id=check_id,
                severity=Severity.UNDETERMINED,
                message=t(
                    "{cli}: the guardrail is installed but has never been consulted. Either nothing has been "
                    "run in {cli} since it was installed, or {cli} is not calling it.",
                    cli=runtime.name,
                ),
                action=t(
                    "Run a shell command in {cli} and check again. If this stays, {cli} is not reaching the "
                    "guardrail and its shell commands are not being checked.",
                    cli=runtime.name,
                ),
            ))
            continue
        minutes = max(0.0, time.time() - float(record.get("at", 0)) / 1000) / 60
        outcomes.append(CheckOutcome(
            id=check_id,
            severity=Severity.OK,
            message=t(
                "{cli}: the guardrail was consulted {count} times, last {minutes:.0f} minutes ago.",
                cli=runtime.name, count=int(record["count"]), minutes=minutes,
            ),
        ))
    return outcomes
