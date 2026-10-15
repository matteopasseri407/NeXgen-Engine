"""Pinning dello split explore/eval di cmds/run prima del refactor.

Fissa il comportamento attuale (non quello desiderato) con modelli e
comandi finti: cmd_explore finto ritorna esito, cmd_run finto ritorna
esito, comando sconosciuto rifiutato con errore chiaro. Solo tmp_path,
solo modelli finti, nessuna rete, nessuna esecuzione reale.
"""
from __future__ import annotations

import argparse
import inspect
from pathlib import Path

import pytest

from nexgen_local.config import LaneConfig
from nexgen_local.engine import LaneResult
from nexgen_local.step_state import StepResult


def _cfg(tmp_path: Path) -> LaneConfig:
    vault = tmp_path / "vault"
    vault.mkdir(exist_ok=True)
    return LaneConfig(
        vault_root=vault,
        repo_roots=(),
        model="fake-model",
        audit_path=tmp_path / "audit.jsonl",
    )


class _FakeLLM:
    """Modello finto: mai rete, mai esecuzione reale."""


def _explore_args() -> argparse.Namespace:
    return argparse.Namespace(
        task="compito finto",
        session_id="",
        max_steps=2,
        json=False,
        model=None,
        router_model=None,
        answer_model=None,
        vault=None,
        repo=None,
        audit=None,
    )


def _run_args() -> argparse.Namespace:
    return argparse.Namespace(
        question="domanda finta sul gatto",
        json=False,
        model=None,
        router_model=None,
        answer_model=None,
        vault=None,
        repo=None,
        audit=None,
    )


def test_fake_explore_returns_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    from nexgen_local.cmds import run as run_mod
    import nexgen_local.steps as steps_mod

    cfg = _cfg(tmp_path)
    monkeypatch.setattr(run_mod, "_config", lambda args: cfg)
    monkeypatch.setattr(run_mod, "_llm", lambda cfg: _FakeLLM())

    def _fake_steps(llm, tools, cfg, task, max_steps=6, **kw) -> StepResult:
        assert task == "compito finto"
        return StepResult(task=task, answer="esito finto explore", steps=1)

    monkeypatch.setattr(steps_mod, "run_steps", _fake_steps)
    assert run_mod.cmd_explore(_explore_args()) == 0
    assert "esito finto explore" in capsys.readouterr().out


def test_fake_run_returns_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    from nexgen_local.cmds import run as run_mod
    import nexgen_local.graph as graph_mod

    cfg = _cfg(tmp_path)
    monkeypatch.setattr(run_mod, "_config", lambda args: cfg)
    monkeypatch.setattr(run_mod, "_llm", lambda cfg: _FakeLLM())

    def _fake_graph(llm, tools, cfg, question, **kw) -> LaneResult:
        assert "finta" in question
        return LaneResult(task=question, answer="risposta finta run")

    monkeypatch.setattr(graph_mod, "run_graph", _fake_graph)
    assert run_mod.cmd_run(_run_args()) == 0
    assert "risposta finta run" in capsys.readouterr().out


def test_unknown_command_rejected_with_clear_error(capsys: pytest.CaptureFixture) -> None:
    from nexgen_local import cli as cli_mod

    with pytest.raises(SystemExit) as exc:
        cli_mod.main(["questo-comando-non-esiste-xyz"])
    assert exc.value.code != 0
    err = capsys.readouterr().err
    assert "invalid choice" in err


def test_public_signatures_unchanged() -> None:
    from nexgen_local.cmds import run as run_mod

    assert list(inspect.signature(run_mod.cmd_run).parameters) == ["args"]
    assert list(inspect.signature(run_mod.cmd_explore).parameters) == ["args"]
    assert list(inspect.signature(run_mod.cmd_eval).parameters) == ["args"]


def test_explore_eval_helpers_in_sequence() -> None:
    import nexgen_local.cmds.run as run_mod

    src = Path(run_mod.__file__).read_text(encoding="utf-8")
    assert "def _run_explore_cmd" in src
    assert "def _run_eval_cmd" in src
    assert src.index("def _run_explore_cmd") < src.index("def _run_eval_cmd")
    explore_pos = src.index("def cmd_explore")
    eval_pos = src.index("def cmd_eval")
    assert "_run_explore_cmd(" in src[explore_pos:eval_pos]
    assert "_run_eval_cmd(" in src[eval_pos:]
