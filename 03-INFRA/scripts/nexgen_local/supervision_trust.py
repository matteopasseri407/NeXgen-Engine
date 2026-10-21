"""The folders the explorer trusts: one exact path, added once, written atomically.

One job: make sure `agy` opens a folder without asking, by putting that exact
path in the tool's own settings file. Only `trustedWorkspaces` is touched, the
file is replaced in one step, and the copy that was there before is kept beside
it, so a wrong entry is always one rename away from being undone. A settings
file that does not parse is an error: rewriting it would silently discard every
other setting the owner has.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

from .supervision_state import SupervisionError

#: The explorer's own settings, in the home of the person running it.
SETTINGS_RELATIVE = Path(".gemini") / "antigravity-cli" / "settings.json"
#: The one key this module writes.
KEY = "trustedWorkspaces"
#: The copy kept beside the settings, so a bad entry is one rename away from undone.
BACKUP_SUFFIX = ".bak"


class TrustError(SupervisionError):
    """The explorer's settings exist but cannot be read, or do not hold what they claim."""


def settings_path(home: str | Path | None = None) -> Path:
    """Where the explorer keeps its settings, under the home of the person running it."""
    return (Path(home).expanduser() if home else Path.home()) / SETTINGS_RELATIVE


def ensure(folder: str | Path, *, settings: str | Path | None = None) -> dict:
    """Add this exact folder to the trusted workspaces, if it is not there already.

    The answer says whether anything was written: a second pass over the same
    folder writes nothing, so a loop of scouts leaves one entry and one backup.
    """
    target = Path(settings).expanduser() if settings else settings_path()
    wanted = _exact(folder)
    payload = _read(target)
    listed = _list(payload, target)
    if wanted in {_exact(item) for item in listed}:
        return {"settings": str(target), "folder": wanted, "added": False, "backup": ""}
    payload[KEY] = [*listed, wanted]
    backup = _atomic(target, payload)
    return {"settings": str(target), "folder": wanted, "added": True,
            "backup": str(backup) if backup else ""}


def inside(folder: str | Path, *, settings: str | Path | None = None) -> bool:
    """Whether this exact folder is already trusted; a parent folder does not cover it."""
    target = Path(settings).expanduser() if settings else settings_path()
    wanted = _exact(folder)
    return wanted in {_exact(item) for item in _list(_read(target), target)}


def _exact(folder: str | Path) -> str:
    """One folder as the settings name it: absolute, resolved, no trailing separator."""
    return str(Path(folder).expanduser().resolve())


def _read(target: Path) -> dict:
    """The settings as they are, or an empty object when the file does not exist yet."""
    try:
        raw = target.read_text(encoding="utf-8")
    except FileNotFoundError:  # nexgen: allow reason="un esploratore mai avviato non ha ancora un file di impostazioni, e la prima fiducia lo crea"
        return {}
    except OSError as exc:
        raise TrustError(f"impostazioni dell'esploratore non leggibili: {target} ({exc})") from exc
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise TrustError(
            f"{target} non è JSON: la fiducia non lo sovrascrive (sistemalo a mano)"
        ) from exc
    if not isinstance(parsed, dict):
        raise TrustError(f"{target} non è un oggetto JSON: la fiducia non lo sovrascrive")
    return parsed


def _list(payload: dict, target: Path) -> list[str]:
    """The trusted folders as written; a value of another shape is refused, not fixed."""
    listed = payload.get(KEY, [])
    if not isinstance(listed, list) or not all(isinstance(item, str) for item in listed):
        raise TrustError(f"`{KEY}` in {target} non è un elenco di percorsi")
    return list(listed)


def _atomic(target: Path, payload: dict) -> Path | None:
    """Keep the old file as a backup, then replace it in one step."""
    target.parent.mkdir(parents=True, exist_ok=True)
    backup: Path | None = None
    if target.is_file():
        backup = target.with_name(target.name + BACKUP_SUFFIX)
        shutil.copy2(target, backup)
    handle, name = tempfile.mkstemp(prefix=f".{target.name}.", dir=str(target.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(name, target)
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise
    return backup
