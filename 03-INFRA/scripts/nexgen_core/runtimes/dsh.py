"""DeepSeek Harness adapter: the product's installed footprint, and no hook yet.

DSH is the sixth runtime for the connectors and for privacy. Its guardrail
surface (a Cordis plugin) is not verified on a running build, so this adapter
does not invent a registration: the base contract explicitly allows `None`,
which means "no verified guardrail hookup". What it does answer is the one
question the MCP renderer must not guess: is the harness actually on this
machine? The patch file this layer writes is never a valid signal, because the
engine creates it; the launcher and the app's own first-run id are.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from nexgen_core.paths import DSH_WINDOWS_LAUNCHER, dsh_home
from nexgen_core.runtimes.base import Runtime


def _installer_launcher() -> Path | None:
    """The launcher the Windows installer leaves behind, which is not on PATH."""
    if os.name != "nt":
        return None
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if not local_app_data:
        return None
    candidate = Path(local_app_data) / DSH_WINDOWS_LAUNCHER
    return candidate if candidate.is_file() else None


class DshRuntime(Runtime):
    """The DeepSeek Harness adapter."""

    name = "dsh"

    def is_installed(self, home: Path) -> bool:
        """Whether the harness itself left a footprint in this home or on this PATH.

        Two signals, both the product's own: `~/.dsh/.anonymous-user-id`, written
        by the app the first time it starts, and a launcher (`dsh` on the PATH for
        an npm install, or the installer's own copy on Windows). The patch file is
        deliberately absent from this list: the MCP renderer writes it on every
        cycle, so it says nothing about whether DSH exists.
        """
        if (dsh_home(home) / ".anonymous-user-id").is_file():
            return True
        return bool(shutil.which("dsh")) or _installer_launcher() is not None

    def install_guardrail(
        self, home: Path, hook_source: Path, engine_hooks_dir: Path
    ) -> str | None:
        """No verified hookup yet: a registration nobody can prove would be worse than none."""
        del home, hook_source, engine_hooks_dir
        return None
