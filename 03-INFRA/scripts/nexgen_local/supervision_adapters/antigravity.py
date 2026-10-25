"""agy (Antigravity) as an adapter: a scout and an executor with find_session.

One job: build the two command lines agy really has (`--mode plan --prompt` for a
read-only round, `--prompt-interactive` for a tab), locate the unit's
conversation in agy's local SQLite store in read-only mode, and declare the rest
absent. A session is attached only when uniquely matched by copy folder or brief.
"""
from __future__ import annotations

import os
import re
import sqlite3
import time
from pathlib import Path

from nexgen_core.paths import resolve_home

from . import base

SESSION_WAIT_S = 30.0
POLL_S = 2.0
CREATED_SLACK_MS = 5000


def _conversations_dir() -> Path:
    custom = os.environ.get("ANTIGRAVITY_HOME")
    if custom:
        p = Path(custom)
        if (p / "conversations").is_dir():
            return p / "conversations"
        return p
    return resolve_home() / ".gemini" / "antigravity-cli" / "conversations"


def _search_terms(folder: Path) -> tuple[bytes, ...]:
    terms = [folder.name, folder.as_posix(), str(folder)]
    if folder.name.startswith("23-unita-"):
        unit = folder.name[len("23-unita-"):]
        terms.extend([f"20-brief-{unit}.md", f"20-brief-{unit}", f"brief-{unit}"])
    return tuple(t.encode("utf-8") for t in terms if t)


def _matches_agy_db(path: Path, terms: tuple[bytes, ...]) -> bool:
    try:
        con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            for term in terms:
                try:
                    cur = con.execute("SELECT 1 FROM steps WHERE instr(step_payload, ?) LIMIT 1", (term,))
                    if cur.fetchone():
                        return True
                except sqlite3.Error:  # nexgen: allow reason="step_payload column missing or read failure"
                    pass
        finally:
            con.close()
    except Exception:  # nexgen: allow reason="fallback to file byte scan on lock or non-sqlite error"
        pass
    for f in (path, path.with_name(path.name + "-wal")):
        if f.is_file():
            try:
                raw = f.read_bytes()
                if any(term in raw for term in terms):
                    return True
            except Exception:  # nexgen: allow reason="unreadable wal or locked file ignored"
                pass
    return False


class AntigravityAdapter(base.Adapter):
    """Antigravity's CLI (`agy`), a full scout and an interactive executor."""

    name = "agy"
    label = "agy (Antigravity)"
    executable = "agy"
    session_id_pattern = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
    session_id_description = "UUID come 'a544d85d-f5b3-4741-bbe2-8044c85b348f'"
    capabilities = base.Capabilities(
        open_tab=True, find_session=True, send_turn=False, live_state=False, cost=False,
        tokens=False, write_permissions=False, resume=True, scout=True,
        dispose_location=False, session_model=False, last_turn=False,
        deny_push="solo pushurl bloccata della copia (agy non ha un profilo di progetto)",
    )

    def tab_argv(self, executable: str, folder: Path, prompt: str, *, session: str = "",
                 model: str = "") -> list[str]:
        """A visible executor tab: edits allowed, the prompt as a pointer, never skip-permissions.

        `agy --model <id>` is the only way this CLI chooses a model, so a model the
        owner named for this run travels on the tab itself.
        """
        argv = [executable, "--mode", "accept-edits"]
        if model:
            argv.extend(["--model", model])
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

    def find_session(
        self, folder: Path, since_ms: int, *, executable: str = "", expected: str = "",
        wait_s: float = SESSION_WAIT_S, poll_s: float = POLL_S, clock=None, sleep=None,
    ) -> base.SessionRef | None:
        """Find the agy conversation SQLite database matching copy folder or brief."""
        conv_dir = _conversations_dir()
        if not conv_dir.is_dir():
            return None
        now, pause = clock or time.monotonic, sleep or time.sleep
        deadline = now() + wait_s
        terms = _search_terms(folder)
        while True:
            candidates: list[Path] = []
            for db_file in conv_dir.glob("*.db"):
                if expected and db_file.stem != expected:
                    continue
                try:
                    st = db_file.stat()
                    mtime_ms = int(st.st_mtime * 1000)
                except OSError:  # nexgen: allow reason="file disappeared between glob and stat"
                    continue
                if since_ms and mtime_ms < since_ms - CREATED_SLACK_MS:
                    continue
                if _matches_agy_db(db_file, terms):
                    candidates.append(db_file)
            if len(candidates) > 1:
                ids = ", ".join(c.stem for c in candidates)
                raise base.AdapterError(
                    f"conversazione di agy ambigua per {folder.name}: trovate {len(candidates)} corrispondenze ({ids})"
                )
            if len(candidates) == 1:
                match = candidates[0]
                mtime_ms = int(match.stat().st_mtime * 1000)
                return base.SessionRef(
                    id=match.stem, folder=str(folder), title=match.stem,
                    created_ms=mtime_ms,
                )
            if now() >= deadline:
                return None
            pause(poll_s)
