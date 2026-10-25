# nexgen: one-job reason="decide which skills OpenClaw may put in its prompt, from the manifest that already decides their exposure"
"""What OpenClaw may load into its prompt: only the skills the manifest makes core.

OpenClaw reads every skill it finds under the shared skill root and writes all of
them, with path and description, into the system prompt of every session. The
engine's own policy is the opposite: a body is loaded when it is used, and
`exposure: manual` means exactly that. OpenClaw has the tool that reads one on
demand (`skills_read`), so the two policies can agree by turning the manual ones
off for the prompt instead of removing them from the machine.

The list is generated from `skills.manifest.yaml`, which is already the one source
of what exists and how it is exposed: nothing here is a second opinion.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from nexgen_core.config import load_skills_manifest
from nexgen_core.paths import skills_manifest

#: The one exposure that belongs in a system prompt.
CORE = "core"


def skill_entries(vault_data: Path | None) -> dict[str, dict[str, bool]]:
    """`skill -> {enabled}` for every skill the manifest declares, from its exposure.

    A skill the manifest does not declare is not in this map at all: it is neither
    enabled nor disabled by the engine, because the engine does not know it.
    """
    manifest = skills_manifest(vault_data)
    if not manifest.is_file():
        return {}
    data = load_skills_manifest(manifest).get("skills", {})
    if not isinstance(data, dict):
        return {}
    entries: dict[str, dict[str, bool]] = {}
    for name, entry in data.items():
        exposure = entry.get("exposure") if isinstance(entry, dict) else None
        entries[str(name)] = {"enabled": str(exposure or "").strip().lower() == CORE}
    return entries


def with_skill_policy(config: dict[str, Any], vault_data: Path | None) -> dict[str, Any]:
    """The configuration plus the derived `skills.entries`, leaving unknown skills alone.

    Additive on purpose: a skill somebody installed through OpenClaw and that the
    manifest does not declare keeps the state it has. Only the manifest's own
    skills are decided here, because only those have one source.
    """
    declared = skill_entries(vault_data)
    if not declared:
        return config
    merged = dict(config)
    skills = dict(merged.get("skills") or {})
    skills["entries"] = {**(skills.get("entries") or {}), **declared}
    merged["skills"] = skills
    return merged
