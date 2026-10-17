"""Pinning dello split run/render di doctor prima del refactor.

Fissa il comportamento attuale (non quello desiderato): run su home
finta ritorna un report con conteggi coerenti, main con --json stampa
JSON valido, check singolo isolato stabile. Solo tmp_path, solo home e
vault finti, nessuna rete, nessuna home vera (KNOWLEDGE_VAULT_REMOTE=local
e PATH su bin vuota per restare offline e deterministici).
"""
from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.doctor import Doctor  # noqa: E402
from nexgen_core.report import Report  # noqa: E402

pytestmark = pytest.mark.filterwarnings("ignore")


def _make_fake_layout(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    home = tmp_path / "home"
    vault = tmp_path / "vault"
    state = tmp_path / "state"
    engine_root = tmp_path / "engine"
    for d in (home, vault, state, engine_root):
        d.mkdir(parents=True, exist_ok=True)
    return home, vault, state, engine_root


def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Solo finti: remoto locale (mai fetch) e PATH senza CLI reali."""
    monkeypatch.setenv("KNOWLEDGE_VAULT_REMOTE", "local")
    monkeypatch.delenv("KNOWLEDGE_VAULT_MIRRORS", raising=False)
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir(exist_ok=True)
    monkeypatch.setenv("PATH", str(empty_bin))


def test_run_on_fake_home_returns_report_with_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)
    home, vault, state, engine_root = _make_fake_layout(tmp_path)

    report = Doctor(
        vault_data=vault, state_dir=state, home=home, engine_root=engine_root
    ).run_diagnostics(apply_remedies=False)

    assert isinstance(report, Report)
    assert report.outcomes, "il report deve contenere esiti"
    total = len(report.outcomes)
    assert (
        report.ok_count
        + len(report.broken)
        + len(report.warnings)
        + len(report.undetermined)
        == total
    )
    ids = {o.id for o in report.outcomes}
    assert "env.state_dir" in ids
    assert "env.vault_path" in ids
    assert report.exit_code() in (0, 1)


def test_main_json_prints_valid_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    _isolate(monkeypatch, tmp_path)
    home, vault, state, engine_root = _make_fake_layout(tmp_path)

    import nexgen_core.doctor as doctor_mod

    real_doctor = doctor_mod.Doctor
    monkeypatch.setattr(
        doctor_mod,
        "Doctor",
        lambda *a, **k: real_doctor(
            vault_data=vault, state_dir=state, home=home, engine_root=engine_root
        ),
    )

    rc = doctor_mod.main(["--json"])
    assert rc in (0, 1)
    data = json.loads(capsys.readouterr().out)
    for key in (
        "healthy",
        "ok_count",
        "broken_count",
        "warning_count",
        "undetermined_count",
        "broken",
        "warnings",
        "undetermined",
        "all",
        "logs",
    ):
        assert key in data, f"chiave JSON mancante: {key}"
    assert (
        data["ok_count"]
        + data["broken_count"]
        + data["warning_count"]
        + data["undetermined_count"]
        == len(data["all"])
    )


def test_single_isolated_check_stable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nexgen_core.checks.env_checks import check_state_dir

    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    first = check_state_dir(state)
    second = check_state_dir(state)
    assert first.id == "env.state_dir"
    assert (first.id, first.severity) == (second.id, second.severity)

    _isolate(monkeypatch, tmp_path)
    home, vault, state2, engine_root = _make_fake_layout(tmp_path)
    kwargs = dict(vault_data=vault, state_dir=state2, home=home, engine_root=engine_root)
    ids_first = {o.id for o in Doctor(**kwargs).run_diagnostics().outcomes}
    ids_second = {o.id for o in Doctor(**kwargs).run_diagnostics().outcomes}
    assert ids_first == ids_second
    assert "env.state_dir" in ids_first


def test_public_signatures_unchanged() -> None:
    import nexgen_core.doctor as doctor_mod

    assert list(inspect.signature(doctor_mod.Doctor.run_diagnostics).parameters) == [
        "self",
        "apply_remedies",
    ]
    assert list(inspect.signature(doctor_mod.main).parameters) == ["argv"]
