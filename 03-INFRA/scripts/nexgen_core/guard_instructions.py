"""Instruction pointers: every CLI reads the canonical AGENTS.md, or a pointer to it.

One job: keep the compatibility pointers (`~/CLAUDE.md`, `~/.codex/AGENTS.md`,
`~/.gemini/config/AGENTS.md`, the OpenCode scope file) aimed at the canonical
`AGENTS.md`, keep the managed block inside each OpenClaw agent workspace's own
`AGENTS.md`, drop the dead OpenCode `instructions` entries an older guard
added, and relink the Windows-only private local-model adapter.

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

#: Delimiters of the block the engine owns inside an OpenClaw agent workspace's
#: `AGENTS.md`. Everything outside them is the person's text and is never
#: rewritten; everything inside is regenerated from the canonical file's path.
OPENCLAW_BLOCK_BEGIN = "<!-- nexgen:instructions:begin -->"
OPENCLAW_BLOCK_END = "<!-- nexgen:instructions:end -->"


def _same_file(a: str, b: Path) -> bool:
    """True if both name the same file, whatever the spelling (absolute or ~)."""
    try:
        return Path(a).expanduser().resolve() == b.expanduser().resolve()
    except OSError:
        return a == str(b)


def align_instructions(runner) -> list[str]:
    """Aligns the instruction compatibility pointers (~/CLAUDE.md etc.)."""
    canon = runner.vault_data / "03-INFRA" / "agent-universal-layer" / "instructions" / "AGENTS.md"
    if not canon.is_file():
        return []

    actions: list[str] = []
    claude_md = runner.home / "CLAUDE.md"
    content = (
        "# Claude compatibility pointer\n\n"
        "Canonical instructions live at:\n"
        f"{canon}\n\n"
        "At session start, read and follow that file when the user-specific agent policy is needed.\n"
        "Do not duplicate the full bootstrap in CLAUDE.md.\n"
    )

    if not claude_md.is_file() or claude_md.read_text(encoding="utf-8") != content:
        # A dotfiles-managed ~/CLAUDE.md is a symlink: write through it so
        # the link (and the repository behind it) survives the update. The
        # exception is a link to the canonical file, which is replaced
        # instead: writing through it would overwrite the instructions.
        from nexgen_core.files import symlink_write_target

        write_target = symlink_write_target(claude_md, keep=canon)
        # The file may contain hand-written lines. Regenerating it is
        # fine; making it disappear without a copy is not: if the
        # safety copy itself fails, stop instead of destroying the
        # only copy of those lines.
        if claude_md.is_file():
            from nexgen_core.files import backup_file

            try:
                backup_file(claude_md, tag="instructions")
            except OSError as exc:
                actions.append(WARN + t(
                    "instruction pointer {path} left untouched: safety backup failed ({error})",
                    path=claude_md, error=exc,
                ))
            else:
                atomic_write_text(write_target, content)
                actions.append(t("Updated instruction pointer {path}", path=claude_md))
        else:
            atomic_write_text(write_target, content)
            actions.append(t("Updated instruction pointer {path}", path=claude_md))

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


def openclaw_instruction_block(canon: Path) -> str:
    """The managed block: a pointer to the canonical file, nothing duplicated.

    OpenClaw loads the `AGENTS.md` of the agent's own working directory at every
    session and truncates it (`agents.defaults.bootstrapMaxChars`), which is why
    this block goes at the top of the file instead of the end.
    """
    return "\n".join((
        OPENCLAW_BLOCK_BEGIN,
        "# NeXgen Engine instructions",
        "",
        "Canonical instructions live at:",
        str(canon),
        "",
        "At session start, read and follow that file before acting: it is the single",
        "source, and copying its content here would only let the two drift apart.",
        "Everything outside this block belongs to you and is never rewritten by the engine.",
        OPENCLAW_BLOCK_END,
    ))


def openclaw_block_state(text: str, block: str) -> str:
    """How the managed block sits in `text`: ok, missing, different, or broken.

    A file with only one of the two markers is "broken" (a person edited inside
    the block, or a write was cut short): guessing where the block starts or
    ends there could delete their text, so the caller reports and leaves it.
    """
    begin = text.find(OPENCLAW_BLOCK_BEGIN)
    end = text.find(OPENCLAW_BLOCK_END)
    if begin < 0 and end < 0:
        return "missing"
    if begin < 0 or end < 0 or end < begin:
        return "broken"
    if text[begin:end + len(OPENCLAW_BLOCK_END)] != block:
        return "different"
    return "ok" if begin == 0 else "different"


def with_openclaw_block(text: str, block: str) -> str:
    """`text` with the managed block on top and the person's own text kept as it is.

    A block found anywhere else is moved, not duplicated: one file, one pointer.
    The separator follows the block's own line endings, so a CRLF file stays CRLF
    instead of ending up with two conventions in one file.
    """
    sep = "\r\n" if "\r\n" in block else "\n"
    begin = text.find(OPENCLAW_BLOCK_BEGIN)
    end = text.find(OPENCLAW_BLOCK_END)
    rest = text
    if 0 <= begin < end:
        rest = text[:begin] + text[end + len(OPENCLAW_BLOCK_END):]
        tail = rest[begin:]
        if tail.startswith("\r\n"):
            rest = rest[:begin] + rest[begin + 2:]
        elif tail.startswith("\n"):
            rest = rest[:begin] + rest[begin + 1:]
    return f"{block}{sep}{rest}"


def openclaw_line_ending(sample: str) -> str:
    """The line ending a file already uses, so a new block doesn't mix two."""
    return "\r\n" if "\r\n" in sample else "\n"


