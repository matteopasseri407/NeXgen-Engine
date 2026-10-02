"""Routing boundaries: preserve the execution pool, all fallbacks and consent."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

COUNCIL_DIR = Path(__file__).resolve().parents[1] / "council"
sys.path.insert(0, str(COUNCIL_DIR))

from proposal import _confirm_pay_per_use, _seat_cost  # noqa: E402
from routing import RoutingContractError, SeatCapability, parse_routing_plan, resolve_role_candidates  # noqa: E402


def _block(rows, role="L-Code"):
    table = "\n".join(
        f"| {'prescelto' if i == 0 else 'rimpiazzo ' + str(i)} | {model} | {channel} | {cost} | {why} |"
        for i, (model, channel, cost, why) in enumerate(rows)
    )
    return ("### Proposta di routing per ruolo\n"
            f"#### {role} - quality-first\n"
            "| Slot | Modello | Canale | Costo | Motivo |\n"
            "|---|---|---|---|---|\n" + table + "\n<!-- model-routing-governor:end -->\n")


def _seat(model):
    return {"cli": "opencode", "model": model, "routing_label": "Shared Model"}


def test_all_six_fallback_channels_are_preserved():
    channels = ["claude", "codex", "agy", "go", "zen-free", "zen"]
    plan = parse_routing_plan(_block([(f"Model {i}", c, "$0", "test") for i, c in enumerate(channels)]))
    assert len(plan.roles["L-Code"]) == 6
    assert [candidate.channel for candidate in plan.roles["L-Code"]] == channels


def test_same_model_on_different_opencode_pools_remains_distinct():
    plan = parse_routing_plan(_block([
        ("Shared Model", "go", "$0.1", "quota"),
        ("Shared Model", "zen-free", "$0", "free"),
        ("Shared Model", "zen", "$0.2", "cash"),
    ]))
    seats = {"go": _seat("opencode-go/shared"), "free": _seat("opencode/shared-free"),
             "paid": _seat("opencode/shared")}
    caps = {name: SeatCapability(True, "synthetic") for name in seats}
    assert resolve_role_candidates(plan, seats, caps, "L-Code")[0] == ["go", "free", "paid"]
    assert [_seat_cost(plan, name, seat) for name, seat in seats.items()] == ["$0.1", "$0", "$0.2"]


def test_free_candidate_cannot_authorize_paid_seat():
    plan = parse_routing_plan(_block([
        ("Shared Model", "zen-free", "$0", "free"),
        ("Alpha", "claude", "forfait", "test"),
        ("Beta", "codex", "forfait", "test"),
    ]))
    seats = {"paid": _seat("opencode/shared")}
    caps = {"paid": SeatCapability(True, "synthetic")}
    assert resolve_role_candidates(plan, seats, caps, "L-Code")[0] == []
    assert _seat_cost(plan, "paid", seats["paid"]) is None


@pytest.mark.parametrize("count", [1, 2])
def test_privacy_keeps_one_or_two_local_candidates(count):
    rows = [(f"local-{i}:latest", "local", "$0", "private") for i in range(count)]
    rows.append(("—", "zen-free", "—", "escluso: dati personali"))
    plan = parse_routing_plan(_block(rows, "Privacy"))
    assert len(plan.roles["Privacy"]) == count


def test_privacy_rejects_a_cloud_candidate():
    with pytest.raises(RoutingContractError, match="local"):
        parse_routing_plan(_block([("Alpha", "claude", "forfait", "test")], "Privacy"))


def test_unknown_channel_is_not_a_wildcard():
    with pytest.raises(RoutingContractError, match="channel"):
        parse_routing_plan(_block([("Alpha", "unrecognized", "forfait", "test")]))


def test_go_quota_price_does_not_request_a_cash_payment(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: pytest.fail("Go consumes prepaid quota"))
    _confirm_pay_per_use("go", _seat("opencode-go/shared"), "$0.125")


@pytest.mark.parametrize("cost", [None, "$0", "$0.2"])
def test_paid_zen_needs_consent_even_without_a_routing_price(monkeypatch, cost):
    monkeypatch.setattr("builtins.input", lambda _: "no")
    with pytest.raises(SystemExit, match="not confirmed"):
        _confirm_pay_per_use("paid", _seat("opencode/shared"), cost)


def test_named_sequence_survives_config_loading(tmp_path):
    from nexgen_core.config import load_council_config
    from relay import _load_relay_sequence
    from types import SimpleNamespace

    path = tmp_path / "seats.yaml"
    path.write_text("schema_version: 1\nseats:\n  local:\n    cli: ollama\n    model: local:latest\n    vendor: local\n"
                    "sequences:\n  review:\n    - role: reviewer\n      seat: local\n", encoding="utf-8")
    config = load_council_config(path)
    stages = _load_relay_sequence(SimpleNamespace(sequence="review", max_seats=5), config, config["seats"])
    assert [(stage.role, stage.candidates) for stage in stages] == [("reviewer", ["local"])]


def test_paid_consult_collects_all_consent_before_any_worker_starts(tmp_path, monkeypatch):
    import threading
    from consult import run_consult

    seats = {"flat": {"cli": "ollama", "model": "local:latest"},
             "paid": _seat("opencode/shared")}
    called = []

    def refuse(_):
        assert threading.current_thread() is threading.main_thread()
        return "no"

    monkeypatch.setattr("builtins.input", refuse)
    with pytest.raises(SystemExit, match="not confirmed"):
        run_consult(seats, "synthetic", ["flat", "paid"], tmp_path, None, 0,
                    runner=lambda *args: called.append(args))
    assert called == []


def test_paid_relay_requires_consent_without_governor(tmp_path, monkeypatch):
    import relay
    from relay import RelayStage

    monkeypatch.setattr("builtins.input", lambda _: "no")
    monkeypatch.setattr(relay, "run_seat", lambda *args: pytest.fail("refused seat must never spawn"))
    with pytest.raises(SystemExit, match="not confirmed"):
        relay._invoke_stage_candidate(1, RelayStage("reviewer", ["paid"]), "paid",
                                     {"paid": _seat("opencode/shared")}, tmp_path, "synthetic", [], None)


def test_payment_confirmation_without_stdin_refuses(monkeypatch):
    def eof(_):
        raise EOFError
    monkeypatch.setattr("builtins.input", eof)
    with pytest.raises(SystemExit, match="not confirmed"):
        _confirm_pay_per_use("paid", _seat("opencode/shared"), None)


@pytest.mark.parametrize("rows", [
    [("Shared Model", "go", "$0", "test"), ("Shared Model", "go", "$0", "duplicate")],
    [("Alpha", "claude", "forfait", "test"), ("—", "codex", "forfait", "missing")],
])
def test_invalid_extra_candidates_fail_closed(rows):
    with pytest.raises(RoutingContractError):
        parse_routing_plan(_block(rows))


def test_legacy_model_is_ambiguous_across_same_cli_pools():
    plan = parse_routing_plan("### Ranking per ruoli reali\n"
                              "| Ruolo | Primario | Fallback 1 | Fallback 2 |\n|---|---|---|---|\n"
                              "| L-Code | Shared Model | Alpha | Beta |\n### Motivazioni concise\n")
    seats = {"go": _seat("opencode-go/shared"), "paid": _seat("opencode/shared")}
    caps = {name: SeatCapability(True, "synthetic") for name in seats}
    selected, reasons = resolve_role_candidates(plan, seats, caps, "L-Code")
    assert selected == []
    assert any("ambiguous" in reason for reason in reasons)


def test_tables_in_governor_notes_are_not_role_candidates():
    block = _block([("local:latest", "local", "$0", "private")], "Privacy")
    block = block.replace("<!-- model-routing-governor:end -->",
                          "### Benchmark notes\n| Metric | Score |\n|---|---|\n| test | 100 |\n"
                          "<!-- model-routing-governor:end -->")
    plan = parse_routing_plan(block)
    assert [candidate.value for candidate in plan.roles["Privacy"]] == ["local:latest"]


def test_explicit_codex_model_can_differ_from_default(tmp_path, monkeypatch):
    import json
    from routing import _probe_codex_inventory

    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    (tmp_path / "config.toml").write_text('model = "default"\nmodel_reasoning_effort = "high"\n')
    (tmp_path / "models_cache.json").write_text(json.dumps({"models": [
        {"slug": "other", "visibility": "list", "supported_reasoning_levels": [{"effort": "max"}]}
    ]}))
    assert _probe_codex_inventory({"model": "other", "reasoning_effort": "max"}).available
    assert not _probe_codex_inventory({"model": "other", "reasoning_effort": "ultra"}).available
