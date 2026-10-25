"""Tests for `nexgen openclaw adopt --auto`: SAFE admission, review tracking, two-pass exit.

Verifies:
1. SAFE candidates enter automatically without prompting and carry OpenClaw provenance.
2. REVIEW candidates are held out of the manifest and stored in local machine review state.
3. Absence after one pass leaves the resource intact and increments the absence counter.
4. Absence after two consecutive passes retires the resource from the canonical library.
5. Resources without OpenClaw provenance belong to the canonical library and are never removed.
6. Unreadable OpenClaw configuration or unreachable gateway RPC removes nothing and modifies no counter.
7. Doctor check summarizes the last pass and warns when items await human review.
"""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from nexgen_core import i18n
from nexgen_core.cli import build_parser
from nexgen_core.cli.openclaw_cmds import cmd_adopt
from nexgen_core.config import load_mcp_manifest, load_skills_manifest
from nexgen_core.openclaw.adopt import run_adopt
from nexgen_core.openclaw.adopt_auto import check_adopt_doctor, load_adopt_state
from nexgen_core.openclaw.adopt_retirement import retire_adopted_server
from nexgen_core.openclaw_processes import RunResult
from nexgen_core.report import Severity

MCP_MANIFEST_INITIAL = """schema_version: 1
retired_servers: []

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
    home: Path
    vault: Path
    engine: Path
    state: Path
    calls: list = field(default_factory=list)
    printed: list = field(default_factory=list)

    @property
    def skills_manifest(self) -> Path:
        return self.vault / "03-INFRA" / "agent-universal-layer" / "skills" / "skills.manifest.yaml"

    @property
    def mcp_manifest(self) -> Path:
        return self.vault / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml"

    @property
    def config_path(self) -> Path:
        return self.home / ".openclaw" / "openclaw.json"

    def out(self, line: str) -> None:
        self.printed.append(str(line))

    def run(self, argv: list[str]) -> RunResult:
        self.calls.append(list(argv))
        return RunResult(returncode=0, stdout="", stderr="")

    def which(self, name: str) -> str | None:
        return "openclaw" if name == "openclaw" else None

    def auto_adopt(self, **overrides) -> int:
        options = {
            "home": self.home,
            "vault_data": self.vault,
            "engine_root": self.engine,
            "state_dir": self.state,
            "out": self.out,
            "run": self.run,
            "which": self.which,
            "auto": True,
        }
        options.update(overrides)
        return run_adopt(**options)


@pytest.fixture
def a_machine(tmp_path: Path) -> Machine:
    home = tmp_path / "home"
    config_path = home / ".openclaw" / "openclaw.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps({
        "agents": {"entries": {"main": {}}},
        "mcp": {"servers": {
            "lazy-mcp": {"command": "python3", "args": ["lazy-mcp.py"]},
            "safe-server": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-memory"]},
        }},
    }), encoding="utf-8")

    vault = tmp_path / "vault"
    skills = vault / "03-INFRA" / "agent-universal-layer" / "skills"
    skills.mkdir(parents=True)
    (skills / "skills.manifest.yaml").write_text("schema_version: 1\nskills: {}\n", encoding="utf-8")

    mcp_dir = vault / "03-INFRA" / "agent-universal-layer" / "mcp"
    mcp_dir.mkdir(parents=True)
    (mcp_dir / "manifest.yaml").write_text(MCP_MANIFEST_INITIAL, encoding="utf-8")

    engine = tmp_path / "engine"
    (engine / "agent-universal-layer" / "skills").mkdir(parents=True)
    state = tmp_path / "state"
    state.mkdir()
    return Machine(home=home, vault=vault, engine=engine, state=state)


def test_auto_adopt_admits_safe_candidate_with_openclaw_provenance(a_machine: Machine):
    code = a_machine.auto_adopt()
    assert code == 0

    mcp_data = load_mcp_manifest(a_machine.mcp_manifest)
    servers = mcp_data.get("servers") or {}
    assert "safe-server" in servers
    assert servers["safe-server"]["origin"] == "openclaw"
    assert servers["safe-server"]["openclaw_id"] == "safe-server"

    state = load_adopt_state(a_machine.state)
    assert "mcp:safe-server" in state.get("last_pass", {}).get("entered", [])


def test_auto_adopt_leaves_review_candidates_and_saves_to_review_list(a_machine: Machine):
    workshop_dir = a_machine.home / ".openclaw" / "agents" / "main" / "agent" / "workshop-skills" / "risky-skill"
    workshop_dir.mkdir(parents=True)
    (workshop_dir / "SKILL.md").write_text(
        "---\nname: risky-skill\ndescription: A risky helper\n---\nRun instructions.",
        encoding="utf-8",
    )
    # Executable script triggers REVIEW verdict
    (workshop_dir / "helper.sh").write_text("#!/bin/sh\necho hi\n", encoding="utf-8")

    code = a_machine.auto_adopt()
    assert code == 0

    skills_data = load_skills_manifest(a_machine.skills_manifest)
    assert "risky-skill" not in (skills_data.get("skills") or {})

    state = load_adopt_state(a_machine.state)
    pending = state.get("last_pass", {}).get("pending_review", [])
    assert any(item["name"] == "risky-skill" and item["kind"] == "skill" for item in pending)


def test_a_blocked_candidate_is_named_and_never_written(a_machine: Machine):
    """A live server carrying a literal credential: no adoption, no silence.

    `vet_server` blocks it, and a pass that runs unattended must say so -- an item
    the engine will not write is exactly what a person has to see.
    """
    a_machine.config_path.write_text(json.dumps({
        "agents": {"entries": {"main": {}}},
        "mcp": {"servers": {
            "lazy-mcp": {"command": "python3"},
            "token-server": {"command": "npx", "env": {"API_KEY": "abcd1234efgh5678ijkl9012"}},
        }},
    }), encoding="utf-8")

    code = a_machine.auto_adopt()

    assert code == 0
    manifest_text = a_machine.mcp_manifest.read_text(encoding="utf-8")
    assert "token-server" not in manifest_text
    assert "abcd1234efgh5678ijkl9012" not in manifest_text
    pending = load_adopt_state(a_machine.state).get("last_pass", {}).get("pending_review", [])
    blocked = [item for item in pending if item["name"] == "token-server"]
    assert blocked and blocked[0]["status"] == "blocked"
    assert any("token-server" in line and "cannot be adopted" in line for line in a_machine.printed)


def test_a_blocked_skill_is_named_and_never_written(a_machine: Machine):
    skill_dir = a_machine.home / ".openclaw" / "agents" / "main" / "agent" / "workshop-skills" / "no-frontmatter"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("Just prose, no frontmatter at all.", encoding="utf-8")

    code = a_machine.auto_adopt()

    assert code == 0
    assert "no-frontmatter" not in a_machine.skills_manifest.read_text(encoding="utf-8")
    pending = load_adopt_state(a_machine.state).get("last_pass", {}).get("pending_review", [])
    blocked = [item for item in pending if item["name"] == "no-frontmatter"]
    assert blocked and blocked[0]["status"] == "blocked"
    assert any("no-frontmatter" in line and "cannot be adopted" in line for line in a_machine.printed)


def test_an_adopted_skill_is_never_retired_by_absence(a_machine: Machine):
    """Adoption archives the Workshop copy, so absence is what adoption itself creates.

    Round one counted that absence and retired the skill on the second pass: the vault
    copy deleted and the manifest entry gone, one hour after it had been adopted.
    """
    safe_skill = a_machine.home / ".openclaw" / "agents" / "main" / "agent" / "workshop-skills" / "release-notes"
    safe_skill.mkdir(parents=True)
    (safe_skill / "SKILL.md").write_text(
        "---\nname: release-notes\ndescription: Writes the release notes\n---\nBody.",
        encoding="utf-8",
    )
    a_machine.auto_adopt()
    manifest = a_machine.skills_manifest.read_text(encoding="utf-8")
    assert "  release-notes:" in manifest

    # What `openclaw skills workshop archive` does to the adopted copy.
    shutil.rmtree(safe_skill)

    a_machine.auto_adopt()
    a_machine.auto_adopt()

    assert "  release-notes:" in a_machine.skills_manifest.read_text(encoding="utf-8")
    assert (a_machine.vault / "03-INFRA" / "agent-universal-layer" / "skills" / "release-notes").is_dir()


def test_first_pass_absence_increments_counter_and_preserves_resource(a_machine: Machine):
    # Pass 1: Adopts safe-server
    a_machine.auto_adopt()

    # User removes safe-server from openclaw.json
    a_machine.config_path.write_text(json.dumps({
        "agents": {"entries": {"main": {}}},
        "mcp": {"servers": {"lazy-mcp": {"command": "python3"}}},
    }), encoding="utf-8")

    # Pass 2: Missing for the first time
    code = a_machine.auto_adopt()
    assert code == 0

    # Must still be in manifest!
    mcp_data = load_mcp_manifest(a_machine.mcp_manifest)
    assert "safe-server" in (mcp_data.get("servers") or {})

    state = load_adopt_state(a_machine.state)
    assert state.get("consecutive_absent", {}).get("mcp:safe-server") == 1
    assert "mcp:safe-server" not in state.get("last_pass", {}).get("exited", [])


def test_second_consecutive_absence_retires_openclaw_resource(a_machine: Machine):
    # Pass 1: Adopts safe-server
    a_machine.auto_adopt()

    # User removes safe-server from openclaw.json
    a_machine.config_path.write_text(json.dumps({
        "agents": {"entries": {"main": {}}},
        "mcp": {"servers": {"lazy-mcp": {"command": "python3"}}},
    }), encoding="utf-8")

    # Pass 2: Absent 1/2
    a_machine.auto_adopt()
    # Pass 3: Absent 2/2 -> Retired!
    code = a_machine.auto_adopt()
    assert code == 0

    mcp_data = load_mcp_manifest(a_machine.mcp_manifest)
    assert "safe-server" not in (mcp_data.get("servers") or {})
    assert "safe-server" in (mcp_data.get("retired_servers") or [])

    state = load_adopt_state(a_machine.state)
    assert "mcp:safe-server" not in state.get("consecutive_absent", {})
    assert "mcp:safe-server" in state.get("last_pass", {}).get("exited", [])


def test_retirement_deletes_the_adopted_row_itself_and_leaves_the_library_alone(a_machine: Machine):
    """The row goes, not just its name: the writer quotes the key it wrote.

    `_emit_manifest_stub` writes `"safe-server":`, so a remover that only looks for
    an unquoted `safe-server:` finds nothing, lists the name under `retired_servers`
    and leaves a dead row in the document for good.
    """
    a_machine.auto_adopt()
    adopted = a_machine.mcp_manifest.read_text(encoding="utf-8")
    assert '"safe-server":' in adopted

    a_machine.config_path.write_text(json.dumps({
        "agents": {"entries": {"main": {}}},
        "mcp": {"servers": {"lazy-mcp": {"command": "python3"}}},
    }), encoding="utf-8")
    a_machine.auto_adopt()
    a_machine.auto_adopt()

    after = a_machine.mcp_manifest.read_text(encoding="utf-8")
    assert '"safe-server":' not in after
    assert "- safe-server" in after
    assert "lazy-mcp:" in after and "lazy-mcp.py" in after


def test_a_manifest_without_retired_servers_gains_the_list_without_losing_its_header(a_machine: Machine):
    """An older manifest has no `retired_servers:` key: it is appended, not prepended."""
    a_machine.mcp_manifest.write_text(
        "# a header a person reads first\nschema_version: 1\n\n"
        "servers:\n"
        "  lazy-mcp:\n"
        "    transport: stdio\n"
        "    command: python3\n"
        '  "safe-server":\n'
        "    origin: openclaw\n"
        "    openclaw_id: safe-server\n"
        "    transport: stdio\n"
        "    command: npx\n",
        encoding="utf-8",
    )

    retired, note = retire_adopted_server("safe-server", vault_data=a_machine.vault)

    assert retired, note
    after = a_machine.mcp_manifest.read_text(encoding="utf-8")
    assert after.startswith("# a header a person reads first")
    assert '"safe-server":' not in after
    assert after.endswith("retired_servers:\n  - safe-server\n")
    data = load_mcp_manifest(a_machine.mcp_manifest)
    assert "lazy-mcp" in (data.get("servers") or {})
    assert "safe-server" in (data.get("retired_servers") or [])


def test_reappearance_resets_absence_counter(a_machine: Machine):
    a_machine.auto_adopt()

    # Absent in pass 1
    a_machine.config_path.write_text(json.dumps({
        "agents": {"entries": {"main": {}}},
        "mcp": {"servers": {"lazy-mcp": {"command": "python3"}}},
    }), encoding="utf-8")
    a_machine.auto_adopt()
    state = load_adopt_state(a_machine.state)
    assert state.get("consecutive_absent", {}).get("mcp:safe-server") == 1

    # Reappears before pass 2
    a_machine.config_path.write_text(json.dumps({
        "agents": {"entries": {"main": {}}},
        "mcp": {"servers": {
            "lazy-mcp": {"command": "python3"},
            "safe-server": {"command": "npx", "args": ["-y", "mcp-server"]},
        }},
    }), encoding="utf-8")
    a_machine.auto_adopt()
    state = load_adopt_state(a_machine.state)
    assert "mcp:safe-server" not in state.get("consecutive_absent", {})


def test_a_retirement_that_cannot_be_written_is_not_reported_as_an_exit(a_machine: Machine, monkeypatch):
    """The pass must not say 'retired' when the manifest refused the write."""
    a_machine.auto_adopt()
    a_machine.config_path.write_text(json.dumps({
        "agents": {"entries": {"main": {}}},
        "mcp": {"servers": {"lazy-mcp": {"command": "python3"}}},
    }), encoding="utf-8")
    a_machine.auto_adopt()
    monkeypatch.setattr(
        "nexgen_core.openclaw.adopt_auto.retire_adopted_server",
        lambda name, **kwargs: (False, "the manifest is read-only"),
    )

    a_machine.auto_adopt()

    state = load_adopt_state(a_machine.state)
    assert "mcp:safe-server" not in state.get("last_pass", {}).get("exited", [])
    assert state.get("consecutive_absent", {}).get("mcp:safe-server") == 2
    assert any("could not be retired" in line and "read-only" in line for line in a_machine.printed)
    assert "safe-server" in (load_mcp_manifest(a_machine.mcp_manifest).get("servers") or {})


def test_canonical_resource_without_openclaw_origin_is_never_removed(a_machine: Machine):
    # Add a canonical library server (origin: vault / omitted)
    manifest_text = a_machine.mcp_manifest.read_text(encoding="utf-8")
    a_machine.mcp_manifest.write_text(manifest_text + """
  canonical-srv:
    transport: stdio
    command: python3
    args: ["srv.py"]
    targets: [claude, codex]
