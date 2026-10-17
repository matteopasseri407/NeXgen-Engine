"""Pinning dello split mcp_placement prima del refactor.

Fissa il comportamento attuale (non quello desiderato) con placement
finti: problems su placement finto ritorna problemi attesi, place finto
stabile, firme pubbliche invariate. Solo finti in memoria/tmp_path, mai
config vere, mai rete.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import mcp_placement as mp  # noqa: E402


def srv(**kw):
    return {"command": "x", **kw}


def test_problems_su_placement_finto_ritorna_problemi_attesi() -> None:
    servers = {"lazy-mcp": srv(tier="core", targets=["claude"]), "a": srv(exposure="lazy")}
    found = mp.problems(servers)
    assert any(line.startswith("codex:") and "a" in line for line in found)
    assert not any(line.startswith("claude:") for line in found)

    bogus = {"lazy-mcp": srv(exposure="lazy"), "b": srv(exposure="bogus")}
    found_bogus = mp.problems(bogus)
    assert any("unknown exposure" in line for line in found_bogus)
    assert any("behind itself" in line for line in found_bogus)


def test_place_finto_stabile() -> None:
    entry = srv(exposure="lazy", targets=["claude", "opencode"])
    first = mp.place(entry, "claude")
    second = mp.place(entry, "claude")
    assert (first.kind, first.why) == ("gateway", "exposure: lazy")
    assert (second.kind, second.why) == (first.kind, first.why)

    servers = {"lazy-mcp": srv(exposure="eager"), "a": srv(exposure="eager"), "b": srv(exposure="lazy")}
    assert mp.plan(servers) == mp.plan(servers)
    assert mp.gateway_servers_for(servers, "claude") == {"b"}
    assert mp.gateway_servers_for(servers, "claude") == mp.gateway_servers_for(servers, "claude")


def test_firme_pubbliche_invariate() -> None:
    assert list(inspect.signature(mp.place).parameters) == ["srv", "cli"]
    assert list(inspect.signature(mp.plan).parameters) == ["servers", "clis"]
    assert list(inspect.signature(mp.problems).parameters) == ["servers", "clis"]
    assert list(inspect.signature(mp.gateway_servers_for).parameters) == ["servers", "cli"]
