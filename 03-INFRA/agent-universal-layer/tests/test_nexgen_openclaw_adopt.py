"""`nexgen openclaw adopt`: what OpenClaw created, made canonical, and never by itself.

The machine is four temporary folders -- a home with a fake OpenClaw state, a
vault with the two manifests, an engine layer and a state directory -- and the
only external command an adoption runs goes through a recorder. The last test
is the one that matters most: a second run finds nothing, so the library and
OpenClaw cannot drift into a cycle.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import i18n  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.cli import build_parser  # noqa: E402
from nexgen_core.cli.openclaw_cmds import cmd_adopt  # noqa: E402
from nexgen_core.config import load_mcp_manifest  # noqa: E402
from nexgen_core.openclaw.adopt import plan_adoption, render_plan, run_adopt  # noqa: E402
from nexgen_core.openclaw.adopt_vetting import BLOCKED, REVIEW, SAFE  # noqa: E402
from nexgen_core.openclaw_processes import RunResult  # noqa: E402

MCP_MANIFEST = """schema_version: 1
servers:
  lazy-mcp:
    exposure: eager
    tier: core
    transport: stdio
    command: python3
    args: ["${AGENT_ENGINE_ROOT}/agent-universal-layer/mcp/lazy-mcp.py"]
    targets: [claude, codex, antigravity, opencode, openclaw]