""", encoding="utf-8")

    # Run auto-adopt multiple times with openclaw lacking canonical-srv
    a_machine.auto_adopt()
    a_machine.auto_adopt()
    a_machine.auto_adopt()

    mcp_data = load_mcp_manifest(a_machine.mcp_manifest)
    assert "canonical-srv" in (mcp_data.get("servers") or {})
    state = load_adopt_state(a_machine.state)
    assert "mcp:canonical-srv" not in state.get("consecutive_absent", {})


def test_unreadable_openclaw_config_removes_nothing_and_touches_no_counter(a_machine: Machine):
    a_machine.auto_adopt()
    # Mark absence counter at 1 manually
    state_file = a_machine.state / "openclaw-adopt-state.json"
    state_file.write_text(json.dumps({"consecutive_absent": {"mcp:safe-server": 1}}), encoding="utf-8")

    # Corrupt openclaw.json
    a_machine.config_path.write_text("{ broken json content", encoding="utf-8")

    code = a_machine.auto_adopt()
    assert code == 0

    # Server must still be present, and counter untouched at 1
    mcp_data = load_mcp_manifest(a_machine.mcp_manifest)
    assert "safe-server" in (mcp_data.get("servers") or {})
    state = load_adopt_state(a_machine.state)
    assert state.get("consecutive_absent", {}).get("mcp:safe-server") == 1


def test_doctor_adoption_check_reflects_last_pass_and_warns_on_review(a_machine: Machine):
    # Before pass
    outcome = check_adopt_doctor(a_machine.state)
    assert outcome.id == "openclaw.adoption"
    assert outcome.severity == Severity.OK
    assert "No auto-adoption pass recorded yet" in outcome.message

    # After clean pass
    a_machine.auto_adopt()
    outcome = check_adopt_doctor(a_machine.state)
    assert outcome.severity == Severity.OK
    assert "1 entered, 0 exited, 0 waiting for review" in outcome.message

    # When item is pending review
    workshop_dir = a_machine.home / ".openclaw" / "agents" / "main" / "agent" / "workshop-skills" / "review-skill"
    workshop_dir.mkdir(parents=True)
    (workshop_dir / "SKILL.md").write_text("---\nname: review-skill\ndescription: Needs human check\n---\nBody", encoding="utf-8")
    (workshop_dir / "run.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    a_machine.auto_adopt()

    outcome = check_adopt_doctor(a_machine.state)
    assert outcome.severity == Severity.WARN
    assert "waiting for review" in outcome.message
    assert outcome.action is not None


def test_cli_adopt_auto_json_output(a_machine: Machine, capsys, monkeypatch):
    # The parser carries no --home: the command reads the environment, so the fixture
    # has to be the environment. Without this the pass runs against the real home and
    # the real Vault of whoever runs the suite.
    monkeypatch.setenv("NEXGEN_HOME", str(a_machine.home))
    monkeypatch.setenv("AGENT_VAULT_DATA", str(a_machine.vault))
    monkeypatch.setenv("AGENT_ENGINE_ROOT", str(a_machine.engine))
    monkeypatch.setenv("AGENT_STATE_DIR", str(a_machine.state))

    parser = build_parser()
    args = parser.parse_args(["openclaw", "adopt", "--auto", "--json"])
    cmd_adopt(args)
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["ok"] is True
    assert "entered" in payload
    assert "exited" in payload
    assert "pending_review" in payload
    assert "lines" in payload
    # It acted on the fixture, not on the machine running the suite.
    assert "safe-server" in (load_mcp_manifest(a_machine.mcp_manifest).get("servers") or {})
    assert (a_machine.state / "openclaw-adopt-state.json").is_file()
