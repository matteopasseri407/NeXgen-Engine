"""Whether OpenClaw is here: the package, the release, the declaration, the file.

One job: the three checks about the installation itself, so `doctor.py` stays
the rules of a configuration and this module answers "is there anything to judge
at all, and is it the release the engine configures against". Every function
takes values and returns one outcome: nothing here reads a machine.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from nexgen_core.i18n import t
from nexgen_core.openclaw.detect import Detection
from nexgen_core.report import CheckOutcome, Severity


def check_installed(snapshot: Detection, declared: bool, pinned: str | None) -> CheckOutcome:
    """Installed, and on the release the engine configures against."""
    install = snapshot.openclaw
    install_command = f"npm install -g openclaw@{pinned or 'latest'}"
    if not install.installed:
        return CheckOutcome(
            id="openclaw.installed",
            severity=Severity.BROKEN,
            message=t("The OpenClaw module is declared on this machine but OpenClaw is not installed."),
            action=t("Install the pinned release: {command}", command=install_command),
        )
    if pinned is None:
        return CheckOutcome(
            id="openclaw.installed",
            severity=Severity.WARN,
            message=t("OpenClaw {version} is installed, but the engine cannot read its pin.",
                      version=install.version or "?"),
            action=t("Check agent-universal-layer/openclaw/pin.json: it holds the release the engine expects."),
        )
    if install.version != pinned:
        return CheckOutcome(
            id="openclaw.installed",
            severity=Severity.WARN,
            message=t("OpenClaw {version} is installed, the engine configures {pinned}.",
                      version=install.version or "?", pinned=pinned),
            action=t("Align it with: {command}", command=install_command),
        )
    return CheckOutcome(
        id="openclaw.installed",
        severity=Severity.OK,
        message=t("OpenClaw {version} is the pinned release.", version=install.version),
    )


def check_declaration(declared: bool) -> CheckOutcome | None:
    """Installed but not declared: the engine will not keep it aligned."""
    if declared:
        return None
    return CheckOutcome(
        id="openclaw.declared",
        severity=Severity.WARN,
        message=t("OpenClaw is installed but this machine does not declare the module: the engine will not keep it aligned."),
        action=t("Declare 'openclaw: local' under this host in modules.state.yaml, or remove OpenClaw."),
    )


def check_config(path: Path, config: dict[str, Any] | None, error: str | None) -> CheckOutcome:
    """The file exists, is JSON, and is an object OpenClaw can use."""
    if config is not None:
        return CheckOutcome(
            id="openclaw.config",
            severity=Severity.OK,
            message=t("The configuration at {path} is valid JSON.", path=path),
        )
    if error:
        return CheckOutcome(
            id="openclaw.config",
            severity=Severity.BROKEN,
            message=t("The configuration at {path} cannot be read: {error}", path=path, error=error),
            action=t("Fix the file or move it aside; 'nexgen openclaw plan' shows what the engine would write."),
        )
    return CheckOutcome(
        id="openclaw.config",
        severity=Severity.WARN,
        message=t("OpenClaw has no configuration at {path} yet.", path=path),
        action=t("Run 'nexgen openclaw plan' to see what the engine would write."),
    )
