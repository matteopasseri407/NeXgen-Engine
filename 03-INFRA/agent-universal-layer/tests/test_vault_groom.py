"""Behavioral tests for vault-groom.sh (the gardener's hand).

Before this file, only shellcheck (syntax) covered vault-groom.sh/.ps1 --
nothing verified that plan/run/NOPUSH/GROOM_RUNNER actually gate what they
claim to gate. These tests stub claude/codex/agy as recording binaries (no
real LLM calls) and assert on the exact argv each mode/runner produces.

.ps1 is not covered here (no pwsh on this runner); mirror any finding here
into vault-groom.ps1 by hand, same caveat as the rest of the Windows twin.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
REAL_UL = TESTS_DIR.parent
REAL_VAULT = REAL_UL.parent.parent
GROOM_SH = REAL_VAULT / "03-INFRA" / "scripts" / "vault-groom.sh"

def _write_stub(bin_dir: Path, name: str, record_path: Path) -> None:
    # A plain Python script with its own shebang, run directly (not piped
    # through `python3 - <<HEREDOC`, which would consume the stub's OWN
    # stdin to supply the script source, leaving nothing for the stub to
    # read the caller's real piped prompt -- caught by
    # test_codex_runner_run_uses_workspace_write_sandbox failing on an
    # empty stdin capture before this fix.
    stub = bin_dir / name
    stub.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys, os\n"
        "argv = sys.argv[1:]\n"
        "stdin_data = ''\n"
        "try:\n"
        "    if not sys.stdin.isatty():\n"
        "        stdin_data = sys.stdin.read()\n"
        "except Exception:\n"
        "    pass\n"
        f"with open({str(record_path)!r}, 'w') as f:\n"
        "    json.dump({'argv': argv, 'stdin': stdin_data}, f)\n"
        "print('stub-output')\n",
        encoding="utf-8",
    )
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)


@pytest.fixture
def groom_env(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "03-INFRA").mkdir()
    (vault / "03-INFRA" / "vault-grooming-playbook.md").write_text("playbook\n", encoding="utf-8")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    record = tmp_path / "record.json"
    for name in ("claude", "codex", "agy"):
        _write_stub(bin_dir, name, record)

    env = dict(os.environ)
    env["VAULT"] = str(vault)
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    env["GROOM_LOG"] = str(tmp_path / "groom.log")
    env["GROOM_TEST_RECORD"] = str(record)
    env.pop("GROOM_RUNNER", None)
    env.pop("GROOM_NOPUSH", None)
    return {"vault": vault, "env": env, "record": record}


def _run(groom_env, *args, extra_env=None):
    env = dict(groom_env["env"])
    env.update(extra_env or {})
    return subprocess.run(
        ["bash", str(GROOM_SH), *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _record(groom_env):
    return json.loads(groom_env["record"].read_text(encoding="utf-8"))


def test_default_mode_is_plan_not_run(groom_env):
    proc = _run(groom_env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    rec = _record(groom_env)
    assert "--allowedTools" in rec["argv"]
    tools = rec["argv"][rec["argv"].index("--allowedTools") + 1:]
    assert "Edit" not in tools, "plan mode must never see a write tool"
    assert "Write" not in tools


def test_plan_mode_grants_no_write_tools(groom_env):
    proc = _run(groom_env, "plan")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    rec = _record(groom_env)
    tools = rec["argv"][rec["argv"].index("--allowedTools") + 1:]
    for forbidden in ("Edit", "Write", "Bash(git:*)"):
        assert forbidden not in tools, f"plan mode leaked a write capability: {forbidden}"


def test_run_mode_grants_write_tools(groom_env):
    proc = _run(groom_env, "run")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    rec = _record(groom_env)
    tools = rec["argv"][rec["argv"].index("--allowedTools") + 1:]
    assert "Edit" in tools
    assert "Bash(git:*)" in tools


def test_run_mode_nopush_blocks_git_push_hard(groom_env):
    proc = _run(groom_env, "run", extra_env={"GROOM_NOPUSH": "1"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    rec = _record(groom_env)
    assert "--disallowedTools" in rec["argv"]
    disallowed = rec["argv"][rec["argv"].index("--disallowedTools") + 1:]
    assert "Bash(git push:*)" in disallowed


def test_run_mode_without_nopush_has_no_disallow(groom_env):
    proc = _run(groom_env, "run")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    rec = _record(groom_env)
    assert "--disallowedTools" not in rec["argv"]


def test_invalid_mode_rejected_before_any_runner_call(groom_env):
    proc = _run(groom_env, "bogus")
    assert proc.returncode == 2
    assert "usage:" in proc.stderr
    assert not groom_env["record"].exists()


def test_codex_runner_plan_uses_read_only_sandbox(groom_env):
    proc = _run(groom_env, "plan", extra_env={"GROOM_RUNNER": "codex"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    rec = _record(groom_env)
    assert "exec" in rec["argv"]
    assert "-s" in rec["argv"]
    assert rec["argv"][rec["argv"].index("-s") + 1] == "read-only"
    assert "read-only planning pass" in rec["stdin"]  # prompt reached stdin, not argv-injected


def test_codex_runner_run_uses_workspace_write_sandbox(groom_env):
    proc = _run(groom_env, "run", extra_env={"GROOM_RUNNER": "codex"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    rec = _record(groom_env)
    assert rec["argv"][rec["argv"].index("-s") + 1] == "workspace-write"
    assert "grooming run" in rec["stdin"]


def test_agy_runner_plan_uses_plan_mode_and_sandbox(groom_env):
    proc = _run(groom_env, "plan", extra_env={"GROOM_RUNNER": "agy"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    rec = _record(groom_env)
    assert "--mode" in rec["argv"]
    assert rec["argv"][rec["argv"].index("--mode") + 1] == "plan"
    assert "--sandbox" in rec["argv"]


def test_agy_runner_run_uses_accept_edits_without_sandbox(groom_env):
    proc = _run(groom_env, "run", extra_env={"GROOM_RUNNER": "agy"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    rec = _record(groom_env)
    assert rec["argv"][rec["argv"].index("--mode") + 1] == "accept-edits"
    assert "--sandbox" not in rec["argv"]


def test_opencode_runner_fails_loud_with_explanation(groom_env):
    proc = _run(groom_env, "plan", extra_env={"GROOM_RUNNER": "opencode"})
    assert proc.returncode == 2
    assert "no per-invocation permission-scoping flag" in proc.stderr
    assert not groom_env["record"].exists()


def test_unknown_runner_rejected(groom_env):
    proc = _run(groom_env, "plan", extra_env={"GROOM_RUNNER": "some-other-cli"})
    assert proc.returncode == 2
    assert "unknown GROOM_RUNNER" in proc.stderr
