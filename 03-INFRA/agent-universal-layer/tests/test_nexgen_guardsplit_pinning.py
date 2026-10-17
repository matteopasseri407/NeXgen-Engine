"""Pinning dello split align/permissions/run di guard prima del refactor.

Fissa il comportamento attuale (non quello desiderato): run su env finto
ritorna esito atteso, align_instructions stabile, apply_runtime_permissions
stabile, firme pubbliche invariate. Solo tmp_path, solo home e vault finti,
nessuna rete, nessuna scrittura fuori tmp.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.guard import GuardMode, GuardResult, GuardRunner  # noqa: E402
import nexgen_core.guard as guard_module  # noqa: E402

PHASES = ("skills", "mcp", "permissions", "instructions", "launchers", "scheduler", "modules", "runtime")


def _fake_runner(tmp_path: Path, monkeypatch) -> tuple[GuardRunner, list[str]]:
    """Runner finto: home e vault solo in tmp, fasi solo record, mai rete."""
    vault = tmp_path / "vault"
    home = tmp_path / "home"
    vault.mkdir(parents=True, exist_ok=True)
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("NEXGEN_DISABLE_HOST_MUTATIONS", "1")
    monkeypatch.delenv("NEXGEN_HOME", raising=False)
    monkeypatch.delenv("AGENT_ENGINE_ROOT", raising=False)
    monkeypatch.delenv("AGENT_VAULT_DATA", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("APPDATA", raising=False)
    runner = GuardRunner(vault_data=vault, home=home)
    assert str(runner.home).startswith(str(tmp_path))
    assert str(runner.vault_data).startswith(str(tmp_path))
    monkeypatch.setattr(runner, "_phase_git", lambda *_a: None)
    monkeypatch.setattr(runner, "_phase_preflight", lambda *_a: None)
    ran: list[str] = []
    for name in PHASES:
        monkeypatch.setattr(runner, f"_phase_{name}", lambda *_a, _n=name: ran.append(_n))
    return runner, ran


def test_run_su_env_finto_ritorna_esito_atteso(tmp_path, monkeypatch) -> None:
    runner, ran = _fake_runner(tmp_path, monkeypatch)
    result = runner.run(GuardMode.APPLY)
    assert isinstance(result, GuardResult)
    assert result.success is True
    assert result.exit_code == 0
    assert result.mode == GuardMode.APPLY
    assert ran == list(PHASES)
    assert isinstance(result.actions_taken, list)


def test_align_instructions_stabile(tmp_path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    canon = vault / "03-INFRA" / "agent-universal-layer" / "instructions" / "AGENTS.md"
    canon.parent.mkdir(parents=True, exist_ok=True)
    canon.write_text("# canon finto\n", encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("NEXGEN_DISABLE_HOST_MUTATIONS", "1")
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("APPDATA", raising=False)
    runner = GuardRunner(vault_data=vault, home=home)
    first = runner.align_instructions()
    assert isinstance(first, list) and len(first) > 0
    claude_md = home / "CLAUDE.md"
    assert claude_md.is_file()
    assert str(claude_md).startswith(str(tmp_path))
    content_after_first = claude_md.read_text(encoding="utf-8")
    assert content_after_first.startswith("# Claude compatibility pointer")
    second = runner.align_instructions()
    assert second == []
    assert claude_md.read_text(encoding="utf-8") == content_after_first
    # Tutti i target restano dentro tmp.
    for target in (
        home / ".codex" / "AGENTS.md",
        home / ".gemini" / "config" / "AGENTS.md",
    ):
        assert str(target).startswith(str(tmp_path))


def test_apply_runtime_permissions_stabile(tmp_path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    vault.mkdir(parents=True, exist_ok=True)
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("NEXGEN_DISABLE_HOST_MUTATIONS", "1")
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("APPDATA", raising=False)
    runner = GuardRunner(vault_data=vault, home=home)
    first = runner.apply_runtime_permissions()
    second = runner.apply_runtime_permissions()
    assert first == second
    assert isinstance(first, list)


def test_firme_pubbliche_invariate() -> None:
    assert list(inspect.signature(GuardRunner.__init__).parameters) == [
        "self", "vault_data", "engine_root", "home",
    ]
    assert list(inspect.signature(GuardRunner.preflight).parameters) == ["self"]
    assert list(inspect.signature(GuardRunner.align_instructions).parameters) == ["self"]
    assert list(inspect.signature(GuardRunner.apply_runtime_permissions).parameters) == ["self"]
    assert list(inspect.signature(GuardRunner.run).parameters) == [
        "self", "mode", "allow_offline", "skip_mcp",
    ]


def test_fasi_separate_wrapper_delegano() -> None:
    src = Path(guard_module.__file__).read_text(encoding="utf-8")
    assert "def _phase_align" in src
    assert "def _phase_permissions" in src
    assert "def _phase_run" in src
    run_pos = src.index("def run")
    align_call = src.index("self._phase_align(", run_pos)
    perm_call = src.index("self._phase_permissions(", run_pos)
    run_call = src.index("self._phase_run(", run_pos)
    assert align_call < perm_call < run_call
    public = [name for name in dir(guard_module.GuardRunner) if not name.startswith("_")]
    assert set(public) == {
        "align_instructions",
        "align_local_model_runtime",
        "apply_runtime_permissions",
        "preflight",
        "run",
    }
