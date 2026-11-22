"""The registry of CLI adapters + the single integration point with the guard.

`apply_all()` is the phase the guard cycle calls: it receives the guardrail body
already RESOLVED (no manifest parsing happens in here -- that stays the
caller's job, guard_permissions.py, which is also the only place that knows
where the Vault is) and returns a list of text actions. It prints nothing:
formatting is the caller's job.

It writes one thing per CLI: the guardrail hook. The permission mode is the
app's own setting and is never touched here.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from nexgen_core.action_notes import WARN  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.runtimes.antigravity import AntigravityRuntime
from nexgen_core.runtimes.base import GuardrailError, Runtime
from nexgen_core.runtimes.claude import ClaudeRuntime
from nexgen_core.runtimes.codex import CodexRuntime
from nexgen_core.runtimes.dsh import DshRuntime
from nexgen_core.runtimes.openclaw import OpenClawRuntime
from nexgen_core.runtimes.opencode import OpenCodeRuntime

#: name -> instance. Adding a CLI means adding a runtimes/<name>.py file
#: with its class and one line here, never touching the others.
REGISTRY: dict[str, Runtime] = {
    "claude": ClaudeRuntime(),
    "codex": CodexRuntime(),
    "opencode": OpenCodeRuntime(),
    "antigravity": AntigravityRuntime(),
    "openclaw": OpenClawRuntime(),
    "dsh": DshRuntime(),
}


def apply_all(
    *,
    home: Path,
    engine_hooks_dir: Path,
    guardrail_source: Path | None = None,
) -> list[str]:
    """Installs the guardrail hook in every installed CLI.

    A CLI that isn't installed is not a failure: it's silently skipped. A REAL
    failure during guardrail installation (corrupted config, unsafe path) is
    reported for that CLI alone, never for the others.
    """
    actions: list[str] = []
    node_spawned_hook = False

    for runtime in REGISTRY.values():
        if not runtime.is_installed(home):
            continue

        if guardrail_source is not None:
            try:
                result = runtime.install_guardrail(home, guardrail_source, engine_hooks_dir)
            except GuardrailError as exc:
                actions.append(f"{WARN}{runtime.name}: guardrail installation refused ({exc})")
            else:
                if result:
                    actions.append(result)
                    # Whatever each adapter calls it, the artefact spawns `node`: the action text
                    # is not a reliable way to recognise one ("plugin deposited", "guardrail
                    # registered"), and a missing runtime therefore went unreported.
                    node_spawned_hook = True

    if node_spawned_hook and not shutil.which("node"):
        # Every registered hook above spawns `node`: without it on the
        # PATH the CLIs hold dead registrations behind a green guard.
        actions.append(
            WARN + "hooks registered but `node` is not on PATH: "
            "guardrail hooks will never fire until it is installed"
        )

    return actions
