"""An adoption that added a server renders the CLIs at once, not at the next tick.

The pass writes a manifest row; until something renders it, Claude Code, Codex,
OpenCode and `agy` still hold yesterday's file. These tests pin the four
contracts of that render: it runs once after a real server adoption, it never
runs when nothing was adopted, `--dry-run` still writes nothing, and a render
that fails is reported without taking the adoption back.

The render itself is faked when the point is who calls it; one test drives the
real renderer to prove the lazy rule still holds: behind `lazy-mcp` for the
gateway CLIs, and directly in the file `agy` shares with OpenClaw.
"""
from __future__ import annotations

import json
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import i18n  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.config import load_mcp_manifest  # noqa: E402
from nexgen_core.openclaw.adopt import run_adopt  # noqa: E402
from nexgen_core.openclaw.adopt_render import render_adopted_servers  # noqa: E402
from nexgen_core.paths import antigravity_config, opencode_config_path  # noqa: E402

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
    """A home, a Vault, an engine root and a state folder, all temporary."""

    home: Path
    vault: Path
    engine: Path
    state: Path
    printed: list = field(default_factory=list)

    @property
    def config_path(self) -> Path:
        return self.home / ".openclaw" / "openclaw.json"

    @property
    def mcp_manifest(self) -> Path:
        return self.vault / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml"

    def out(self, line: str) -> None:
        self.printed.append(str(line))

    def adopt(self, **overrides) -> int:
        options = {
            "home": self.home, "vault_data": self.vault, "engine_root": self.engine,
            "state_dir": self.state, "out": self.out, "skills": False,
        }
        options.update(overrides)
        return run_adopt(**options)


def write_config(machine: Machine, servers: dict, *, local_agy: bool = True) -> None:
    """OpenClaw's file: only the servers given, plus the local `agy` backend when asked."""
    config: dict = {"agents": {"entries": {"main": {}}}, "mcp": {"servers": servers}}
    if local_agy:
        config["plugins"] = {"allow": ["google-antigravity-cli"]}
    machine.config_path.parent.mkdir(parents=True, exist_ok=True)
    machine.config_path.write_text(json.dumps(config), encoding="utf-8")


@pytest.fixture
def a_machine(tmp_path) -> Machine:
    home = tmp_path / "home"
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
    machine = Machine(home=home, vault=vault, engine=engine, state=state)
    # Written by a person in OpenClaw's panel: the engine knows nothing of it yet.
    write_config(machine, {"posted-by-hand": {"command": "npx", "args": ["-y", "some-mcp"]}})
    return machine


@pytest.fixture
def a_fake_render(monkeypatch) -> list[dict]:
    """Records each call and answers success, so the test can judge the caller."""
    calls: list[dict] = []

    def fake(*, home, vault_data=None, engine_root=None):
        calls.append({"home": home, "vault_data": vault_data, "engine_root": engine_root})
        return True, "rendered for this test"

    monkeypatch.setattr("nexgen_core.openclaw.adopt_render.render_adopted_servers", fake)
    return calls


def declared_servers(machine: Machine) -> dict:
    return load_mcp_manifest(machine.mcp_manifest)["servers"]


# ------------------------------------------------------------- who calls the render


def test_an_adoption_that_added_a_server_renders_once_with_the_same_places(a_machine, a_fake_render):
    code = a_machine.adopt(yes=True)

    assert code == 0
    assert len(a_fake_render) == 1
    assert a_fake_render[0] == {
        "home": a_machine.home, "vault_data": a_machine.vault, "engine_root": a_machine.engine,
    }
    assert "posted-by-hand" in declared_servers(a_machine)
    assert "rendered for this test" in a_machine.printed


def test_a_pass_with_nothing_to_adopt_does_not_render(a_machine, a_fake_render):
    write_config(a_machine, {"lazy-mcp": {"command": "python3", "args": ["lazy-mcp.py"]}})

    code = a_machine.adopt(yes=True)

    assert code == 0
    assert "Nothing to adopt" in a_machine.printed[0]
    assert a_fake_render == []


def test_a_dry_run_shows_the_plan_and_renders_nothing(a_machine, a_fake_render):
    before = a_machine.mcp_manifest.read_text(encoding="utf-8")

    code = a_machine.adopt(dry_run=True)

    assert code == 0
    assert "Dry run: nothing was written." in a_machine.printed
    assert a_fake_render == []
    assert a_machine.mcp_manifest.read_text(encoding="utf-8") == before


