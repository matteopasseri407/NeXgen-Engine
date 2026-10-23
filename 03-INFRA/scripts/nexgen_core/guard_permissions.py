"""Turns the Vault's permission policy into the posture each CLI actually runs with.

One job: read the private permission manifest (a posture per CLI, one
guardrail hook body), and translate them into the plain arguments
`nexgen_core.runtimes` applies. The mechanism lives there; this component
only decides what to ask it for.

The policy is Vault-private data, never the public engine's: without that
manifest the phase only runs legacy cleanup, so no end user inherits someone
else's permission posture. It used to be a method of `GuardRunner`, beside
the git quarantine and the instruction pointers; the coordinator keeps a
one-line method that calls this.
"""
from __future__ import annotations

import logging
from pathlib import Path

import yaml

from nexgen_core.action_notes import WARN
from nexgen_core.i18n import t
from nexgen_core.runtimes import apply_all as apply_runtimes

logger = logging.getLogger(__name__)


def apply_runtime_permissions(runner) -> list[str]:
    """Permission posture + guardrail hook for every installed CLI.

    The POLICY -- which posture, which guardrail body -- is Vault private
    data (03-INFRA/agent-universal-layer/permissions/manifest.yaml),
    never the public engine's: without that file this phase is a
    complete no-op, so no end user inherits someone else's permission
    posture. The mechanism that applies it lives in nexgen_core.runtimes;
    here we only read the manifest and translate it into plain arguments
    for that mechanism.
    """
    manifest_path = runner.vault_data / "03-INFRA" / "agent-universal-layer" / "permissions" / "manifest.yaml"
    engine_hooks_dir = runner.engine_root / "agent-universal-layer" / "hooks"
    if not manifest_path.is_file():
        # Even without a permission policy, runtimes clean up legacy hooks (like event sink).
        return apply_runtimes(home=runner.home, engine_hooks_dir=engine_hooks_dir)
    try:
        raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        return [WARN + t("runtime-permissions: could not read {path} ({error})", path=manifest_path, error=exc)]
    if not isinstance(raw, dict):
        return [WARN + t("runtime-permissions: the root of {path} is not a map", path=manifest_path)]

    posture = {
        cli: value
        for cli, value in (raw.get("posture") or {}).items()
        if isinstance(cli, str) and isinstance(value, str)
    }

    # Only one guardrail policy is supported (the first declared hook):
    # it's the only real case, and generalizing to an arbitrary list of
    # events/matchers per CLI is exactly the five-map complexity this
    # package replaces.
    guardrail_source: Path | None = None
    for spec in raw.get("hooks") or []:
        if not isinstance(spec, dict) or not isinstance(spec.get("file"), str):
            continue
        candidate = (manifest_path.parent / spec["file"]).resolve()
        try:
            parent_resolved = manifest_path.parent.resolve()
            is_inside = candidate.is_relative_to(parent_resolved)
        except (OSError, ValueError):
            is_inside = False
        if not is_inside:
            name = spec.get("name", spec["file"])
            return [WARN + t("runtime-permissions: {name} escapes permissions/, guardrail rejected", name=name)]
        from nexgen_core.runtimes.base import Runtime

        hook_name = Path(spec["file"]).name
        if not Runtime._is_safe_hook_filename(hook_name):
            name = spec.get("name", spec["file"])
            return [WARN + t("runtime-permissions: {name} has an unsafe hook filename, guardrail rejected", name=name)]
        if not candidate.is_file():
            return [WARN + t("runtime-permissions: guardrail body missing ({path})", path=candidate)]
        guardrail_source = candidate
        break

    return apply_runtimes(
        home=runner.home,
        engine_hooks_dir=engine_hooks_dir,
        posture=posture,
        guardrail_source=guardrail_source,
    )
