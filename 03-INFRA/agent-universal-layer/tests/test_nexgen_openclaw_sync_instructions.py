"""OpenClaw's instructions: one managed block per agent workspace.

OpenClaw has no pointer file of its own. It loads the `AGENTS.md` of the agent's
working directory at every session, truncated, so the engine owns a delimited
block at the top of each of those files and nothing else in them. These tests run
the real guard step and the real doctor check against a fake machine: the block
lands, the person's text survives byte for byte, the second cycle writes nothing,
and a machine without OpenClaw is left completely alone.
"""
from __future__ import annotations

import json
from pathlib import Path

from nexgen_core.checks.instructions_checks import check_openclaw_instructions
from nexgen_core.guard import GuardRunner
from nexgen_core.guard_instructions import (
    OPENCLAW_BLOCK_BEGIN,
    OPENCLAW_BLOCK_END,
    openclaw_block_state,
    openclaw_instruction_block,
    openclaw_workspaces,
    with_openclaw_block,
)
from nexgen_core.report import Severity

CANON_RELATIVE = ("03-INFRA", "agent-universal-layer", "instructions", "AGENTS.md")


def a_machine(tmp_path: Path, config: dict | None) -> tuple[Path, Path]:
    """A fake vault and home. `config=None` means OpenClaw was never set up here."""
    vault = tmp_path / "vault"
    canon = vault.joinpath(*CANON_RELATIVE)
    canon.parent.mkdir(parents=True)
    canon.write_text("# canonical instructions\n", encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    if config is not None:
        state = home / ".openclaw"
        state.mkdir(parents=True)
        (state / "openclaw.json").write_text(json.dumps(config), encoding="utf-8")
    return vault, home


def aligns(vault: Path, home: Path) -> list[str]:
    return GuardRunner(vault_data=vault, home=home).align_instructions()


def touching(actions: list[str], target: Path) -> list[str]:
    """The action lines that name `target`. The message itself is translated, the path is not."""
    return [action for action in actions if str(target) in action]


def read_bytes_of(path: Path) -> bytes:
    return path.read_bytes()


# ------------------------------------------------------------------ the block


def test_the_block_names_the_canonical_file_and_says_to_follow_it():
    canon = Path("/vault/03-INFRA/agent-universal-layer/instructions/AGENTS.md")
    block = openclaw_instruction_block(canon)
    assert block.startswith(OPENCLAW_BLOCK_BEGIN) and block.endswith(OPENCLAW_BLOCK_END)
    assert str(canon) in block
    assert "session start" in block
    assert openclaw_block_state(block, block) == "ok"


def test_a_block_below_the_persons_text_is_moved_and_never_duplicated():
    canon = Path("/v/canon.md")
    block = openclaw_instruction_block(canon)
    text = f"my notes first\n\n{block}\nmy notes last\n"
    rewritten = with_openclaw_block(text, block)
    assert rewritten.count(OPENCLAW_BLOCK_BEGIN) == 1
    assert rewritten.startswith(block)
    assert rewritten.endswith("\nmy notes last\n")
    assert "my notes first" in rewritten


def test_only_one_of_the_two_markers_is_broken_not_guessed():
    text = f"{OPENCLAW_BLOCK_BEGIN}\nsomeone deleted the end marker\nmy rules\n"
    assert openclaw_block_state(text, "any") == "broken"


# ------------------------------------------------------- the workspace is real


def test_the_block_goes_on_top_and_the_persons_text_survives_untouched(tmp_path):
    workspace = tmp_path / "work"
    workspace.mkdir()
    user_text = "# My own rules\n\n- be brief\n"
    (workspace / "AGENTS.md").write_text(user_text, encoding="utf-8")
    vault, home = a_machine(tmp_path, {"agents": {"entries": {"main": {"workspace": str(workspace)}}}})

    actions = aligns(vault, home)

    written = (workspace / "AGENTS.md").read_text(encoding="utf-8")
    assert written.startswith(OPENCLAW_BLOCK_BEGIN)
    assert written.endswith(user_text)
    assert str(vault.joinpath(*CANON_RELATIVE)) in written
    assert touching(actions, workspace / "AGENTS.md")


def test_a_second_run_writes_nothing_and_leaves_no_second_backup(tmp_path):
    workspace = tmp_path / "work"
    workspace.mkdir()
    target = workspace / "AGENTS.md"
    target.write_text("my rules\n", encoding="utf-8")
    vault, home = a_machine(tmp_path, {"agents": {"defaults": {"workspace": str(workspace)}}})

    aligns(vault, home)
    after_first = read_bytes_of(target)
    backups_after_first = sorted(workspace.glob("AGENTS.md*"))
    actions = aligns(vault, home)

    assert touching(actions, target) == []
    assert read_bytes_of(target) == after_first
    assert sorted(workspace.glob("AGENTS.md*")) == backups_after_first


def test_a_missing_file_gets_the_block_and_nothing_else(tmp_path):
    workspace = tmp_path / "work"
    workspace.mkdir()
    vault, home = a_machine(tmp_path, {"agents": {"defaults": {"workspace": str(workspace)}}})

    aligns(vault, home)

    block = openclaw_instruction_block(vault.joinpath(*CANON_RELATIVE))
    assert (workspace / "AGENTS.md").read_text(encoding="utf-8") == f"{block}\n"


def test_a_workspace_folder_that_does_not_exist_is_never_created(tmp_path):
    missing = tmp_path / "not-yet"
    vault, home = a_machine(tmp_path, {"agents": {"defaults": {"workspace": str(missing)}}})

    actions = aligns(vault, home)

    assert not missing.exists()
    assert touching(actions, missing) == []


def test_without_an_openclaw_config_nothing_is_written(tmp_path):
    vault, home = a_machine(tmp_path, None)

    actions = aligns(vault, home)

    assert touching(actions, home / ".openclaw") == []
    assert not (home / ".openclaw").exists()
    assert check_openclaw_instructions(vault, home) is None


def test_every_declared_agent_workspace_gets_its_own_block(tmp_path):
    pinned = tmp_path / "agents" / "one"
    shared = tmp_path / "agents" / "shared"
    pinned.mkdir(parents=True)
    shared.mkdir(parents=True)
    config = {
        "agents": {
            "defaults": {"workspace": str(shared)},
            "entries": {
                "one": {"workspace": str(pinned)},
                "two": {},  # no workspace: OpenClaw falls back to agents.defaults.workspace
            },
        }
    }
    vault, home = a_machine(tmp_path, config)

    aligns(vault, home)

    assert OPENCLAW_BLOCK_BEGIN in (pinned / "AGENTS.md").read_text(encoding="utf-8")
    assert OPENCLAW_BLOCK_BEGIN in (shared / "AGENTS.md").read_text(encoding="utf-8")


def test_without_a_declared_workspace_the_state_dirs_workspace_is_used(tmp_path):
    vault, home = a_machine(tmp_path, {"gateway": {"port": 1}})
    fallback = home / ".openclaw" / "workspace"
    fallback.mkdir()

    aligns(vault, home)

    assert OPENCLAW_BLOCK_BEGIN in (fallback / "AGENTS.md").read_text(encoding="utf-8")


def test_a_tilde_path_is_expanded_against_the_given_home_not_the_real_one(tmp_path):
    vault, home = a_machine(tmp_path, {"agents": {"defaults": {"workspace": "~/work"}}})
    (home / "work").mkdir()

    aligns(vault, home)

    assert (home / "work" / "AGENTS.md").is_file()


def test_a_symlinked_agents_md_is_written_through_not_replaced(tmp_path):
    """A dotfiles-managed AGENTS.md is a link: replacing it detaches it from its owner."""
    workspace = tmp_path / "work"
    workspace.mkdir()
    dotfiles = tmp_path / "dotfiles" / "AGENTS.md"
    dotfiles.parent.mkdir()
    dotfiles.write_text("le mie regole\n", encoding="utf-8")
    link = workspace / "AGENTS.md"
    link.symlink_to(dotfiles)
    vault, home = a_machine(tmp_path, {"agents": {"defaults": {"workspace": str(workspace)}}})

    aligns(vault, home)

    assert link.is_symlink(), "the link was replaced by a regular file"
    written = dotfiles.read_text(encoding="utf-8")
    assert written.startswith(OPENCLAW_BLOCK_BEGIN)
    assert written.endswith("le mie regole\n")


def test_a_link_to_the_canonical_file_is_replaced_not_written_through(tmp_path):
    """Writing through that link would put the pointer inside the file it names."""
    workspace = tmp_path / "work"
    workspace.mkdir()
    vault, home = a_machine(tmp_path, {"agents": {"defaults": {"workspace": str(workspace)}}})
    canon = vault.joinpath(*CANON_RELATIVE)
    link = workspace / "AGENTS.md"
    link.symlink_to(canon)

    aligns(vault, home)

    assert not link.is_symlink()
    assert link.read_text(encoding="utf-8").startswith(OPENCLAW_BLOCK_BEGIN)
    assert canon.read_text(encoding="utf-8") == "# canonical instructions\n"


def test_crlf_line_endings_survive_a_rewrite(tmp_path):
    workspace = tmp_path / "work"
    workspace.mkdir()
    target = workspace / "AGENTS.md"
    target.write_bytes(b"line one\r\nline two\r\n")
    vault, home = a_machine(tmp_path, {"agents": {"defaults": {"workspace": str(workspace)}}})

    aligns(vault, home)

    written = target.read_bytes()
    assert b"line one\r\nline two\r\n" in written
    assert b"\r\r\n" not in written
    assert OPENCLAW_BLOCK_BEGIN.encode() + b"\r\n" in written


def test_an_incomplete_marker_pair_is_reported_and_left_alone(tmp_path):
    workspace = tmp_path / "work"
    workspace.mkdir()
    target = workspace / "AGENTS.md"
    half = f"{OPENCLAW_BLOCK_BEGIN}\nmy rules\n"
    target.write_text(half, encoding="utf-8")
    vault, home = a_machine(tmp_path, {"agents": {"defaults": {"workspace": str(workspace)}}})

    actions = aligns(vault, home)

    assert target.read_text(encoding="utf-8") == half
    assert touching(actions, target)


def test_openclaw_workspaces_are_deduplicated(tmp_path):
    shared = tmp_path / "shared"
    shared.mkdir()
    config = tmp_path / "openclaw.json"
    config.write_text(
        json.dumps({"agents": {"defaults": {"workspace": str(shared)},
                               "entries": {"a": {}, "b": {"workspace": str(shared)}}}}),
        encoding="utf-8",
    )
    assert openclaw_workspaces(config, tmp_path) == [shared]


# --------------------------------------------------------------- the doctor


def test_the_check_warns_when_the_block_is_missing_and_names_the_command(tmp_path):
    workspace = tmp_path / "work"
    workspace.mkdir()
    vault, home = a_machine(tmp_path, {"agents": {"defaults": {"workspace": str(workspace)}}})

    outcome = check_openclaw_instructions(vault, home)

    assert outcome is not None and outcome.severity is Severity.WARN
    assert "nexgen sync" in (outcome.action or "")


def test_the_check_is_green_once_the_block_is_aligned(tmp_path):
    workspace = tmp_path / "work"
    workspace.mkdir()
    vault, home = a_machine(tmp_path, {"agents": {"defaults": {"workspace": str(workspace)}}})
    aligns(vault, home)

    outcome = check_openclaw_instructions(vault, home)

    assert outcome is not None and outcome.severity is Severity.OK


def test_the_check_reports_a_workspace_folder_that_does_not_exist(tmp_path):
    vault, home = a_machine(tmp_path, {"agents": {"defaults": {"workspace": str(tmp_path / "later")}}})

    outcome = check_openclaw_instructions(vault, home)

    assert outcome is not None and outcome.severity is Severity.WARN
    assert "nexgen openclaw setup" in (outcome.action or "")


def test_the_check_warns_when_the_block_is_present_but_different(tmp_path):
    workspace = tmp_path / "work"
    workspace.mkdir()
    (workspace / "AGENTS.md").write_text(
        f"{OPENCLAW_BLOCK_BEGIN}\nCanonical instructions live at:\n/nonexistent/AGENTS.md\n{OPENCLAW_BLOCK_END}\n",
        encoding="utf-8",
    )
    vault, home = a_machine(tmp_path, {"agents": {"defaults": {"workspace": str(workspace)}}})

    outcome = check_openclaw_instructions(vault, home)

    assert outcome is not None and outcome.severity is Severity.WARN
    assert "different" in (outcome.message or "")


def test_guard_and_doctor_agree_on_an_aligned_block(tmp_path):
    """If they disagreed, one would rewrite what the other calls aligned, every cycle."""
    workspace = tmp_path / "work"
    workspace.mkdir()
    vault, home = a_machine(tmp_path, {"agents": {"defaults": {"workspace": str(workspace)}}})
    block = openclaw_instruction_block(vault.joinpath(*CANON_RELATIVE))
    (workspace / "AGENTS.md").write_text(f"{block}\nmine\n", encoding="utf-8")

    assert check_openclaw_instructions(vault, home).severity is Severity.OK
    assert touching(aligns(vault, home), workspace / "AGENTS.md") == []


def test_guard_and_doctor_agree_on_a_misaligned_block(tmp_path):
    workspace = tmp_path / "work"
    workspace.mkdir()
    vault, home = a_machine(tmp_path, {"agents": {"defaults": {"workspace": str(workspace)}}})
    (workspace / "AGENTS.md").write_text("mine\n", encoding="utf-8")

    assert check_openclaw_instructions(vault, home).severity is Severity.WARN
    assert touching(aligns(vault, home), workspace / "AGENTS.md")
