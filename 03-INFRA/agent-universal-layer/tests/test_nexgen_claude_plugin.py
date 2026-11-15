"""The native Claude Code plugin: one generated directory, installed by Claude Code itself.

The switch is opt-in, so every test here is about one of two facts: what the generated
directory contains when the switch is on, and that the three classic writers stand down so
the same hook, server or skill is never registered twice. No test touches the real home:
the plugin renders under `tmp_path`, and `claude` is either a recording double or a stub
script, never the binary on PATH.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from conftest import FIXTURES, REAL_UL

from nexgen_core import plugin_claude
from nexgen_core.i18n import t
from nexgen_core.mcp_render.claude import render as render_claude_mcp
from nexgen_core.paths import claude_config, resolve_state_dir
from nexgen_core.renderer import McpRenderer
from nexgen_core.runtimes.claude import ClaudeRuntime
from nexgen_core.skills import SkillMaterializer

ENGINE_ROOT = REAL_UL.parent
VAULT_REL = Path("03-INFRA") / "agent-universal-layer"

#: Three synthetic servers: one stdio, one remote with a bearer token, one this engine must
#: not mount in Claude Code. `tier: core` is what makes a server mount without `enabled`.
MANIFEST = """schema_version: 1
servers:
  fake-stdio-tool:
    tier: core
    transport: stdio
    command: fake-cmd
    args: ["--flag", "value"]
    env:
      FAKE_STDIO_VAR: "http://127.0.0.1:19001"
    targets: [claude, codex]
  fake-http-api:
    tier: core
    transport: http
    url: "https://fake-api.example.invalid/mcp"
    auth: { type: bearer, env: FAKE_TOKEN }
    targets: [claude]
  fake-codex-only:
    tier: core
    transport: stdio
    command: npx
    args: ["-y", "fake-codex-only-mcp@1.0.0"]
    targets: [codex]
