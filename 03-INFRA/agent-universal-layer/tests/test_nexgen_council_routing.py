"""Unit test per il parser del blocco governato v4 nel council (routing.py).

Il blocco governato v4 usa colonne `Slot|Modello|Canale|Costo|Motivo` e slot
`prescelto/rimpiazzo N`: il parser deve estrarre i primi tre candidati con la
CLI derivata dal canale. Un blocco v3 (legacy) deve continuare a passare dal
parser legacy.
"""
from __future__ import annotations

import sys

import pytest
from pathlib import Path

COUNCIL_DIR = Path(__file__).resolve().parents[2] / "agent-universal-layer" / "council"
if str(COUNCIL_DIR) not in sys.path:
    sys.path.insert(0, str(COUNCIL_DIR))

from routing import parse_routing_plan, RoutingContractError

GOVERNOR_BLOCK_V4 = """### Proposta di routing per ruolo

#### L-Code - ratio

Codice.

| Slot | Modello | Canale | Costo | Motivo |
|---|---|---|---:|---|
| prescelto | Muse Spark 1.2 | go | $0.125 | il piu leggero |
| rimpiazzo 1 | GPT-5.6 Luna | codex | forfait | pool mensile |
| rimpiazzo 2 | Gemini 3.7 Flash | agy | forfait | merito 72 |

#### Privacy - quality-first

NDA.

| Slot | Modello | Canale | Costo | Motivo |
|---|---|---|---:|---|
| prescelto | gemma4-12b-64k:latest | local | $0 | fuori classifica |
| rimpiazzo 1 | gemma4-12b-openclaw:latest | local | $0 | fuori classifica |
| rimpiazzo 2 | gemma4:12b | local | $0 | fuori classifica |
| rimpiazzo 3 | — | zen-free | — | escluso: dati personali |

<!-- model-routing-governor:end -->
"""

LEGACY_TABLE = """### Ranking per ruoli reali

| Ruolo | Primario | Fallback 1 | Fallback 2 | Note |
|---|---|---|---|---|
| L-Arch | Gemini 3.1 Pro (Antigravity) | GPT-5.6 Terra (Codex) | DeepSeek V4 Pro (OpenCode Go) | deterministico |

### Motivazioni concise

Vecchio formato.
"""


def test_v4_parser_extracts_top_three_with_cli() -> None:
    plan = parse_routing_plan(GOVERNOR_BLOCK_V4)
    assert plan.source == "governor-role-tables"
    assert set(plan.roles) == {"L-Code", "Privacy"}

    code = plan.roles["L-Code"]
    assert [(c.value, c.cli) for c in code] == [
        ("Muse Spark 1.2", "opencode"),
        ("GPT-5.6 Luna", "codex"),
        ("Gemini 3.7 Flash", "agy"),
    ]

    privacy = plan.roles["Privacy"]
    assert [(c.value, c.cli) for c in privacy] == [
        ("gemma4-12b-64k:latest", "ollama"),
        ("gemma4-12b-openclaw:latest", "ollama"),
        ("gemma4:12b", "ollama"),
    ]


def test_v4_parser_rejects_wrong_columns() -> None:
    broken = GOVERNOR_BLOCK_V4.replace("| Slot | Modello | Canale | Costo | Motivo |",
                                       "| Slot | Modello | CLI | $ | Motivo |")
    try:
        parse_routing_plan(broken)
    except RoutingContractError:
        return
    raise AssertionError("atteso RoutingContractError per colonne incompatibili")


def test_legacy_table_still_parses() -> None:
    plan = parse_routing_plan(LEGACY_TABLE)
    assert plan.source == "legacy-routing-table"
    assert [c.value for c in plan.roles["L-Arch"]] == [
        "Gemini 3.1 Pro",
        "GPT-5.6 Terra",
        "DeepSeek V4 Pro",
    ]


