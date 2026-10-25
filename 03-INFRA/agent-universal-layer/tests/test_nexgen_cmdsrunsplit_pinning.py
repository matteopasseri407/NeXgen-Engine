"""Pinning di cmds/run dopo il ritiro del loop.

Fissa il comportamento attuale (non quello desiderato) con modelli e
comandi finti: cmd_run finto ritorna esito, comando sconosciuto
rifiutato con errore chiaro. Solo tmp_path, solo modelli finti, nessuna
rete, nessuna esecuzione reale.
"""
from __future__ import annotations

import argparse
import inspect
from pathlib import Path

import pytest

from nexgen_local.config import LaneConfig


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


def test_fake_run_returns_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    from nexgen_local.cmds import run as run_mod

    assert run_mod.cmd_run(_run_args()) == 0
    out = capsys.readouterr().out
    assert "openclaw" in out.lower()
    assert "ritirato" in out.lower()


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
    assert list(inspect.signature(run_mod.cmd_eval).parameters) == ["args"]


def test_eval_helper_is_called_by_its_command() -> None:
    import nexgen_local.cmds.run as run_mod

    src = Path(run_mod.__file__).read_text(encoding="utf-8")
    assert "def _run_eval_cmd" in src
    eval_pos = src.index("def cmd_eval")
    assert "_run_eval_cmd(" in src[eval_pos:]
