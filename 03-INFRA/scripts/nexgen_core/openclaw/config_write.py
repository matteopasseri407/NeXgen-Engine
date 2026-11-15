"""Handing a configuration patch to OpenClaw, and reading its verdict.

One job: put the patch document beside the configuration, run `openclaw config
patch` on it, and turn the answer into "accepted" or "refused, and why".

OpenClaw owns the mechanism this module replaced. Objects merge recursively,
arrays and scalars replace, `--replace-path` makes a subtree exactly the one in
the patch, and the whole result is validated before it is committed: the schema,
the SecretRefs, and the model references. The previous file is kept in a rotating
backup beside it, the new one is published in one atomic step at 0600, and a
patch OpenClaw does not like leaves the file exactly as it was. So there is no
restore path here: a refusal has nothing to undo.

The engine keeps the policy: which sections to send (`setup_write.patch_document`),
the gateway stopped around the write, and what a refusal means for the person.
Both callers go through this module so the flags and the parsing are written once.
"""
from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from nexgen_core.openclaw.setup_commands import config_env, patch_argv
from nexgen_core.openclaw_service import RunResult

#: The patch document's name: beside the configuration, named after it, gone after
#: the write. It carries the gateway token, so it is created private and removed.
PATCH_SUFFIX = ".patch.json5"

#: What OpenClaw calls the copy of the previous file: a ring of five beside it
#: (`.bak` and `.bak.1` to `.bak.4`), all at 0600, rotated on every write.
BACKUP_SUFFIX = ".bak"

#: Read, check and publish outlast a question, because a large patch is validated
#: whole. Bounded anyway: a command that never returns must not own the terminal.
PATCH_TIMEOUT_S = 120.0


@dataclass(frozen=True)
class Verdict:
    """What OpenClaw said about one patch: whether it takes it, and why not."""

    ok: bool
    detail: str = ""


def patch_path(config_path: Path) -> Path:
    """Where the patch document waits while it is applied."""
    return config_path.with_name(config_path.name + PATCH_SUFFIX)


def backup_path(config_path: Path) -> Path:
    """Where OpenClaw keeps the previous configuration: a rotating ring beside it."""
    return config_path.with_name(config_path.name + BACKUP_SUFFIX)


def write_patch(config_path: Path, text: str) -> Path:
    """Put the patch beside the configuration, unreadable to anyone but its owner.

    The mode is set when the file is created, not after: it holds the same gateway
    token as the configuration, and a chmod after a plain write leaves a window in
    which every local user can read it.
    """
    target = patch_path(config_path)
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return target


def clear_patch(config_path: Path) -> None:
    """Remove the patch document: it is a message to OpenClaw, not a record."""
    patch_path(config_path).unlink(missing_ok=True)


def verdict(result: RunResult) -> Verdict:
    """The dry run's own JSON answer, or the words OpenClaw printed instead.

    `--dry-run --json` answers `{ok, operations, checks, errors}`, where every
    error carries a `kind` and a sentence a person can act on. When the file in
    force cannot even be read, OpenClaw prints that reason instead of the JSON,
    so the text is the answer in that case.
    """
    answer = _json_object(result.stdout)
    if answer is None:
        return Verdict(False, _words(result))
    if answer.get("ok") is True:
        return Verdict(True)
    reasons = [
        str(error.get("message", "")).strip()
        for error in answer.get("errors") or []
        if isinstance(error, dict) and str(error.get("message", "")).strip()
    ]
    return Verdict(False, "\n".join(reasons) or _words(result))


def accepted(result: RunResult) -> Verdict:
    """The verdict of a real patch, from its exit code and its own words."""
    return Verdict(True) if result.returncode == 0 else Verdict(False, _words(result))


def dry_run(
    run, openclaw: str, config_path: Path, env: Mapping[str, str], replace_paths: list[str],
) -> Verdict:
    """Ask whether the patch waiting beside the file would be accepted. Writes nothing."""
    argv = patch_argv(openclaw, patch_path(config_path), dry_run=True, replace_paths=replace_paths)
    return verdict(run(argv, config_env(env, config_path)))


def apply(
    run, openclaw: str, config_path: Path, env: Mapping[str, str], replace_paths: list[str],
) -> Verdict:
    """Let OpenClaw merge, validate, back up and publish the patch waiting beside the file."""
    argv = patch_argv(openclaw, patch_path(config_path), replace_paths=replace_paths)
    return accepted(run(argv, config_env(env, config_path)))


def _json_object(text: str | None) -> dict | None:
    """The JSON object a stream carries, or None when it carries something else."""
    stripped = (text or "").strip()
    if not stripped.startswith("{"):
        return None
    try:
        loaded = json.loads(stripped)
    except ValueError:  # nexgen: allow reason="a stream that is not JSON is the CLI's own words, and the caller reads those as the refusal instead of an answer"
        return None
    return loaded if isinstance(loaded, dict) else None


def _words(result: RunResult) -> str:
    """Both streams' non-empty lines, which is where OpenClaw puts the reason."""
    lines = [
        line.strip()
        for stream in (result.stdout, result.stderr)
        for line in (stream or "").splitlines()
        if line.strip()
    ]
    return "\n".join(lines)
