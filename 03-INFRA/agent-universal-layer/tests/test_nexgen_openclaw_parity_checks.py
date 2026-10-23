"""The parity control: does OpenClaw hold what Claude Code holds, layer by layer?

The machine is built twice in these tests: once aligned, where every layer must be a PASS, and
once with one side deliberately missing a layer, where the check must name the gap and the
action that closes it. A check that only ever says PASS proves nothing, which is why the
diverging cases are the majority here.

The last test is the privacy contract: an MCP entry can carry a token in its `env` block, and
the report must never repeat it. Only names are compared.
"""
from __future__ import annotations

import json
from pathlib import Path

from conftest import REAL_UL
from nexgen_core.checks.openclaw_parity_checks import check_openclaw_parity
from nexgen_core.guard_instructions import OPENCLAW_BLOCK_BEGIN, openclaw_instruction_block
from nexgen_core.paths import claude_settings, openclaw_config
from nexgen_core.report import Severity
from nexgen_core.runtimes.openclaw import OpenClawRuntime

HOOKS = REAL_UL / "hooks"
CANON_RELATIVE = ("03-INFRA", "agent-universal-layer", "instructions", "AGENTS.md")

OPENCLAW_JSON = {"agents": {}, "mcp": {"servers": {}}}


def a_home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    (home / ".openclaw").mkdir(parents=True)
    openclaw_config(home).write_text(json.dumps(OPENCLAW_JSON), encoding="utf-8")
    return home


def a_body(tmp_path: Path) -> Path:
    body = tmp_path / "guardrail-body.mjs"
    body.write_text("// the private policy, in the test only\n", encoding="utf-8")
    return body


def a_vault(tmp_path: Path) -> Path:
    """The smallest vault that has all three manifests the parity layers read."""
    vault = tmp_path / "vault"
    ul = vault / "03-INFRA" / "agent-universal-layer"
    (ul / "instructions").mkdir(parents=True)
    (ul / "mcp").mkdir(parents=True)
    (ul / "skills").mkdir(parents=True)
    (ul / "instructions" / "AGENTS.md").write_text("# canonical\n", encoding="utf-8")
    (ul / "mcp" / "manifest.yaml").write_text(
        "schema_version: 1\n"
        "servers:\n"
        "  alpha:\n    transport: stdio\n    command: node\n    exposure: eager\n"
        "    targets: [claude, openclaw]\n"
        "  beta:\n    transport: stdio\n    command: node\n    exposure: eager\n"
        "    targets: [claude, openclaw]\n"
        "  claude-only:\n    transport: stdio\n    command: node\n    exposure: eager\n"
        "    targets: [claude]\n",
        encoding="utf-8",
    )
    (ul / "skills" / "skills.manifest.yaml").write_text(
        "schema_version: 1\n"
        "skills:\n"
        "  demo:\n    origin: vault\n    exposure: lazy\n    targets: [claude, openclaw]\n"
        "  claude-only:\n    origin: vault\n    exposure: lazy\n    targets: [claude]\n",
        encoding="utf-8",
    )
    return vault


def a_workspace(home: Path, canon: Path) -> Path:
    """The workspace as the guard leaves it: the complete managed block on top."""
    workspace = home / "work"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "AGENTS.md").write_text(
        f"{openclaw_instruction_block(canon)}\n",
        encoding="utf-8",
    )
    return workspace


