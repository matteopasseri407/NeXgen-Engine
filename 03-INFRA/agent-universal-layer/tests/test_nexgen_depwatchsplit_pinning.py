"""Pinning dello split collect/pin/run di depwatch prima del refactor.

Fissa il comportamento attuale (non quello desiderato) con pin finti:
collect su pin finti ritorna elenco atteso, pin invariato se gia
aggiornato, run completo su repo finto stabile. Solo tmp_path, solo
resolver finti, nessuna rete, nessuna modifica fuori tmp.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import yaml

INFRA = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(INFRA / "scripts"))

from nexgen_core import depwatch  # noqa: E402

FAKE_COMMIT = "abc123def456abc123def456abc123def456abc12345"
FAKE_SKILLS = {
    "demo-github": {"origin": "github", "repo": "acme/demo", "commit": FAKE_COMMIT},
    "demo-npm": {"origin": "installer", "install": ["npx", "-y", "demo-pkg@1.2.3"]},
}
FAKE_MCP = {
    "demo-mcp": {"command": "npx", "args": ["-y", "demo-mcp-pkg@4.5.6"], "targets": ["claude"]},
}


def _machine(tmp_path: Path, monkeypatch) -> dict[str, Path]:
    vault = tmp_path / "vault"
    engine = tmp_path / "engine"
    state = tmp_path / "state"
    home = tmp_path / "home"
    layer = vault / "03-INFRA" / "agent-universal-layer"
    (layer / "mcp").mkdir(parents=True)
    (layer / "skills").mkdir(parents=True)
    engine.mkdir(parents=True, exist_ok=True)
    (state / "nexgen").mkdir(parents=True)
    home.mkdir(exist_ok=True)
    (layer / "mcp" / "manifest.yaml").write_text(
        yaml.safe_dump({"servers": FAKE_MCP}), encoding="utf-8"
    )
    (layer / "skills" / "skills.manifest.yaml").write_text(
        yaml.safe_dump({"schema_version": 1, "skills": FAKE_SKILLS}), encoding="utf-8"
    )
    monkeypatch.setenv("NEXGEN_HOME", str(home))
    monkeypatch.setenv("AGENT_STATE_DIR", str(state))
    monkeypatch.setenv("AGENT_VAULT_DATA", str(vault))
    monkeypatch.setenv("AGENT_ENGINE_ROOT", str(engine))
    return {"vault": vault, "engine": engine, "state": state, "home": home}


def _stable_resolvers():
    return {
        "git_ls_remote": lambda repo: FAKE_COMMIT if repo == "acme/demo" else None,
        "npm_latest_version": lambda pkg: {"demo-pkg": "1.2.3", "demo-mcp-pkg": "4.5.6"}.get(pkg),
        "npm_deprecation": lambda pkg, ver: None,
        "git_latest_tag": lambda repo: None,
    }


def test_collect_su_pin_finti_ritorna_elenco_atteso() -> None:
    skill_pins = depwatch._collect_skill_pins(FAKE_SKILLS)
    assert len(skill_pins) == 2
    by_label = {label: (kind, pin, key) for label, kind, pin, key in skill_pins}
    assert by_label["skill 'demo-github' (github acme/demo)"] == ("git-commit", FAKE_COMMIT, "acme/demo")
    assert by_label["skill 'demo-npm' (npm demo-pkg)"] == ("npm-version", "1.2.3", "demo-pkg")
    mcp_pins = depwatch._collect_mcp_pins(FAKE_MCP)
    assert mcp_pins == [("MCP server 'demo-mcp' (npm demo-mcp-pkg)", "npm-version", "4.5.6", "demo-mcp-pkg")]


def test_pin_invariato_se_gia_aggiornato(tmp_path, monkeypatch) -> None:
    m = _machine(tmp_path, monkeypatch)
    assert depwatch._is_stale("npm-version", "1.2.3", "1.2.3") is False
    assert depwatch._is_stale("git-commit", FAKE_COMMIT, FAKE_COMMIT) is False
    result = depwatch.run_depwatch(vault_data=m["vault"], state_dir=m["state"], **_stable_resolvers())
    assert len(result.findings) == 3
    assert all(f.stale is False for f in result.findings)
    assert all(f.upstream is not None for f in result.findings)


def test_run_completo_su_repo_finto_stabile(tmp_path, monkeypatch) -> None:
    m = _machine(tmp_path, monkeypatch)
    result = depwatch.run_depwatch(vault_data=m["vault"], state_dir=m["state"], **_stable_resolvers())
    assert result.wrote is True
    assert result.report_path == m["state"] / depwatch.REPORT_FILE_NAME
    assert result.report_path.is_file()
    report = result.report_path.read_text(encoding="utf-8")
    assert "## Up to date" in report
    assert "## Moved upstream" not in report
    sidecar = m["state"] / "nexgen" / depwatch.STATUS_FILE_NAME
    assert sidecar.is_file()


def test_public_signatures_unchanged() -> None:
    assert list(inspect.signature(depwatch.run_depwatch).parameters) == [
        "vault_data",
        "state_dir",
        "git_ls_remote",
        "npm_latest_version",
        "npm_deprecation",
        "git_latest_tag",
    ]


def test_collect_pin_run_helpers_in_sequence() -> None:
    src = Path(depwatch.__file__).read_text(encoding="utf-8")
    assert "def _collect_pins" in src
    assert "def _pin_one" in src
    assert "def _run_depwatch" in src
    assert src.index("def _collect_pins") < src.index("def _pin_one") < src.index("def _run_depwatch")
    run_pos = src.index("def run_depwatch")
    body = src[run_pos:]
    assert "_collect_pins(" in body
    assert "_pin_one(" in body
    assert "_run_depwatch(" in body
    assert body.index("_collect_pins(") < body.index("_pin_one(") < body.index("_run_depwatch(")
