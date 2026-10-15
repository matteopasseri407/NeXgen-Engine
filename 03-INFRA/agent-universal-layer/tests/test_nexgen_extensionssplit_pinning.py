"""Pinning dello split extensions prima del refactor.

Fissa il comportamento attuale (non quello desiderato) con manifest finti:
describe su stato finto ritorna righe attese, skill_rows e mcp_rows su
manifest finti sono stabili, firme pubbliche invariate. Solo manifest finti
in tmp_path, mai manifest veri, nessuna rete.
"""
from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path

import yaml

INFRA = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(INFRA / "scripts"))

from nexgen_core import extensions  # noqa: E402


def _fake_state(tmp_path: Path, *, with_pins: bool = True) -> Path:
    state = tmp_path / "state"
    (state / "nexgen").mkdir(parents=True, exist_ok=True)
    if with_pins:
        (state / "nexgen" / "third-party-status.json").write_text(json.dumps({
            "checked_at": 1.0,
            "pins": [
                {"what": "MCP server 'demo-mcp' (npm demo-pkg)", "kind": "npm-version",
                 "pinned": "1.2.3", "upstream": "1.4.0", "stale": True},
                {"what": "skill 'demo-skill' (github o/r)", "kind": "git-commit",
                 "pinned": "a" * 40, "upstream": "a" * 40, "stale": False},
            ],
        }), encoding="utf-8")
        (state / "nexgen" / "third-party-guard.json").write_text(json.dumps({
            "checked_at": 1.0, "auto": [], "batch": [],
            "hold": [{"what": "MCP server 'demo-mcp' (npm demo-pkg)",
                      "pinned": "1.2.3", "upstream": "1.4.0", "plain": "leggere prima"}],
        }), encoding="utf-8")
    return state


def _fake_machine(tmp_path: Path):
    vault = tmp_path / "vault"
    engine = tmp_path / "engine"
    state = tmp_path / "state"
    home = tmp_path / "home"
    layer = vault / "03-INFRA" / "agent-universal-layer"
    (layer / "mcp").mkdir(parents=True, exist_ok=True)
    (layer / "skills").mkdir(parents=True, exist_ok=True)
    (engine / "agent-universal-layer" / "skills" / "shipped-skill").mkdir(parents=True, exist_ok=True)
    (engine / "agent-universal-layer" / "skills" / "shipped-skill" / "SKILL.md").write_text(
        "# shipped\n", encoding="utf-8")
    (layer / "skills" / "shipped-skill").mkdir(parents=True, exist_ok=True)
    (layer / "skills" / "shipped-skill" / "SKILL.md").write_text(
        "# shipped\n", encoding="utf-8")
    (layer / "skills" / "mine-skill").mkdir(parents=True, exist_ok=True)
    (layer / "skills" / "mine-skill" / "SKILL.md").write_text("# mine\n", encoding="utf-8")
    for name in ("shipped-skill", "mine-skill", "demo-skill"):
        (home / ".agents" / "skill-library" / name).mkdir(parents=True, exist_ok=True)
    servers = {
        "demo-mcp": {"command": "npx", "args": ["-y", "demo-pkg@1.2.3"],
                     "targets": ["claude"]},
        "mine-mcp": {"command": "python3", "args": ["${AGENT_VAULT_DATA}/mine.py"],
                     "targets": ["claude"]},
    }
    (layer / "mcp" / "manifest.yaml").write_text(
        yaml.safe_dump({"servers": servers}), encoding="utf-8")
    manifest = {"schema_version": 1, "skills": {
        "shipped-skill": {"origin": "vault", "exposure": "core", "targets": ["claude"]},
        "mine-skill": {"origin": "vault", "exposure": "manual", "targets": ["claude"]},
        "demo-skill": {"origin": "github", "repo": "o/r", "commit": "a" * 40,
                       "exposure": "manual", "targets": ["claude"]},
    }}
    (layer / "skills" / "skills.manifest.yaml").write_text(
        yaml.safe_dump(manifest), encoding="utf-8")
    (state / "nexgen").mkdir(parents=True, exist_ok=True)
    home.mkdir(parents=True, exist_ok=True)
    return vault, engine, state, home


