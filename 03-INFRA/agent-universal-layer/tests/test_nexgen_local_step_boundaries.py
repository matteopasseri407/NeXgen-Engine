"""Exercise the shared policy/action/state boundary without a loop driver."""
from __future__ import annotations

import json
from dataclasses import asdict

import pytest

from nexgen_local.config import LaneConfig
from nexgen_local.step_actions import execute_action
from nexgen_local.step_policy import build_menu, validate_action
from nexgen_local.step_state import LoopState, MAX_CONTINUATIONS
from nexgen_local.tools import ToolError, ToolRegistry


class NoModelCalls:
    def text(self, *args):
        raise AssertionError("Retrieval and policy must not invoke the model")


def config(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    return LaneConfig(vault_root=vault, repo_roots=(), model="synthetic", read_chars=16,
                      audit_path=tmp_path / "audit.jsonl")


def test_admitted_read_can_resume_and_enforces_the_continuation_budget(tmp_path):
    cfg = config(tmp_path)
    (cfg.vault_root / "nota.md").write_text("(bozza) " * 30, encoding="utf-8")
    state = LoopState(task="leggi nota.md", route="vault", named_path="nota.md")
    tools = ToolRegistry(cfg)
    menu = build_menu(cfg, state)
    assert validate_action(cfg, state, menu, "read_file", "nota.md") is None
    execute_action(NoModelCalls(), tools, state, "read_file", "nota.md")
    assert state.reads and "(bozza)" in state.reads[0]
    assert state.receipts[-1]["status"] == "ok"

    # The persisted representation has no dependency on the coordinator's types.
    resumed = LoopState(**json.loads(json.dumps(asdict(state))))
    for _ in range(MAX_CONTINUATIONS):
        menu = build_menu(cfg, resumed)
        assert "continue_read" in [candidate.action for candidate in menu]
        assert validate_action(cfg, resumed, menu, "continue_read", "") is None
        execute_action(NoModelCalls(), tools, resumed, "continue_read", "")
    assert resumed.continuations == MAX_CONTINUATIONS
    assert len(resumed.reads) == MAX_CONTINUATIONS + 1
    assert "continue_read" not in [candidate.action for candidate in build_menu(cfg, resumed)]
    count = len(tools.calls)
    with pytest.raises(ToolError, match="tetto raggiunto"):
        execute_action(NoModelCalls(), tools, resumed, "continue_read", "")
    assert len(tools.calls) == count


def test_audit_failure_does_not_advance_loop_state(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    (cfg.vault_root / "nota.md").write_text("Contenuto.", encoding="utf-8")
    state = LoopState(task="leggi nota.md", route="vault", named_path="nota.md")
    tools = ToolRegistry(cfg)
    before = asdict(state)

    def fail(*args, **kwargs):
        raise ToolError("audit unavailable")

    monkeypatch.setattr(tools, "_audit", fail)
    with pytest.raises(ToolError, match="audit unavailable"):
        execute_action(NoModelCalls(), tools, state, "read_file", "nota.md")
    assert asdict(state) == before
    assert tools.calls == []
