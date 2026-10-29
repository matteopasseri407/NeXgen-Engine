"""Two per-agent squad fields: `tools_deny` in the config, `skills` in the workspace.

`tools_deny` lands in `agents.entries.<id>.tools.deny`; `skills` puts each named
`SKILL.md` body at `<workspace>/skills/<name>.md` with an `INDICE.md` listing
them. A missing field touches nothing. These tests use fake skill roots in
`tmp_path`: no setup, no OpenClaw, no home of anybody's.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from nexgen_core import i18n
from nexgen_core.openclaw.setup_skills import (
    INDEX_FILE,
    SKILL_MARK,
    SKILLS_DIR,
    prepare_skills,
    write_skills,
)
from nexgen_core.openclaw.squad import apply_squad, get_squad_skills


@pytest.fixture(autouse=True)
def _english():
    """The sentences asserted here are the source ones, not this machine's language."""
    i18n.set_language("en")
    yield
    i18n.set_language(None)


def _skill(root: Path, name: str, description: str, body: str) -> None:
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}",
        encoding="utf-8",
    )


# --- tools_deny ---------------------------------------------------------------


def test_tools_deny_reaches_the_agent_entry() -> None:
    config: dict = {}
    squad = {"agents": {"siti": {"name": "siti", "tools_deny": ["supabase__*", " n8n-mcp__* ", "", 3]}}}

    config = apply_squad(config, squad)

    assert config["agents"]["entries"]["siti"]["tools"]["deny"] == ["supabase__*", "n8n-mcp__*"]


def test_without_tools_deny_the_tools_are_not_touched() -> None:
    config: dict = {"agents": {"entries": {"siti": {"tools": {"alsoAllow": ["x"]}}}}}
    squad = {"agents": {"siti": {"name": "siti"}}}

    config = apply_squad(config, squad)

    tools = config["agents"]["entries"]["siti"].get("tools", {})
    assert "deny" not in tools
    assert tools.get("alsoAllow") == ["x"]


def test_tools_deny_adds_to_what_the_entry_already_denies() -> None:
    config: dict = {"agents": {"entries": {"siti": {"tools": {"deny": ["browser", "supabase__*"]}}}}}
    squad = {"agents": {"siti": {"name": "siti", "tools_deny": ["supabase__*", "vercel__*"]}}}

    config = apply_squad(config, squad)

    assert config["agents"]["entries"]["siti"]["tools"]["deny"] == ["browser", "supabase__*", "vercel__*"]


# --- skills: reading the squad -------------------------------------------------


def test_only_agents_that_declare_skills_are_listed() -> None:
    squad = {
        "agents": {
            "capo": {"skills": ["vault-save", "vault-save", " nexgen-doctor ", ""]},
            "main": {},
            "siti": {"skills": "vault-save"},
        }
    }

    assert get_squad_skills(squad) == {"capo": ["vault-save", "nexgen-doctor"]}


# --- skills: the files in the workspace ---------------------------------------


def test_a_declared_skill_becomes_a_file_and_an_index_line(tmp_path: Path) -> None:
    roots = [tmp_path / "skills-src"]
    _skill(roots[0], "vault-save", "Save one durable fact.", "# Save\n\nDo it.\n")
    workspace = tmp_path / "workspace-capo"

    written = write_skills(workspace, ["vault-save"], roots, lambda _line: None)

    assert written == 2
    target = workspace / SKILLS_DIR / "vault-save.md"
    text = target.read_text(encoding="utf-8")
    assert text.startswith(SKILL_MARK)
    assert "# Save\n\nDo it.\n" in text
    assert "description:" not in text
    index = (workspace / SKILLS_DIR / INDEX_FILE).read_text(encoding="utf-8")
    assert index.startswith(SKILL_MARK)
    assert f"- vault-save: Save one durable fact. ({target})" in index


def test_the_vault_copy_comes_before_the_engine_one(tmp_path: Path) -> None:
    vault_root, engine_root = tmp_path / "vault", tmp_path / "engine"
    _skill(vault_root, "vault-save", "From the vault.", "vault body\n")
    _skill(engine_root, "vault-save", "From the engine.", "engine body\n")
    workspace = tmp_path / "ws"

    write_skills(workspace, ["vault-save"], [vault_root, engine_root], lambda _line: None)

    assert "vault body" in (workspace / SKILLS_DIR / "vault-save.md").read_text(encoding="utf-8")


