"""A local model as an executor: OpenCode with the model pinned in the copy's profile.

One job: say that a local unit is an OpenCode unit whose model comes from the
lane's configuration instead of the owner's, so the journal names it `local` and
the cost, the permissions and the resume are exactly OpenCode's. The server's
lifecycle is not here: `supervision_local_model` starts and stops it, and the
engine calls that around this adapter.
"""
from __future__ import annotations

from pathlib import Path

from .. import supervision_policy as policy
from .base import AdapterError
from .opencode import OpenCodeAdapter


class LocalAdapter(OpenCodeAdapter):
    """OpenCode driving the model the lane declares, on the lane's own server."""

    name = "local"
    label = "modello locale (OpenCode)"
    executable = "opencode"

    def model(self) -> str:
        """The model this lane pins for a local unit; a lane without one is refused."""
        return policy.local_model_id()

    def write_profile(self, task_id: str, folder: Path, *, model: str = "") -> None:
        """The OpenCode profile, with `model` set to the local one and declared under its provider.

        A model the harness does not find in the provider falls back to the
        provider's first model: the requests still reach the local server, which
        serves one model whatever name it is asked for, but the session and the
        journal would name a model that never ran. Declaring the pin keeps the
        recorded model equal to the one the lane started.
        """
        pinned = model or self.model()
        if not pinned:
            raise AdapterError(
                "nessun modello locale configurato: scrivi `supervision.local_model.model` "
                "nel config.json della lane (provider/modello)"
            )
        provider, _, name = pinned.partition("/")
        declared = (
            {provider: {name: {"name": f"{name} (locale)", "tool_call": True}}}
            if provider and name else None
        )
        super().write_profile(task_id, folder, model=pinned, models=declared)
