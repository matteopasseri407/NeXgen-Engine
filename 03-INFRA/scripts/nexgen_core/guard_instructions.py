"""Instruction pointers: every CLI reads the canonical AGENTS.md, or a pointer to it.

nexgen: one-job reason="every runtime's instruction pointer and every OpenClaw workspace block is one wiring step, written once here so no CLI keeps a stale copy"
One job: keep the compatibility pointers (`~/.claude/CLAUDE.md`,
`~/.codex/AGENTS.md`, `~/.gemini/config/AGENTS.md`, the OpenCode scope file)
aimed at the canonical `AGENTS.md`, run the step that keeps the managed blocks
of each OpenClaw agent workspace's own `AGENTS.md` current, drop the dead
OpenCode `instructions` entries an older guard added, and relink the
Windows-only private local-model adapter. Those blocks are written by
`openclaw/org_block.py`, their owner: this module only calls it, so one place
decides what their bytes are.

The canonical file is the one source; several runtimes reading their own stale
copy is the opposite of the invariant this engine exists for. It used to be the
body of `GuardRunner`, where a CLI pointer sat beside git quarantine and
permission posture. The coordinator keeps one method per step so a single
step can still be replaced in a test, and every write goes through the same
atomic writer and safety copy as before.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from nexgen_core.action_notes import WARN
from nexgen_core.files import atomic_write_text, symlink_write_target
from nexgen_core.i18n import t
from nexgen_core.jsonc import parse_jsonc, set_jsonc_top_level_value


def _same_file(a: str, b: Path) -> bool:
    """True if both name the same file, whatever the spelling (absolute or ~)."""
    try:
        return Path(a).expanduser().resolve() == b.expanduser().resolve()
    except OSError:
        return a == str(b)


#: First line of the engine's own pointer file. It is how a file on disk is
#: recognised as ours rather than as the person's own instructions: the
#: pointers written by earlier engine versions start with the same line.
CLAUDE_POINTER_HEADER = "# Claude compatibility pointer"


def claude_pointer_path(home: Path) -> Path:
    """Where Claude Code reads user-scope instructions: `~/.claude/CLAUDE.md`.

    Documented user scope, loaded for every project
    (https://code.claude.com/docs/en/memory). `~/CLAUDE.md` is only a
    home-directory project file, so a session started outside the home folder
    never sees it. One owner for the path: the guard writes it, the doctor
    validates it, and neither spells it out again.
    """
    return home / ".claude" / "CLAUDE.md"


def is_engine_pointer(text: str) -> bool:
    """True when the text is the engine's own pointer, whatever version wrote it.

    The header line is the marker: it is what distinguishes a file the engine
    may regenerate from a person's own instructions living at the same path.
    """
    return text.lstrip().startswith(CLAUDE_POINTER_HEADER)


def _claude_pointer_text(canon: Path) -> str:
    """The pointer body: short, stable, and the only content the engine owns."""
    return (
        f"{CLAUDE_POINTER_HEADER}\n\n"
        "Canonical instructions live at:\n"
        f"{canon}\n\n"
        "At session start, read and follow that file when the user-specific agent policy is needed.\n"
        "Do not duplicate the full bootstrap in CLAUDE.md.\n"
    )


def align_instructions(runner) -> list[str]:
    """Aligns the instruction compatibility pointers (~/.claude/CLAUDE.md etc.)."""
    canon = runner.vault_data / "03-INFRA" / "agent-universal-layer" / "instructions" / "AGENTS.md"
    if not canon.is_file():
        return []

    actions: list[str] = []
    claude_md = claude_pointer_path(runner.home)
    content = _claude_pointer_text(canon)

    # The new pointer goes in first: the old home file only disappears once home
    # sessions read the same pointer at the documented path, never before.
    actions.extend(_write_claude_pointer(claude_md, canon, content))
    actions.extend(_retire_home_claude_pointer(
        runner.home / "CLAUDE.md", claude_md, content, may_remove=_pointer_in_place(claude_md, content),
    ))

    # The other three CLIs read the canonical file directly. Aligning
    # only one of them would mean having a canonical source for one
    # runtime and three stale copies for the others, which is the
    # opposite of the invariant.
    for label, target in (
        ("codex", runner.home / ".codex" / "AGENTS.md"),
        ("antigravity", runner.home / ".gemini" / "config" / "AGENTS.md"),
    ):
        changed, warn = runner._link_to_canonical(target, canon)
        if changed:
            actions.append(t("{label} instructions restored to canonical", label=label))
        if warn:
            actions.append(warn)

    actions.extend(_align_opencode_steps(runner, canon))
    actions.extend(runner._align_openclaw_instructions())

    return actions


def _symlink_points_at(link: Path, expected: Path) -> bool:
    """True when `link` is a symlink whose final target is `expected`.

    Recognises the layout where the file the guard wants to retire *is* the file
    Claude reads through a link: deleting it would leave the link dangling.
    """
    if not link.is_symlink():
        return False
    try:
        return link.resolve() == expected.resolve()
    except (OSError, RuntimeError):  # nexgen: allow reason="a link loop or a vanished parent is not this layout, and the step that follows handles it"
        return False


def _pointer_in_place(target: Path, content: str) -> bool:
    """True when the file Claude reads is the pointer the engine just wrote.

    Read back rather than assumed: the write step skips a file it does not own,
    and its failure warnings are already in the action list.
    """
    try:
        return target.is_file() and target.read_text(encoding="utf-8") == content
    except (OSError, UnicodeDecodeError):  # nexgen: allow reason="an unreadable file is not the pointer in place; the write step already reported why"
        return False


def _retire_home_claude_pointer(legacy: Path, target: Path, content: str, *, may_remove: bool) -> list[str]:
    """Drops the engine's old `~/CLAUDE.md` so home sessions do not load it twice.

    Only an exact copy of the engine's own pointer is deleted, only after the
    usual safety backup, and only when `may_remove` says the pointer is already
    at the documented path: removing it earlier would leave a home session with
    no pointer at all. A symlink is the owner's layout (a dotfiles repository,
    typically) and any other text is the person's own content: both stay
    exactly as they are and are reported, because this file is no longer the
    engine's to write. The same holds when the documented path is a link to
    this very file: there the home file *is* what Claude reads, so deleting it
    would leave the link dangling and no instructions anywhere.
    """
    if legacy.is_symlink():
        return [t("{path} is a symlink: left untouched, Claude reads {target}", path=legacy, target=target)]
    if _symlink_points_at(target, legacy):
        return [t("{path} kept: {target} is a symlink to it, removing it would leave the link dangling",
                  path=legacy, target=target)]
    if not legacy.is_file():
        return []
    try:
        text = legacy.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return [WARN + t(
            "instruction pointer {path} left untouched: it cannot be read ({error})",
            path=legacy, error=exc,
        )]
    if text != content:
        return [t("{path} holds your own instructions: left untouched, Claude reads {target}",
                  path=legacy, target=target)]
    if not may_remove:
        return [t("{path} still holds the old pointer: kept, because {target} holds your own instructions",
                  path=legacy, target=target)]
    from nexgen_core.files import backup_file

    try:
        backup_file(legacy, tag="instructions")
    except OSError as exc:
        return [WARN + t(
            "instruction pointer {path} left untouched: safety backup failed ({error})",
            path=legacy, error=exc,
        )]
    try:
        legacy.unlink()
    except OSError as exc:
        return [WARN + t("instruction pointer {path} could not be removed ({error})", path=legacy, error=exc)]
    return [t("Removed the old instruction pointer {path}: Claude reads ~/.claude/CLAUDE.md", path=legacy)]


def _write_claude_pointer(target: Path, canon: Path, content: str) -> list[str]:
    """Puts the pointer in Claude's user-scope file, and only there.

    `~/.claude/CLAUDE.md` is documented user scope, a place people already
    keep personal instructions: the bytes there -- read through a symlink when
    there is one, because that is what Claude loads -- decide whether anything
    is written at all. A real file that is neither the engine's pointer nor
    empty is left alone and reported instead of overwritten, because a backup
    is not consent. A dotfiles-managed symlink keeps the behaviour it had for
    the home file: the link survives and the file behind it is written, except
    for a link to the canonical file, which is replaced.
    """
    try:
        existing = target.read_text(encoding="utf-8") if target.is_file() else None
    except (OSError, UnicodeDecodeError) as exc:
        return [WARN + t(
            "instruction pointer {path} left untouched: it cannot be read ({error})",
            path=target, error=exc,
        )]
    if existing == content:
        return []
    if not target.is_symlink() and existing is not None and existing.strip() and not is_engine_pointer(existing):
        return [t("{path} holds your own instructions: left untouched", path=target)]

    # A dotfiles-managed pointer is a symlink: write through it so the link
    # (and the repository behind it) survives the update. The exception is a
    # link to the canonical file, which is replaced instead: writing through
    # it would overwrite the instructions.
    write_target = symlink_write_target(target, keep=canon)
    if not target.is_file():
        atomic_write_text(write_target, content)
        return [t("Updated instruction pointer {path}", path=target)]
    # The file may contain hand-written lines. Regenerating it is fine;
    # making it disappear without a copy is not: if the safety copy itself
    # fails, stop instead of destroying the only copy of those lines.
    from nexgen_core.files import backup_file

    try:
        backup_file(target, tag="instructions")
    except OSError as exc:
        return [WARN + t(
            "instruction pointer {path} left untouched: safety backup failed ({error})",
            path=target, error=exc,
        )]
    atomic_write_text(write_target, content)
    return [t("Updated instruction pointer {path}", path=target)]


def _align_opencode_steps(runner, canon: Path) -> list[str]:
    """OpenCode's own two steps: the scope file, then the dead V1 array.

    Extracted so the coordinator's body stays readable; the two seams
    (`_align_opencode_instructions`, `_drop_dead_opencode_instructions_array`)
    stay where they were, one per step, replaceable in a test.
    """
    actions: list[str] = []
    for action in (
        runner._align_opencode_instructions(canon),
        runner._drop_dead_opencode_instructions_array(),
    ):
        if action:
            actions.append(action)
    return actions


def _link_to_canonical(runner, target: Path, canon: Path) -> tuple[bool, str | None]:
    """Points `target` at the canonical file.

    Returns (changed, warning): a failed safety copy skips the target
    with a warning instead of silently leaving a stale pointer behind.
    """
    try:
        if target.is_symlink() and target.resolve() == canon.resolve():
            return False, None
        target.parent.mkdir(parents=True, exist_ok=True)
        # A folded copy identical to the canon is aligned: no backup, no rewrite.
        same_copy = target.is_file() and not target.is_symlink() and target.read_bytes() == canon.read_bytes()
        if (target.exists() or target.is_symlink()) and not same_copy:
            # A real copy may contain hand-written lines. If the safety
            # copy itself fails, stop here: unlinking anyway would destroy
            # the only copy of those lines.
            if target.is_file() and not target.is_symlink():
                from nexgen_core.files import backup_file

                try:
                    backup_file(target, tag="instructions")
                except OSError as exc:
                    return False, WARN + t(
                        "instruction pointer {path} left untouched: safety backup failed ({error})",
                        path=target, error=exc)
        try:
            from nexgen_core.files import publish_symlink

            publish_symlink(target, canon)
        except OSError:
            if same_copy:
                return False, None
            # Windows without symlink privileges: a copy beats nothing.
            # publish_symlink never leaves the target missing, so a copy
            # fallback only runs when the atomic path failed.
            if target.is_dir() and not target.is_symlink():
                return False, WARN + t("instruction pointer {path} is a directory, left untouched",
                                       path=target)
            try:
                if target.is_symlink() or target.is_file():
                    target.unlink()
            except OSError:  # nexgen: allow reason="the copy below is the fallback that does the work; a real failure surfaces there"
                pass
            shutil.copy2(canon, target)
        return True, None
    except OSError as exc:
        return False, WARN + t("instruction pointer {path} not aligned ({error})",
                               path=target, error=exc)


def _align_opencode_instructions(runner, canon: Path) -> str | None:
    """Points OpenCode V2 at the canonical file through the scope file it
    actually loads (`~/.config/opencode/AGENTS.md`).

    The V2 config schema accepts an `instructions` array but does not
    resolve its files (official docs, verified 2026-09-22): a green
    doctor on that array was a false green. The scope file is a symlink
    at the canonical bootstrap, exactly like Codex and Antigravity --
    no content is ever copied, so no private policy or identity can leak
    into the public engine through this path.

    A REAL file (not a symlink) is left untouched: on a machine with the
    private identity layer it is that layer's derivative, and replacing
    it with a pointer would destroy the persona. The doctor reports that
    case separately instead of "fixing" it here.
    """
    from nexgen_core.paths import opencode_agents_file

    target = opencode_agents_file(runner.home)
    if target.is_symlink() or not target.exists():
        changed, warn = runner._link_to_canonical(target, canon)
        if changed:
            return t("opencode instructions restored to canonical")
        return warn
    # A real file: private derivative or hand-written. Not ours to take.
    return None


def _drop_dead_opencode_instructions_array(runner, renderer_cls) -> str | None:
    """Removes engine-added canonical entries from the dead V1 array.

    The array is accepted but unresolved by V2, so entries the old guard
    added only pretend to load the bootstrap. Entries equal to the
    canonical path go away (backup first); anything else -- a choice the
    user made -- stays exactly as it is.

    `renderer_cls` is handed in by the coordinator on purpose: a test that
    patches `nexgen_core.guard.McpRenderer` still replaces the renderer this
    step uses, because the lookup happens in the coordinator's namespace at
    call time.
    """
    from nexgen_core.paths import canonical_instructions

    canon = canonical_instructions(runner.vault_data)
    renderer = renderer_cls(vault_data=runner.vault_data, home=runner.home)
    candidate = renderer.opencode_config_path()
    if not candidate.is_file():
        return None
    try:
        raw = candidate.read_text(encoding="utf-8")
        data = parse_jsonc(raw) if candidate.suffix == ".jsonc" else json.loads(raw or "{}")
    except (OSError, ValueError):  # nexgen: allow reason="an unreadable or non-JSON config has no dead array to remove"
        return None
    if not isinstance(data, dict) or not isinstance(data.get("instructions"), list):
        return None
    entries = data["instructions"]
    kept = [e for e in entries if not (isinstance(e, str) and _same_file(e, canon))]
    if len(kept) == len(entries):
        return None
    try:
        if candidate.suffix == ".jsonc" and raw.strip():
            if kept:
                body = set_jsonc_top_level_value(raw, "instructions", kept)
            else:
                from nexgen_core.jsonc import remove_jsonc_top_level_value

                body = remove_jsonc_top_level_value(raw, "instructions")
        else:
            data["instructions"] = kept
            if not kept:
                del data["instructions"]
            body = json.dumps(data, indent=2) + "\n"
        from nexgen_core.files import backup_file

        # Same rule as the pointer above: no safety copy, no rewrite. The
        # user's opencode.json may hold lines the engine never added.
        try:
            backup_file(candidate, tag="instructions")
        except OSError as exc:
            return WARN + t(
                "opencode dead 'instructions' entries left untouched: safety backup failed ({error})",
                error=exc,
            )
        atomic_write_text(candidate, body)
    except OSError:  # nexgen: allow reason="a config that cannot be rewritten keeps its dead entries; the next cycle retries"
        return None
    return t("opencode dead 'instructions' entries removed")


def align_openclaw_instructions(runner) -> list[str]:
    """Puts the managed blocks in every OpenClaw workspace: their owner does the work.

    The blocks, the squad's manual and the workspace resolution live in
    `openclaw/org_block.py`, which the setup and the doctor read too. This stays
    as the one name the sync cycle calls, so the step never depends on where the
    bytes are actually written.
    """
    from nexgen_core.openclaw.org_block import align_openclaw_instructions as align_blocks

    return align_blocks(runner)


def align_local_model_runtime(runner) -> list[str]:
    """Windows-only: relinks the private local-model-agent.ps1 adapter (bring-your-own).

    Port of the release's local_model_runtime step: the vault can supply
    a private adapter (never in the public product); if it's absent
    that's the expected default, not an error. Also installs the stable
    local-worker/local-agent wrappers.
    """
    if sys.platform != "win32":
        return []
    src = runner.vault_data / "03-INFRA" / "scripts" / "local-model-agent.ps1"
    if not src.is_file():
        return []
    local_bin = runner.home / ".local" / "bin"
    local_bin.mkdir(parents=True, exist_ok=True)
    actions: list[str] = []
    runtime = local_bin / "local-model-agent.ps1"
    try:
        if runtime.is_symlink() or runtime.exists():
            runtime.unlink(missing_ok=True)
    except OSError as exc:
        # An optional adapter must never fail the whole apply: on
        # Windows a locked .ps1 is routine, not corruption.
        actions.append(WARN + t("local-model: adapter not relinked ({error})", error=exc))
        return actions
    try:
        runtime.symlink_to(src)
        actions.append(t("local-model: relinked local-model-agent.ps1"))
    except OSError:
        shutil.copy2(src, runtime)
        actions.append(t("local-model: copied local-model-agent.ps1"))
    wrappers = {
        "local-worker.ps1": "$ScriptPath = Join-Path $PSScriptRoot 'local-model-agent.ps1'\r\n& $ScriptPath -Mode worker @args\r\n",
        "local-agent.ps1": "$ScriptPath = Join-Path $PSScriptRoot 'local-model-agent.ps1'\r\n& $ScriptPath -Mode agent @args\r\n",
    }
    for name, content in wrappers.items():
        target = local_bin / name
        if not target.is_file() or target.read_text(encoding="utf-8", errors="replace") != content:
            atomic_write_text(target, content)
            actions.append(t("local-model: installed wrapper {name}", name=name))
    return actions
