"""`nexgen info`: which MCP servers and skills are installed, whose they are, what they are pinned to, what moved.

Provenance is `engine` (updates with the engine), `yours` (only you change it) or `third-party` (somebody else's,
watched upstream). The upstream answer comes from the files the dependency watch leaves in the state folder; nothing
here touches the network.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

INFRA = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(INFRA / "scripts"))

from nexgen_core import extensions, i18n  # noqa: E402
from nexgen_core.cli import build_parser  # noqa: E402
from nexgen_core.tools import info as info_mod  # noqa: E402


@pytest.fixture(autouse=True)
def _english():
    i18n.set_language("en")
    yield
    i18n.set_language(None)


# --- provenance ------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("entry, expected", [
    ({"command": "node", "args": ["${AGENT_ENGINE_ROOT}/agent-universal-layer/mcp/playwright-human-safe.mjs"]}, "engine"),
    ({"command": "nexgen-local", "args": ["drive-mcp"]}, "engine"),
    ({"url": "${VAULT_LIBRARY_URL}"}, "engine"),
    ({"command": "python3", "args": ["${AGENT_VAULT_DATA}/03-INFRA/agent-universal-layer/mcp/workspace_mcp.py"]}, "yours"),
    ({"command": "node", "args": ["/srv/tools/dist/index.js"]}, "yours"),
    ({"command": "npx", "args": ["-y", "firecrawl-mcp@3.25.3"]}, "third-party"),
    ({"url": "https://mcp.example.com/"}, "third-party"),
    # The directory handed to a third-party package does not make the package the engine's.
    ({"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem@1.0.0", "${AGENT_ENGINE_ROOT}"]}, "third-party"),
])
def test_where_a_server_came_from(entry, expected):
    assert extensions.mcp_provenance(entry) == expected


@pytest.mark.parametrize("entry, expected", [
    ({"command": "npx", "args": ["-y", "firecrawl-mcp@3.25.3"]}, ("firecrawl-mcp@3.25.3", "pinned")),
    ({"command": "npx", "args": ["-y", "@supabase/mcp-server-supabase"]}, ("@supabase/mcp-server-supabase", "unpinned")),
    # a launcher that pins its own package carries no pin here: its module declares it (`upstream:`)
    ({"command": "node", "args": ["x.mjs"]}, ("", "none")),
    ({"command": "python3", "args": ["x.py"]}, ("", "none")),
    ({"url": "https://mcp.example.com/"}, ("", "none")),
])
def test_what_a_server_is_pinned_to(entry, expected):
    assert extensions._mcp_pin(entry) == expected


# --- a whole machine ---------------------------------------------------------------------------------------------

@pytest.fixture
def machine(tmp_path, monkeypatch):
    vault, engine, state, home = tmp_path / "vault", tmp_path / "engine", tmp_path / "state", tmp_path / "home"
    layer = vault / "03-INFRA" / "agent-universal-layer"
    (layer / "mcp").mkdir(parents=True)
    (layer / "skills").mkdir(parents=True)
    (engine / "agent-universal-layer" / "skills").mkdir(parents=True)
    (state / "nexgen").mkdir(parents=True)
    home.mkdir()
    monkeypatch.setenv("NEXGEN_HOME", str(home))
    monkeypatch.setenv("AGENT_STATE_DIR", str(state))
    monkeypatch.setenv("AGENT_VAULT_DATA", str(vault))
    monkeypatch.setenv("AGENT_ENGINE_ROOT", str(engine))
    servers = {
        "firecrawl": {"exposure": "eager", "command": "npx", "args": ["-y", "firecrawl-mcp@3.25.3"],
                      "tools_deny": ["firecrawl_crawl", "firecrawl_map"], "targets": ["claude"]},
        "supabase": {"exposure": "lazy", "command": "npx", "args": ["-y", "@supabase/mcp-server-supabase"],
                     "targets": ["claude"]},
        "mine": {"exposure": "eager", "command": "python3", "args": ["${AGENT_VAULT_DATA}/mine.py"], "targets": ["claude"]},
        "parked": {"command": "npx", "args": ["-y", "parked-mcp@1.0.0"], "targets": ["claude"]},
    }
    (layer / "mcp" / "manifest.yaml").write_text(yaml.safe_dump({"servers": servers}), encoding="utf-8")

    def skill(root: Path, name: str, body: str) -> None:
        (root / name).mkdir(parents=True)
        (root / name / "SKILL.md").write_text(body, encoding="utf-8")

    skill(engine / "agent-universal-layer" / "skills", "same-as-engine", "one\n")
    skill(engine / "agent-universal-layer" / "skills", "drifted", "engine's newer text\n")
    skill(layer / "skills", "same-as-engine", "one\n")
    skill(layer / "skills", "drifted", "old text\n")
    skill(layer / "skills", "my-own", "mine\n")
    for name in ("same-as-engine", "drifted", "my-own", "borrowed"):
        (home / ".agents" / "skill-library" / name).mkdir(parents=True)
    manifest = {"schema_version": 1, "skills": {
        "same-as-engine": {"origin": "vault", "exposure": "core", "targets": ["claude"]},
        "drifted": {"origin": "vault", "exposure": "core", "targets": ["claude"]},
        "my-own": {"origin": "vault", "exposure": "manual", "targets": ["claude"]},
        "borrowed": {"origin": "github", "repo": "o/r", "commit": "ab" * 20, "exposure": "manual", "targets": ["claude"]},
    }}
    (layer / "skills" / "skills.manifest.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    return {"vault": vault, "engine": engine, "state": state, "home": home}


def _collect(m):
    return extensions.collect(home=m["home"], vault_data=m["vault"], engine_root=m["engine"], state_dir=m["state"])


def _by_name(rows):
    return {r["name"]: r for r in rows}


def test_servers_are_listed_with_where_they_came_from_and_what_is_hidden(machine):
    rows = _by_name(_collect(machine)["mcp"])
    assert rows["firecrawl"]["provenance"] == "third-party" and rows["firecrawl"]["where"] == "direct"
    assert rows["firecrawl"]["hidden_tools"] == 2 and rows["firecrawl"]["pin"] == "firecrawl-mcp@3.25.3"
    assert rows["mine"]["provenance"] == "yours"
    assert rows["supabase"]["where"] == "gateway" and rows["supabase"]["clis"] == ["claude"]
    assert rows["supabase"]["pin_state"] == "unpinned"
    # A server nobody mounts says why, instead of simply being missing from the picture.
    assert rows["parked"]["where"] == "off" and rows["parked"]["why_off"] == "inert (not core, not enabled)"


def test_a_skill_the_engine_also_ships_says_whether_the_vault_copy_still_matches(machine):
    rows = _by_name(_collect(machine)["skills"])
    assert rows["same-as-engine"]["provenance"] == "engine" and rows["same-as-engine"]["engine_copy"] == "same"
    assert rows["drifted"]["provenance"] == "engine" and rows["drifted"]["engine_copy"] == "differs"
    assert rows["my-own"]["provenance"] == "yours" and rows["my-own"]["engine_copy"] is None
    assert rows["borrowed"]["provenance"] == "third-party" and rows["borrowed"]["pin"] == "ab" * 4 + "a"


def test_what_the_dependency_watch_found_is_shown_without_asking_the_network(machine):
    state = machine["state"] / "nexgen"
    (state / "third-party-status.json").write_text(json.dumps({
        "checked_at": 1.0, "stale": ["MCP server 'firecrawl' (npm firecrawl-mcp)"],
        "pins": [
            {"what": "MCP server 'firecrawl' (npm firecrawl-mcp)", "kind": "npm-version", "pinned": "3.25.3", "upstream": "3.28.2", "stale": True},
            {"what": "skill 'borrowed' (github o/r)", "kind": "git-commit", "pinned": "ab" * 20, "upstream": "ab" * 20, "stale": False},
        ],
    }), encoding="utf-8")
    (state / "third-party-guard.json").write_text(json.dumps({
        "checked_at": 1.0, "auto": [], "batch": [],
        "hold": [{"what": "MCP server 'firecrawl' (npm firecrawl-mcp)", "pinned": "3.25.3", "upstream": "3.28.2", "plain": "read first"}],
    }), encoding="utf-8")
    data = _collect(machine)
    firecrawl = _by_name(data["mcp"])["firecrawl"]["update"]
    assert (firecrawl["state"], firecrawl["pinned"], firecrawl["upstream"], firecrawl["verdict"]) == ("stale", "3.25.3", "3.28.2", "held")
    assert _by_name(data["skills"])["borrowed"]["update"]["state"] == "current"
    assert [u["name"] for u in extensions.updates(data)] == ["firecrawl"]


def test_a_status_file_from_before_it_carried_the_pins_still_names_what_is_stale(machine):
    (machine["state"] / "nexgen" / "third-party-status.json").write_text(json.dumps({
        "checked_at": 1.0, "stale": ["MCP server 'firecrawl' (npm firecrawl-mcp)"],
    }), encoding="utf-8")
    assert _by_name(_collect(machine)["mcp"])["firecrawl"]["update"]["state"] == "stale"


def test_nothing_checked_yet_is_not_the_same_as_up_to_date(machine):
    data = _collect(machine)
    assert data["upstream_checked_at"] is None
    assert _by_name(data["mcp"])["firecrawl"]["update"]["state"] == "unknown"


def test_an_unreadable_manifest_is_reported_and_the_rest_still_lists(machine):
    (machine["vault"] / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml").write_text("servers: [oops\n", encoding="utf-8")
    data = _collect(machine)
    assert data["errors"] and data["mcp"] == []
    assert _by_name(data["skills"])


def test_a_machine_with_nothing_installed_lists_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXGEN_HOME", str(tmp_path / "home"))
    data = extensions.collect(home=tmp_path / "home", vault_data=tmp_path / "no-vault", engine_root=tmp_path / "no-engine", state_dir=tmp_path / "state")
    assert data["mcp"] == [] and data["skills"] == [] and data["errors"] == []


# --- what `nexgen info` prints ----------------------------------------------------------------------------------------

def test_info_names_the_unpinned_server_and_the_drifted_core_skill(machine):
    text = "\n".join(info_mod._extension_lines(_collect(machine), show_all=False))
    assert "CONNECTORS (MCP)" in text and "SKILLS" in text
    assert "not pinned" in text
    assert "2 tools hidden" in text
    assert "neither core nor switched on" in text
    assert "drifted" in text and "differs from the engine's" in text
    # A frozen copy is listed either way (it will not follow the engine); skills that are simply yours are not.
    assert "same-as-engine" in text and "nexgen skills adopt" in text
    assert "my-own" not in text
    everything = "\n".join(info_mod._extension_lines(_collect(machine), show_all=True))
    assert "my-own" in everything and "same-as-engine" in everything


def test_info_points_at_the_update_command_when_something_moved(machine):
    (machine["state"] / "nexgen" / "third-party-status.json").write_text(json.dumps({
        "checked_at": 1.0, "stale": ["MCP server 'firecrawl' (npm firecrawl-mcp)"],
    }), encoding="utf-8")
    text = "\n".join(info_mod._extension_lines(_collect(machine), show_all=False))
    assert "Newer upstream versions" in text and "mcp bump" in text


def test_info_json_carries_the_same_data(machine, monkeypatch):
    monkeypatch.setattr(info_mod, "resolve_home", lambda *a, **k: machine["home"])
    data = json.loads(info_mod.render_info(as_json=True, vault_data=machine["vault"]))
    assert {r["name"] for r in data["extensions"]["mcp"]} >= {"firecrawl", "supabase"}


def test_the_verbs_exist():
    parser = build_parser()
    assert parser.parse_args(["info", "--all"]).all is True
    assert parser.parse_args(["mcp", "bump"]).mcp_command == "bump"


def test_what_the_dependency_watch_writes_is_what_info_reads(machine):
    """The producer and the reader agree: run the watch with a faked registry, then ask for the inventory."""
    from nexgen_core.depwatch import run_depwatch

    result = run_depwatch(
        vault_data=machine["vault"], state_dir=machine["state"],
        git_ls_remote=lambda repo: "ab" * 20,
        npm_latest_version=lambda package: "3.28.2" if package == "firecrawl-mcp" else None,
    )
    assert result.wrote
    data = _collect(machine)
    assert data["upstream_checked_at"] is not None
    firecrawl = _by_name(data["mcp"])["firecrawl"]["update"]
    assert (firecrawl["state"], firecrawl["pinned"], firecrawl["upstream"]) == ("stale", "3.25.3", "3.28.2")
    assert _by_name(data["skills"])["borrowed"]["update"]["state"] == "current"
