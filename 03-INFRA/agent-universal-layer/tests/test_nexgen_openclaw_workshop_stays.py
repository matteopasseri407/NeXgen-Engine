"""What the Skill Workshop learns by itself stays inside OpenClaw.

Decided 2026-10-29: the automatic pass and the doctor carry what a person put
there -- a skill from ClawHub, a plugin's skill, a server added from the panel --
and a skill the Workshop taught itself is not waiting for adoption. The learning
is OpenClaw's own; a person who wants one in the library says so with
`nexgen openclaw adopt --skills`, and that pass still asks before writing.

Every folder here is built under `tmp_path` and every command an adoption would
run is a recorder: nothing reads a real home, a real Vault, or a real OpenClaw.
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
from nexgen_core.cli import build_parser  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.cli.openclaw_cmds import cmd_adopt  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.config import load_mcp_manifest, load_skills_manifest  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw.adopt import run_adopt  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw.adopt_auto import check_adopt_doctor, load_adopt_state  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw.adopt_scan import PLUGIN, WORKSHOP, WORKSPACE  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw.doctor_runtime import check_unadopted  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw_processes import RunResult  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.report import Severity  # noqa: E402 - sys.path shim for cloned checkout

MCP_MANIFEST = """schema_version: 1
retired_servers: []

servers:
  lazy-mcp:
    exposure: eager
    tier: core
    transport: stdio
    command: python3
    args: ["lazy-mcp.py"]
    targets: [claude, codex, opencode, openclaw]
"""


@pytest.fixture(autouse=True)
def _english():
    i18n.set_language("en")
    yield
    i18n.set_language(None)


def a_skill(folder: Path, name: str) -> Path:
    """One skill folder, shaped the way OpenClaw loads it: a name and a description."""
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: what {name} does\n---\n\n# {name}\n\nSteps.\n",
        encoding="utf-8",
    )
    return folder


@dataclass
class Machine:
    """A fake home, Vault, engine and state folder, plus the two injected commands."""

    home: Path
    vault: Path
    engine: Path
    state: Path
    calls: list = field(default_factory=list)
    printed: list = field(default_factory=list)

    @property
    def config_path(self) -> Path:
        return self.home / ".openclaw" / "openclaw.json"

    @property
    def workshop(self) -> Path:
        """Where the Workshop keeps what it learned, for the default agent."""
        return self.config_path.parent / "agents" / "main" / "agent" / "workshop-skills"

    @property
    def workspace(self) -> Path:
        """Where ClawHub installs: the agent's workspace `skills/` folder."""
        return self.config_path.parent / "workspace" / "skills"

    @property
    def skills_manifest(self) -> Path:
        return self.vault / "03-INFRA" / "agent-universal-layer" / "skills" / "skills.manifest.yaml"

    @property
    def mcp_manifest(self) -> Path:
        return self.vault / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml"

    def out(self, line: str) -> None:
        self.printed.append(str(line))

    def run(self, argv: list[str]) -> RunResult:
        self.calls.append(list(argv))
        return RunResult(returncode=0, stdout="", stderr="")

    def which(self, name: str) -> str | None:
        return "/fake/bin/openclaw" if name == "openclaw" else None

    def auto(self) -> int:
        return run_adopt(
            auto=True,
            home=self.home,
            vault_data=self.vault,
            engine_root=self.engine,
            state_dir=self.state,
            out=self.out,
            run=self.run,
            which=self.which,
        )


@pytest.fixture
def a_machine(tmp_path: Path) -> Machine:
    home = tmp_path / "home"
    config_path = home / ".openclaw" / "openclaw.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps({"agents": {"entries": {"main": {}}}}), encoding="utf-8")

    vault = tmp_path / "vault"
    skills = vault / "03-INFRA" / "agent-universal-layer" / "skills"
    skills.mkdir(parents=True)
    (skills / "skills.manifest.yaml").write_text("schema_version: 1\nskills: {}\n", encoding="utf-8")
    mcp = vault / "03-INFRA" / "agent-universal-layer" / "mcp"
    mcp.mkdir(parents=True)
    (mcp / "manifest.yaml").write_text(MCP_MANIFEST, encoding="utf-8")

    engine = tmp_path / "engine"
    (engine / "agent-universal-layer" / "skills").mkdir(parents=True)
    state = tmp_path / "state"
    state.mkdir()
    return Machine(home=home, vault=vault, engine=engine, state=state)


def declared_skills(machine: Machine) -> dict:
    return load_skills_manifest(machine.skills_manifest).get("skills") or {}


def a_hand_added_server(machine: Machine) -> None:
    machine.config_path.write_text(json.dumps({
        "agents": {"entries": {"main": {}}},
        "mcp": {"servers": {"posted-by-hand": {"command": "npx", "args": ["-y", "some-mcp"]}}},
    }), encoding="utf-8")


# ----------------------------------------------------------- the automatic pass


def test_the_automatic_pass_leaves_a_workshop_skill_where_it_is(a_machine: Machine):
    """The Workshop learns on its own, and an unattended pass is not a person asking."""
    a_skill(a_machine.workshop / "learned", "learned")

    code = a_machine.auto()

    assert code == 0
    assert "learned" not in declared_skills(a_machine)
    assert not (a_machine.vault / "03-INFRA" / "agent-universal-layer" / "skills" / "learned").exists()
    state = load_adopt_state(a_machine.state)
    assert state.get("last_pass", {}).get("entered") == []
    assert state.get("last_pass", {}).get("pending_review") == []
    assert not any("learned" in line for line in a_machine.printed)
    assert a_machine.calls == []
    # The copy is still OpenClaw's, untouched: nothing archived it, nothing copied it.
    assert (a_machine.workshop / "learned" / "SKILL.md").is_file()


