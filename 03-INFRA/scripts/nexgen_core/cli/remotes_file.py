"""Reads sync/remotes.yaml without ever losing what is in it.

One job: turn the file into a dict, or refuse. A malformed or unreadable file
may still hold the user's mirrors and settings, so callers must not rewrite it
from an empty document. They get a RemotesFileError that names the file instead.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from nexgen_core.i18n import t


class RemotesFileError(Exception):
    """remotes.yaml exists but cannot be read as YAML."""


def read_remotes_file(path: Path) -> dict:
    """The parsed mapping in `path`, or {} when there is no such file.

    Raises RemotesFileError when the file exists but cannot be read or parsed.
    """
    if not path.is_file():
        return {}
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise RemotesFileError(t(
            "{path} could not be read, so it was left unchanged. "
            "Fix or remove it, then retry: {error}",
            path=path, error=exc,
        )) from exc
    return loaded if isinstance(loaded, dict) else {}
