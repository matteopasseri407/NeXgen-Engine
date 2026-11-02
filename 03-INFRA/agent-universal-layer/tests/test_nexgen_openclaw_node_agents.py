"""The agent of a machine: the shorthand, the manual, and the chart line.

An agent that declares `node: "MatteoPC"` is the remote control of that PC: its
shell runs there, its manual is the machine manual, and every other agent reads
in the org chart which machine it controls. These tests pin the three parts and
the ownership rule: the engine replaces its own manual and never the person's.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import i18n  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw.node_agents import exec_node, node_agents  # noqa: E402
from nexgen_core.openclaw.setup_agents import prepare  # noqa: E402
from nexgen_core.openclaw.squad import apply_squad  # noqa: E402
from nexgen_core.openclaw.templates import render_workspace, shipped_manuals  # noqa: E402

NODE = "MatteoPC"


@pytest.fixture(autouse=True)
def _english():
    """These lines are English by construction."""
    i18n.set_language("en")
    yield
    i18n.set_language(None)


def _config() -> dict:
    """The proposal a setup builds: one agent, and whatever the squad adds."""
    return {"agents": {"entries": {"main": {"name": "main", "model": "opencode-go/deepseek-v4.1-flash"}}}}


# --------------------------------------------------- the one-line declaration


def test_a_declared_node_is_the_exec_block_openclaw_reads():
    squad = {"agents": {"windows": {"model": "anthropic/claude-opus-5-5", "node": NODE}}}

    applied = apply_squad(_config(), squad)

    entry = applied["agents"]["entries"]["windows"]
    assert entry["tools"]["exec"] == {"host": "node", "node": NODE}
    assert node_agents(applied["agents"]["entries"]) == {"windows": NODE}
    assert exec_node(entry) == NODE


def test_a_routing_the_person_wrote_wins_over_the_shorthand():
    """`node:` fills what the agent did not decide; it never overrides its exec block."""
    squad = {"agents": {"windows": {"node": NODE, "tools": {"exec": {"host": "gateway"}}}}}

    applied = apply_squad(_config(), squad)

    exec_block = applied["agents"]["entries"]["windows"]["tools"]["exec"]
    assert exec_block["host"] == "gateway"
    assert node_agents(applied["agents"]["entries"]) == {}


def test_an_agent_with_no_machine_is_absent_from_the_roster_of_machines():
    squad = {"agents": {"capo": {"model": "anthropic/claude-opus-5-5"}}}

    applied = apply_squad(_config(), squad)

    assert node_agents(applied["agents"]["entries"]) == {}


# ------------------------------------------------------------- the manual


def test_a_machine_agent_reads_the_machine_manual_naming_its_machine():
    text = render_workspace("windows", "en", NODE)["AGENTS.md"]

    assert f"the agent of the machine `{NODE}`" in text
    assert "nexgen-local sup status --json" in text
    assert "nexgen-local sup start" in text
    assert "sup attach" in text
    assert "is off or not" in text, "a machine that is off is reported, never faked"
    assert "{node}" not in text


def test_the_machine_manual_is_the_same_text_for_every_machine():
    first = render_workspace("windows", "en", NODE)["AGENTS.md"]
    second = render_workspace("laptop", "en", "matteo-HP-EliteBook")["AGENTS.md"]

    assert first.replace(NODE, "X") == second.replace("matteo-HP-EliteBook", "X")


def test_an_agent_with_no_machine_still_has_no_manual():
    with pytest.raises(ValueError):
        render_workspace("windows", "en")

    assert shipped_manuals("windows") == ()
    assert shipped_manuals("windows", NODE), "a machine manual is the engine's text"


def test_the_machine_manual_is_written_once_and_then_left_alone(tmp_path):
    """A second setup writes not one byte, and the person's own file is never replaced."""
    directory = tmp_path / "workspace-windows"
    lines: list[str] = []

    prepare({"windows": directory}, set(), "en", lines.append, nodes={"windows": NODE})
    written = (directory / "AGENTS.md").read_text(encoding="utf-8")

    assert NODE in written
    prepare({"windows": directory}, set(), "en", lines.append, nodes={"windows": NODE})
    assert (directory / "AGENTS.md").read_text(encoding="utf-8") == written

    (directory / "AGENTS.md").write_text("il mio manuale\n", encoding="utf-8")
    prepare({"windows": directory}, set(), "en", lines.append, nodes={"windows": NODE})
    kept = (directory / "AGENTS.md").read_text(encoding="utf-8")
    assert kept.startswith("il mio manuale\n"), "their manual is never replaced"
    assert "nexgen-local sup" not in kept, "the engine's manual is not appended to their text"


