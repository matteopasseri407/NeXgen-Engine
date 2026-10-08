"""One rule for where each MCP server lives, and every consumer of it in agreement.

The renderer, the gateway (lazy-mcp) and `nexgen mcp plan` each used to decide this for themselves and did
not agree: the gateway served every lazy server to every CLI, ignoring `targets` and `enabled`, so a server
mounted directly in a CLI was also in that CLI's gateway, and one restricted to two CLIs was still offered
to the other two. These tests hold them to the same answer.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tomllib
from pathlib import Path

import pytest
import yaml

INFRA = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(INFRA / "scripts"))
LAZY_MCP = INFRA / "agent-universal-layer" / "mcp" / "lazy-mcp.py"

from nexgen_core import mcp_placement as mp  # noqa: E402
from nexgen_core.mcp_plan import build  # noqa: E402
from nexgen_core.renderer import McpRenderer  # noqa: E402


def srv(**kw):
    return {"command": "x", **kw}


# --- the rule -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("entry,cli,kind,why", [
    (srv(exposure="eager"), "claude", "direct", "exposure: eager"),
    (srv(exposure="lazy"), "codex", "gateway", "exposure: lazy"),
    (srv(exposure="eager", targets=["claude"]), "codex", "absent", "not in targets"),
    (srv(exposure="lazy", enabled=False), "claude", "absent", "disabled"),
    (srv(exposure="eager", tier="core", lazy=True), "claude", "direct", "exposure: eager"),  # exposure beats the old knobs
    (srv(exposure="native"), "claude", "absent", "unknown exposure 'native'"),
    # legacy, no exposure
    (srv(tier="core"), "opencode", "direct", "legacy: core"),
    (srv(enabled=True), "opencode", "direct", "legacy: enabled"),
    (srv(), "opencode", "absent", "inert (not core, not enabled)"),
    (srv(lazy=True), "codex", "gateway", "legacy: lazy"),
    (srv(lazy=True, enabled=True, lazy_targets=["claude"]), "codex", "direct", "legacy: enabled"),
    (srv(lazy=True, lazy_targets=["claude"]), "codex", "absent", "inert (not core, not enabled)"),
    (srv(lazy=True, targets=["claude", "opencode"], lazy_targets=["claude", "codex"]), "codex", "absent", "not in targets"),
])
def test_the_rule(entry, cli, kind, why):
    placement = mp.place(entry, cli)
    assert (placement.kind, placement.why) == (kind, why)


def test_the_gateway_must_be_mounted_where_servers_are_routed_to_it():
    servers = {"lazy-mcp": srv(tier="core", targets=["claude"]), "a": srv(exposure="lazy")}
    found = mp.problems(servers)
    assert any(line.startswith("codex:") and "a" in line for line in found)
    assert not any(line.startswith("claude:") for line in found)


def test_an_unknown_exposure_and_a_gateway_behind_itself_are_called_out():
    found = mp.problems({"lazy-mcp": srv(exposure="lazy"), "b": srv(exposure="bogus")})
    assert any("unknown exposure" in line for line in found)
    assert any("behind itself" in line for line in found)


# --- every consumer agrees ----------------------------------------------------------------------------

#: A manifest shaped like a real one: legacy knobs, new declarations, restrictions and an inert server.
MIXED = {
    "lazy-mcp": srv(tier="core", args=["lazy-mcp.py"]),
    "browser": srv(tier="core"),
    "hosted": srv(exposure="eager", transport="http", url="https://example.com/mcp"),
    "notes": srv(exposure="lazy", targets=["claude", "opencode"]),
    "mail": srv(lazy=True, enabled=True, targets=["claude", "opencode"], lazy_targets=["claude", "codex", "antigravity"]),
    "old-lazy": srv(lazy=True),
    "direct-here": srv(lazy=True, enabled=True, lazy_targets=["claude"]),
    "off": srv(exposure="lazy", enabled=False),
    "dormant": srv(),
}


@pytest.fixture
def manifest(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    path = vault / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump({"servers": MIXED}), encoding="utf-8")
    monkeypatch.setenv("AGENT_VAULT_DATA", str(vault))
    return path


def lazy_module(monkeypatch, cli=None):
    if cli is None:
        monkeypatch.delenv("LAZY_MCP_CLI", raising=False)
    else:
        monkeypatch.setenv("LAZY_MCP_CLI", cli)
    spec = importlib.util.spec_from_file_location("lazy_mcp_for_placement", LAZY_MCP)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("cli", mp.CLIS)
def test_renderer_and_gateway_serve_exactly_what_the_plan_says(manifest, tmp_path, monkeypatch, cli):
    renderer = McpRenderer(vault_data=manifest.parents[3], engine_root=tmp_path / "engine", home=tmp_path / "home")
    direct = set(renderer.load_resolved_servers(cli))
    gateway = set(lazy_module(monkeypatch, cli)._lazy_servers())
    expected_direct = {n for n, s in MIXED.items() if mp.place(s, cli).kind == mp.DIRECT}
    expected_gateway = {n for n, s in MIXED.items() if mp.place(s, cli).kind == mp.GATEWAY_KIND}
    assert direct == expected_direct
    assert gateway == expected_gateway
    assert not direct & gateway, "a server is both mounted directly and behind the gateway"
    assert "dormant" not in direct | gateway and "off" not in direct | gateway


def test_a_server_restricted_to_two_clis_is_not_offered_by_the_other_two_gateways(manifest, monkeypatch):
    assert "notes" in lazy_module(monkeypatch, "claude")._lazy_servers()
    assert "notes" not in lazy_module(monkeypatch, "codex")._lazy_servers()
    assert "mail" not in lazy_module(monkeypatch, "codex")._lazy_servers()


def test_a_server_mounted_directly_in_a_cli_is_not_also_in_its_gateway(manifest, tmp_path, monkeypatch):
    """direct-here is behind the gateway only in claude (its lazy_targets); everywhere else it is mounted directly."""
    renderer = McpRenderer(vault_data=manifest.parents[3], engine_root=tmp_path / "engine", home=tmp_path / "home")
    assert "direct-here" in lazy_module(monkeypatch, "claude")._lazy_servers()
    assert "direct-here" not in renderer.load_resolved_servers("claude")
    for cli in ("codex", "antigravity", "opencode"):
        assert "direct-here" not in lazy_module(monkeypatch, cli)._lazy_servers()
        assert "direct-here" in renderer.load_resolved_servers(cli)


def test_a_gateway_never_told_its_cli_keeps_the_old_behaviour_until_the_next_render(manifest, monkeypatch):
    served = set(lazy_module(monkeypatch, None)._lazy_servers())
    assert {"notes", "mail", "old-lazy", "direct-here", "off"} <= served


@pytest.mark.parametrize("cli", mp.CLIS)
def test_the_rendered_gateway_entry_carries_its_cli_in_every_dialect(manifest, tmp_path, cli):
    renderer = McpRenderer(vault_data=manifest.parents[3], engine_root=tmp_path / "engine", home=tmp_path / "home")
    renderer.render_all(write=True)
    home = tmp_path / "home"
    if cli == "claude":
        env = json.loads((home / ".claude.json").read_text())["mcpServers"]["lazy-mcp"]["env"]
    elif cli == "antigravity":
        env = json.loads((home / ".gemini/antigravity/mcp_config.json").read_text())["mcpServers"]["lazy-mcp"]["env"]
    elif cli == "codex":
        env = tomllib.loads((home / ".codex/config.toml").read_text())["mcp_servers"]["lazy_mcp"]["env"]
    else:
        from nexgen_core.jsonc import parse_jsonc
        path = renderer.opencode_config_path()
        env = parse_jsonc(path.read_text())["mcp"]["servers"]["lazy-mcp"]["environment"]
    assert env["LAZY_MCP_CLI"] == cli


def test_everyone_the_same_when_the_manifest_says_so(tmp_path, monkeypatch):
    """The goal: eager servers identical in every CLI, everything else behind the same gateway in every CLI."""
    servers = {"lazy-mcp": srv(exposure="eager"), "a": srv(exposure="eager"), "b": srv(exposure="lazy"), "c": srv(exposure="lazy")}
    table = mp.plan(servers)
    for cli in mp.CLIS:
        assert {n for n, row in table.items() if row[cli].kind == mp.DIRECT} == {"lazy-mcp", "a"}
        assert mp.gateway_servers_for(servers, cli) == {"b", "c"}
    assert mp.problems(servers) == []


# --- the plan command ---------------------------------------------------------------------------------

def test_the_plan_names_what_changes_for_the_gateway_and_what_is_incoherent(manifest):
    plan = build(manifest)
    row = {r["server"]: r for r in plan["rows"]}
    assert row["browser"]["cells"] == {c: "direct" for c in mp.CLIS}
    assert row["mail"]["cells"] == {"claude": "gateway", "codex": "absent", "antigravity": "absent", "opencode": "direct"}
    assert "mail" in plan["changes"]["codex"]["no_longer_served"]
    assert plan["problems"] == []


def test_the_shipped_manifest_is_coherent():
    shipped = INFRA / "agent-universal-layer" / "mcp" / "manifest.yaml"
    servers = yaml.safe_load(shipped.read_text(encoding="utf-8"))["servers"]
    assert mp.problems({**servers, "lazy-mcp": srv(tier="core")}) == []


# --- doctor and `mcp add` ------------------------------------------------------------------------------

def test_doctor_flags_a_gateway_that_is_not_where_it_is_needed(tmp_path):
    from nexgen_core.checks.mcp_checks import check_mcp_placement
    from nexgen_core.report import Severity

    vault = tmp_path / "vault"
    path = vault / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump({"servers": {"a": srv(exposure="lazy")}}), encoding="utf-8")
    outcome = check_mcp_placement(vault)
    assert outcome.severity == Severity.BROKEN and "not mounted" in outcome.message
    path.write_text(yaml.safe_dump({"servers": {"a": srv(exposure="lazy"), "lazy-mcp": srv(tier="core")}}), encoding="utf-8")
    assert check_mcp_placement(vault).severity == Severity.OK
    assert check_mcp_placement(tmp_path / "nowhere") is None


def test_mcp_add_declares_the_exposure_and_keeps_the_legacy_keys_for_older_engines():
    from nexgen_core.mcp_add import build_entry

    lazy = build_entry(name="x", targets=["claude", "codex"], command="npx", args=["-y", "pkg@1.0.0"], url=None,
                       auth_env=None, env=None, lazy=True, readonly=True)
    eager = build_entry(name="y", targets=["claude"], command="npx", args=["-y", "pkg@1.0.0"], url=None,
                        auth_env=None, env=None, lazy=False, readonly=False)
    assert lazy["exposure"] == "lazy" and lazy["lazy"] is True and lazy["tier"] == "core"
    assert eager["exposure"] == "eager" and "lazy" not in eager and eager["tier"] == "core"
    assert mp.place(lazy, "claude").kind == mp.GATEWAY_KIND and mp.place(eager, "claude").kind == mp.DIRECT