def test_v4_parser_keeps_cost_cell() -> None:
    plan = parse_routing_plan(GOVERNOR_BLOCK_V4)
    code = plan.roles["L-Code"]
    assert [c.cost for c in code] == ["$0.125", "forfait", "forfait"]
    privacy = plan.roles["Privacy"]
    assert [c.cost for c in privacy] == ["$0", "$0", "$0"]


def test_is_pay_per_use_classification() -> None:
    from routing import is_pay_per_use
    for cell in ("$0.125", "0.15 €/1M", "$ 2.5", "pay as you go", "a consumo", "zen pay-per-use", "5"):
        assert is_pay_per_use(cell), cell
    for cell in ("$0", "€0", "0", "forfait", "flat", "gratis", "free", "incluso", "—", "-", "", None, "n/a"):
        assert not is_pay_per_use(cell), repr(cell)


def test_agy_capability_no_longer_blocked() -> None:
    from routing import seat_capabilities
    caps = seat_capabilities({"gemini": {"cli": "agy", "model": "gemini-3.7-flash-high"}})
    cap = caps["gemini"]
    assert cap.available is True, cap.reason
    assert "stateless" in cap.reason


def test_pay_per_use_confirmation_required_and_blocking(monkeypatch, capsys) -> None:
    from proposal import _confirm_pay_per_use
    seat = {"cli": "opencode", "model": "muse-spark"}
    monkeypatch.setattr("builtins.input", lambda _prompt: "no")
    with pytest.raises(SystemExit):
        _confirm_pay_per_use("muse", seat, "$0.125")
    err = capsys.readouterr().err
    assert "pay-per-use" in err and "spends real money" in err

    monkeypatch.setattr("builtins.input", lambda _prompt: "yes")
    _confirm_pay_per_use("muse", seat, "$0.125")  # no SystemExit


def test_pay_per_use_confirmation_skipped_when_free() -> None:
    from proposal import _confirm_pay_per_use
    _confirm_pay_per_use("muse", {"cli": "opencode", "model": "muse-spark"}, "forfait")  # no prompt, no exit


def test_v4_parser_keeps_cost_cell() -> None:
    plan = parse_routing_plan(GOVERNOR_BLOCK_V4)
    code = plan.roles["L-Code"]
    assert [c.cost for c in code] == ["$0.125", "forfait", "forfait"]
    privacy = plan.roles["Privacy"]
    assert [c.cost for c in privacy] == ["$0", "$0", "$0"]


def test_is_pay_per_use_classification() -> None:
    from routing import is_pay_per_use
    for cell in ("$0.125", "0.15 €/1M", "$ 2.5", "pay as you go", "a consumo", "zen pay-per-use", "5"):
        assert is_pay_per_use(cell), cell
    for cell in ("$0", "€0", "0", "forfait", "flat", "gratis", "free", "incluso", "—", "-", "", None, "n/a"):
        assert not is_pay_per_use(cell), repr(cell)


def test_agy_capability_no_longer_blocked() -> None:
    from routing import seat_capabilities
    caps = seat_capabilities({"gemini": {"cli": "agy", "model": "gemini-3.7-flash-high"}})
    cap = caps["gemini"]
    assert cap.available is True, cap.reason
    assert "stateless" in cap.reason


def test_pay_per_use_confirmation_required_and_blocking(monkeypatch, capsys) -> None:
    from proposal import _confirm_pay_per_use
    seat = {"cli": "opencode", "model": "muse-spark"}
    monkeypatch.setattr("builtins.input", lambda _prompt: "no")
    with pytest.raises(SystemExit):
        _confirm_pay_per_use("muse", seat, "$0.125")
    err = capsys.readouterr().err
    assert "pay-per-use" in err and "spends real money" in err

    monkeypatch.setattr("builtins.input", lambda _prompt: "yes")
    _confirm_pay_per_use("muse", seat, "$0.125")  # no SystemExit


def test_pay_per_use_confirmation_skipped_when_free() -> None:
    from proposal import _confirm_pay_per_use
    _confirm_pay_per_use("muse", {"cli": "opencode", "model": "muse-spark"}, "forfait")  # no prompt, no exit