def test_a_file_the_person_wrote_stays(tmp_path: Path) -> None:
    roots = [tmp_path / "src"]
    _skill(roots[0], "vault-save", "Save.", "engine body\n")
    folder = tmp_path / "ws" / SKILLS_DIR
    folder.mkdir(parents=True)
    (folder / "vault-save.md").write_text("my own notes\n", encoding="utf-8")
    lines: list[str] = []

    write_skills(tmp_path / "ws", ["vault-save"], roots, lines.append)

    assert (folder / "vault-save.md").read_text(encoding="utf-8") == "my own notes\n"
    assert any("the engine did not write it" in line for line in lines)
    assert "- vault-save: Save." in (folder / INDEX_FILE).read_text(encoding="utf-8")


def test_an_engine_file_is_rewritten_when_the_source_changes(tmp_path: Path) -> None:
    root = tmp_path / "src"
    _skill(root, "vault-save", "Save.", "first\n")
    workspace = tmp_path / "ws"
    write_skills(workspace, ["vault-save"], [root], lambda _line: None)
    (root / "vault-save" / "SKILL.md").write_text("---\ndescription: Save.\n---\nsecond\n", encoding="utf-8")

    write_skills(workspace, ["vault-save"], [root], lambda _line: None)

    text = (workspace / SKILLS_DIR / "vault-save.md").read_text(encoding="utf-8")
    assert "second" in text and "first" not in text


def test_a_missing_or_path_like_skill_is_skipped_and_left_out_of_the_index(tmp_path: Path) -> None:
    roots = [tmp_path / "src"]
    _skill(roots[0], "vault-save", "Save.", "body\n")
    workspace = tmp_path / "ws"
    lines: list[str] = []

    write_skills(workspace, ["../evil", "nope", "vault-save"], roots, lines.append)

    folder = workspace / SKILLS_DIR
    assert sorted(path.name for path in folder.iterdir()) == [INDEX_FILE, "vault-save.md"]
    index = (folder / INDEX_FILE).read_text(encoding="utf-8")
    assert "nope" not in index and "evil" not in index
    assert any("cannot be a path" in line for line in lines)
    assert any("no SKILL.md" in line for line in lines)
    assert not (tmp_path / "evil.md").exists()


def test_an_agent_without_skills_gets_no_skills_folder(tmp_path: Path) -> None:
    root = tmp_path / "src"
    _skill(root, "vault-save", "Save.", "body\n")
    dirs = {"capo": tmp_path / "ws-capo", "main": tmp_path / "ws-main"}
    squad = {"agents": {"capo": {"skills": ["vault-save"]}, "main": {}}}

    prepare_skills(dirs, get_squad_skills(squad), [root], lambda _line: None)

    assert (dirs["capo"] / SKILLS_DIR / "vault-save.md").is_file()
    assert not (dirs["main"] / SKILLS_DIR).exists()


def test_the_setup_step_materializes_the_declared_skills(tmp_path: Path) -> None:
    """The wiring: a squad in the vault with `skills` reaches the workspace."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from nexgen_core.openclaw.setup_steps import workspaces_step

    vault = tmp_path / "vault"
    _skill(vault / "03-INFRA" / "agent-universal-layer" / "skills", "vault-save", "Save one fact.", "body\n")
    workspace = tmp_path / "workspace-capo"
    (vault / "squad.yaml").write_text(
        f"agents:\n  capo:\n    workspace: '{workspace}'\n    skills: [vault-save]\n",
        encoding="utf-8",
    )
    setup = MagicMock()
    setup.vault_data = vault
    setup.engine_root = None
    setup.home = tmp_path / "home"
    setup.state_dir = tmp_path / "state"
    setup.out = lambda _line: None
    plan = SimpleNamespace(
        config={"agents": {"entries": {"capo": {"workspace": str(workspace)}}}},
        choices=SimpleNamespace(lang="en"),
    )

    workspaces_step(setup, plan, set())

    assert (workspace / SKILLS_DIR / "vault-save.md").is_file()
    assert "- vault-save: Save one fact." in (workspace / SKILLS_DIR / INDEX_FILE).read_text(encoding="utf-8")
