# nexgen: one-job reason="retire an adopted MCP server from the canonical manifest"
"""Retirement of an OpenClaw-adopted MCP server from the canonical library.

One job: remove a single server that entered with OpenClaw provenance from
`mcp/manifest.yaml` and list it under `retired_servers:`, which is the manifest's
own cross-CLI removal mechanism -- the next sync unmounts it everywhere and
leaves every entry a person wrote in a CLI untouched.

Only servers are retired this way. A skill cannot be: adoption archives the
Workshop copy it came from, so the OpenClaw side of an adopted skill is gone by
design and any absence check would delete what the library had just adopted.
A skill leaves the library when its manifest entry does.

Invariants:
- A server without `origin: openclaw` belongs to the canonical library and is
  refused, never removed.
- The new text must parse before it replaces the old one.
"""
from __future__ import annotations

import os
import re
from collections.abc import Callable
from pathlib import Path

from nexgen_core.config import load_mcp_manifest
from nexgen_core.files import atomic_write_text
from nexgen_core.i18n import t
from nexgen_core.manifest_text import entry_span
from nexgen_core.paths import mcp_manifest


def _without_entry(text: str, name: str) -> str:
    """The manifest text with one entry's block gone, every other byte untouched.

    The block is found by `manifest_text.entry_span`, the reader every manifest edit
    already uses: it knows the key may be quoted and that the block ends at a blank
    line or the next entry. Two hand-rolled copies of that rule lived here and
    disagreed with it about quoted keys and blank lines.
    """
    span = entry_span(text, name)
    if span is None:
        return text
    start, end = span
    return text[:start] + text[end:]


def _add_retired_server_yaml(text: str, name: str) -> str:
    """Add server name to `retired_servers:` in manifest text, creating the list at the end."""
    if re.search(rf"^\s*-\s+[\"']?{re.escape(name)}[\"']?\s*$", text, re.MULTILINE):
        return text
    match_empty = re.search(r"^retired_servers:\s*\[\s*\]\s*$", text, re.MULTILINE)
    if match_empty:
        return text[:match_empty.start()] + f"retired_servers:\n  - {name}" + text[match_empty.end():]
    match_header = re.search(r"^retired_servers:\s*$", text, re.MULTILINE)
    if match_header:
        pos = match_header.end()
        return text[:pos] + f"\n  - {name}" + text[pos:]
    # A manifest written before this list existed: appended, never prepended, so the
    # document's header comments stay at the top where a person reads them.
    return text.rstrip("\n") + f"\n\nretired_servers:\n  - {name}\n"


def _publish(path: Path, updated: str, validate: Callable[[Path], object]) -> str | None:
    """Replace a manifest with `updated` only once the loader has accepted it.

    The candidate is written with the one writer (`files.atomic_write_text`), the
    same loader the sync will use reads it, and only then is it moved into place:
    a manifest that would no longer parse never exists on the canonical path.
    Returns the loader's complaint, or None when the new text is in place.
    """
    candidate = path.with_name(f"{path.name}.{os.getpid()}.candidate")
    try:
        atomic_write_text(candidate, updated)
        validate(candidate)
    except Exception as exc:  # noqa: BLE001 - nexgen: allow reason="a candidate that does not parse is put back"
        candidate.unlink(missing_ok=True)
        return str(exc)
    os.replace(candidate, path)
    return None


def retire_adopted_server(name: str, *, vault_data: Path | None = None) -> tuple[bool, str]:
    """Retire an adopted MCP server: remove from servers, add to retired_servers."""
    path = mcp_manifest(vault_data)
    if not path.is_file():
        return False, t("MCP manifest does not exist")
    try:
        raw_before = path.read_text(encoding="utf-8")
        data = load_mcp_manifest(path)
    except Exception as exc:  # noqa: BLE001 - nexgen: allow reason="unreadable manifest is reported as failure"
        return False, t("cannot read the manifest ({error}); fix it before modifying", error=exc)
    servers = data.get("servers") or {}
    if name not in servers:
        return False, t("server '{name}' is not declared in servers", name=name)
    target_srv = servers[name]
    if target_srv.get("origin") != "openclaw":
        return False, t("server '{name}' is not origin: openclaw: will not remove automatically", name=name)

    updated = _add_retired_server_yaml(_without_entry(raw_before, name), name)
    error = _publish(path, updated, load_mcp_manifest)
    if error:
        return False, t("manifest modification failed validation: {error}", error=error)
    return True, t("server '{name}' retired from MCP manifest", name=name)
