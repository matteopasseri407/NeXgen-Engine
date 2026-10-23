"""Merging the proposal into `openclaw.json`, atomically, with a way back.

One job: take the sections the engine owns, put them inside the file that is
already in force without losing a single key the person wrote, publish the
result in one atomic step, and be able to put the previous file back. Nothing
here decides *when* that happens: the gateway must be stopped first, and that
is the orchestrator's business.

Three rules are not preferences:

- a key the person wrote outside the proposal survives, at every depth;
- `plugins.allow` is a set, so it is merged and sorted rather than replaced:
  replacing it would silently disable a plugin the person installed;
- the gateway token is generated only when there is none, and never returned
  in a printable form. `with_token` says whether it generated one; no caller
  ever receives the value.
"""
from __future__ import annotations

import json
import secrets
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from nexgen_core.files import atomic_write_text
from nexgen_core.openclaw.profile import PENDING_TOKEN

#: The suffix a backup carries, so the file it protects is still recognisable.
BACKUP_MARKER = ".pre-nexgen-"


def _merge_dict(current: dict[str, Any], proposed: dict[str, Any]) -> dict[str, Any]:
    """A recursive merge that never deletes: proposal wins, absent keys stay."""
    merged = dict(current)
    for key, value in proposed.items():
        existing = merged.get(key)
        if isinstance(value, dict) and isinstance(existing, dict):
            merged[key] = _merge_dict(existing, value)
        else:
            merged[key] = value
    return merged


def _union_allow(current: Any, proposed: Any) -> list[str]:
    """`plugins.allow` as a sorted set: the union, never the proposal alone."""
    items: set[str] = set()
    for source in (current, proposed):
        if isinstance(source, list):
            items.update(str(item) for item in source)
    return sorted(items)


def _merge_models(current: Any, proposed: list[Any]) -> list[Any]:
    """Model entries merged by `id`: a model the person added is not deleted.

    The list is keyed by `id` even though OpenClaw stores it as an array, so
    replacing it whole would drop a hand-written model for the same provider.
    """
    merged = [item for item in current if isinstance(item, dict)] if isinstance(current, list) else []
    positions = {str(item.get("id")): index for index, item in enumerate(merged)}
    for item in proposed:
        if not isinstance(item, dict):
            continue
        key = str(item.get("id"))
        if key in positions:
            merged[positions[key]] = _merge_dict(merged[positions[key]], item)
        else:
            positions[key] = len(merged)
            merged.append(dict(item))
    return merged


def _merge_plugin_entries(current: Any, proposed: Any) -> Any:
    """Plugin entries merged per plugin, then the allow list unioned."""
    merged = _merge_dict(current, proposed) if isinstance(current, dict) and isinstance(proposed, dict) else proposed
    if isinstance(merged, dict) and isinstance(proposed, dict) and "allow" in proposed:
        merged["allow"] = _union_allow(current.get("allow") if isinstance(current, dict) else None, proposed["allow"])
    return merged


def merged_document(current: dict[str, Any] | None, proposed: dict[str, Any]) -> dict[str, Any]:
    """The file to write: the person's keys, the engine's values.

    `current` is the configuration in force as a value (it may be None when
    there is no file yet); the result is a new document and neither input is
    modified.
    """
    base = dict(current) if isinstance(current, dict) else {}
    merged = _merge_dict(base, proposed)

    if isinstance(proposed.get("plugins"), dict):
        merged["plugins"] = _merge_plugin_entries(base.get("plugins"), proposed["plugins"])

    proposed_providers = proposed.get("models", {}).get("providers")
    if isinstance(proposed_providers, dict):
        merged_models = dict(merged.get("models") or {})
        providers = dict(merged_models.get("providers") or {})
        current_providers = base.get("models", {}).get("providers") if isinstance(base.get("models"), dict) else {}
        for provider, block in proposed_providers.items():
            if not isinstance(block, dict) or not isinstance(block.get("models"), list):
                continue
            existing = current_providers.get(provider) if isinstance(current_providers, dict) else None
            current_list = existing.get("models") if isinstance(existing, dict) else None
            merged_block = _merge_dict(providers.get(provider) or {}, block)
            merged_block["models"] = _merge_models(current_list, block["models"])
            providers[provider] = merged_block
        merged_models["providers"] = providers
        merged["models"] = merged_models
    return merged


def generate_token() -> str:
    """A URL-safe secret, 32 bytes of entropy, generated only when missing."""
    return secrets.token_urlsafe(32)


def with_token(
    document: dict[str, Any],
    token_factory: Callable[[], str] = generate_token,
) -> tuple[dict[str, Any], bool]:
    """The document with a real gateway token, and whether one was generated.

    An existing token is the person's and is kept; the placeholder a plan
    prints (or a file left behind by an interrupted run) counts as missing.
    The value is returned inside the document only, never to a printer.
    """
    gateway = document.get("gateway")
    auth = gateway.get("auth") if isinstance(gateway, dict) else None
    if not isinstance(auth, dict):
        return document, False
    token = auth.get("token")
    if isinstance(token, str) and token.strip() and token != PENDING_TOKEN:
        return document, False
    return {**document, "gateway": {**gateway, "auth": {**auth, "token": token_factory()}}}, True


def render(document: dict[str, Any]) -> str:
    """The bytes `openclaw.json` receives: JSON, one key per line, trailing LF."""
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def backup_path(path: Path, stamp: str) -> Path:
    """Where the copy of the previous file lives: beside it, with the stamp."""
    return path.with_name(f"{path.name}{BACKUP_MARKER}{stamp}")


def backup_file(path: Path, stamp: str) -> Path | None:
    """Copy the file in force aside; None when there is nothing to protect."""
    if not path.is_file():
        return None
    target = backup_path(path, stamp)
    shutil.copy2(path, target)
    return target


def restore_file(path: Path, backup: Path | None) -> None:
    """Put the previous file back, or remove the one this run created."""
    if backup is not None and backup.is_file():
        shutil.copy2(backup, path)
        return
    path.unlink(missing_ok=True)


def write_document(path: Path, text: str) -> None:
    """Publish the document in one atomic step (temp file, then `os.replace`)."""
    atomic_write_text(path, text)
