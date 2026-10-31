"""The org chart as a managed block in every workspace `AGENTS.md`.

The chart is not part of the shipped manual: it depends on the roster, so it
lives in its own delimited block that the setup and the sync both write. These
tests drive the real guard step against a fake machine and prove the block
arrives on an existing file, is not rewritten twice, follows a changed
`reportsTo`, never touches the person's text, and keeps the file's line endings.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from nexgen_core import i18n
from nexgen_core.action_notes import WARN
from nexgen_core.guard import GuardRunner
from nexgen_core.openclaw.org_block import (
    OPENCLAW_BLOCK_BEGIN,
    OPENCLAW_BLOCK_END,
    OPENCLAW_ORG_BEGIN,
    OPENCLAW_ORG_END,
)

CANON_RELATIVE = ("03-INFRA", "agent-universal-layer", "instructions", "AGENTS.md")


@pytest.fixture(autouse=True)
def _english():
    """The rendered sentences are asserted in the source language, not the machine's."""
    i18n.set_language("en")
    yield
    i18n.set_language(None)


def a_machine(tmp_path: Path, workspaces: dict[str, Path]) -> tuple[Path, Path]:
    """A fake vault with the canonical file, and a home whose config declares the agents."""
    vault = tmp_path / "vault"
    canon = vault.joinpath(*CANON_RELATIVE)
    canon.parent.mkdir(parents=True)
    canon.write_text("# canonical instructions\n", encoding="utf-8")
    home = tmp_path / "home"
    state = home / ".openclaw"
    state.mkdir(parents=True)
    entries = {name: {"workspace": str(path)} for name, path in workspaces.items()}
    (state / "openclaw.json").write_text(json.dumps({"agents": {"entries": entries}}), encoding="utf-8")
    return vault, home


def aligns(vault: Path, home: Path) -> list[str]:
    return GuardRunner(vault_data=vault, home=home).align_instructions()


def a_squad(vault: Path, text: str, agent: str = "capo", extra: str = "") -> None:
    """`squad.yaml` beside the vault root, with one agent's own instructions file."""
    (vault / "roles").mkdir(parents=True, exist_ok=True)
    (vault / "roles" / f"{agent}.md").write_text(text, encoding="utf-8")
    (vault / "squad.yaml").write_text(
        f"agents:\n  {agent}:\n    agents_md: roles/{agent}.md\n{extra}",
        encoding="utf-8",
    )


def outside_the_blocks(text: str) -> str:
    """The text with both managed blocks taken out: that part is the person's."""
    for begin, end in (
        (OPENCLAW_BLOCK_BEGIN, OPENCLAW_BLOCK_END),
        (OPENCLAW_ORG_BEGIN, OPENCLAW_ORG_END),
    ):
        start = text.find(begin)
        if start < 0:
            continue
        stop = text.find(end, start) + len(end)
        text = text[:start] + text[stop:]
    return text.strip("\r\n")


def test_a_persons_file_gets_the_chart_below_its_own_text(tmp_path):
    workspace = tmp_path / "work"
    workspace.mkdir()
    mine = "# My own rules\n\n- be brief\n"
    target = workspace / "AGENTS.md"
    target.write_text(mine, encoding="utf-8")
    vault, home = a_machine(tmp_path, {"main": workspace})

    actions = aligns(vault, home)

    written = target.read_text(encoding="utf-8")
    assert written.startswith(OPENCLAW_BLOCK_BEGIN)
    assert written.endswith(OPENCLAW_ORG_END + "\n"), "the chart is the last thing in the file"
    assert outside_the_blocks(written) == mine.strip("\n")
    assert "### `main`" in written
    assert any(str(target) in action for action in actions)


def test_a_second_cycle_writes_nothing_and_leaves_no_second_backup(tmp_path):
    workspace = tmp_path / "work"
    workspace.mkdir()
    target = workspace / "AGENTS.md"
    target.write_text("my rules\n", encoding="utf-8")
    vault, home = a_machine(tmp_path, {"main": workspace})

    aligns(vault, home)
    after_first = target.read_bytes()
    backups_after_first = sorted(workspace.glob("AGENTS.md*"))
    actions = aligns(vault, home)

    assert [action for action in actions if str(target) in action] == []
    assert target.read_bytes() == after_first
    assert sorted(workspace.glob("AGENTS.md*")) == backups_after_first


def test_a_changed_reports_to_updates_only_the_chart(tmp_path):
    workspace = tmp_path / "work"
    workspace.mkdir()
    mine = "# My own rules\n"
    target = workspace / "AGENTS.md"
    target.write_text(mine, encoding="utf-8")
    vault, home = a_machine(tmp_path, {"main": workspace, "capo": workspace, "automazioni": workspace})

    aligns(vault, home)
    before = target.read_text(encoding="utf-8")
    a_squad(vault, "", agent="automazioni", extra="    reportsTo: main\n")

    aligns(vault, home)
    after = target.read_text(encoding="utf-8")

    assert after != before
    assert outside_the_blocks(after) == outside_the_blocks(before)
    assert before[:before.find(OPENCLAW_ORG_BEGIN)] == after[:after.find(OPENCLAW_ORG_BEGIN)]
    chart = after[after.find(OPENCLAW_ORG_BEGIN):]
    assert "### `automazioni`" in chart and "Your lead: `main`." in chart
    third = aligns(vault, home)
    assert [action for action in third if str(target) in action] == []


def test_the_chart_is_written_for_a_squad_agent_with_its_own_manual(tmp_path):
    """The workspace the VPS has: its own `agents_md`, so nothing of the body is ours to change."""
    workspace = tmp_path / "workspace-capo"
    workspace.mkdir()
    vault, home = a_machine(tmp_path, {"capo": workspace})
    a_squad(vault, "# capo\n\nOwn the code.\n")

    aligns(vault, home)

    written = (workspace / "AGENTS.md").read_text(encoding="utf-8")
    assert "Own the code." in written
    assert OPENCLAW_ORG_BEGIN in written and "### `capo`" in written


def test_half_a_chart_marker_pair_is_reported_and_nothing_is_written(tmp_path):
    workspace = tmp_path / "work"
    workspace.mkdir()
    target = workspace / "AGENTS.md"
    half = f"{OPENCLAW_ORG_BEGIN}\nsomeone deleted the end marker\nmy rules\n"
    target.write_text(half, encoding="utf-8")
    vault, home = a_machine(tmp_path, {"main": workspace})

    actions = aligns(vault, home)

    assert target.read_text(encoding="utf-8") == half
    assert any(str(target) in action and action.startswith(WARN) for action in actions)


def test_a_crlf_file_keeps_its_line_endings_in_both_blocks(tmp_path):
    workspace = tmp_path / "work"
    workspace.mkdir()
    target = workspace / "AGENTS.md"
    target.write_bytes(b"line one\r\nline two\r\n")
    vault, home = a_machine(tmp_path, {"main": workspace})

    aligns(vault, home)

    written = target.read_bytes()
    assert b"line one\r\nline two\r\n" in written
    assert b"\r\r\n" not in written
    assert OPENCLAW_BLOCK_BEGIN.encode() + b"\r\n" in written
    assert OPENCLAW_ORG_BEGIN.encode() + b"\r\n" in written
    assert written.endswith(OPENCLAW_ORG_END.encode() + b"\r\n")