def aligned_machine(tmp_path: Path) -> tuple[Path, Path]:
    """Both CLIs hold the same seven layers: the check must say PASS on every one."""
    home = a_home(tmp_path)
    vault = a_vault(tmp_path)
    workspace = a_workspace(home, vault.joinpath(*CANON_RELATIVE))
    openclaw_config(home).write_text(json.dumps({
        "plugins": {"allow": ["nexgen"], "entries": {"nexgen": {"enabled": True}}},
        "agents": {"defaults": {"workspace": str(workspace)}},
        "mcp": {"servers": {"alpha": {"command": "npx"}, "beta": {"command": "npx"}}},
    }), encoding="utf-8")

    settings = claude_settings(home)
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps({
        "permissions": {"defaultMode": "bypassPermissions"},
        "hooks": {
            "PreToolUse": [{"matcher": "Bash", "hooks": [
                {"type": "command", "command": 'node "claude-guardrail-adapter.mjs"'},
            ]}],
            "SessionStart": [{"hooks": [
                {"type": "command", "command": 'node "claude-vault-checkpoint.mjs"'},
            ]}],
        },
    }), encoding="utf-8")
    (home / ".claude.json").write_text(json.dumps({
        "mcpServers": {"alpha": {"command": "npx"}, "beta": {"command": "npx"}},
    }), encoding="utf-8")
    (home / "CLAUDE.md").write_text(
        f"Read {vault / '03-INFRA' / 'agent-universal-layer' / 'instructions' / 'AGENTS.md'}\n",
        encoding="utf-8",
    )
    (home / ".claude" / "skills" / "demo").mkdir(parents=True)
    (home / ".agents" / "skills" / "demo").mkdir(parents=True)

    runtime = OpenClawRuntime()
    runtime.install_guardrail(home, a_body(tmp_path), HOOKS)
    runtime.apply_posture(home, "bypass")
    return home, vault


def test_the_parity_check_is_silent_without_both_clis(tmp_path):
    home = a_home(tmp_path)
    vault = a_vault(tmp_path)

    assert check_openclaw_parity(home, vault) == []


def test_an_aligned_machine_is_green_on_every_layer(tmp_path):
    home, vault = aligned_machine(tmp_path)

    outcomes = check_openclaw_parity(home, vault)

    assert [o.id for o in outcomes] == [
        "openclaw.parity.mcp",
        "openclaw.parity.instructions",
        "openclaw.parity.skills",
        "openclaw.parity.guardrail",
        "openclaw.parity.briefing",
        "openclaw.parity.posture",
    ]
    assert [o.severity for o in outcomes] == [Severity.OK] * 6, [o.message for o in outcomes]


def test_a_server_only_one_cli_mounts_is_a_warning_with_the_action(tmp_path):
    home, vault = aligned_machine(tmp_path)
    openclaw_config(home).write_text(json.dumps({
        "agents": {"defaults": {"workspace": str(home / "work")}},
        "mcp": {"servers": {"alpha": {"command": "npx"}}},
    }), encoding="utf-8")

    outcomes = {o.id: o for o in check_openclaw_parity(home, vault)}

    mcp = outcomes["openclaw.parity.mcp"]
    assert mcp.severity is Severity.WARN
    assert "beta" in mcp.message and "OpenClaw" in mcp.message
    assert "nexgen sync" in (mcp.action or "")


def test_a_skill_targeted_at_open_code_is_visible_to_openclaw(tmp_path):
    """OpenClaw and OpenCode share ~/.agents/skills: the target names the reader, not a folder."""
    home, vault = aligned_machine(tmp_path)
    manifest = vault / "03-INFRA" / "agent-universal-layer" / "skills" / "skills.manifest.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8")
        + "  shared:\n    origin: vault\n    exposure: lazy\n    targets: [claude, opencode]\n",
        encoding="utf-8",
    )
    (home / ".claude" / "skills" / "shared").mkdir()
    (home / ".agents" / "skills" / "shared").mkdir()

    outcomes = {o.id: o for o in check_openclaw_parity(home, vault)}

    skills = outcomes["openclaw.parity.skills"]
    assert skills.severity is Severity.OK, skills.message
    assert "2 shared skills" in skills.message


def test_a_briefing_on_one_side_only_is_a_warning(tmp_path):
    home, vault = aligned_machine(tmp_path)
    settings = claude_settings(home)
    data = json.loads(settings.read_text(encoding="utf-8"))
    data["hooks"].pop("SessionStart")
    settings.write_text(json.dumps(data), encoding="utf-8")

    outcomes = {o.id: o for o in check_openclaw_parity(home, vault)}

    briefing = outcomes["openclaw.parity.briefing"]
    assert briefing.severity is Severity.WARN
    assert "OpenClaw" in briefing.message
    assert "claude-vault-checkpoint.mjs" in (briefing.action or "")


