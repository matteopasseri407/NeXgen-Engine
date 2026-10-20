"""Turns the Vault's permission policy into the posture each CLI actually runs with.

One job: read the private permission manifest (a posture per CLI, one
guardrail hook body) together with the event-sink requirement the declared
modules state, and translate them into the plain arguments
`nexgen_core.runtimes` applies. The mechanism lives there; this component
only decides what to ask it for.

The policy is Vault-private data, never the public engine's: without that
manifest the phase is a complete no-op, so no end user inherits someone
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
    event_sink_source = engine_hooks_dir / "nexgen-event-sink.mjs"
    sink_wanted = runner._event_sink_wanted()
    sink_args = {
        "event_sink_source": event_sink_source if sink_wanted and event_sink_source.is_file() else None,
        "remove_event_sink": sink_wanted is False,
    }
    if not manifest_path.is_file():
        # No permission policy here, but the event sink does not depend on one.
        if sink_args["event_sink_source"] is None and not sink_args["remove_event_sink"]:
            return []
        return apply_runtimes(home=runner.home, engine_hooks_dir=engine_hooks_dir, **sink_args)
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
        **sink_args,
    )


def _event_sink_wanted(runner) -> bool | None:
    """Does a module this machine declared need the event-sink hook?

    True/False when the module state says so; None when it cannot be read, in which case
    the hook is left as it is: removing it because of a read error would turn a transient
    fault into a lost integration. A module declared `local` but held back by missing env
    gates (a timer without the user's tokens) still counts: that is the environment, not a
    decision to remove it.
    """
    try:
        from nexgen_core.modules import modules_state

        states = modules_state(vault_data=runner.vault_data, engine_root=runner.engine_root)
    except Exception as exc:  # noqa: BLE001 - an unreadable module state changes nothing
        logger.debug("event sink left as it is: module state unreadable (%s)", type(exc).__name__)
        return None
    return any(
        "event_sink" in state.module.provides.runtime_hooks
        and "local" in (state.state, state.declared)
        for state in states
    )