def openclaw_file_state(text: str, block: str) -> str:
    """`openclaw_block_state` for a file whose line endings are its own."""
    return openclaw_block_state(text, block.replace("\n", openclaw_line_ending(text)))


def _openclaw_path(value: str, home: Path, base: Path) -> Path:
    """A path from OpenClaw's own config, expanded against `home`.

    `~` is expanded by hand, not by `expanduser`: this file must resolve the
    same way for a test pointing at a temporary home as for the real machine,
    and `expanduser` reads the environment's home regardless.
    """
    if value == "~":
        return home
    if value.startswith(("~/", "~\\")):
        return home / value[2:]
    candidate = Path(value)
    return candidate if candidate.is_absolute() else base / candidate


def openclaw_workspaces(config_path: Path, home: Path) -> list[Path]:
    """Every agent workspace the OpenClaw config declares, deduplicated.

    `agents.entries.<id>.workspace` per agent, `agents.defaults.workspace` for
    the agents that pin none, and `<state dir>/workspace` when neither exists --
    the same fallback OpenClaw applies, with the state dir being the folder the
    config file lives in. An unreadable config yields nothing: this step cannot
    fix it, and the MCP checks already name the file.
    """
    from nexgen_core.mcp_render import load_json_object

    try:
        data = load_json_object(config_path)
    except ValueError:  # nexgen: allow reason="a corrupt config has no workspaces to align; the MCP checks report it"
        return []
    agents = data.get("agents") if isinstance(data.get("agents"), dict) else {}
    defaults = agents.get("defaults") if isinstance(agents.get("defaults"), dict) else {}
    declared = defaults.get("workspace")
    default_path = (
        _openclaw_path(declared, home, config_path.parent)
        if isinstance(declared, str) and declared.strip()
        else config_path.parent / "workspace"
    )
    entries = agents.get("entries") if isinstance(agents.get("entries"), dict) else {}
    found: list[Path] = []
    for agent in entries.values() or [None]:
        value = agent.get("workspace") if isinstance(agent, dict) else None
        path = (
            _openclaw_path(value, home, config_path.parent)
            if isinstance(value, str) and value.strip()
            else default_path
        )
        if path not in found:
            found.append(path)
    return found


def align_openclaw_instructions(runner) -> list[str]:
    """Puts the managed block in every OpenClaw agent workspace's `AGENTS.md`.

    OpenClaw has no instruction pointer file of its own: it reads the
    `AGENTS.md` of the agent's working directory. That file is the person's, so
    only the delimited block is ever written, only when it is missing or
    different, and a copy is taken first. A workspace folder that does not
    exist is left to the setup command that creates it.
    """
    from nexgen_core.paths import openclaw_config

    config_path = openclaw_config(runner.home)
    if not config_path.is_file():
        return []  # OpenClaw was never set up here: nothing points anywhere
    canon = runner.vault_data / "03-INFRA" / "agent-universal-layer" / "instructions" / "AGENTS.md"
    if not canon.is_file():
        return []
    block = openclaw_instruction_block(canon)
    actions: list[str] = []
    for workspace in openclaw_workspaces(config_path, runner.home):
        action = _align_openclaw_workspace(workspace, block, canon)
        if action:
            actions.append(action)
    return actions


def _align_openclaw_workspace(workspace: Path, block: str, canon: Path) -> str | None:
    """One OpenClaw workspace: the block written, or why it was left alone.

    A dotfiles-managed `AGENTS.md` is a symlink into its repository: it is
    followed, not replaced, except when it points at the canonical file.
    """
    target = workspace / "AGENTS.md"
    if not workspace.is_dir():
        return None  # the setup command creates the folder; the check reports it
    write_target = symlink_write_target(target, keep=canon)
    try:
        # newline="": the person's line endings are read and written back as they
        # are. Text mode would turn a CRLF file into CRCRLF on a rewrite.
        existing = ""
        if target.is_file():
            with target.open("r", encoding="utf-8", newline="") as handle:
                existing = handle.read()
    except OSError as exc:
        return WARN + t("OpenClaw instructions at {path} left untouched ({error})", path=target, error=exc)
    file_block = block.replace("\n", openclaw_line_ending(existing))
    state = openclaw_file_state(existing, block)
    if state == "ok":
        return None
    if state == "broken":
        return WARN + t(
            "OpenClaw instructions at {path}: the managed block's markers are incomplete, left untouched",
            path=target,
        )
    if target.is_file():
        from nexgen_core.files import backup_file

        try:
            backup_file(target, tag="instructions")
        except OSError as exc:
            return WARN + t(
                "OpenClaw instructions at {path} left untouched: safety backup failed ({error})",
                path=target, error=exc,
            )
    atomic_write_text(write_target, with_openclaw_block(existing, file_block), newline="")
    return t("OpenClaw instructions aligned in {path}", path=target)


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