def test_a_posture_that_diverges_names_both_sides(tmp_path):
    home, vault = aligned_machine(tmp_path)
    OpenClawRuntime().apply_posture(home, "ask")

    outcomes = {o.id: o for o in check_openclaw_parity(home, vault)}

    posture = outcomes["openclaw.parity.posture"]
    assert posture.severity is Severity.WARN
    assert "bypass" in posture.message and "ask" in posture.message


def test_an_undeposited_plugin_is_reported_as_a_missing_guardrail(tmp_path):
    home, vault = aligned_machine(tmp_path)
    (home / ".openclaw" / "extensions" / "nexgen" / "nexgen-guardrail.config.json").unlink()

    outcomes = {o.id: o for o in check_openclaw_parity(home, vault)}

    assert outcomes["openclaw.parity.guardrail"].severity is Severity.WARN
    assert "openclaw setup" in (outcomes["openclaw.parity.guardrail"].action or "")


def test_a_plugin_the_allow_list_does_not_name_is_not_a_working_guardrail(tmp_path):
    """The sidecar alone proves nothing: OpenClaw never loads a plugin it did not allow."""
    home, vault = aligned_machine(tmp_path)
    data = json.loads(openclaw_config(home).read_text(encoding="utf-8"))
    data["plugins"]["allow"] = ["browser"]
    openclaw_config(home).write_text(json.dumps(data), encoding="utf-8")

    outcomes = {o.id: o for o in check_openclaw_parity(home, vault)}

    guardrail = outcomes["openclaw.parity.guardrail"]
    assert guardrail.severity is Severity.WARN
    assert "setup" in (guardrail.action or "")


def test_a_sidecar_without_the_job_that_reads_it_is_not_a_working_guardrail(tmp_path):
    home, vault = aligned_machine(tmp_path)
    (home / ".openclaw" / "extensions" / "nexgen" / "guardrail.mjs").unlink()

    outcomes = {o.id: o for o in check_openclaw_parity(home, vault)}

    assert outcomes["openclaw.parity.guardrail"].severity is Severity.WARN


def test_a_declared_workspace_that_does_not_exist_is_a_warning(tmp_path):
    """Skipping it silently let one aligned workspace hide an agent with no instructions."""
    home, vault = aligned_machine(tmp_path)
    data = json.loads(openclaw_config(home).read_text(encoding="utf-8"))
    data["agents"]["entries"] = {
        "main": {"workspace": str(home / "work")},
        "missing": {"workspace": str(home / "not-created")},
    }
    openclaw_config(home).write_text(json.dumps(data), encoding="utf-8")

    outcomes = {o.id: o for o in check_openclaw_parity(home, vault)}

    assert outcomes["openclaw.parity.instructions"].severity is Severity.WARN


def test_an_incomplete_instruction_block_is_a_warning(tmp_path):
    """Only the opening marker used to be enough to call the file aligned."""
    home, vault = aligned_machine(tmp_path)
    (home / "work" / "AGENTS.md").write_text(
        f"{OPENCLAW_BLOCK_BEGIN}\nmy own rules\n", encoding="utf-8",
    )

    outcomes = {o.id: o for o in check_openclaw_parity(home, vault)}

    assert outcomes["openclaw.parity.instructions"].severity is Severity.WARN


def test_the_parity_check_never_reads_an_mcp_env_block(tmp_path):
    """A token in an MCP entry must not appear in the report, which only knows names."""
    home, vault = aligned_machine(tmp_path)
    (home / ".claude.json").write_text(json.dumps({
        "mcpServers": {"alpha": {"command": "npx", "env": {"TOKEN": "super-secret-value"}}},
    }), encoding="utf-8")

    rendered = " ".join(o.message + (o.action or "") for o in check_openclaw_parity(home, vault))

    assert "super-secret-value" not in rendered
