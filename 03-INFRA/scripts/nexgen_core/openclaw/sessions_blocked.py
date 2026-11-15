"""Which backends this machine cannot ask to compact a chat, and why.

One job: decide whether a session's runtime can be asked for a native compaction
on this machine, and name the runtime that never can.

Why it exists: the groomer and the doctor read the same question, and the answer
depends on a file an ordinary plugin update rewrites. `claude-cli` is never
blocked: OpenClaw's own backend declares a manual `/compact` and checks its
output. `google-antigravity-cli` is blocked only while the installed plugin
carries no manual compaction, because the engine adds one that runs `agy`'s own
`/compact` (`agy_compaction`), and that marker lives in a file the next plugin
release overwrites. The same check puts the backend back here instead of failing
the hourly run.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from nexgen_core.i18n import t
from nexgen_core.openclaw import agy_compaction, groom_memory
from nexgen_core.openclaw.terms import LOCAL_AGY_BACKEND

#: Backends that cannot be asked for a native compaction on this machine: their sessions are left alone.
UNCONFIRMABLE_RUNTIMES = frozenset({"google-antigravity-cli"})


def state_dir_of(home: Path | None) -> Path | None:
    """OpenClaw's own folder, where the installed plugins live; None when it cannot be named."""
    from nexgen_core.paths import openclaw_config

    try:
        return Path(openclaw_config(home)).parent
    except OSError:  # nexgen: allow reason="a state folder that cannot be resolved leaves every backend blocked, which is the safe answer"
        return None


def blocked_runtimes(state_dir: Path | None) -> frozenset[str]:
    """The backends this machine cannot honestly ask to compact, judged on the installed plugin."""
    if state_dir is not None and agy_compaction.patched(state_dir):
        return UNCONFIRMABLE_RUNTIMES - {LOCAL_AGY_BACKEND}
    return UNCONFIRMABLE_RUNTIMES


def askable(
    state_dir: Path | None,
    memory: dict[str, dict[str, Any]],
    *,
    dry_run: bool,
    emit: Callable[[str], None],
) -> frozenset[str]:
    """The backends this run may ask to compact, after putting back the fix a plugin release removes.

    The Antigravity plugin's manual compaction lives in a file its next release
    overwrites, and `agent-sync` only aligns the OpenClaw release: without this the
    first update after a setup silently stopped every chat on that backend being
    compacted until a person ran `nexgen openclaw setup` again. The chats it was
    refused for are offered again the moment it can answer, because a memory of a
    machine that could not ask proves nothing. A dry run writes nothing.
    """
    if not dry_run and agy_compaction.reapply(state_dir):
        forgotten = groom_memory.forget_runtime(memory, LOCAL_AGY_BACKEND)
        emit(t("The Antigravity plugin had lost the engine's manual compaction: it is applied again, "
               "and {count} chat(s) of that backend are offered again.", count=len(forgotten)))
    return blocked_runtimes(state_dir)
