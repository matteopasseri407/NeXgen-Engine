"""The Claude vault-checkpoint hook finds its briefing even when deployed alone.

The old vault agent-sync copied only the `.mjs` into `~/.claude/`; since the
briefing text moved into `vault-briefing.txt`, a lone script must still read the
text the installed engine carries instead of going silent.

The same hook also injects the engine's one-line quota at every session start.
Every run here gets a fake `nexgen` first on `PATH`, so what the line says is the
test's literal and never whatever engine the machine happens to have installed.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
SCRIPT = HOOKS / "claude-vault-checkpoint.mjs"
TEXT = (HOOKS / "vault-briefing.txt").read_text(encoding="utf-8").strip()

#: The line the fake engine prints, and the exact line the hook must inject.
QUOTA_LINE = "Quota: Claude 5h 40% (reset 18:00) · Codex 30d 72%"


def fake_engine(folder: Path, *, line: str = "", wait_s: int = 0) -> Path:
    """A `nexgen` first on `PATH`: it prints `line` after waiting, and nothing else.

    Both platform spellings are written, and the text is a file the launcher reads,
    because a batch `echo` eats a lone `%` and a percentage is exactly what the
    line carries. With no `line` this is the silent engine every other test wants:
    a machine whose quota cannot be read adds nothing to a session.
    """
    folder.mkdir(parents=True, exist_ok=True)
    text = folder / "line.txt"
    text.write_text(line + "\n" if line else "", encoding="utf-8")
    posix = folder / "nexgen"
    posix.write_text(f'#!/bin/sh\nsleep {wait_s}\ncat "{text}"\n', encoding="utf-8")
    posix.chmod(0o755)
    # `ping` is the portable way to wait in a batch file: there is no `sleep` there.
    (folder / "nexgen.cmd").write_text(
        f'@echo off\r\nping -n {wait_s + 1} 127.0.0.1 >nul\r\ntype "{text}"\r\n', encoding="utf-8",
    )
    return folder


def _run(script: Path, env: dict[str, str], source: str = "compact") -> str:
    event = json.dumps({"hook_event_name": "SessionStart", "source": source})
    done = subprocess.run(
        ["node", str(script)], input=event, capture_output=True, text=True,
        encoding="utf-8", env=env, timeout=60, check=False,
    )
    assert done.returncode == 0
    return done.stdout


def _env(home_dir: Path, *, engine: Path | None = None, **extra: str) -> dict[str, str]:
    """The environment one hook run sees: a home of its own and a fake engine first on PATH."""
    bin_dir = engine or fake_engine(home_dir / "bin")
    env = {k: v for k, v in os.environ.items() if k not in ("AGENT_ENGINE_ROOT",)}
    env.update(HOME=str(home_dir / "home"), USERPROFILE=str(home_dir / "home"), **extra)
    env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
    return env


def test_a_lone_script_reads_the_text_the_engine_carries(tmp_path: Path) -> None:
    lone = tmp_path / "claude" / SCRIPT.name
    lone.parent.mkdir(parents=True)
    shutil.copyfile(SCRIPT, lone)
    engine = tmp_path / "engine" / "03-INFRA"
    (engine / "agent-universal-layer" / "hooks").mkdir(parents=True)
    shutil.copyfile(HOOKS / "vault-briefing.txt", engine / "agent-universal-layer" / "hooks" / "vault-briefing.txt")

    out = _run(lone, _env(tmp_path, AGENT_ENGINE_ROOT=str(engine)))
    assert json.loads(out)["hookSpecificOutput"]["additionalContext"] == TEXT


def test_the_text_beside_the_script_wins(tmp_path: Path) -> None:
    here = tmp_path / "claude"
    here.mkdir()
    shutil.copyfile(SCRIPT, here / SCRIPT.name)
    (here / "vault-briefing.txt").write_text("beside", encoding="utf-8")

    out = _run(here / SCRIPT.name, _env(tmp_path))
    assert json.loads(out)["hookSpecificOutput"]["additionalContext"] == "beside"


def test_no_text_anywhere_is_silence_not_an_error(tmp_path: Path) -> None:
    lone = tmp_path / "claude" / SCRIPT.name
    lone.parent.mkdir(parents=True)
    shutil.copyfile(SCRIPT, lone)

    assert _run(lone, _env(tmp_path)) == ""


def test_the_quota_line_is_what_a_fresh_session_receives(tmp_path: Path) -> None:
    """A fresh session has nothing to re-ground and everything to look at: the gauge only."""
    lone = tmp_path / "claude" / SCRIPT.name
    lone.parent.mkdir(parents=True)
    shutil.copyfile(SCRIPT, lone)
    engine = fake_engine(tmp_path / "engine", line=QUOTA_LINE)

    out = _run(lone, _env(tmp_path, engine=engine), source="startup")

    assert json.loads(out)["hookSpecificOutput"]["additionalContext"] == QUOTA_LINE


def test_the_quota_line_follows_the_briefing_on_a_resume(tmp_path: Path) -> None:
    here = tmp_path / "claude"
    here.mkdir()
    shutil.copyfile(SCRIPT, here / SCRIPT.name)
    (here / "vault-briefing.txt").write_text("beside", encoding="utf-8")
    engine = fake_engine(tmp_path / "engine", line=QUOTA_LINE)

    out = _run(here / SCRIPT.name, _env(tmp_path, engine=engine))

    assert json.loads(out)["hookSpecificOutput"]["additionalContext"] == "beside\n\n" + QUOTA_LINE


def test_an_engine_too_slow_for_the_budget_costs_the_line_and_nothing_else(tmp_path: Path) -> None:
    """The three-second cap: the briefing still arrives, the gauge is missing, the start goes on."""
    here = tmp_path / "claude"
    here.mkdir()
    shutil.copyfile(SCRIPT, here / SCRIPT.name)
    (here / "vault-briefing.txt").write_text("beside", encoding="utf-8")
    engine = fake_engine(tmp_path / "engine", line=QUOTA_LINE, wait_s=10)

    started = time.monotonic()
    out = _run(here / SCRIPT.name, _env(tmp_path, engine=engine))
    elapsed = time.monotonic() - started

    assert json.loads(out)["hookSpecificOutput"]["additionalContext"] == "beside"
    assert elapsed < 8, "the hook waited for an engine it should have given up on"


def test_an_engine_that_is_not_installed_adds_nothing(tmp_path: Path) -> None:
    here = tmp_path / "claude"
    here.mkdir()
    shutil.copyfile(SCRIPT, here / SCRIPT.name)
    (here / "vault-briefing.txt").write_text("beside", encoding="utf-8")
    empty = tmp_path / "no-engine"
    empty.mkdir()
    env = _env(tmp_path, engine=empty)
    env["PATH"] = str(empty)  # no engine anywhere, and nothing else either

    out = _run(here / SCRIPT.name, env, source="startup")

    assert out == ""


def test_an_engine_that_answers_a_whole_report_is_not_a_line(tmp_path: Path) -> None:
    """An older engine that ignores `--line` prints the report: that is not the session's line."""
    here = tmp_path / "claude"
    here.mkdir()
    shutil.copyfile(SCRIPT, here / SCRIPT.name)
    report = "Codex (free): 72% of the 30d window used (read 2m ago)\nAntigravity: not measured"
    engine = fake_engine(tmp_path / "engine", line=report)

    out = _run(here / SCRIPT.name, _env(tmp_path, engine=engine), source="startup")

    assert out == ""
