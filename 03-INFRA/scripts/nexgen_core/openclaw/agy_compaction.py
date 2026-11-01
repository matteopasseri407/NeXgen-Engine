"""The one compaction command the Antigravity plugin does not declare.

One job: give the installed `google-antigravity-cli` backend a `manualCompaction`
that runs `agy`'s own `/compact`, so `openclaw sessions compact` compacts a
conversation instead of refusing it.

Why a patch and not a setting: OpenClaw asks the backend object the plugin
exports for a manual compaction, and no configuration key replaces a capability
the plugin never declared. `agy` 1.3.5 added `/compact`; release 0.3.4 of the
plugin predates it and says in a comment that no such command exists. The next
release of the plugin overwrites this file, so the insertion is a bridge:
idempotent, marked in the file it changes, reported to the person, and never
attempted when the file no longer has the shape this engine knows.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from nexgen_core.files import atomic_write_text, backup_file
from nexgen_core.openclaw.agy_prereqs import extension_dir
from nexgen_core.openclaw.terms import LOCAL_AGY_BACKEND

#: The line that marks this file as patched, so a second run is a no-op.
MARKER = "nexgen-manual-compaction"
#: The one place the block is inserted: the backend's own compaction declaration.
ANCHOR = "        ownsNativeCompaction: true,\n"
#: The tag of the copy kept beside the file the patch replaces.
BACKUP_TAG = "agy-compaction"
#: The browser-side name of the file the plugin exports its backend from.
BACKEND_RELATIVE = Path("dist") / "backend.js"

#: OpenClaw's manual-compaction contract, answered by agy's own command.
#: `agy` prints stream-json: a checkpoint step when it compacts, then a result.
BLOCK = """\
        /* nexgen-manual-compaction: agy 1.3.5+ compacts on /compact; plugin 0.3.4 predates it */
        manualCompaction: {
            buildPrompt: (customInstructions) => {
                const extra = customInstructions?.trim();
                return extra ? `/compact ${extra}` : "/compact";
            },
            input: "arg",
            validateOutput: (rawOutput) => {
                for (const line of rawOutput.split("\\n")) {
                    const trimmed = line.trim();
                    if (!trimmed) continue;
                    try {
                        const event = JSON.parse(trimmed);
                        if (event.event === "step_update" && event.step_update?.step_type === "checkpoint") return { ok: true };
                        if (event.event === "result" && event.result?.status === "SUCCESS") return { ok: true };
                    } catch {}
                }
                return { ok: false, reason: "agy did not confirm that native compaction ran." };
            },
        },
"""

#: Every way the patch can end, named once so the setup and its tests agree.
MISSING = "missing"
UNEXPECTED = "unexpected"
ALREADY = "already"
PATCHED = "patched"
WOULD_PATCH = "would-patch"
FAILED = "failed"


@dataclass(frozen=True)
class Patch:
    """What one run found and did, with the paths a person may need afterwards."""

    state: str
    path: Path
    backup: Path | None = None
    detail: str = ""


def backend_file(state_dir: Path, backend: str = LOCAL_AGY_BACKEND) -> Path:
    """The compiled backend the plugin exports, inside the installed extension."""
    return extension_dir(state_dir, backend) / BACKEND_RELATIVE


def patched(state_dir: Path, backend: str = LOCAL_AGY_BACKEND) -> bool:
    """Whether the installed backend carries the manual compaction this engine adds."""
    try:
        return MARKER in backend_file(state_dir, backend).read_text(encoding="utf-8")
    except OSError:  # nexgen: allow reason="an absent plugin is simply not patched; the groomer then leaves that backend alone"
        return False


def apply(state_dir: Path, backend: str = LOCAL_AGY_BACKEND, *, dry_run: bool = False) -> Patch:
    """Add the manual compaction when it is absent, and say what happened.

    Nothing is guessed about a file this engine cannot read or no longer
    recognises: an unknown shape ends in `unexpected`, which the setup prints,
    because a silent skip is how a backend quietly stops being compactable.
    """
    path = backend_file(state_dir, backend)
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
        atomic_write_text(path, text.replace(ANCHOR, ANCHOR + BLOCK, 1))
    except OSError as exc:  # nexgen: allow reason="a plugin file that cannot be written is reported, never a crash after a successful install"
        return Patch(FAILED, path, detail=str(exc))
    return Patch(PATCHED, path, backup=backup)
