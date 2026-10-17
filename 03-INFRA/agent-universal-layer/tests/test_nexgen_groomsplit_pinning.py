"""Pin current groom preview/apply/publish behavior before the split.

Fissa il comportamento attuale (non quello desiderato) con vault finti:
preview non scrive niente, apply su vault finto applica con report,
publish finto solo con flag esplicito. Solo tmp_path, solo runner finti,
mai publish reale, mai vault vero.
"""
from __future__ import annotations

import inspect
import json
import subprocess
import sys
import types
from dataclasses import dataclass
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.vault import groom  # noqa: E402
from nexgen_core.vault.runner import RunResult  # noqa: E402

TRANCHE = "| Note | Action | Why |\n| --- | --- | --- |\n| `notes/example.md` | compress | too verbose |\n"


def _run_git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, timeout=30)


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True, capture_output=True, timeout=30)
    _run_git(path, "config", "user.email", "test@example.com")
    _run_git(path, "config", "user.name", "Test")
    return path


def _commit_file(repo: Path, relpath: str, content: str, message: str) -> None:
    _run_git(repo, "config", "user.email", "test@example.com")
    _run_git(repo, "config", "user.name", "Test")
    target = repo / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    _run_git(repo, "add", relpath)
    _run_git(repo, "commit", "-q", "-m", message)


def _head(repo: Path) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True, timeout=30,
    )
    return proc.stdout.strip()


def _status(repo: Path) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        capture_output=True, text=True, check=True, timeout=30,
    )
    return proc.stdout.strip()


@dataclass
class FakeRunner:
    """Runner finto: mai processi, mai LLM, mai rete."""

    propose_text: str
    write_effect: object = None
    name: str = "fake"
    model: str = "fake-model"

    def run_readonly(self, prompt: str) -> RunResult:
        return RunResult(text=self.propose_text, exit_code=0)

    def run_write(self, prompt: str, workdir: Path) -> RunResult:
        if self.write_effect is not None:
            self.write_effect(workdir)
        return RunResult(text="write pass done", exit_code=0)


def _write_example(workdir: Path) -> None:
    _commit_file(workdir, "notes/example.md", "compressed content\n", "compress note")


def test_preview_writes_nothing_to_fake_vault(tmp_path: Path) -> None:
    vault = _init_repo(tmp_path / "vault")
    _commit_file(vault, "notes/example.md", "verbose content\n", "seed")
    head_before = _head(vault)

    runner = FakeRunner(propose_text=TRANCHE)
    log_path = tmp_path / "preview.log"
    exit_code = groom.preview(vault=vault, runner=runner, log_path=log_path, output=lambda *_: None)

    assert exit_code == 0
    assert _head(vault) == head_before
    assert _status(vault) == ""
    assert (vault / "notes" / "example.md").read_text(encoding="utf-8") == "verbose content\n"
    assert log_path.read_text(encoding="utf-8") == TRANCHE


def test_apply_on_fake_vault_applies_with_report(tmp_path: Path) -> None:
    vault = _init_repo(tmp_path / "vault")
    _commit_file(vault, "notes/example.md", "verbose content\n", "seed")
    head_before = _head(vault)
    state_dir = tmp_path / "state"
    shown: list[str] = []

    runner = FakeRunner(propose_text=TRANCHE, write_effect=_write_example)
    exit_code = groom.apply(
        vault=vault,
        runner=runner,
        state_dir=state_dir,
        timestamp="20260101-000010",
        engine_scripts=None,
        push_if_clean=False,
        input_func=lambda _: "yes",
        output=lambda *parts: shown.append(" ".join(str(p) for p in parts)),
    )

    assert exit_code == 0
    assert _head(vault) != head_before
    assert (vault / "notes" / "example.md").read_text(encoding="utf-8") == "compressed content\n"
    record_path = state_dir / "20260101-000010.json"
    assert record_path.is_file()
    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert record["promoted"] is True
    assert record["pushed"] is False
    assert any("20260101-000010.json" in line for line in shown)
    backlog = vault / "99-INDEX" / "vault-cleanup-backlog.md"
    assert backlog.is_file()
    assert "20260101-000010" in backlog.read_text(encoding="utf-8")
    clones = [p for p in state_dir.glob("*") if p.is_dir() and "clone" in p.name]
    assert clones == []


def test_fake_publish_only_with_explicit_flag(tmp_path: Path, monkeypatch) -> None:
    import nexgen_core.vault.promote as promote_module

    # Senza flag esplicito: nessun publish, anche se il mock fallirebbe.
    vault = _init_repo(tmp_path / "vault")
    _commit_file(vault, "notes/example.md", "verbose content\n", "seed")
    state_dir = tmp_path / "state"

    def _must_not_run(*args, **kwargs):  # pragma: no cover - deve mai partire
        raise AssertionError("publish reale/finto non deve partire senza flag esplicito")

    monkeypatch.setattr(promote_module, "run_publish", _must_not_run)
    runner = FakeRunner(propose_text=TRANCHE, write_effect=_write_example)
    exit_code = groom.apply(
        vault=vault,
        runner=runner,
        state_dir=state_dir,
        timestamp="20260101-000011",
        engine_scripts=None,
        push_if_clean=False,
        input_func=lambda _: "yes",
        output=lambda *_: None,
    )
    assert exit_code == 0
    record = json.loads((state_dir / "20260101-000011.json").read_text(encoding="utf-8"))
    assert record["promoted"] is True
    assert record["pushed"] is False

    # Con flag esplicito: il publish finto parte una volta sola.
    vault2 = _init_repo(tmp_path / "vault2")
    _commit_file(vault2, "notes/example.md", "verbose content\n", "seed")
    state_dir2 = tmp_path / "state2"
    calls: list[tuple[str, str]] = []

    def _fake_publish(vault_arg: Path, engine_scripts: Path):
        calls.append((str(vault_arg), str(engine_scripts)))
        return types.SimpleNamespace(stdout="", stderr="", returncode=0)

    monkeypatch.setattr(promote_module, "run_publish", _fake_publish)
    runner2 = FakeRunner(propose_text=TRANCHE, write_effect=_write_example)
    exit_code2 = groom.apply(
        vault=vault2,
        runner=runner2,
        state_dir=state_dir2,
        timestamp="20260101-000012",
        engine_scripts=tmp_path / "engine-scripts",
        push_if_clean=True,
        input_func=lambda _: "yes",
        output=lambda *_: None,
    )
    assert exit_code2 == 0
    assert len(calls) == 1
    assert calls[0][0] == str(vault2)
    record2 = json.loads((state_dir2 / "20260101-000012.json").read_text(encoding="utf-8"))
    assert record2["promoted"] is True
    assert record2["pushed"] is True


def test_public_signatures_unchanged() -> None:
    assert list(inspect.signature(groom.preview).parameters) == ["vault", "runner", "log_path", "output"]
    assert list(inspect.signature(groom.apply).parameters) == [
        "vault", "runner", "state_dir", "timestamp", "engine_scripts",
        "push_if_clean", "log_path", "input_func", "output",
    ]
