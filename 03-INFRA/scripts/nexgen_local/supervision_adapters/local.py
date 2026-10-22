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
        """The OpenCode profile, with `model` set to the local one."""
        pinned = model or self.model()
        if not pinned:
            raise AdapterError(
                "nessun modello locale configurato: scrivi `supervision.local_model.model` "
                "nel config.json della lane (provider/modello)"
            )
        super().write_profile(task_id, folder, model=pinned)
