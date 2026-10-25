"""The manuals in an agent workspace, and who owns the file they land in.

The setup writes an `AGENTS.md` the squad declares. A file the person wrote is
never replaced, and the engine's own mark is what tells the two apart; the block
`nexgen sync` puts above it survives a rewrite. These tests drive the folder
directly: no setup, no OpenClaw, no home of anybody's.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from nexgen_core import i18n
from nexgen_core.guard import GuardRunner
from nexgen_core.openclaw.setup_agents import BOOTSTRAP_FILE, INSTRUCTIONS_FILE, prepare, write_instructions
from nexgen_core.openclaw.squad import INSTRUCTION_MARK
from nexgen_core.openclaw.templates import render_workspace

#: The text the squad would have produced through `get_squad_instructions`.
MARKED = f"{INSTRUCTION_MARK}: written from squad.yaml -->\n# capo\n\nOwn the code.\n"

#: What `nexgen sync` leaves above the mark: the pointer to the canonical file.
SYNC_BLOCK = "<!-- nexgen:instructions:begin -->\n# NeXgen Engine instructions\n<!-- nexgen:instructions:end -->\n"


@pytest.fixture(autouse=True)
def _english():
    """The sentences asserted here are the source ones, not this machine's language."""
    i18n.set_language("en")
    yield
    i18n.set_language(None)


def test_a_squad_manual_replaces_the_standard_one_in_a_new_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace-capo"

    prepare({"capo": workspace}, {"capo": True}, "en", lambda line: None, instructions={"capo": MARKED})

    text = (workspace / INSTRUCTIONS_FILE).read_text(encoding="utf-8")
    assert text == MARKED
    assert "You are the agent that writes and changes code." not in text


def test_the_other_manuals_of_a_squad_agent_are_still_written(tmp_path: Path) -> None:
    """`capo` also gets what Claude Code reads on its own; only AGENTS.md is the squad's."""
    workspace = tmp_path / "workspace-capo"

    prepare({"capo": workspace}, {"capo": True}, "en", lambda line: None, instructions={"capo": MARKED})

    assert (workspace / "CLAUDE.md").is_file()
    assert (workspace / ".claude" / "settings.json").is_file()


def test_an_agents_file_without_the_engine_mark_is_left_byte_for_byte(tmp_path: Path) -> None:
    """Hand-written text is not the engine's, whatever it says."""
    workspace = tmp_path / "workspace-capo"
    workspace.mkdir()
    mine = "# My own notes\n\nDo not touch this.\n"
    (workspace / INSTRUCTIONS_FILE).write_text(mine, encoding="utf-8")
    lines: list[str] = []

    prepare({"capo": workspace}, set(), "en", lines.append, instructions={"capo": MARKED})

    assert (workspace / INSTRUCTIONS_FILE).read_text(encoding="utf-8") == mine
    assert any("yours" in line for line in lines)


