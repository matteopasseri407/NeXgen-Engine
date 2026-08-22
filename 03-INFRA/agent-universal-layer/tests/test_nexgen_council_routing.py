"""Unit test per il parser del blocco governato v4 nel council (routing.py).

Il blocco governato v4 usa colonne `Slot|Modello|Canale|Costo|Motivo` e slot
`prescelto/rimpiazzo N`: il parser deve estrarre i primi tre candidati con la
CLI derivata dal canale. Un blocco v3 (legacy) deve continuare a passare dal
parser legacy.
"""
from __future__ import annotations

import sys
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
