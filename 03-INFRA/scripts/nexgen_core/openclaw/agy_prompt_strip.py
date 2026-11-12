"""The paragraph break the Antigravity plugin's prompt filter deletes.

One job: keep the user's message in the turn the installed `google-antigravity-cli`
plugin hands to `agy`.

The plugin removes OpenClaw's `⟦openclaw:ctx⟧` context blocks before `agy` runs,
because `agy` keeps that history in its own conversation. Release 0.3.4 also
removes the blank line in front of each block it cuts, so whatever followed the
block joins the line before it. A Telegram turn has a "Conversation info" block
and then a "Conversation context" block: cutting the second puts the message on
the closing fence of the first, and cutting the first then deletes the message.
`agy` receives only the runtime instructions and answers something else.

The fix puts one paragraph break back where a block was cut. Like the manual
compaction in `agy_compaction`, it is a bridge until the plugin ships the fix:
idempotent, marked in the file it changes, saved beside a backup, and never
attempted on a file whose shape this engine no longer recognises. OpenClaw loads
the plugin from a copy it makes at start, so the fix applies from the next
gateway restart.
"""
from __future__ import annotations

from pathlib import Path

from nexgen_core.files import atomic_write_text, backup_file
from nexgen_core.openclaw.agy_compaction import ALREADY, FAILED, MISSING, PATCHED, UNEXPECTED, WOULD_PATCH, Patch
from nexgen_core.openclaw.agy_prereqs import extension_dir
from nexgen_core.openclaw.terms import LOCAL_AGY_BACKEND

#: The comment that marks this file as fixed, so a second run is a no-op.
MARKER = "nexgen-prompt-strip-separator"
#: The one line that drops the separator when a context block is cut.
ANCHOR = "            result = result.slice(0, sliceStart) + result.slice(blockEnd);\n"
#: The same cut, keeping one paragraph break between what was before and after it.
#: A block at the very start needs none; the filter's final trim removes a trailing one.
REPLACEMENT = (
    "            /* nexgen-prompt-strip-separator: keep a paragraph break where a context block was cut */\n"
    '            result = result.slice(0, sliceStart) + (sliceStart > 0 ? "\\n\\n" : "") + result.slice(blockEnd);\n'
)
#: The tag of the copy kept beside the file the fix replaces.
BACKUP_TAG = "agy-prompt-strip"
#: The compiled filter the plugin's wrapper runs on every `--print` prompt.
FILTER_RELATIVE = Path("dist") / "prompt-strip.js"

__all__ = ["ALREADY", "FAILED", "MISSING", "PATCHED", "UNEXPECTED", "WOULD_PATCH", "Patch", "apply", "filter_file"]


def filter_file(state_dir: Path, backend: str = LOCAL_AGY_BACKEND) -> Path:
    """The compiled prompt filter inside the installed extension."""
    return extension_dir(state_dir, backend) / FILTER_RELATIVE


def apply(state_dir: Path, backend: str = LOCAL_AGY_BACKEND, *, dry_run: bool = False) -> Patch:
    """Restore the paragraph break when it is missing, and say what happened.

    A file this engine cannot read or no longer recognises ends in `unexpected`,
    which the setup prints: a silent skip is how the message quietly goes missing again.
    """
    path = filter_file(state_dir, backend)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:  # nexgen: allow reason="an absent or unreadable plugin file is reported as missing, not raised"
        return Patch(MISSING, path, detail=str(exc))
    if MARKER in text:
        return Patch(ALREADY, path)
    if text.count(ANCHOR) != 1:
        return Patch(UNEXPECTED, path)
    if dry_run:
        return Patch(WOULD_PATCH, path)
    try:
        backup = backup_file(path, tag=BACKUP_TAG)
        atomic_write_text(path, text.replace(ANCHOR, REPLACEMENT, 1))
    except OSError as exc:  # nexgen: allow reason="a plugin file that cannot be written is reported, never a crash after a successful install"
        return Patch(FAILED, path, detail=str(exc))
    return Patch(PATCHED, path, backup=backup)