def test_an_automatic_pass_renders_after_it_adopts_a_server(a_machine, a_fake_render):
    code = a_machine.adopt(auto=True)

    assert code == 0
    assert len(a_fake_render) == 1
    assert "posted-by-hand" in declared_servers(a_machine)
    assert "rendered for this test" in a_machine.printed


def test_an_automatic_dry_run_renders_nothing(a_machine, a_fake_render):
    before = a_machine.mcp_manifest.read_text(encoding="utf-8")

    code = a_machine.adopt(auto=True, dry_run=True)

    assert code == 0
    assert a_fake_render == []
    assert a_machine.mcp_manifest.read_text(encoding="utf-8") == before


def test_a_render_that_fails_is_said_and_leaves_the_adoption_written(a_machine, monkeypatch):
    monkeypatch.setattr(
        "nexgen_core.openclaw.adopt_render.render_adopted_servers",
        lambda **kwargs: (False, "the CLIs could not be written"),
    )

    code = a_machine.adopt(yes=True)

    assert code == 0
    assert "posted-by-hand" in declared_servers(a_machine)
    assert "the CLIs could not be written" in a_machine.printed


def test_a_render_that_fails_in_the_automatic_pass_keeps_the_pass_result(a_machine, monkeypatch):
    monkeypatch.setattr(
        "nexgen_core.openclaw.adopt_render.render_adopted_servers",
        lambda **kwargs: (False, "the CLIs could not be written"),
    )

    code = a_machine.adopt(auto=True)

    assert code == 0
    assert "posted-by-hand" in declared_servers(a_machine)
    assert (a_machine.state / "openclaw-adopt-state.json").is_file()
    assert "the CLIs could not be written" in a_machine.printed


# ------------------------------------------------------------ the render itself


class _ExplodingRenderer:
    """A renderer that cannot even start: the helper must report it, never raise."""

    last_errors: dict = {}

    def __init__(self, **kwargs):
        raise OSError("the config folder is not readable")

    def render_all(self, write: bool = False):
        raise AssertionError("unreachable")


class _HalfFailingRenderer:
    """One CLI lands, one does not: the line must name the one that failed and why."""

    last_errors = {"codex": "config.toml is read-only"}

    def __init__(self, **kwargs):
        pass

    def render_all(self, write: bool = False):
        return {"claude": True, "codex": False, "opencode": True}


def test_a_render_that_cannot_start_returns_the_reason(a_machine, monkeypatch):
    monkeypatch.setattr("nexgen_core.renderer.McpRenderer", _ExplodingRenderer)

    ok, line = render_adopted_servers(home=a_machine.home, vault_data=a_machine.vault, engine_root=a_machine.engine)

    assert ok is False
    assert "not readable" in line
    assert "next pass will retry" in line


def test_a_partial_render_names_the_cli_that_did_not_land(a_machine, monkeypatch):
    monkeypatch.setattr("nexgen_core.renderer.McpRenderer", _HalfFailingRenderer)

    ok, line = render_adopted_servers(home=a_machine.home, vault_data=a_machine.vault, engine_root=a_machine.engine)

    assert ok is False
    assert "codex (config.toml is read-only)" in line


# ------------------------------------------------------------- the real renderer


def test_the_adopted_server_goes_behind_the_gateway_and_agy_gets_openclaws_file(a_machine):
    """The render obeys `mcp_placement`, so adoption is not a second placement rule."""
    code = a_machine.adopt(yes=True)

    assert code == 0
    assert "MCP configurations regenerated for every CLI" in a_machine.printed
    claude = json.loads((a_machine.home / ".claude.json").read_text(encoding="utf-8"))["mcpServers"]
    assert "lazy-mcp" in claude and "posted-by-hand" not in claude
    codex = tomllib.loads((a_machine.home / ".codex" / "config.toml").read_text(encoding="utf-8"))
    assert "lazy_mcp" in codex["mcp_servers"] and "posted_by_hand" not in codex["mcp_servers"]
    opencode = json.loads(opencode_config_path(a_machine.home).read_text(encoding="utf-8"))
    assert "lazy-mcp" in opencode["mcp"]["servers"] and "posted-by-hand" not in opencode["mcp"]["servers"]
    openclaw = json.loads(a_machine.config_path.read_text(encoding="utf-8"))["mcp"]["servers"]
    assert "posted-by-hand" in openclaw and "lazy-mcp" in openclaw
    assert openclaw["lazy-mcp"]["env"]["LAZY_MCP_CLI"] == "openclaw"
    agy = json.loads(antigravity_config(a_machine.home).read_text(encoding="utf-8"))["mcpServers"]
    assert "posted-by-hand" in agy and "lazy-mcp" not in agy