# ---------------------------------------------------------------- the chart


def test_a_squad_manual_keeps_its_text_and_still_gets_the_machine_contract(tmp_path):
    """The role text is the person's; the contract is the engine's, and it rides under it."""
    directory = tmp_path / "workspace-windows"
    declared = "<!-- nexgen:squad:begin -->\nSei il responsabile del PC fisso.\n"
    lines: list[str] = []

    prepare({"windows": directory}, set(), "en", lines.append,
            instructions={"windows": declared}, nodes={"windows": NODE})
    text = (directory / "AGENTS.md").read_text(encoding="utf-8")

    assert "Sei il responsabile del PC fisso." in text, "their role text is kept"
    assert "nexgen-local sup status --json" in text, "the machine contract reaches it too"
    assert f"`{NODE}` is off or not" in text, "the contract names the machine"
    prepare({"windows": directory}, set(), "en", lines.append,
            instructions={"windows": declared}, nodes={"windows": NODE})
    assert (directory / "AGENTS.md").read_text(encoding="utf-8") == text


def test_a_workspace_holding_the_squads_text_without_the_mark_is_still_ours(tmp_path):
    """An earlier engine left the role text unmarked; a byte copy of it is not the person's."""
    directory = tmp_path / "workspace-windows"
    directory.mkdir()
    role = "Sei il responsabile del PC fisso.\n"
    marked = "<!-- nexgen:squad:begin -->\n" + role
    (directory / "AGENTS.md").write_text(
        "<!-- nexgen:instructions:begin -->\npointer\n<!-- nexgen:instructions:end -->\n" + role,
        encoding="utf-8",
    )
    lines: list[str] = []

    prepare({"windows": directory}, set(), "en", lines.append,
            instructions={"windows": marked}, nodes={"windows": NODE})
    text = (directory / "AGENTS.md").read_text(encoding="utf-8")

    assert "nexgen-local sup" in text, "the contract lands on a file the old engine left unmarked"
    assert "<!-- nexgen:squad:begin" in text, "and the file carries the mark from now on"


def test_a_workspace_prepared_before_the_shared_section_is_still_ours(tmp_path):
    """The role text alone is the earlier shape of the same manual; the shared part came later."""
    directory = tmp_path / "workspace-windows"
    directory.mkdir()
    role = "Sei il responsabile del PC fisso.\n"
    shared = "## La squadra\n\nLavori in squadra.\n"
    marked = "<!-- nexgen:squad:begin -->\n" + role + "\n" + shared
    (directory / "AGENTS.md").write_text(role, encoding="utf-8")
    lines: list[str] = []

    prepare({"windows": directory}, set(), "en", lines.append, instructions={"windows": marked},
            nodes={"windows": NODE}, common=shared)
    text = (directory / "AGENTS.md").read_text(encoding="utf-8")

    assert "Lavori in squadra." in text, "the shared section reaches a workspace that predates it"
    assert "nexgen-local sup" in text, "and the machine contract lands with it"


def test_a_workspace_that_carries_a_chart_is_still_the_engines_to_update():
    """The chart is appended after the manual; it must not make the file look like the person's."""
    from nexgen_core.openclaw.org_block import OPENCLAW_ORG_BEGIN, OPENCLAW_ORG_END
    from nexgen_core.openclaw.setup_agents import next_manual_text

    manual = render_workspace("main", "en")["AGENTS.md"]
    existing = (
        "<!-- nexgen:instructions:begin -->\npointer\n<!-- nexgen:instructions:end -->\n"
        f"{manual}\n"
        f"{OPENCLAW_ORG_BEGIN}\nchart\n{OPENCLAW_ORG_END}\n"
    )

    updated = next_manual_text(existing, "nuovo testo\n", (manual,))

    assert updated is not None, "the engine's own file is updated, not treated as the person's"
    assert updated.startswith("<!-- nexgen:instructions:begin -->")
    assert "nuovo testo" in updated
    assert OPENCLAW_ORG_BEGIN in updated, "the chart is carried over"


def test_the_chart_names_the_machine_each_agent_controls():
    from nexgen_core.openclaw.hierarchy import render_org_block

    chart = render_org_block(("main", "windows"), "en", ("main", "windows"), None, {"windows": NODE})

    assert "The machine it controls: `MatteoPC`." in chart
    section = chart.split("### `windows`")[1]
    assert "The machine it controls: `MatteoPC`." in section


def test_the_chart_says_nothing_about_machines_when_no_agent_controls_one():
    from nexgen_core.openclaw.hierarchy import render_org_block

    chart = render_org_block(("main",), "en", ("main",))

    assert "The machine it controls" not in chart