"""


#: "the recording double of this machine", for `Machine.apply`.
_RECORD = object()


class Machine:
    """A temporary home, vault and state folder, with the switch and a recording `claude`."""

    def __init__(self, tmp_path: Path) -> None:
        self.home = tmp_path / "home"
        self.home.mkdir()
        # The vault where every path helper looks for it: `~/KnowledgeVault`.
        self.vault = self.home / "KnowledgeVault"
        self.state = resolve_state_dir(self.home)
        self.ul = self.vault / VAULT_REL
        (self.ul / "mcp").mkdir(parents=True)
        (self.ul / "skills").mkdir()
        (self.ul / "permissions").mkdir()
        (self.ul / "sync").mkdir()
        (self.ul / "mcp" / "manifest.yaml").write_text(MANIFEST, encoding="utf-8")
        (self.ul / "skills" / "skills.manifest.yaml").write_bytes(
            (FIXTURES / "skills.manifest.yaml").read_bytes()
        )
        for skill in (FIXTURES / "skills").iterdir():
            if skill.is_dir():
                import shutil

                shutil.copytree(skill, self.ul / "skills" / skill.name)
        # One guardrail body, declared the way the private permission manifest declares it.
        (self.ul / "permissions" / "guardrail-fake.mjs").write_text("// policy\n", encoding="utf-8")
        (self.ul / "permissions" / "manifest.yaml").write_text(
            "hooks:\n  - name: fake\n    file: guardrail-fake.mjs\n", encoding="utf-8"
        )
        self.calls: list[list[str]] = []

    def switch(self, value: str = "true") -> None:
        (self.ul / "sync" / "remotes.yaml").write_text(
            f"schema_version: 1\nplugins:\n  claude:\n    enabled: {value}\n", encoding="utf-8"
        )

    def run(self, argv):
        """A recorded `claude` call, accepted unless a test says otherwise."""
        self.calls.append(list(argv))
        return _completed(0, "Validation passed")

    def apply(self, *, run=_RECORD, which=None):
        return plugin_claude.apply(
            vault_data=self.vault,
            engine_root=ENGINE_ROOT,
            home=self.home,
            state_dir=self.state,
            run=self.run if run is _RECORD else run,
            which=which or (lambda name: "/usr/bin/claude" if name == "claude" else None),
        )

    @property
    def root(self) -> Path:
        return plugin_claude.marketplace_dir(self.state)

    @property
    def plugin(self) -> Path:
        return self.root / "nexgen"

    def json(self, relative: str) -> dict:
        return json.loads((self.root / relative).read_text(encoding="utf-8"))

    def materialize_skills(self) -> None:
        SkillMaterializer(vault_data=self.vault, engine_root=ENGINE_ROOT, home=self.home).materialize(apply=True)


class _Completed:
    def __init__(self, returncode: int, output: str) -> None:
        self.returncode = returncode
        self.stdout = output
        self.stderr = ""



def _completed(returncode: int, output: str = "") -> _Completed:
    return _Completed(returncode, output)


@pytest.fixture
def machine(tmp_path) -> Machine:
    return Machine(tmp_path)


# --------------------------------------------------------------------- the switch


def test_the_plugin_is_off_until_the_private_config_asks_for_it(machine):
    """Default OFF is the whole safety story: a machine that never opted in must not change."""
    assert plugin_claude.enabled(machine.home) is False
    assert machine.apply() == []
    assert not machine.root.exists()


def test_only_a_literal_true_switches_the_plugin_on(machine):
    machine.switch("true")
    assert plugin_claude.enabled(machine.home) is True
    machine.switch('"yes"')
    assert plugin_claude.enabled(machine.home) is False


def test_an_unreadable_private_config_leaves_the_classic_writers_working(machine):
    """Off, not broken: the classic writers are the ones that keep Claude guarded."""
    (machine.ul / "sync" / "remotes.yaml").write_text("plugins: [\n", encoding="utf-8")
    assert plugin_claude.enabled(machine.home) is False


# ------------------------------------------------------------- the generated directory


def test_the_generated_directory_carries_the_hook_the_servers_and_the_skills(machine):
    machine.switch()
    machine.materialize_skills()
    machine.apply()

    catalog = machine.json(".claude-plugin/marketplace.json")
    assert catalog["name"] == "nexgen-engine"
    assert catalog["plugins"] == [
        {
            "name": "nexgen",
            "source": "./nexgen",
            "description": "Guardrail hook, MCP connectors and skills from the engine manifest.",
        }
    ]

    manifest = machine.json("nexgen/.claude-plugin/plugin.json")
    assert manifest["name"] == "nexgen"
    assert manifest["author"] == {"name": "NeXgen Engine"}

    hooks = machine.json("nexgen/hooks/hooks.json")
    assert hooks["hooks"]["PreToolUse"] == [
        {
            "matcher": "Bash|PowerShell",
            "hooks": [
                {
                    "type": "command",
                    "command": 'node "${CLAUDE_PLUGIN_ROOT}/hooks/claude-guardrail-adapter.mjs"',
                    "timeout": 10,
                }
            ],
        }
    ]
    assert (machine.plugin / "hooks" / "claude-guardrail-adapter.mjs").is_file()
    assert (machine.plugin / "hooks" / "nexgen-guardrail-core.mjs").is_file()
    assert (machine.plugin / "hooks" / "guardrail-fake.mjs").read_text(encoding="utf-8") == "// policy\n"

    servers = machine.json("nexgen/.mcp.json")["mcpServers"]
    assert sorted(servers) == ["fake-http-api", "fake-stdio-tool"]
    assert servers["fake-stdio-tool"] == {
        "type": "stdio",
        "command": "fake-cmd",
        "args": ["--flag", "value"],
        "env": {"FAKE_STDIO_VAR": "http://127.0.0.1:19001"},
    }

    assert (machine.plugin / "skills" / "fake-skill-a" / "SKILL.md").is_file()


def test_the_sidecar_names_the_body_inside_the_generated_tree(machine):
    """The adapter runs from the plugin directory, so the body it runs must not be relative."""
    machine.switch()
    machine.apply()
    sidecar = machine.json("nexgen/hooks/nexgen-guardrail.config.json")
    assert sidecar["hooks"] == [{"file": str(machine.plugin / "hooks" / "guardrail-fake.mjs"), "timeout": 5}]
    assert sidecar["auditFile"] == str(resolve_state_dir(machine.home) / "guardrail" / "claude.json")
    assert sidecar["strict"] is False


def test_a_secret_becomes_a_sensitive_option_instead_of_a_value_in_a_file(machine):
    """Claude Code stores a sensitive option in the credential store; a file never holds it."""
    machine.switch()
    machine.apply()

    options = machine.json("nexgen/.claude-plugin/plugin.json")["userConfig"]
    assert list(options) == ["FAKE_TOKEN"]
    assert options["FAKE_TOKEN"]["type"] == "string"
    assert options["FAKE_TOKEN"]["sensitive"] is True
    assert options["FAKE_TOKEN"]["required"] is True

    entries = machine.json("nexgen/.mcp.json")["mcpServers"]
    assert entries["fake-http-api"]["headers"] == {"Authorization": "Bearer ${user_config.FAKE_TOKEN}"}


def test_a_skill_that_leaves_the_manifest_leaves_the_plugin_too(machine):
    machine.switch()
    machine.materialize_skills()
    machine.apply()
    assert (machine.plugin / "skills" / "fake-skill-excluded").is_dir()

    manifest = machine.ul / "skills" / "skills.manifest.yaml"
    manifest.write_text(
        "skills:\n  fake-skill-a:\n    origin: vault\n    targets: [claude]\n", encoding="utf-8"
    )
    machine.materialize_skills()
    machine.apply()
    assert not (machine.plugin / "skills" / "fake-skill-excluded").exists()
    assert (machine.plugin / "skills" / "fake-skill-a").is_dir()


# ------------------------------------------------------------------------ installing


def test_install_asks_claude_to_validate_the_directory_then_to_install_it(machine):
    machine.switch()
    machine.apply()

    assert len(machine.calls) == 3
    exe = "/usr/bin/claude"
    root = str(machine.root)
    assert machine.calls[0] == [exe, "plugin", "validate", root, "--json"]
    assert machine.calls[1] == [exe, "plugin", "marketplace", "add", root, "--scope", "user"]
    assert machine.calls[2] == [exe, "plugin", "install", "nexgen@nexgen-engine", "--scope", "user", "--json"]

    marker = json.loads((machine.root / plugin_claude.MARKER_NAME).read_text(encoding="utf-8"))
    assert marker["plugin"] == "nexgen@nexgen-engine"
    assert len(marker["digest"]) == 64


def test_a_directory_claude_would_refuse_is_never_installed(machine):
    machine.switch()
    def refuse(argv):
        machine.calls.append(list(argv))
        return _completed(1, '{"errors":["Path not found"]}')

    actions = machine.apply(run=refuse)

    assert len(machine.calls) == 1
    assert actions[0].startswith("[WARN]")
    assert "Path not found" in actions[0]
    assert not (machine.root / plugin_claude.MARKER_NAME).exists()


def test_an_install_that_fails_is_reported_and_retried_next_cycle(machine):
    machine.switch()
    machine.apply()

    def failing_install(argv):
        machine.calls.append(list(argv))
        return _completed(1, "marketplace is unreachable") if argv[2] == "install" else _completed(0)

    # A changed manifest makes the tree move, so the installer is asked again.
    (machine.ul / "mcp" / "manifest.yaml").write_text(
        "schema_version: 1\nservers:\n  fake-http-api:\n    tier: core\n    transport: stdio\n"
        "    command: fake-cmd\n    targets: [claude]\n",
        encoding="utf-8",
    )
    actions = machine.apply(run=failing_install)

    assert "marketplace is unreachable" in actions[0]
    # The marker still names the tree the installer accepted, so the next cycle asks again.
    machine.calls.clear()
    assert "marketplace is unreachable" in machine.apply(run=failing_install)[0]
    assert machine.calls[-1][2] == "install"


def test_an_unchanged_directory_is_not_installed_again(machine):
    """The guard runs every half hour: an install per cycle would churn the plugin cache."""
    machine.switch()
    machine.apply()
    assert len(machine.calls) == 3

    machine.calls.clear()
    assert machine.apply() == []
    assert machine.calls == []


def test_a_machine_without_claude_renders_the_directory_and_says_nothing(machine):
    """The classic adapters skip an uninstalled CLI too: a warning per cycle would be noise."""
    machine.switch()
    assert machine.apply(which=lambda name: None) == []
    assert (machine.plugin / ".claude-plugin" / "plugin.json").is_file()
    assert not (machine.root / plugin_claude.MARKER_NAME).exists()


def test_the_real_seam_runs_a_claude_on_path(machine, tmp_path):
    """The injectable run is not the only path: a `claude` found on PATH is really executed."""
    machine.switch()
    log = tmp_path / "claude-calls.log"
    script = tmp_path / "fake-claude.py"
    script.write_text(
        "import sys, pathlib\n"
        f"pathlib.Path({str(log)!r}).open('a', encoding='utf-8').write(' '.join(sys.argv[1:]) + '\\n')\n",
        encoding="utf-8",
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    if os.name == "nt":
        stub = bin_dir / "claude.cmd"
        stub.write_text(f'@echo off\r\n"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
    else:
        stub = bin_dir / "claude"
        stub.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
        stub.chmod(0o755)

    machine.apply(run=None, which=lambda name: str(stub) if name == "claude" else None)

    calls = log.read_text(encoding="utf-8").splitlines()
    assert calls == [
        f"plugin validate {machine.root} --json",
        f"plugin marketplace add {machine.root} --scope user",
        "plugin install nexgen@nexgen-engine --scope user --json",
    ]


# ------------------------------------------------------ the classic writers stand down


def _claude_home(home: Path, settings: dict) -> Path:
    (home / ".claude").mkdir(parents=True, exist_ok=True)
    path = home / ".claude" / "settings.json"
    path.write_text(json.dumps(settings), encoding="utf-8")
    return path


def test_the_settings_hook_is_removed_once_the_plugin_carries_it(machine):
    """Two registrations run the guardrail twice per command: the plugin owns it now."""
    machine.switch()
    machine.apply()
    body = machine.ul / "permissions" / "guardrail-fake.mjs"
    command = f'node "{machine.home / ".claude" / "claude-guardrail-adapter.mjs"}"'
    path = _claude_home(machine.home, {
        "hooks": {"PreToolUse": [{"matcher": "Bash|PowerShell", "hooks": [{"type": "command", "command": command}]}]}
    })

    action = ClaudeRuntime().install_guardrail(machine.home, body, ENGINE_ROOT / "agent-universal-layer" / "hooks")

    assert action == t(
        "claude: guardrail hook moved into the plugin (removed from {path})",
        path=machine.home / ".claude" / "settings.json",
    )
    assert json.loads(path.read_text(encoding="utf-8"))["hooks"] == {}


def test_another_tools_hook_in_the_same_group_survives(machine):
    machine.switch()
    machine.apply()
    body = machine.ul / "permissions" / "guardrail-fake.mjs"
    command = f'node "{machine.home / ".claude" / "claude-guardrail-adapter.mjs"}"'
    path = _claude_home(machine.home, {
        "hooks": {"PreToolUse": [{"matcher": "Bash|PowerShell", "hooks": [
            {"type": "command", "command": command},
            {"type": "command", "command": "node \"/somewhere/else/audit.mjs\""},
        ]}]}
    })

    ClaudeRuntime().install_guardrail(machine.home, body, ENGINE_ROOT / "agent-universal-layer" / "hooks")

    groups = json.loads(path.read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
    assert groups == [{"matcher": "Bash|PowerShell", "hooks": [
        {"type": "command", "command": "node \"/somewhere/else/audit.mjs\""}
    ]}]


def test_the_classic_mcp_writer_stops_writing_servers_the_plugin_mounts(machine):
    machine.switch()
    machine.materialize_skills()
    machine.apply()

    live = claude_config(machine.home)
    live.write_text(json.dumps({"mcpServers": {"fake-stdio-tool": {"type": "stdio", "command": "stale"}}}),
                    encoding="utf-8")
    renderer = McpRenderer(vault_data=machine.vault, engine_root=ENGINE_ROOT, home=machine.home)
    ok, message = render_claude_mcp(renderer, write=True)

    assert ok is True
    assert message == t("Claude configuration left to the plugin")
    assert json.loads(live.read_text(encoding="utf-8"))["mcpServers"] == {}


def test_claude_skill_views_are_not_written_and_old_ones_are_taken_back(machine):
    """A link under ~/.claude/skills beside the plugin loads every skill twice."""
    SkillMaterializer(vault_data=machine.vault, engine_root=ENGINE_ROOT, home=machine.home).materialize(apply=True)
    assert (machine.home / ".claude" / "skills" / "fake-skill-a").exists()

    machine.switch()
    machine.apply()
    materializer = SkillMaterializer(vault_data=machine.vault, engine_root=ENGINE_ROOT, home=machine.home)
    _changes, actions = materializer.materialize(apply=True)

    assert list(materializer.claude_dir.iterdir()) == []
    assert t("Skill '{name}': the Claude view moved into the plugin", name="fake-skill-a") in actions


def test_a_failed_install_leaves_the_classic_engine_writing(machine):
    """The safety property: asking for the plugin can never leave Claude Code unguarded.

    The classic writers stand down on "Claude Code accepted the tree", not on the switch, so
    an install that keeps failing costs a warning and nothing else.
    """
    machine.switch()
    body = machine.ul / "permissions" / "guardrail-fake.mjs"
    command = f'node "{machine.home / ".claude" / "claude-guardrail-adapter.mjs"}"'
    path = _claude_home(machine.home, {
        "hooks": {"PreToolUse": [{"matcher": "Bash|PowerShell", "hooks": [{"type": "command", "command": command}]}]}
    })
    machine.apply(run=lambda argv: _completed(1, "marketplace is unreachable"))

    ClaudeRuntime().install_guardrail(machine.home, body, ENGINE_ROOT / "agent-universal-layer" / "hooks")
    groups = json.loads(path.read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
    assert [group["hooks"][0]["command"] for group in groups] == [command]

    renderer = McpRenderer(vault_data=machine.vault, engine_root=ENGINE_ROOT, home=machine.home)
    ok, message = render_claude_mcp(renderer, write=True)
    assert ok is True
    assert message == t("Claude configuration updated")
    written = json.loads(claude_config(machine.home).read_text(encoding="utf-8"))["mcpServers"]
    assert written["fake-stdio-tool"]["command"] == "fake-cmd"


def test_the_same_skill_view_is_written_when_the_plugin_is_off(machine):
    """The stand-down is the switch, not a silent change of behaviour for every machine."""
    materializer = SkillMaterializer(vault_data=machine.vault, engine_root=ENGINE_ROOT, home=machine.home)
    materializer.materialize(apply=True)
    assert (machine.home / ".claude" / "skills" / "fake-skill-a").exists()
