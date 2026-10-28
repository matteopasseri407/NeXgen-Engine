"""Whether the OpenCode config on disk is one the installed CLI can read.

The engine writes the V2 dialect (`permissions`, ordered `action`/`resource`/
`effect` rules) and `opencode_compat` decides which dialect to write. This is
the other half of the same question, asked about what is already there: a V2
config in front of a V1 binary is refused at parse time, so every `opencode`
invocation dies and the whole OpenCode lane -- council seats, the model
inventory, the provider behind it -- dies with it, silently.

Issue 142 was exactly that state: config written for V2, `opencode-ai@1.19.3`
on PATH, doctor green. A guard cycle never asks this question, because it is
about the machine as it is now, not about what a render wrote; the doctor does.
"""
from __future__ import annotations

from pathlib import Path

from nexgen_core.i18n import t
from nexgen_core.jsonc import parse_jsonc
from nexgen_core.opencode_compat import binary_path, installed_major, installed_version
from nexgen_core.paths import opencode_config_path
from nexgen_core.report import CheckOutcome, Severity

#: The V2 key: its presence is what a V1 binary refuses to parse.
_V2_KEY = "permissions"


def check_opencode_config_dialect(home: Path) -> CheckOutcome | None:
    """Reports a config that speaks V2 to a CLI that speaks V1.

    Silence means "nothing to say": no config at all (OpenCode never ran here),
    or a config without `permissions`, which every V1 binary reads happily. The
    missing binary is a warning rather than a failure: a desktop-only install
    keeps its CLI off PATH, and the config is then unusable rather than broken.
    """
    path = opencode_config_path(home)
    if not path.is_file():
        return None
    try:
        data = parse_jsonc(path.read_text(encoding="utf-8"))
    # A config this parser cannot read is a syntax question, not a dialect one, and the
    # CLI's own parse error names the line: answering here would use this check's id
    # for a different problem.
    except (OSError, ValueError):  # nexgen: allow reason="an unreadable config is a syntax question the CLI reports itself, not a dialect one"
        return None
    if not isinstance(data, dict) or _V2_KEY not in data:
        return None

    major = installed_major()
    if major is not None and major >= 2:
        return CheckOutcome(
            id="opencode.config_dialect",
            severity=Severity.OK,
            message=t("The OpenCode config and the installed CLI both speak V2."),
        )
    if binary_path() is None:
        return CheckOutcome(
            id="opencode.config_dialect",
            severity=Severity.WARN,
            message=t("The OpenCode config uses V2 `permissions` but no `opencode` binary is on PATH."),
            action=t("Install the OpenCode 2 CLI, or make 'opencode' resolve to it."),
            detail=str(path),
        )
    return CheckOutcome(
        id="opencode.config_dialect",
        severity=Severity.BROKEN,
        message=t(
            "OpenCode {version} cannot parse the V2 `permissions` in {path}: every `opencode` invocation fails there.",
            version=installed_version() or t("(version not readable)"),
            path=str(path),
        ),
        action=t("Install the OpenCode 2 CLI and make 'opencode' on PATH resolve to it, then run 'nexgen sync apply'."),
    )
