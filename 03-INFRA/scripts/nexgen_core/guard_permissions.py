"""Installs the Vault's declared guardrail hook in every CLI that runs here.

One job: read the private permission manifest (one guardrail hook body per
CLI) and hand it to `nexgen_core.runtimes` as a plain path. The mechanism
lives there; this component only decides what to ask it for.

The policy is Vault-private data, never the public engine's: without that
manifest the phase only runs legacy cleanup, so no end user inherits someone
else's guardrail. It used to be a method of `GuardRunner`, beside the git
quarantine and the instruction pointers; the coordinator keeps a one-line
method that calls this.

A manifest may still carry a `posture:` block from an earlier engine. It is
ignored here, on purpose: the permission mode is each app's own setting now,
and `nexgen doctor` says so once. Parsing must not care what shape it has.
"""
from __future__ import annotations

import logging
from pathlib import Path

import yaml

from nexgen_core.action_notes import WARN
from nexgen_core.i18n import t
from nexgen_core.runtimes import apply_all as apply_runtimes

logger = logging.getLogger(__name__)


def guardrail_body(vault_data: Path) -> tuple[Path | None, list[str]]:
    """The one guardrail body the private permission manifest declares, and why there is none.

    The POLICY -- which body, for which CLIs -- is Vault-private data
    (03-INFRA/agent-universal-layer/permissions/manifest.yaml), never the public engine's:
    without that file the answer is "no body", so no end user inherits someone else's
    guardrail. The refusals are returned, not printed, because the caller owns the
    wording of the phase it is running.
    """
    manifest_path = vault_data / "03-INFRA" / "agent-universal-layer" / "permissions" / "manifest.yaml"
    if not manifest_path.is_file():
        return None, []
    try:
        raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        return None, [WARN + t("runtime-permissions: could not read {path} ({error})", path=manifest_path, error=exc)]
    if not isinstance(raw, dict):
        return None, [WARN + t("runtime-permissions: the root of {path} is not a map", path=manifest_path)]

    # Only one guardrail policy is supported (the first declared hook):
    # it's the only real case, and generalizing to an arbitrary list of
    # events/matchers per CLI is exactly the five-map complexity this
    # package replaces.
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
            return None, [WARN + t("runtime-permissions: {name} escapes permissions/, guardrail rejected", name=name)]
        from nexgen_core.runtimes.base import Runtime

        hook_name = Path(spec["file"]).name
        if not Runtime._is_safe_hook_filename(hook_name):
            name = spec.get("name", spec["file"])
            return None, [WARN + t("runtime-permissions: {name} has an unsafe hook filename, guardrail rejected", name=name)]
        if not candidate.is_file():
            return None, [WARN + t("runtime-permissions: guardrail body missing ({path})", path=candidate)]
        return candidate, []
    return None, []


def apply_runtime_permissions(runner) -> list[str]:
    """Guardrail hook for every installed CLI.

    The POLICY is read by :func:`guardrail_body`; the mechanism that applies it lives in
    nexgen_core.runtimes. Here we only translate what it answers into plain arguments.
    """
    engine_hooks_dir = runner.engine_root / "agent-universal-layer" / "hooks"
    body, refusals = guardrail_body(runner.vault_data)
    if refusals or body is None:
        # No declared guardrail body, so nothing to install: the retired-registration cleanup
        # now runs inside each adapter's own install step, which this call never reaches. The
        # call stays as the phase's one entry point and writes nothing.
        return refusals or apply_runtimes(home=runner.home, engine_hooks_dir=engine_hooks_dir)

    return apply_runtimes(
        home=runner.home,
        engine_hooks_dir=engine_hooks_dir,
        guardrail_source=body,
    )
