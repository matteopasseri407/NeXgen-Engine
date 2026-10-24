"""The Claude vault-checkpoint hook finds its briefing even when deployed alone.

The old vault agent-sync copied only the `.mjs` into `~/.claude/`; since the
briefing text moved into `vault-briefing.txt`, a lone script must still read the
text the installed engine carries instead of going silent.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
SCRIPT = HOOKS / "claude-vault-checkpoint.mjs"
TEXT = (HOOKS / "vault-briefing.txt").read_text(encoding="utf-8").strip()


def _run(script: Path, env: dict[str, str]) -> str:
    event = json.dumps({"hook_event_name": "SessionStart", "source": "compact"})
    done = subprocess.run(
        ["node", str(script)], input=event, capture_output=True, text=True,
        encoding="utf-8", env=env, timeout=30, check=False,
    )
    assert done.returncode == 0
    return done.stdout


def _env(tmp_path: Path, **extra: str) -> dict[str, str]:
    import os

    env = {k: v for k, v in os.environ.items() if k not in ("AGENT_ENGINE_ROOT",)}
    env.update(HOME=str(tmp_path / "home"), USERPROFILE=str(tmp_path / "home"), **extra)
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
