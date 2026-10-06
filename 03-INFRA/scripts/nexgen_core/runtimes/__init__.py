"""The registry of CLI adapters + the single integration point with the guard.

`apply_all()` is the phase the guard cycle calls: it receives the posture
and the guardrail body already RESOLVED (no manifest parsing happens in
here -- that stays the caller's job, guard.py, which is also the only place
that knows where the Vault is) and returns a list of text actions. It
prints nothing: formatting is the caller's job.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from nexgen_core.action_notes import WARN  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.runtimes.antigravity import AntigravityRuntime
from nexgen_core.runtimes.base import GuardrailError, Runtime
from nexgen_core.runtimes.claude import ClaudeRuntime
from nexgen_core.runtimes.codex import CodexRuntime
from nexgen_core.runtimes.opencode import OpenCodeRuntime

#: name -> instance. Adding a fifth CLI means adding a
#: runtimes/<name>.py file with its class and one line here, never touching the others.
REGISTRY: dict[str, Runtime] = {
    "claude": ClaudeRuntime(),
    "codex": CodexRuntime(),
    "opencode": OpenCodeRuntime(),
    "antigravity": AntigravityRuntime(),
}


def apply_all(
    *,
    home: Path,
    engine_hooks_dir: Path,
    posture: dict[str, str] | None = None,
    guardrail_source: Path | None = None,
    event_sink_source: Path | None = None,
) -> list[str]:
    """Applies posture + guardrail hook to every installed CLI.

    Order per CLI, non-negotiable: guardrail FIRST, posture AFTER. A
    posture that removes prompts must never reach disk if its declared
    guardrail just failed to install -- that's exactly the incident this
    ordering prevents.

    A CLI that isn't installed is not a failure: it's silently skipped. A
    REAL failure during guardrail installation (corrupted config, unsafe
    path) blocks ONLY that CLI's posture for this round, never the others.
    """
    posture = posture or {}
    actions: list[str] = []

    for runtime in REGISTRY.values():
        if not runtime.is_installed(home):
            continue

        guardrail_ok = True
        if guardrail_source is not None:
            try:
                result = runtime.install_guardrail(home, guardrail_source, engine_hooks_dir)
            except GuardrailError as exc:
                actions.append(f"{WARN}{runtime.name}: guardrail installation refused ({exc})")
                guardrail_ok = False
            else:
                if result:
                    actions.append(result)

        if event_sink_source is not None:
            try:
                sink_result = runtime.install_event_sink(home, event_sink_source)
                if sink_result:
                    actions.append(sink_result)
            except (OSError, ValueError, TypeError, GuardrailError) as exc:
                # Same isolation as guardrail/posture above: one corrupt CLI
                # config must warn for its own CLI, never abort the rest.
                actions.append(f"{WARN}{runtime.name}: event sink installation failed ({exc})")

        desired_posture = posture.get(runtime.name)
        if not desired_posture:
            continue
        if desired_posture not in runtime.rendered_postures():
            # Asked for a posture this CLI cannot render: saying nothing
            # would let the user believe in a least-privilege that was
            # never applied. Warn once per cycle, apply nothing.
            actions.append(
                f"{WARN}{runtime.name}: posture '{desired_posture}' has no verified "
                f"rendering on this CLI -- nothing applied"
            )
            continue
        if not guardrail_ok:
            actions.append(
                f"{WARN}{runtime.name}: posture '{desired_posture}' NOT applied -- "
                "its declared guardrail did not install correctly"
            )
            continue
        try:
            result = runtime.apply_posture(home, desired_posture)
        except GuardrailError as exc:
            actions.append(f"{WARN}{runtime.name}: posture application refused ({exc})")
        else:
            if result:
                actions.append(result)

    if any("hook" in action for action in actions):
        if not shutil.which("node"):
            # Every registered hook above spawns `node`: without it on the
            # PATH the CLIs hold dead registrations behind a green guard.
            actions.append(
                WARN + "hooks registered but `node` is not on PATH: "
                "guardrail and event-sink hooks will never fire until it is installed"
            )

    return actions
