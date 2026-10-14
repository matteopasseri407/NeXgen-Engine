"""Checks for the desktop apps that share a config with a CLI.

ChatGPT desktop bundles its own Codex and reads the same ~/.codex as the
`codex` CLI, so the two can drift apart without anybody noticing. The check
is read-only: a skew is reported, never "fixed", because which of the two
versions should win is the user's call.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from nexgen_core.i18n import t
from nexgen_core.platforms import existing_roots, installed_desktop_apps
from nexgen_core.report import CheckOutcome, Severity

#: How long a version probe may take. The bundled binary is large, but
#: `--version` answers in well under a second; anything slower is a hang.
_VERSION_TIMEOUT_S = 20

_VERSION_RE = re.compile(r"codex-cli\s+(\S+)")


def parse_codex_version(output: str) -> str | None:
    """The version out of `codex --version` ("codex-cli 0.159.2"), or None."""
    match = _VERSION_RE.search(output or "")
    return match.group(1) if match else None


def _binary_version(binary: Path | str) -> str | None:
    """Asks a Codex binary for its version, or None when it cannot say.

    The probe runs with a throwaway CODEX_HOME. Codex writes its home on
    start-up, and a doctor must never touch the ~/.codex that the real
    sessions share.
    """
    try:
        with tempfile.TemporaryDirectory(prefix="nexgen-codex-version-") as scratch:
            env = {**os.environ, "CODEX_HOME": scratch}
            done = subprocess.run(
                [str(binary), "--version"],
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=_VERSION_TIMEOUT_S,
                env=env,
                check=False,
            )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    return parse_codex_version(done.stdout)


def _bundled_codex() -> Path | None:
    """The Codex binary inside an installed ChatGPT desktop app, if there is one."""
    for app in installed_desktop_apps():
        if not app.bundled:
            continue
        for root in existing_roots(app):
            for relative in app.bundled:
                candidate = root / relative
                if candidate.is_file():
                    return candidate
    return None


def _cli_codex() -> str | None:
    return shutil.which("codex")


def check_codex_desktop_skew() -> CheckOutcome | None:
    """ChatGPT desktop's bundled Codex and the CLI `codex` should be the same version.

    Silent when there is nothing to compare: no desktop app, or no CLI on
    the PATH. UNDETERMINED when a version cannot be read, because a guess
    would look like a verified match or a verified skew.
    """
    bundled = _bundled_codex()
    cli = _cli_codex()
    if bundled is None or cli is None:
        return None

    bundled_version = _binary_version(bundled)
    cli_version = _binary_version(cli)
    if bundled_version is None or cli_version is None:
        unreadable = []
        if bundled_version is None:
            unreadable.append(t("the one bundled in ChatGPT desktop"))
        if cli_version is None:
            unreadable.append(t("the codex CLI"))
        return CheckOutcome(
            id="desktop.codex_version_skew",
            severity=Severity.UNDETERMINED,
            message=t("Could not read the Codex version of {which}, so the desktop app and the CLI were not compared.",
                      which=" / ".join(unreadable)),
        )
    if bundled_version == cli_version:
        return CheckOutcome(
            id="desktop.codex_version_skew",
            severity=Severity.OK,
            message=t("ChatGPT desktop and the codex CLI run the same Codex version ({version})", version=cli_version),
        )
    return CheckOutcome(
        id="desktop.codex_version_skew",
        severity=Severity.WARN,
        message=t(
            "ChatGPT desktop bundles Codex {desktop} but the codex CLI is {cli}; both read the same ~/.codex, "
            "so each may run settings the other does not understand.",
            desktop=bundled_version, cli=cli_version,
        ),
    )
