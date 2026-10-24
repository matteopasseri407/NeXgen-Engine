"""agy (Antigravity) as an adapter: a good scout, an executor that cannot be followed.

One job: build the two command lines agy really has (`--mode plan --prompt` for a
read-only round, `--prompt-interactive` for a tab) and declare the rest absent.
agy exposes no listing of its conversations bound to a folder, so the engine
cannot say which session a tab opened: it opens the tab, leaves the session
unattached, and says so. Claiming an id it cannot verify would send the next
unit's work into the previous unit's conversation.
"""
from __future__ import annotations

import re
from pathlib import Path

from . import base


class AntigravityAdapter(base.Adapter):
    """Antigravity's CLI (`agy`), a full scout and a blind executor."""

    name = "agy"
    label = "agy (Antigravity)"
    executable = "agy"
    session_id_pattern = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
    session_id_description = "UUID come 'a544d85d-f5b3-4741-bbe2-8044c85b348f'"
    capabilities = base.Capabilities(
        open_tab=True, find_session=False, send_turn=False, live_state=False, cost=False,
        tokens=False, write_permissions=False, resume=True, scout=True,
        dispose_location=False, session_model=False, last_turn=False,
        deny_push="solo pushurl bloccata della copia (agy non ha un profilo di progetto)",
    )

    def tab_argv(self, executable: str, folder: Path, prompt: str, *, session: str = "") -> list[str]:
        """A visible executor tab: edits allowed, the prompt as a pointer, never skip-permissions."""
        argv = [executable, "--mode", "accept-edits"]
        if session:
            argv.extend(["--conversation", session])
        argv.extend(["--prompt-interactive", prompt])
        return argv

    def resume_argv(self, executable: str, session: str, folder: Path, prompt: str = "") -> list[str]:
        """`agy --conversation <id> --prompt-interactive <pointer>`: the one resume agy has."""
        return self.tab_argv(executable, folder, prompt, session=session)

    def scout_argv(self, executable: str, prompt: str) -> list[str]:
        """The read-only round the engine already used: `--mode plan --prompt <pointer>`."""
        return [executable, "--mode", "plan", "--prompt", prompt]
