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
from pathlib import Path

from nexgen_core.config import load_mcp_manifest, load_skills_manifest
from nexgen_core.i18n import t
from nexgen_core.paths import (
    mcp_manifest,
    resolve_engine_root,
    resolve_home,
    resolve_vault_data,
    skills_manifest,
)
from nexgen_core.skills import SkillMaterializer


def _remove_server_yaml_block(text: str, name: str) -> str:
    """Remove a server entry from the `servers:` mapping in manifest text."""
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    in_target = False
    target_prefix = f"  {name}:"

    for line in lines:
        stripped = line.rstrip("\r\n")
        if stripped == target_prefix or stripped.startswith(f"  {name}: "):
            in_target = True
            continue
        if in_target:
            if line.startswith("    "):
                continue
            if stripped == "" or line.strip().startswith("#"):
                continue
            in_target = False
        out.append(line)
    return "".join(out)


def _add_retired_server_yaml(text: str, name: str) -> str:
    """Add server name to `retired_servers:` in manifest text."""
    if re.search(rf"^\s*-\s+[\"']?{re.escape(name)}[\"']?\s*$", text, re.MULTILINE):
        return text
    match_empty = re.search(r"^retired_servers:\s*\[\s*\]\s*$", text, re.MULTILINE)
    if match_empty:
        return text[:match_empty.start()] + f"retired_servers:\n  - {name}" + text[match_empty.end():]
    match_header = re.search(r"^retired_servers:\s*$", text, re.MULTILINE)
    if match_header:
        pos = match_header.end()
        return text[:pos] + f"\n  - {name}" + text[pos:]
    return f"retired_servers:\n  - {name}\n\n" + text


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

    updated = _add_retired_server_yaml(_remove_server_yaml_block(raw_before, name), name)
    temp_path = path.with_suffix(".tmp")
    temp_path.write_text(updated, encoding="utf-8")
    try:
        load_mcp_manifest(temp_path)
    except Exception as exc:  # noqa: BLE001 - nexgen: allow reason="unreadable manifest is reported as failure"
        temp_path.unlink(missing_ok=True)
        return False, t("manifest modification failed validation: {error}", error=exc)

    os.replace(temp_path, path)
    return True, t("server '{name}' retired from MCP manifest", name=name)


def _remove_skill_yaml_block(text: str, name: str) -> str:
    """Remove a skill entry from skills.manifest.yaml."""
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    in_target = False
    target_prefix = f"  {name}:"

    for line in lines:
        stripped = line.rstrip("\r\n")
        if stripped == target_prefix or stripped.startswith(f"  {name}: "):
            in_target = True
            continue
        if in_target:
            if line.startswith("    "):
                continue
            if stripped == "" or line.strip().startswith("#"):
                continue
            in_target = False
        out.append(line)
    return "".join(out)


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

    updated = _remove_skill_yaml_block(raw_before, name)
    materializer = SkillMaterializer(vault_data=vault, engine_root=engine, home=resolved_home)
    temp_path = path.with_suffix(".tmp")
    temp_path.write_text(updated, encoding="utf-8")
    try:
        load_skills_manifest(temp_path)
    except Exception as exc:  # noqa: BLE001 - nexgen: allow reason="unreadable manifest is reported as failure"
        temp_path.unlink(missing_ok=True)
        return False, t("skills manifest modification failed validation: {error}", error=exc)

    os.replace(temp_path, path)
    if skill_dir.is_dir():
        shutil.rmtree(skill_dir, ignore_errors=True)
    materializer.materialize(apply=True)
    return True, t("skill '{name}' retired from library", name=name)
