"""Il doctor deve vedere una config V2 servita a una CLI che parla V1.

Issue 142 (2026-10-28): l'engine scrive `permissions` (dialetto V2) e su una
macchina con `opencode-ai@1.19.3` su PATH ogni invocazione moriva al parse
della config, mentre il doctor usciva tutto verde. La scrittura ora passa da
`nexgen_core.opencode_compat`; qui si fissa la regola complementare: cio' che
sta su disco deve essere leggibile dal binario che lo legge.

Si asserisce la REGOLA (quale severita' e perche'), mai il testo esatto.
"""
from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import opencode_compat  # noqa: E402
from nexgen_core.checks.opencode_checks import check_opencode_config_dialect  # noqa: E402
from nexgen_core.paths import opencode_config_path  # noqa: E402
from nexgen_core.report import Severity  # noqa: E402

pytestmark = pytest.mark.filterwarnings("ignore")


def _stub_binary(directory: Path, version: str | None) -> None:
    """An `opencode` on PATH that answers `--version` with `version`.

    `version=None` writes a binary that prints junk: the case where the major
    cannot be read, which `opencode_compat` treats as V1 on purpose.
    """
    directory.mkdir(parents=True, exist_ok=True)
    printed = version if version is not None else "unknown build"
    if sys.platform == "win32":
        (directory / "opencode.cmd").write_text(f"@echo off\r\necho {printed}\r\n", encoding="utf-8")
        return
    stub = directory / "opencode"
    stub.write_text(f"#!/bin/sh\necho {printed}\n", encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)


def _isolate(monkeypatch: pytest.MonkeyPatch, bin_dir: Path) -> None:
    monkeypatch.setenv("PATH", str(bin_dir))
    opencode_compat._probe.cache_clear()  # noqa: SLF001 - the cache is what the check reads


def _write_config(home: Path, data: dict) -> Path:
    path = opencode_config_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


_V2_CONFIG = {
    "$schema": "https://opencode.ai/config.json",
    "permissions": [{"action": "shell", "resource": "*", "effect": "ask"}],
}
_V1_CONFIG = {"permission": {"bash": "ask", "edit": "allow"}}


def test_v2_config_behind_a_v1_binary_is_broken(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Il caso della issue: la CLI non parte, quindi il doctor non puo' tacere."""
    _stub_binary(tmp_path / "bin", "1.19.3")
    _isolate(monkeypatch, tmp_path / "bin")
    home = tmp_path / "home"
    _write_config(home, _V2_CONFIG)

    outcome = check_opencode_config_dialect(home)

    assert outcome is not None
    assert outcome.severity is Severity.BROKEN
    assert outcome.action, "un guasto senza azione non e' un guasto riparabile"
    assert "1.19.3" in (outcome.message + (outcome.detail or "")), "la versione va detta: e' la diagnosi"


def test_v2_config_behind_a_v2_binary_is_aligned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_binary(tmp_path / "bin", "2.2.0")
    _isolate(monkeypatch, tmp_path / "bin")
    home = tmp_path / "home"
    _write_config(home, _V2_CONFIG)

    outcome = check_opencode_config_dialect(home)

    assert outcome is not None
    assert outcome.severity is Severity.OK


def test_v1_config_behind_a_v1_binary_is_silent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Il dialetto vecchio e' leggibile: niente da segnalare, non e' un guasto."""
    _stub_binary(tmp_path / "bin", "1.19.3")
    _isolate(monkeypatch, tmp_path / "bin")
    home = tmp_path / "home"
    _write_config(home, _V1_CONFIG)

    assert check_opencode_config_dialect(home) is None


def test_no_config_is_silent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Un OpenCode mai avviato non e' un guasto."""
    _stub_binary(tmp_path / "bin", "2.2.0")
    _isolate(monkeypatch, tmp_path / "bin")

    assert check_opencode_config_dialect(tmp_path / "home") is None


def test_v2_config_without_any_binary_warns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """La CLI desktop che non sta su PATH: config V2, nessun binario da interrogare."""
    empty = tmp_path / "bin"
    empty.mkdir()
    _isolate(monkeypatch, empty)
    home = tmp_path / "home"
    _write_config(home, _V2_CONFIG)

    outcome = check_opencode_config_dialect(home)

    assert outcome is not None
    assert outcome.severity is Severity.WARN


def test_unknown_version_counts_as_v1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`opencode_compat` tratta l'ignoto come V1: la config V2 resta illegibile."""
    _stub_binary(tmp_path / "bin", None)
    _isolate(monkeypatch, tmp_path / "bin")
    home = tmp_path / "home"
    _write_config(home, _V2_CONFIG)

    outcome = check_opencode_config_dialect(home)

    assert outcome is not None
    assert outcome.severity is Severity.BROKEN
