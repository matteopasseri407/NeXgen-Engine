"""State follows NEXGEN_HOME because it goes through the one resolver, never Path.home().

Each place that asked the process for its home directory instead of the engine's was a place where a
checkout run in a sandbox home beside a working installation read and wrote the working installation's
state: the connectors' tokens, the Council's sessions, the lane's audit trail and proposals. This keeps
the next one from being written.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

INFRA = Path(__file__).resolve().parents[2]
ROOTS = (INFRA / "scripts" / "nexgen_core", INFRA / "scripts" / "nexgen_local",
         INFRA / "agent-universal-layer" / "council", INFRA / "agent-universal-layer" / "mcp")

#: file (relative to 03-INFRA) -> why it may ask for the real home.
ALLOWED = {
    "scripts/nexgen_core/paths.py": "the resolver itself",
    "scripts/nexgen_core/tools/ruff_baseline.py": "looks for a linter in the developer's own tool directories",
    "scripts/nexgen_core/tools/notifier_boot.py": "Windows branch that is handed the home explicitly and falls back to it",
    "scripts/nexgen_local/relay.py": "the real Codex login: another tool's credentials, read-only",
    "agent-universal-layer/council/routing.py": "the real Codex model cache: another tool's data, read-only",
    "agent-universal-layer/council/seat_process.py": "the real Codex login a seat needs a copy of",
    "agent-universal-layer/mcp/lazy-mcp.py": "a standalone proxy with its own last-resort fallbacks when the engine package is unavailable",
}


def _asks_for_the_real_home(tree: ast.AST) -> list[int]:
    lines = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Attribute) and f.attr == "home" and isinstance(f.value, ast.Name) and f.value.id == "Path":
            lines.append(node.lineno)
        elif (isinstance(f, ast.Attribute) and f.attr == "expanduser" and node.args
              and isinstance(node.args[0], ast.Constant) and node.args[0].value == "~"):
            lines.append(node.lineno)
    return lines


@pytest.mark.parametrize("path", sorted(p for root in ROOTS for p in root.rglob("*.py")), ids=lambda p: str(p.relative_to(INFRA)))
def test_no_state_is_placed_by_asking_for_the_real_home(path):
    rel = path.relative_to(INFRA).as_posix()
    if rel in ALLOWED or "__pycache__" in path.parts:
        return
    lines = _asks_for_the_real_home(ast.parse(path.read_text(encoding="utf-8")))
    assert not lines, f"{rel}:{lines} asks for Path.home(); use nexgen_core.paths.resolve_home()"


def test_the_allowlist_has_no_stale_entries():
    for rel in ALLOWED:
        assert (INFRA / rel).is_file(), f"{rel} is gone: drop it from ALLOWED"


def test_the_lane_follows_nexgen_home(tmp_path, monkeypatch):
    import sys

    sys.path.insert(0, str(INFRA / "scripts"))
    from nexgen_local.config import LaneConfig

    sandbox = tmp_path / "sandbox"
    monkeypatch.setenv("NEXGEN_HOME", str(sandbox))
    monkeypatch.delenv("AGENT_VAULT_DATA", raising=False)
    monkeypatch.delenv("NEXGEN_LOCAL_AUDIT", raising=False)
    monkeypatch.setenv("KNOWLEDGE_VAULT_PATH", str(tmp_path / "vault"))
    (tmp_path / "vault").mkdir()
    cfg = LaneConfig.from_env()
    lane = sandbox / ".local/state/nexgen/local-lane"
    assert cfg.audit_path == lane / "audit.jsonl"
    assert cfg.proposals_dir == lane / "proposals" and cfg.research_dir == lane / "research"
    assert cfg.vault_root == (tmp_path / "vault").resolve(), "KNOWLEDGE_VAULT_PATH was ignored"
