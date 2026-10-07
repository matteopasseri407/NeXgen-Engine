"""A copy of the operator's Codex credentials lives only while the seat that needs it runs."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

COUNCIL_DIR = Path(__file__).resolve().parents[1] / "council"
if str(COUNCIL_DIR) not in sys.path:
    sys.path.insert(0, str(COUNCIL_DIR))

import seat_process  # noqa: E402
from seat_process import SeatInvocation, SeatRunError, run_seat  # noqa: E402


@pytest.fixture
def real_codex_home(tmp_path, monkeypatch):
    home = tmp_path / "real-codex"
    home.mkdir()
    (home / "auth.json").write_text('{"tokens": {"access_token": "synthetic"}}', encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(home))
    return home


def _codex_invocation(tmp_path: Path) -> SeatInvocation:
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    return seat_process._build_seat_command({"cli": "codex", "model": "m"}, "prompt", session_dir)


def test_the_isolated_codex_environment_holds_a_copy_of_the_credentials(tmp_path, real_codex_home):
    invocation = _codex_invocation(tmp_path)
    assert invocation.scratch is not None and invocation.scratch.is_dir()
    copy = Path(invocation.env["CODEX_HOME"]) / "auth.json"
    assert copy.read_text(encoding="utf-8") == (real_codex_home / "auth.json").read_text(encoding="utf-8")
    assert invocation.scratch in copy.parents
    if os.name != "nt":
        assert copy.stat().st_mode & 0o077 == 0


def _run_with_scratch(monkeypatch, tmp_path, source: str):
    scratch = tmp_path / "council-env-codex-x"
    (scratch / "codex-home").mkdir(parents=True)
    (scratch / "codex-home" / "auth.json").write_text("copy of the credentials", encoding="utf-8")
    script = tmp_path / "provider.py"
    script.write_text(source, encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP"}}
    invocation = SeatInvocation([sys.executable, "-u", str(script)], "", None, None, env, tmp_path, scratch)
    monkeypatch.setattr(seat_process, "_build_seat_command", lambda *args: invocation)
    return scratch


def test_the_copy_is_gone_once_the_seat_has_answered(monkeypatch, tmp_path):
    scratch = _run_with_scratch(monkeypatch, tmp_path, "print('VERDICT: APPROVE')\n")
    answer, _ = run_seat({"cli": "ollama", "model": "offline-test"}, "", tmp_path, 10)
    assert "APPROVE" in answer
    assert not scratch.exists()


def test_the_copy_is_gone_when_the_seat_fails(monkeypatch, tmp_path):
    scratch = _run_with_scratch(monkeypatch, tmp_path, "import sys\nsys.stderr.write('boom')\nsys.exit(3)\n")
    with pytest.raises(SeatRunError):
        run_seat({"cli": "ollama", "model": "offline-test"}, "", tmp_path, 10)
    assert not scratch.exists()


def test_the_copy_is_gone_when_the_seat_times_out(monkeypatch, tmp_path):
    scratch = _run_with_scratch(monkeypatch, tmp_path, "import time\nprint('x', flush=True)\ntime.sleep(30)\n")
    with pytest.raises(SeatRunError):
        run_seat({"cli": "ollama", "model": "offline-test"}, "", tmp_path, 1)
    assert not scratch.exists()


def test_the_real_credentials_are_never_touched(monkeypatch, tmp_path, real_codex_home):
    before = (real_codex_home / "auth.json").read_bytes()
    empty = tmp_path / "empty-path"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))  # the child gets this PATH: no real codex can be found or run
    invocation = _codex_invocation(tmp_path)
    monkeypatch.setattr(seat_process, "_build_seat_command", lambda *args: invocation)
    with pytest.raises(SeatRunError):  # no codex binary here: the seat cannot even start
        run_seat({"cli": "codex", "model": "m"}, "p", tmp_path, 5)
    assert (real_codex_home / "auth.json").read_bytes() == before
    assert not invocation.scratch.exists()