"""


@pytest.fixture(autouse=True)
def _english():
    i18n.set_language("en")
    yield
    i18n.set_language(None)


@dataclass
class Machine:
    """The four folders, plus the two external commands a test controls."""

    home: Path
    vault: Path
    engine: Path
    state: Path
    calls: list = field(default_factory=list)
    printed: list = field(default_factory=list)

    @property
    def manifest(self) -> Path:
        return self.vault / "03-INFRA" / "agent-universal-layer" / "skills" / "skills.manifest.yaml"

    @property
    def mcp_manifest(self) -> Path:
        return self.vault / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml"

    @property
    def workshop(self) -> Path:
        return self.home / ".openclaw" / "agents" / "main" / "agent" / "workshop-skills"

    def out(self, line: str) -> None:
        self.printed.append(str(line))

    def run(self, argv: list[str]) -> RunResult:
        self.calls.append(list(argv))
        return RunResult(returncode=0, stdout="", stderr="")

    def which(self, name: str) -> str | None:
        return "openclaw" if name == "openclaw" else None

    def adopt(self, **overrides) -> int:
        options = {
            "home": self.home, "vault_data": self.vault, "engine_root": self.engine,
            "state_dir": self.state, "out": self.out, "run": self.run, "which": self.which,
        }
        options.update(overrides)
        return run_adopt(**options)


@pytest.fixture
def a_machine(tmp_path) -> Machine:
    home = tmp_path / "home"
    config_path = home / ".openclaw" / "openclaw.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps({
        "agents": {"entries": {"main": {}}},
        "mcp": {"servers": {
            # Written by the engine's renderer: it carries a manifest name.
            "lazy-mcp": {"command": "python3", "args": ["lazy-mcp.py"]},
            # Written by a person in OpenClaw's panel: the engine knows nothing of it.
            "posted-by-hand": {"command": "npx", "args": ["-y", "some-mcp"]},
        }},
    }), encoding="utf-8")
    vault = tmp_path / "vault"
    skills = vault / "03-INFRA" / "agent-universal-layer" / "skills"
    skills.mkdir(parents=True)
    (skills / "skills.manifest.yaml").write_text("schema_version: 1\nskills: {}\n", encoding="utf-8")
    (vault / "03-INFRA" / "agent-universal-layer" / "mcp").mkdir(parents=True)
    (vault / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml").write_text(
        MCP_MANIFEST, encoding="utf-8",
    )
    engine = tmp_path / "engine"
    (engine / "agent-universal-layer" / "skills").mkdir(parents=True)
    state = tmp_path / "state"
    state.mkdir()
    return Machine(home=home, vault=vault, engine=engine, state=state)


def declared_servers(machine: Machine) -> dict:
    """The MCP manifest as the engine reads it, not as the writer happens to quote it."""
    return load_mcp_manifest(machine.mcp_manifest)["servers"]


def a_learned_skill(machine: Machine, name: str = "release-notes", *, body: str | None = None) -> Path:
    folder = machine.workshop / name
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        body if body is not None else (
            f"---\nname: {name}\ndescription: writes the release notes\n---\n\n# {name}\n\nSteps.\n"
        ),
        encoding="utf-8",
    )
    return folder


# ------------------------------------------------------------------------ plan


def test_the_plan_finds_a_learned_skill_and_a_server_the_library_does_not_declare(a_machine):
    a_learned_skill(a_machine)

    plan = plan_adoption(home=a_machine.home, vault_data=a_machine.vault)

    assert [(item.kind, item.name, item.status) for item in plan.pending] == [
        ("skill", "release-notes", SAFE),
        ("mcp", "posted-by-hand", SAFE),
    ]
    assert plan.total == 2


def test_the_server_the_engine_wrote_is_not_a_candidate(a_machine):
    plan = plan_adoption(home=a_machine.home, vault_data=a_machine.vault, skills=False)

    assert [item.name for item in plan.pending] == ["posted-by-hand"]


def test_a_skill_with_a_script_is_planned_as_a_question(a_machine):
    folder = a_learned_skill(a_machine)
    (folder / "scripts").mkdir()
    (folder / "scripts" / "run.py").write_text("print('hi')\n", encoding="utf-8")

    plan = plan_adoption(home=a_machine.home, vault_data=a_machine.vault)

    skill = plan.pending[0]
    assert skill.status == REVIEW
    assert "scripts/run.py" in skill.verdict.joined()


def test_a_server_holding_a_credential_is_planned_as_refused(a_machine):
    config_path = a_machine.home / ".openclaw" / "openclaw.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["mcp"]["servers"]["with-token"] = {"command": "npx", "env": {"GITHUB_TOKEN": "0123456789abcdef" * 3}}
    config_path.write_text(json.dumps(config), encoding="utf-8")

    plan = plan_adoption(home=a_machine.home, vault_data=a_machine.vault, skills=False)

    assert [item.status for item in plan.pending] == [SAFE, BLOCKED]


def test_a_machine_where_openclaw_created_nothing_has_nothing_to_adopt(a_machine):
    (a_machine.home / ".openclaw" / "openclaw.json").unlink()

    plan = plan_adoption(home=a_machine.home, vault_data=a_machine.vault)

    assert plan.total == 0
    assert "Nothing to adopt" in render_plan(plan)


def test_the_plan_names_each_resource_and_its_reason(a_machine):
    folder = a_learned_skill(a_machine)
    (folder / "scripts").mkdir()
    (folder / "scripts" / "run.py").write_text("print('hi')\n", encoding="utf-8")

    text = render_plan(plan_adoption(home=a_machine.home, vault_data=a_machine.vault))

    assert "2 resources created in OpenClaw are not in the canonical library yet" in text
    assert "skill" in text and "release-notes" in text
    assert "needs a person first" in text
    assert "scripts/run.py" in text


# ------------------------------------------------------------------------- run


def test_a_dry_run_shows_the_plan_and_writes_nothing(a_machine):
    a_learned_skill(a_machine)
    before = a_machine.manifest.read_text(encoding="utf-8")
    mcp_before = a_machine.mcp_manifest.read_text(encoding="utf-8")

    code = a_machine.adopt(dry_run=True)

    assert code == 0
    assert "Dry run: nothing was written." in a_machine.printed
    assert a_machine.manifest.read_text(encoding="utf-8") == before
    assert a_machine.mcp_manifest.read_text(encoding="utf-8") == mcp_before
    assert a_machine.calls == []
    assert not (a_machine.home / ".agents" / "skills" / "release-notes").exists()


def test_without_a_yes_the_question_is_asked_and_a_no_adopts_nothing(a_machine):
    a_learned_skill(a_machine)
    asked: list[str] = []

    code = a_machine.adopt(confirm=lambda question: asked.append(question) or False)

    assert code == 0
    assert asked and "canonical library" in asked[0]
    assert "Nothing adopted." in a_machine.printed
    assert a_machine.calls == []
    assert not (a_machine.home / ".agents" / "skills" / "release-notes").exists()


def test_a_yes_run_adopts_both_and_archives_the_workshop_copy(a_machine):
    a_learned_skill(a_machine)

    code = a_machine.adopt(yes=True)

    assert code == 0
    manifest = a_machine.manifest.read_text(encoding="utf-8")
    assert "  release-notes:\n    origin: vault\n    openclaw_id: release-notes\n" in manifest
    servers = declared_servers(a_machine)
    assert "posted-by-hand" in servers
    assert servers["posted-by-hand"]["origin"] == "openclaw"
    assert servers["posted-by-hand"]["openclaw_id"] == "posted-by-hand"
    for target in ("claude", "codex", "antigravity", "opencode"):
        assert (a_machine.home / {"claude": ".claude", "codex": ".codex",
                                 "antigravity": ".gemini/antigravity-cli", "opencode": ".opencode"}[target]
                / "skills" / "release-notes" / "SKILL.md").is_file()
    assert (a_machine.home / ".agents" / "skills" / "release-notes" / "SKILL.md").is_file()
    assert (a_machine.home / ".agents" / "skill-library" / "release-notes" / "SKILL.md").is_file()
    assert (a_machine.vault / "03-INFRA" / "agent-universal-layer" / "skills" / "release-notes" / "SKILL.md").is_file()
    assert [call[3] for call in a_machine.calls] == ["archive"]
    assert any(line.startswith("  + skill release-notes") for line in a_machine.printed)
    assert any(line.startswith("  + mcp posted-by-hand") for line in a_machine.printed)


def test_a_second_run_finds_nothing_to_adopt(a_machine):
    a_learned_skill(a_machine)
    assert a_machine.adopt(yes=True) == 0
    a_machine.printed.clear()

    code = a_machine.adopt(yes=True)

    assert code == 0
    assert "Nothing to adopt" in a_machine.printed[0]
    assert len(a_machine.calls) == 1


def test_yes_never_adopts_what_needs_a_person(a_machine):
    folder = a_learned_skill(a_machine)
    (folder / "scripts").mkdir()
    (folder / "scripts" / "run.py").write_text("print('hi')\n", encoding="utf-8")

    code = a_machine.adopt(yes=True)

    assert code == 0
    assert "  release-notes:" not in a_machine.manifest.read_text(encoding="utf-8")
    assert any("needs a person" in line and "release-notes" in line for line in a_machine.printed)
    assert a_machine.calls == []  # nothing is archived unless it was adopted first
    assert "posted-by-hand" in declared_servers(a_machine)


def test_a_person_can_still_say_yes_to_a_skill_with_a_script(a_machine):
    folder = a_learned_skill(a_machine)
    (folder / "scripts").mkdir()
    (folder / "scripts" / "run.py").write_text("print('hi')\n", encoding="utf-8")

    def confirm(question: str) -> bool:
        return "release-notes" in question or "resources" in question

    code = a_machine.adopt(confirm=confirm)

    assert code == 0
    assert "  release-notes:\n" in a_machine.manifest.read_text(encoding="utf-8")


def test_a_blocked_resource_is_named_and_does_not_stop_the_others(a_machine):
    config_path = a_machine.home / ".openclaw" / "openclaw.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["mcp"]["servers"]["with-token"] = {"command": "npx", "env": {"GITHUB_TOKEN": "0123456789abcdef" * 3}}
    config_path.write_text(json.dumps(config), encoding="utf-8")

    code = a_machine.adopt(yes=True, skills=False)

    assert code == 0
    declared = declared_servers(a_machine)
    assert "posted-by-hand" in declared
    assert "with-token" not in declared
    assert any("cannot be adopted" in line for line in a_machine.printed)


def test_a_library_that_cannot_be_read_is_said_out_loud(a_machine):
    a_learned_skill(a_machine)
    a_machine.mcp_manifest.write_text("servers: not-a-map\n", encoding="utf-8")

    code = a_machine.adopt(yes=True)

    assert code == 0
    assert any("MCP manifest cannot be read" in line for line in a_machine.printed)
    assert "  release-notes:" in a_machine.manifest.read_text(encoding="utf-8")


def test_an_adoption_that_cannot_happen_returns_non_zero(a_machine):
    a_learned_skill(a_machine)
    taken = a_machine.vault / "03-INFRA" / "agent-universal-layer" / "skills" / "release-notes"
    taken.mkdir()
    (taken / "SKILL.md").write_text(
        "---\nname: release-notes\ndescription: the one in the Vault\n---\n", encoding="utf-8",
    )

    code = a_machine.adopt(yes=True, mcp=False)

    assert code == 1
    assert any(line.startswith("  ! skill release-notes") for line in a_machine.printed)
    assert any("already in the Vault" in line for line in a_machine.printed)
    assert a_machine.manifest.read_text(encoding="utf-8").strip() == "schema_version: 1\nskills: {}".strip()
    assert a_machine.calls == []  # nothing was archived, because nothing was adopted


# ------------------------------------------------------------------------- cli


def parsed(*argv: str):
    return build_parser().parse_args(["openclaw", "adopt", *argv])


def test_the_command_takes_the_documented_switches():
    args = parsed("--skills", "--mcp", "--dry-run", "--yes")

    assert args.openclaw_command == "adopt"
    assert (args.skills, args.mcp, args.dry_run, args.yes) == (True, True, True, True)
    assert args.func is cmd_adopt


def test_choosing_one_side_chooses_only_that_one(a_machine, monkeypatch):
    seen: dict = {}
    monkeypatch.setattr("nexgen_core.openclaw.adopt.run_adopt", lambda **kwargs: seen.update(kwargs) or 0)

    assert cmd_adopt(parsed("--mcp")) == 0

    assert seen["skills"] is False and seen["mcp"] is True
    assert seen["dry_run"] is False and seen["yes"] is False


def test_no_selector_means_both_sides(monkeypatch):
    seen: dict = {}
    monkeypatch.setattr("nexgen_core.openclaw.adopt.run_adopt", lambda **kwargs: seen.update(kwargs) or 0)

    assert cmd_adopt(parsed()) == 0

    assert seen["skills"] is True and seen["mcp"] is True
