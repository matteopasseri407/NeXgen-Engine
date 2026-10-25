# nexgen: one-job reason="retire adopted MCP servers and skills from the canonical manifests and filesystem"
"""Retirement of OpenClaw-adopted resources from the canonical library.

One job: remove resources that entered with OpenClaw provenance from the
canonical manifests (`mcp/manifest.yaml`, `skills.manifest.yaml`) and unmount
their CLI views.

Invariants:
- Resources belonging to the canonical library (without `origin: openclaw` or
  an OpenClaw identifier) are refused and never removed.
- Retired MCP servers are appended to `retired_servers:` so every CLI unmounts
  them on the next sync while preserving user-declared entries.
- Retired skills are removed from `skills.manifest.yaml`, deleted from the
  Vault, and their active CLI views unlinked via `SkillMaterializer`.
"""
from __future__ import annotations

import os
import re
import shutil
from collections.abc import Callable
from pathlib import Path

from nexgen_core.config import load_mcp_manifest, load_skills_manifest
from nexgen_core.files import atomic_write_text
from nexgen_core.i18n import t
from nexgen_core.manifest_text import entry_span
from nexgen_core.paths import (
    mcp_manifest,
    resolve_engine_root,
    resolve_home,
    resolve_vault_data,
    skills_manifest,
)
from nexgen_core.skills import SkillMaterializer


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


def is_openclaw_skill(skill_entry: dict, skill_dir: Path) -> bool:
    """True if skill has OpenClaw provenance from manifest or frontmatter."""
    if skill_entry.get("openclaw_id") or skill_entry.get("origin") == "openclaw":
        return True
    if skill_dir.is_dir():
        skill_md = skill_dir / "SKILL.md"
        if skill_md.is_file():
            try:
                return "origin: openclaw" in skill_md.read_text(encoding="utf-8")
            except OSError:  # nexgen: allow reason="unreadable SKILL.md file cannot be checked for provenance"
                return False
    return False


def retire_adopted_skill(
    name: str,
    *,
    vault_data: Path | None = None,
    engine_root: Path | None = None,
    home: Path | None = None,
) -> tuple[bool, str]:
    """Retire an adopted skill: remove from manifest, delete folder, unmount views."""
    resolved_home = resolve_home(home)
    vault = resolve_vault_data(resolved_home, vault_data)
    engine = resolve_engine_root(resolved_home, engine_root)
    path = skills_manifest(vault)
    if not path.is_file():
        return False, t("skills manifest does not exist")
    try:
        raw_before = path.read_text(encoding="utf-8")
        data = load_skills_manifest(path)
    except Exception as exc:  # noqa: BLE001 - nexgen: allow reason="unreadable manifest is reported as failure"
        return False, t("cannot read skills manifest ({error})", error=exc)

    skills = data.get("skills") or {}
    if name not in skills:
        return False, t("skill '{name}' is not in skills manifest", name=name)
    skill_dir = vault / "03-INFRA" / "agent-universal-layer" / "skills" / name

    if not is_openclaw_skill(skills[name], skill_dir):
        return False, t("skill '{name}' has no openclaw provenance: will not remove automatically", name=name)

    updated = _without_entry(raw_before, name)
    materializer = SkillMaterializer(vault_data=vault, engine_root=engine, home=resolved_home)
    error = _publish(path, updated, load_skills_manifest)
    if error:
        return False, t("skills manifest modification failed validation: {error}", error=error)
    if skill_dir.is_dir():
        shutil.rmtree(skill_dir, ignore_errors=True)
    materializer.materialize(apply=True)
    return True, t("skill '{name}' retired from library", name=name)