def test_the_automatic_pass_still_adopts_a_skill_clawhub_installed(a_machine: Machine):
    """A copy a person installed is still a candidate, and it is not archived."""
    folder = a_skill(a_machine.workspace / "from-clawhub", "from-clawhub")

    code = a_machine.auto()

    assert code == 0
    assert "from-clawhub" in declared_skills(a_machine)
    assert "skill:from-clawhub" in load_adopt_state(a_machine.state).get("last_pass", {}).get("entered", [])
    assert a_machine.calls == []
    assert (folder / "SKILL.md").is_file()


def test_the_automatic_pass_still_adopts_a_server(a_machine: Machine):
    a_hand_added_server(a_machine)

    code = a_machine.auto()

    assert code == 0
    servers = load_mcp_manifest(a_machine.mcp_manifest).get("servers") or {}
    assert servers["posted-by-hand"]["origin"] == "openclaw"
    assert "mcp:posted-by-hand" in load_adopt_state(a_machine.state).get("last_pass", {}).get("entered", [])


def test_the_workshop_is_not_one_of_the_sources_the_automatic_lane_carries():
    """One constant, so the pass and the doctor cannot disagree about what waits."""
    from nexgen_core.openclaw.adopt_scan import AUTOMATIC_SOURCES

    assert set(AUTOMATIC_SOURCES) == {WORKSPACE, PLUGIN}
    assert WORKSHOP not in AUTOMATIC_SOURCES


# ------------------------------------------------------------------- the doctor


def test_a_workshop_skill_alone_is_not_counted_as_waiting(a_machine: Machine):
    """The doctor asks a person to decide about what the pass carries, and this is not."""
    a_skill(a_machine.workshop / "learned", "learned")

    outcome = check_unadopted(
        {"agents": {"entries": {"main": {}}}},
        a_machine.config_path,
        a_machine.home,
        a_machine.vault,
    )

    assert outcome is not None
    assert outcome.severity is Severity.OK
    assert outcome.action is None


def test_a_hand_added_server_is_still_counted_beside_a_workshop_skill(a_machine: Machine):
    a_skill(a_machine.workshop / "learned", "learned")
    a_hand_added_server(a_machine)

    outcome = check_unadopted(
        json.loads(a_machine.config_path.read_text(encoding="utf-8")),
        a_machine.config_path,
        a_machine.home,
        a_machine.vault,
    )

    assert outcome is not None and outcome.severity is Severity.WARN
    assert "1 resources created in OpenClaw are not adopted yet" in outcome.message


def test_a_workshop_entry_already_saved_in_review_is_ignored(a_machine: Machine):
    """An older state file can name one: reading it must warn about nothing and crash never."""
    (a_machine.state / "openclaw-adopt-state.json").write_text(json.dumps({
        "consecutive_absent": {},
        "last_pass": {
            "timestamp": "2026-10-28T10:00:00+00:00",
            "entered": [],
            "exited": [],
            "pending_review": [{
                "kind": "skill", "name": "learned", "status": "review",
                "reasons": ["holds files a person should read first: run.sh"],
            }],
        },
    }), encoding="utf-8")
    a_skill(a_machine.workshop / "learned", "learned")

    outcome = check_adopt_doctor(a_machine.state)

    assert outcome.severity is Severity.OK
    assert "0 waiting for review" in outcome.message
    assert outcome.action is None

    # The next pass rewrites the list from what it still carries: the entry goes.
    assert a_machine.auto() == 0
    assert load_adopt_state(a_machine.state).get("last_pass", {}).get("pending_review") == []


# ------------------------------------------------------- the person's own door


def a_cli_machine(a_machine: Machine, monkeypatch) -> None:
    """The parser takes no --home: the environment is what the command reads."""
    monkeypatch.setenv("NEXGEN_HOME", str(a_machine.home))
    monkeypatch.setenv("AGENT_VAULT_DATA", str(a_machine.vault))
    monkeypatch.setenv("AGENT_ENGINE_ROOT", str(a_machine.engine))
    monkeypatch.setenv("AGENT_STATE_DIR", str(a_machine.state))


def test_naming_the_skills_still_offers_the_one_the_workshop_learned(a_machine: Machine, monkeypatch, capsys):
    a_cli_machine(a_machine, monkeypatch)
    a_skill(a_machine.workshop / "learned", "learned")

    code = cmd_adopt(build_parser().parse_args(["openclaw", "adopt", "--skills", "--dry-run"]))

    assert code == 0
    shown = capsys.readouterr().out
    assert "learned" in shown
    assert "safe to adopt" in shown


def test_the_plain_command_says_where_the_learned_skills_stay(a_machine: Machine, monkeypatch, capsys):
    """Without --skills the plan is honest about what it left out, and adopts nothing."""
    a_cli_machine(a_machine, monkeypatch)
    a_skill(a_machine.workshop / "release-notes", "release-notes")

    code = cmd_adopt(build_parser().parse_args(["openclaw", "adopt", "--dry-run"]))

    assert code == 0
    shown = capsys.readouterr().out
    assert "release-notes" not in shown
    assert "stay inside OpenClaw" in shown
    assert "nexgen openclaw adopt --skills" in shown