def test_describe_su_manifest_finto_ritorna_righe_attese(tmp_path: Path) -> None:
    state = _fake_state(tmp_path)
    upstream = extensions._Upstream(state)
    assert upstream.names("mcp") == ["demo-mcp"]
    stale = upstream.lookup("mcp", "demo-mcp")
    assert stale["state"] == "stale"
    assert (stale["pinned"], stale["upstream"], stale["verdict"]) == ("1.2.3", "1.4.0", "held")
    current = upstream.lookup("skill", "demo-skill")
    assert current["state"] == "current"
    assert upstream.lookup("mcp", "missing") == {"state": "unknown"}
    # Withdrawn flag survives on both branches.
    dep = upstream.describe("mcp", "demo-mcp", [
        {"what": "x", "pinned": "1.2.3", "upstream": "1.4.0", "stale": True,
         "deprecated": "withdrawn"}])
    assert dep["deprecated"] == "withdrawn"
    cur_dep = upstream.describe("skill", "demo-skill", [
        {"what": "x", "upstream": "a" * 40, "deprecated": "withdrawn"}])
    assert cur_dep == {"state": "current", "deprecated": "withdrawn"}


def test_mcp_rows_stabili_su_manifest_finto(tmp_path: Path) -> None:
    vault, engine, state, home = _fake_machine(tmp_path)
    upstream = extensions._Upstream(state)
    servers = {
        "demo-mcp": {"command": "npx", "args": ["-y", "demo-pkg@1.2.3"],
                     "targets": ["claude"]},
        "mine-mcp": {"command": "python3", "args": ["${AGENT_VAULT_DATA}/mine.py"],
                     "targets": ["claude"]},
        "broken": "not-a-dict",
    }
    first = extensions.mcp_rows(servers, upstream)
    second = extensions.mcp_rows(servers, upstream)
    assert first == second
    by_name = {r["name"]: r for r in first}
    assert set(by_name) == {"demo-mcp", "mine-mcp"}
    assert by_name["demo-mcp"]["provenance"] == "third-party"
    assert by_name["demo-mcp"]["pin"] == "demo-pkg@1.2.3"
    assert by_name["demo-mcp"]["pin_state"] == "pinned"
    assert by_name["mine-mcp"]["provenance"] == "yours"
    assert by_name["mine-mcp"]["pin_state"] == "none"
    for row in first:
        for key in ("name", "provenance", "state", "where", "pin", "pin_state",
                    "placement", "clis", "update"):
            assert key in row


def test_skill_rows_stabili_su_manifest_finto(tmp_path: Path) -> None:
    vault, engine, state, home = _fake_machine(tmp_path)
    upstream = extensions._Upstream(state)
    first, err1, outside1 = extensions.skill_rows(vault, engine, home, upstream)
    second, err2, outside2 = extensions.skill_rows(vault, engine, home, upstream)
    assert err1 is None and err2 is None
    assert first == second
    assert outside1 == outside2
    by_name = {r["name"]: r for r in first}
    assert by_name["shipped-skill"]["provenance"] == "engine"
    assert by_name["shipped-skill"]["engine_copy"] == "same"
    assert by_name["mine-skill"]["provenance"] == "yours"
    assert by_name["demo-skill"]["provenance"] == "third-party"
    assert by_name["demo-skill"]["pin"] == ("a" * 40)[:9]
    for row in first:
        for key in ("name", "provenance", "origin", "exposure", "pin",
                    "in_library", "engine_copy", "update"):
            assert key in row


def test_firme_pubbliche_invariate() -> None:
    assert list(inspect.signature(extensions.mcp_provenance).parameters) == ["srv"]
    assert list(inspect.signature(extensions.mcp_rows).parameters) == ["servers", "upstream"]
    assert list(inspect.signature(extensions.skill_rows).parameters) == [
        "vault_data", "engine_root", "home", "upstream"]
    assert list(inspect.signature(extensions.collect).parameters) == [
        "home", "vault_data", "engine_root", "state_dir"]
    assert list(inspect.signature(extensions.module_rows).parameters) == ["upstream"]
    assert list(inspect.signature(extensions.counts).parameters) == ["items"]
    assert list(inspect.signature(extensions.updates).parameters) == ["data"]
    assert list(inspect.signature(extensions.deprecations).parameters) == ["data"]
    assert list(inspect.signature(extensions.age_text).parameters) == ["checked_at", "now"]
    assert list(inspect.signature(extensions._Upstream.describe).parameters) == [
        "self", "kind", "name", "pins"]
    assert list(inspect.signature(extensions._Upstream.lookup).parameters) == [
        "self", "kind", "name"]