def test_a_marked_file_is_replaced_and_the_sync_block_on_top_survives(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace-capo"
    workspace.mkdir()
    (workspace / INSTRUCTIONS_FILE).write_text(SYNC_BLOCK + MARKED + "old text\n", encoding="utf-8")

    prepare({"capo": workspace}, set(), "en", lambda line: None, instructions={"capo": MARKED})

    text = (workspace / INSTRUCTIONS_FILE).read_text(encoding="utf-8")
    assert text.startswith(SYNC_BLOCK)
    assert text.count("<!-- nexgen:squad:begin") == 1
    assert "old text" not in text


def test_the_manual_the_engine_shipped_before_the_squad_declared_one_is_replaced(tmp_path: Path) -> None:
    """The workspace was prepared before `agents_md` existed: the file is still ours.

    The shipped manual carries no mark, so the ownership test alone would keep it
    byte for byte and a squad change would never reach that workspace again.
    """
    workspace = tmp_path / "workspace-capo"
    workspace.mkdir()
    shipped = render_workspace("capo", "en")["AGENTS.md"]
    (workspace / INSTRUCTIONS_FILE).write_text(shipped, encoding="utf-8")

    prepare({"capo": workspace}, set(), "en", lambda line: None, instructions={"capo": MARKED})

    text = (workspace / INSTRUCTIONS_FILE).read_text(encoding="utf-8")
    assert text == MARKED


def test_the_shipped_manual_is_replaced_under_the_sync_block_too(tmp_path: Path) -> None:
    """What `nexgen sync` leaves on a workspace the setup prepared earlier."""
    workspace = tmp_path / "workspace-capo"
    workspace.mkdir()
    shipped = render_workspace("capo", "en")["AGENTS.md"]
    (workspace / INSTRUCTIONS_FILE).write_text(SYNC_BLOCK + shipped, encoding="utf-8")

    prepare({"capo": workspace}, set(), "en", lambda line: None, instructions={"capo": MARKED})

    text = (workspace / INSTRUCTIONS_FILE).read_text(encoding="utf-8")
    assert text.startswith(SYNC_BLOCK)
    assert INSTRUCTION_MARK in text
    assert "You are the agent that writes and changes code." not in text


def test_the_shipped_manual_of_another_language_is_still_the_engines(tmp_path: Path) -> None:
    """The engine may speak a different language than the run that wrote the file."""
    workspace = tmp_path / "workspace-capo"
    workspace.mkdir()
    (workspace / INSTRUCTIONS_FILE).write_text(render_workspace("capo", "it")["AGENTS.md"], encoding="utf-8")

    prepare({"capo": workspace}, set(), "en", lambda line: None, instructions={"capo": MARKED})

    assert (workspace / INSTRUCTIONS_FILE).read_text(encoding="utf-8") == MARKED


def test_a_fresh_workspace_is_prepared_and_its_first_run_file_goes(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace-main"
    workspace.mkdir()
    (workspace / BOOTSTRAP_FILE).write_text("redefine me\n", encoding="utf-8")

    made = prepare({"main": workspace}, {"main": True}, "en", lambda line: None, instructions={"main": MARKED})

    assert made == []
    assert not (workspace / BOOTSTRAP_FILE).exists()
    assert (workspace / INSTRUCTIONS_FILE).read_text(encoding="utf-8") == MARKED


def test_the_standard_manual_is_still_written_for_an_agent_without_instructions(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace-main"

    prepare({"main": workspace}, {"main": True}, "en", lambda line: None)

    text = (workspace / INSTRUCTIONS_FILE).read_text(encoding="utf-8")
    assert "You are the main assistant of this installation." in text


def test_an_unreadable_agents_file_is_reported_not_replaced(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace-capo"
    workspace.mkdir()
    (workspace / INSTRUCTIONS_FILE).write_text("mine\n", encoding="utf-8")
    lines: list[str] = []

    def refuse(self: Path, *args, **kwargs):
        raise OSError("denied")

    monkeypatch.setattr(Path, "read_text", refuse)

    written = write_instructions(workspace / INSTRUCTIONS_FILE, MARKED, False, lines.append)

    assert written == 0
    assert any("cannot be read" in line for line in lines)


def test_the_setup_step_reads_the_squad_from_the_vault_and_writes_the_manual(tmp_path: Path) -> None:
    """The wiring, not the pieces: a squad file in the vault reaches the workspace."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from nexgen_core.openclaw.setup_steps import workspaces_step

    workspace = tmp_path / "workspace-capo"
    vault = tmp_path / "vault"
    (vault / "roles").mkdir(parents=True)
    (vault / "roles" / "capo.md").write_text("# capo\n\nOwn the code.\n", encoding="utf-8")
    (vault / "squad.yaml").write_text(
        f"agents:\n  capo:\n    workspace: '{workspace}'\n    agents_md: \"roles/capo.md\"\n",
        encoding="utf-8",
    )
    lines: list[str] = []
    setup = MagicMock()
    setup.vault_data = vault
    setup.home = tmp_path / "home"
    setup.state_dir = tmp_path / "state"
    setup.out = lines.append
    plan = SimpleNamespace(
        config={"agents": {"entries": {"capo": {"workspace": str(workspace)}}}},
        choices=SimpleNamespace(lang="en"),
    )

    workspaces_step(setup, plan, set())

    text = (workspace / INSTRUCTIONS_FILE).read_text(encoding="utf-8")
    assert INSTRUCTION_MARK in text
    assert "Own the code." in text
    assert not any("cannot be read" in line for line in lines)


def test_a_common_section_reaches_every_workspace_on_setup_and_on_sync(tmp_path: Path) -> None:
    """The proof the brief asks for: `common_md` and every agent, on both commands.

    Two agents, one with its own `agents_md` and one with nothing but the shared
    section: both manuals carry it after the setup step, and both carry the new
    text after the sync that follows an edit. The agent without a file of its own
    is the case worth pinning: it is the one a role file silently skips.
    """
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from nexgen_core.openclaw.setup_steps import workspaces_step

    vault = tmp_path / "vault"
    canon = vault / "03-INFRA" / "agent-universal-layer" / "instructions" / "AGENTS.md"
    canon.parent.mkdir(parents=True)
    canon.write_text("# canonical instructions\n", encoding="utf-8")
    squad = vault / "squad"
    squad.mkdir()
    (squad / "capo.md").write_text("# capo\n\nOwn the code.\n", encoding="utf-8")
    (squad / "common.md").write_text("## Common\n\nFirst rule.\n", encoding="utf-8")
    (vault / "squad.yaml").write_text(
        "common_md: squad/common.md\nagents:\n  capo:\n    agents_md: squad/capo.md\n  main: {}\n",
        encoding="utf-8",
    )
    home = tmp_path / "home"
    (home / ".openclaw").mkdir(parents=True)
    workspaces = {"capo": tmp_path / "workspace-capo", "main": tmp_path / "workspace-main"}
    (home / ".openclaw" / "openclaw.json").write_text(
        json.dumps({"agents": {"entries": {name: {"workspace": str(path)} for name, path in workspaces.items()}}}),
        encoding="utf-8",
    )
    setup = MagicMock()
    setup.vault_data = vault
    setup.home = home
    setup.state_dir = tmp_path / "state"
    setup.out = lambda _line: None
    plan = SimpleNamespace(
        config={"agents": {"entries": {name: {"workspace": str(path)} for name, path in workspaces.items()}}},
        choices=SimpleNamespace(lang="en"),
    )

    workspaces_step(setup, plan, set())

    for name, workspace in workspaces.items():
        text = (workspace / INSTRUCTIONS_FILE).read_text(encoding="utf-8")
        assert "First rule." in text, name
    assert "Own the code." in (workspaces["capo"] / INSTRUCTIONS_FILE).read_text(encoding="utf-8")

    (squad / "common.md").write_text("## Common\n\nSecond rule.\n", encoding="utf-8")

    GuardRunner(vault_data=vault, home=home).align_instructions()

    for name, workspace in workspaces.items():
        text = (workspace / INSTRUCTIONS_FILE).read_text(encoding="utf-8")
        assert "Second rule." in text, name
        assert "First rule." not in text, name
        assert text.startswith("<!-- nexgen:instructions:begin -->"), name


def test_the_shipped_manual_is_exactly_what_the_render_writes(tmp_path: Path) -> None:
    """A rule added to every manual must not make the engine's own file look like the person's.

    The ownership test compares a workspace against the text this engine ships. It
    has to be the same text `render_workspace` produces, or a section appended to
    every manual makes every workspace the engine wrote look hand-written, and the
    squad's instructions stop reaching it.
    """
    from nexgen_core.openclaw.templates import shipped_manuals

    for agent in ("main", "assistente", "capo", "automazioni", "sviluppo"):
        for language in ("it", "en"):
            assert render_workspace(agent, language)["AGENTS.md"] in shipped_manuals(agent), (agent, language)
