"""Pinning dello split bump/schedule/rollback di thirdparty_bump prima del refactor.

Fissa il comportamento attuale (non quello desiderato) con pin finti:
bump_batch su pin finti stabile, schedule finto coerente, rollback
ripristina, firme pubbliche invariate. Solo tmp_path, solo pin finti,
nessuna rete, nessun pin vero.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.thirdparty_bump import apply_plan, bump_batch, collect_plan  # noqa: E402
from nexgen_core import thirdparty_bump as bump  # noqa: E402
from nexgen_core import i18n  # noqa: E402


@pytest.fixture(autouse=True)
def _english_notes():
    """Pin the source language: these tests assert note contents, and the
    machine locale would otherwise turn them Italian."""
    i18n.set_language("en")
    yield
    i18n.set_language(None)


PIN_FINTO = "a" * 40
NUOVO_FINTO = "b" * 40
PIN_ALTRO = "d" * 40
NUOVO_ALTRO = "e" * 40


def _vault(root: Path) -> Path:
    vault = root / "vault"
    skills = vault / "03-INFRA" / "agent-universal-layer" / "skills"
    mcp = vault / "03-INFRA" / "agent-universal-layer" / "mcp"
    skills.mkdir(parents=True)
    mcp.mkdir(parents=True)
    (skills / "skills.manifest.yaml").write_text(
        "# commento finto che deve sopravvivere\n"
        "schema_version: 1\n"
        "skills:\n"
        "  finto-one:\n"
        "    origin: github\n"
        f"    repo: o/r\n    commit: {PIN_FINTO}\n"
        "    targets: [claude]\n"
        "  finto-held:\n"
        "    origin: github\n"
        f"    repo: o/r\n    commit: {PIN_ALTRO}\n"
        "    targets: [claude]\n",
        encoding="utf-8",
    )
    (mcp / "manifest.yaml").write_text(
        "schema_version: 1\nservers: {}\n", encoding="utf-8"
    )
    return vault


def _batch_item() -> dict:
    return {
        "what": "skill 'finto-one' (github o/r)", "pinned": PIN_FINTO,
        "upstream": NUOVO_FINTO, "reasons": ["patch"], "plain": "piano finto",
        "target": {"kind": "git-commit", "skill": "finto-one", "repo": "o/r",
                   "field": "commit", "scope": "."},
    }


def _hold_item() -> dict:
    return {
        "what": "skill 'finto-held' (github o/r)", "pinned": PIN_ALTRO,
        "upstream": NUOVO_ALTRO, "reasons": ["script"], "plain": "fermo finto",
        "target": {"kind": "git-commit", "skill": "finto-held", "repo": "o/r",
                   "field": "commit", "scope": "."},
    }


def _payload() -> dict:
    return {
        "checked_at": 9999999999.0,
        "auto": [],
        "batch": [_batch_item()],
        "hold": [_hold_item()],
    }


def _run_batch(root: Path) -> tuple[int, str]:
    import json

    vault = _vault(root)
    state = root / "state"
    (state / "nexgen").mkdir(parents=True)
    (state / "nexgen" / "third-party-guard.json").write_text(
        json.dumps(_payload()), encoding="utf-8")
    rc = bump_batch(home=root / "home", vault_data=vault, state_dir=state,
                    input_fn=lambda _prompt="": "s", sync=False)
    text = (vault / "03-INFRA" / "agent-universal-layer" / "skills"
            / "skills.manifest.yaml").read_text(encoding="utf-8")
    return rc, text


def test_bump_batch_su_pin_finti_stabile(tmp_path: Path) -> None:
    first_rc, first_text = _run_batch(tmp_path / "uno")
    second_rc, second_text = _run_batch(tmp_path / "due")
    assert first_rc == 0
    assert second_rc == 0
    assert first_text == second_text
    assert NUOVO_FINTO in first_text and PIN_FINTO not in first_text
    assert PIN_ALTRO in first_text and NUOVO_ALTRO not in first_text
    assert "# commento finto che deve sopravvivere" in first_text


def test_schedule_finto_coerente() -> None:
    payload = _payload()
    auto, batch, held = collect_plan(payload)
    assert auto == []
    assert [i["what"] for i in batch] == ["skill 'finto-one' (github o/r)"]
    assert [i["what"] for i in held] == ["skill 'finto-held' (github o/r)"]
    # Deterministico: stesso ingresso, stesso esito.
    assert collect_plan(payload) == (auto, batch, held)
    # Senza target preciso non si solleva nulla: resta held.
    no_target = {"what": "finto 'x'", "pinned": "1", "upstream": "2", "target": None}
    auto2, batch2, held2 = collect_plan({"auto": [no_target], "batch": [], "hold": []})
    assert auto2 == [] and batch2 == [] and len(held2) == 1


def test_rollback_ripristina(tmp_path: Path, monkeypatch) -> None:
    vault = _vault(tmp_path)
    skills_file = (vault / "03-INFRA" / "agent-universal-layer" / "skills"
                   / "skills.manifest.yaml")
    before = skills_file.read_text(encoding="utf-8")
    monkeypatch.setattr(bump, "_rematerialize", lambda *a, **k: ["[ERROR] finto offline"])

    bumps, notes, moved = apply_plan([_batch_item()], vault,
                                     sync=True, home=tmp_path / "home")
    assert bumps == 0 and moved == []
    assert any("rolled back" in n for n in notes)
    assert skills_file.read_text(encoding="utf-8") == before


def test_firme_pubbliche_invariate() -> None:
    assert list(inspect.signature(bump.read_guard_payload).parameters) == ["state_dir"]
    assert list(inspect.signature(bump.refresh_verdicts).parameters) == [
        "home", "vault_data", "state_dir",
    ]
    assert list(inspect.signature(bump.collect_plan).parameters) == ["payload"]
    assert list(inspect.signature(bump.apply_plan).parameters) == [
        "raisable", "vault_data", "sync", "home", "verify",
    ]
    assert list(inspect.signature(bump.bump_batch).parameters) == [
        "home", "vault_data", "state_dir", "input_fn", "sync", "verify",
    ]
    assert list(inspect.signature(bump.auto_apply).parameters) == [
        "payload", "vault_data", "home", "state_dir", "sync",
    ]
    # Nessuna nuova funzione pubblica: solo helper privati aggiunti.
    public = {
        name for name, fn in inspect.getmembers(bump, inspect.isfunction)
        if not name.startswith("_") and fn.__module__ == "nexgen_core.thirdparty_bump"
    }
    assert public == {
        "read_guard_payload",
        "refresh_verdicts",
        "collect_plan",
        "apply_plan",
        "bump_batch",
        "auto_apply",
    }


def test_bump_schedule_rollback_separati_wrapper_delegano() -> None:
    src = Path(bump.__file__).read_text(encoding="utf-8")
    for helper in ("def _schedule_item", "def _write_bump", "def _rollback_bump"):
        assert helper in src
    assert src.index("def _schedule_item") < src.index("def apply_plan")
    assert src.index("def _write_bump") < src.index("def apply_plan")
    assert src.index("def _rollback_bump") < src.index("def apply_plan")
    body = src[src.index("def apply_plan"): src.index("def _revalidate")]
    assert "_schedule_item(" in body
    assert "_write_bump(" in body
    assert "_rollback_bump(" in body
    # I wrapper restano: delegano agli helper, non duplicano la logica.
    assert "def _schedule(" in body
    assert "def _write_phase(" in body
    assert "def _rollback_pins(" in body
    public = {
        name for name, fn in inspect.getmembers(bump, inspect.isfunction)
        if not name.startswith("_") and fn.__module__ == "nexgen_core.thirdparty_bump"
    }
    assert "_schedule_item" not in public
    assert "_write_bump" not in public
    assert "_rollback_bump" not in public
