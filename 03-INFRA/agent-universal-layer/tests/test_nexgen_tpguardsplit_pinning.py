"""Pinning dello split regola/giudizio di thirdparty_guard prima del refactor.

Fissa il comportamento attuale (non quello desiderato) con pin e finding
finti: run_guardian ok su finding finti stabili, judge_finding coerente
su instradamento puro, firme pubbliche invariate. Solo tmp_path, solo
pin finti, nessuna rete, nessun pin vero.
"""
from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.depwatch import PinFinding  # noqa: E402
from nexgen_core import thirdparty_guard as guard  # noqa: E402


def _fake_stale_npm_patch() -> PinFinding:
    return PinFinding(
        kind="npm-version",
        what="MCP server 'finto-demo' (npm finto-demo-pkg)",
        pinned="9.9.1",
        upstream="9.9.2",
        stale=True,
    )


def _fake_stable_npm() -> PinFinding:
    return PinFinding(
        kind="npm-version",
        what="MCP server 'finto-demo' (npm finto-demo-pkg)",
        pinned="9.9.2",
        upstream="9.9.2",
        stale=False,
    )


def test_run_guardian_ok_su_finding_finti_stabili(tmp_path: Path) -> None:
    state = tmp_path / "state"
    findings = [_fake_stale_npm_patch(), _fake_stable_npm()]
    first = guard.run_guardian(findings, state)
    assert first["ok"] is True
    assert first == {"ok": True, "auto": 0, "batch": 1, "hold": 0}
    sidecar = state / "nexgen" / "third-party-guard.json"
    assert sidecar.is_file()
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    assert set(payload) == {"checked_at", "auto", "batch", "hold"}
    assert len(payload["batch"]) == 1
    assert payload["batch"][0]["what"] == findings[0].what
    # Secondo giro stabile: stesso esito, nessun tocco fuori tmp_path.
    second = guard.run_guardian(findings, state)
    assert second == first


def test_judge_finding_coerente_su_finti() -> None:
    # Non stale: HOLD, niente di nuovo.
    current = guard.judge_finding(_fake_stable_npm())
    assert current.verdict == guard.HOLD
    assert any("nothing moved" in r for r in current.reasons)

    # Modulo: HOLD con messaggio componente, mai riscritto da qui.
    module = PinFinding(
        kind="npm-version",
        what="module finto-demo",
        pinned="9.9.1",
        upstream="9.9.2",
        stale=True,
    )
    routed = guard.judge_finding(module)
    assert routed.verdict == guard.HOLD
    assert any("component" in r for r in routed.reasons)

    # npm finto: patch batcha, major trattiene, deterministico.
    patch = guard.judge_finding(_fake_stale_npm_patch())
    assert patch.verdict == guard.BATCH
    assert guard.judge_finding(_fake_stale_npm_patch()).verdict == patch.verdict
    major = PinFinding(
        kind="npm-version",
        what="MCP server 'finto-demo' (npm finto-demo-pkg)",
        pinned="9.9.1",
        upstream="10.0.0",
        stale=True,
    )
    assert guard.judge_finding(major).verdict == guard.HOLD

    # git-commit senza chiave repo: HOLD senza clonare (nessuna rete).
    no_key = PinFinding(
        kind="git-commit",
        what="skill 'finto-demo'",
        pinned="a" * 40,
        upstream="b" * 40,
        stale=True,
    )
    assert guard.judge_finding(no_key).verdict == guard.HOLD

    # Tipo ignoto: HOLD.
    unknown = PinFinding(
        kind="finto-tipo",
        what="finto 'x'",
        pinned="1",
        upstream="2",
        stale=True,
    )
    held = guard.judge_finding(unknown)
    assert held.verdict == guard.HOLD
    assert any("unknown pin kind" in r for r in held.reasons)


def test_judge_npm_su_finti_patch_batch_minor_hold() -> None:
    assert guard.judge_npm_package("finto 'x'", "9.9.1", "9.9.2").verdict == guard.BATCH
    assert guard.judge_npm_package("finto 'x'", "9.9.1", "9.10.0").verdict == guard.HOLD


def test_firme_pubbliche_invariate() -> None:
    assert guard.AUTO == "auto"
    assert guard.BATCH == "batch"
    assert guard.HOLD == "hold"
    assert list(inspect.signature(guard.judge_github_skill).parameters) == [
        "what",
        "repo",
        "pinned",
        "head",
        "scope",
    ]
    assert list(inspect.signature(guard.judge_npm_package).parameters) == [
        "what",
        "pinned",
        "latest",
    ]
    assert list(inspect.signature(guard.judge_finding).parameters) == [
        "finding",
        "skill_scopes",
    ]
    assert list(inspect.signature(guard.run_guardian).parameters) == [
        "findings",
        "state_dir",
        "skill_scopes",
    ]


def test_regola_giudizio_separati_wrapper_delegano() -> None:
    src = Path(guard.__file__).read_text(encoding="utf-8")
    assert "def _npm_jump_rule" in src
    assert "def _finding_route" in src
    assert src.index("def _npm_jump_rule") < src.index("def judge_npm_package")
    assert src.index("def _finding_route") < src.index("def judge_finding")
    npm_pos = src.index("def judge_npm_package")
    npm_body = src[npm_pos: src.index("def judge_finding")]
    assert "_npm_jump_rule(" in npm_body
    finding_pos = src.index("def judge_finding")
    finding_body = src[finding_pos: src.index("def _target_of_pin")]
    assert "_finding_route(" in finding_body
    # Nessuna nuova funzione pubblica: solo helper privati aggiunti.
    public = [
        name
        for name in dir(guard)
        if not name.startswith("_") and callable(getattr(guard, name))
    ]
    assert set(public) >= {
        "judge_github_skill",
        "judge_npm_package",
        "judge_finding",
        "run_guardian",
    }
    assert "_npm_jump_rule" not in public
    assert "_finding_route" not in public
