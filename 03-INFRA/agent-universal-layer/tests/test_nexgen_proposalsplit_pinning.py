"""Pin current proposal-split behavior with synthetic routing only.

Fissa il comportamento attuale (non quello desiderato): sedia fuori ruolo
rifiutata con motivo, sedia a pagamento senza conferma rifiutata, proposta
valida con righe per ruolo, due esecuzioni stabili. Nessuna CLI reale,
nessuna rete, nessuna HOME reale.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pytest

COUNCIL_DIR = Path(__file__).resolve().parents[1] / "council"
if str(COUNCIL_DIR) not in sys.path:
    sys.path.insert(0, str(COUNCIL_DIR))

ROLE_BLOCK = """### Proposta di routing per ruolo

#### L-Test - ratio

Test role.

| Slot | Modello | Canale | Costo | Motivo |
|---|---|---|---:|---|
| prescelto | Alpha Model | claude | forfait | best |
| rimpiazzo 1 | Beta Model | codex | forfait | runner-up |
| rimpiazzo 2 | Paid Model | zen | $0.125 | paid |

#### L-Second - ratio

Second role.

| Slot | Modello | Canale | Costo | Motivo |
|---|---|---|---:|---|
| prescelto | Gamma Model | codex | forfait | best |
| rimpiazzo 1 | Delta Model | claude | forfait | runner-up |
| rimpiazzo 2 | Epsilon Model | agy | forfait | third |

<!-- model-routing-governor:end -->
"""


def _role_config(tmp_path, monkeypatch):
    import proposal
    from routing import SeatCapability

    vault = tmp_path / "vault"
    vault.mkdir(exist_ok=True)
    (vault / "crm.md").write_text(ROLE_BLOCK, encoding="utf-8")
    monkeypatch.setenv("AGENT_VAULT_DATA", str(vault))
    monkeypatch.delenv("KNOWLEDGE_VAULT_PATH", raising=False)
    seats = {
        "seat-a": {"cli": "claude", "vendor": "v", "model": "m-a",
                   "routing_label": "Alpha Model"},
        "seat-b": {"cli": "codex", "vendor": "v", "model": "m-b",
                   "routing_label": "Beta Model"},
        "seat-paid": {"cli": "opencode", "vendor": "v", "model": "opencode/paid-model",
                      "routing_label": "Paid Model"},
        "seat-c": {"cli": "ollama", "vendor": "v", "model": "m-c",
                   "routing_label": "Zeta Model"},
        "seat-g": {"cli": "codex", "vendor": "v", "model": "m-g",
                   "routing_label": "Gamma Model"},
    }
    config = {"seats": seats,
              "routing": {"enabled": True, "decision_file": "crm.md"}}
    monkeypatch.setattr(
        proposal, "seat_capabilities",
        lambda names: {n: SeatCapability(True, "test") for n in names},
    )
    args = argparse.Namespace(seat=None, mode="challenge", routing_role="L-Test")
    return proposal, config, seats, args


def test_out_of_role_seat_refused_with_reason(tmp_path, monkeypatch):
    proposal, config, seats, args = _role_config(tmp_path, monkeypatch)
    args.seat = "seat-c"
    with pytest.raises(SystemExit) as exc:
        proposal._check_seat_allowed("seat-c", seats["seat-c"], args, config=config)
    text = str(exc.value)
    assert "not a candidate for role 'L-Test'" in text
    assert "Eligible:" in text


def test_paid_seat_without_confirmation_refused(tmp_path, monkeypatch):
    proposal, config, seats, args = _role_config(tmp_path, monkeypatch)
    args.seat = "seat-paid"
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    with pytest.raises(SystemExit) as exc:
        proposal._check_seat_allowed("seat-paid", seats["seat-paid"], args, config=config)
    assert "not confirmed" in str(exc.value)


def test_valid_proposal_contains_lines_per_role(tmp_path, monkeypatch, capsys):
    from routing import SeatCapability

    proposal, config, seats, args = _role_config(tmp_path, monkeypatch)
    plan = proposal._routing_context_or_exit(config)
    caps = {name: SeatCapability(True, "test") for name in seats}
    for role in ("L-Test", "L-Second"):
        lines, has_candidates = proposal._proposal_lines_for_role(plan, seats, caps, role)
        assert has_candidates
        assert lines[0] == f"  {role}:"
        assert any("seat-" in line or "Model" in line for line in lines[1:])
    ok = proposal._print_routing_proposal(args, config, seats, ["L-Test", "L-Second"], title="T")
    assert ok
    out = capsys.readouterr().out
    assert "L-Test:" in out
    assert "L-Second:" in out


def test_two_runs_are_stable(tmp_path, monkeypatch, capsys):
    from routing import SeatCapability

    proposal, config, seats, _ = _role_config(tmp_path, monkeypatch)
    plan = proposal._routing_context_or_exit(config)
    caps = {name: SeatCapability(True, "test") for name in seats}
    first_lines, first_ok = proposal._proposal_lines_for_role(plan, seats, caps, "L-Test")
    second_lines, second_ok = proposal._proposal_lines_for_role(plan, seats, caps, "L-Test")
    assert first_lines == second_lines
    assert first_ok == second_ok is True
    args = argparse.Namespace(seat=None, mode="challenge", routing_role="L-Test")
    proposal._print_routing_proposal(args, config, seats, ["L-Test"], title="T")
    first_out = capsys.readouterr().out
    proposal._print_routing_proposal(args, config, seats, ["L-Test"], title="T")
    second_out = capsys.readouterr().out
    assert first_out == second_out
    assert "L-Test:" in first_out
