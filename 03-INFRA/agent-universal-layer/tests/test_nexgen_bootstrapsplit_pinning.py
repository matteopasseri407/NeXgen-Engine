"""Pinning dello split bootstrap prima del refactor.

Fissa il comportamento attuale (non quello desiderato): wire_absorbed_secrets
su env finto e stabile, main --check (il dry-run di bootstrap: scrive nulla)
non scrive fuori tmp, firme pubbliche invariate. Solo tmp_path, solo home e
vault finti, solo valori sintetici, mai home vera, mai segreti veri.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import bootstrap as bootstrap_mod  # noqa: E402
from nexgen_core import bootstrap_secrets  # noqa: E402


def _isolate_fake_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Solo finte: home, config e PATH dentro tmp_path, mai la home vera."""
    fake_home = tmp_path / "home-finta"
    fake_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("NEXGEN_HOME", str(fake_home))
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(fake_home / ".config"))
    monkeypatch.setenv("APPDATA", str(fake_home / "AppData" / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(fake_home / "AppData" / "Local"))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir(exist_ok=True)
    monkeypatch.setenv("PATH", str(empty_bin))
    return fake_home


def test_wire_absorbed_secrets_su_env_finto_stabile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    _isolate_fake_home(monkeypatch, tmp_path)
    vault = tmp_path / "vault-finto"
    vault.mkdir()
    fake_script = tmp_path / "nexgen-secrets.py"
    fake_script.write_text("# finto\n", encoding="utf-8")
    monkeypatch.setattr(bootstrap_secrets, "_store_script", lambda root: fake_script)

    def _names(script: Path):
        assert script == fake_script
        return True, {"SYNTH_SHARED_OLD"}, set(), 1

    calls = {"materialize": 0}

    def _materialize(script: Path) -> bool:
        assert script == fake_script
        calls["materialize"] += 1
        return True

    def _must_not_set(script: Path, name: str, value: str, *, host_only: bool) -> bool:
        raise AssertionError("nessuna scrittura attesa: tutto e gia memorizzato")

    monkeypatch.setattr(bootstrap_secrets, "_store_names", _names)
    monkeypatch.setattr(bootstrap_secrets, "_store_materialize", _materialize)
    monkeypatch.setattr(bootstrap_secrets, "_store_set", _must_not_set)

    refs = {"SYNTH_SHARED_OLD": ["srv-finto"]}
    bootstrap_mod.wire_absorbed_secrets(vault, refs, [])
    out1 = capsys.readouterr().out
    assert "already stored" in out1 or "materialized" in out1
    assert calls["materialize"] == 1
    assert "sk-live" not in out1 and "ghp_" not in out1

    bootstrap_mod.wire_absorbed_secrets(vault, refs, [])
    out2 = capsys.readouterr().out
    assert out1 == out2
    assert calls["materialize"] == 2
    assert list(vault.iterdir()) == []


def test_wire_senza_store_non_scrive_e_avvisa(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    _isolate_fake_home(monkeypatch, tmp_path)
    vault = tmp_path / "vault-senza-store"
    vault.mkdir()
    before = {p.relative_to(tmp_path) for p in tmp_path.rglob("*")}
    bootstrap_mod.wire_absorbed_secrets(vault, {}, ["srv-finto-masked"])
    out = capsys.readouterr().out
    assert "srv-finto-masked" in out
    assert "manual fix" in out
    after = {p.relative_to(tmp_path) for p in tmp_path.rglob("*")}
    assert before == after


def test_main_check_non_scrive_fuori_tmp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    _isolate_fake_home(monkeypatch, tmp_path)
    vault = tmp_path / "vault-check"
    vault.mkdir()

    def _must_not_run(*args, **kwargs):
        raise AssertionError("main --check non deve scrivere ne allineare")

    for name in (
        "write_user_profile",
        "write_remotes",
        "seed_skill_manifest",
        "commit_setup",
        "align_now",
        "remaining_problems",
        "install_launchers",
    ):
        monkeypatch.setattr(bootstrap_mod, name, _must_not_run)

    before = {p.relative_to(tmp_path) for p in tmp_path.rglob("*")}
    rc1 = bootstrap_mod.main(["--check", "--root", str(vault)])
    out1 = capsys.readouterr().out
    after1 = {p.relative_to(tmp_path) for p in tmp_path.rglob("*")}
    assert rc1 == 1
    assert before == after1
    assert "first run" in out1.lower() or "prerequis" in out1.lower() or "vault" in out1.lower()

    rc2 = bootstrap_mod.main(["--check", "--root", str(vault)])
    capsys.readouterr()
    after2 = {p.relative_to(tmp_path) for p in tmp_path.rglob("*")}
    assert rc2 == rc1
    assert after1 == after2


def test_firme_pubbliche_invariate() -> None:
    # `preflight` guadagna `check` con un default: le chiamate a zero argomenti
    # restano valide, e il resto delle firme non si muove.
    assert list(inspect.signature(bootstrap_mod.wire_absorbed_secrets).parameters) == [
        "root",
        "refs",
        "masked",
    ]
    assert list(inspect.signature(bootstrap_mod.main).parameters) == ["argv"]
    assert list(inspect.signature(bootstrap_mod.preflight).parameters) == ["check"]
    assert list(inspect.signature(bootstrap_mod.scaffold).parameters) == ["root", "write"]
    assert list(inspect.signature(bootstrap_mod.detect_clis).parameters) == ["home"]
    assert list(inspect.signature(bootstrap_mod.desktop_app_orphans).parameters) == [
        "root",
        "home",
    ]
    assert list(inspect.signature(bootstrap_mod.print_desktop_stubs).parameters) == [
        "names",
        "home",
    ]
    assert list(inspect.signature(bootstrap_mod.render).parameters) == [
        "findings",
        "title",
        "stream",
    ]
