"""Behavioral tests for vault-groom.ps1 (the gardener's hand, Windows twin).

Runs the REAL vault-groom.ps1 through pwsh -- module-level skip if pwsh
isn't on PATH so this stays a clean skip on any dev machine that lacks it,
same idea as the rest of this repo's pwsh-dependent gates. This repo's own
CI (ubuntu-latest AND windows-latest) both ship pwsh preinstalled, so this
file genuinely executes on both, not just Windows.

Mirrors test_vault_groom.py's 2026-07-13 temp-clone-gate redesign (see that
file's own docstring for the full architect-review rationale) and adds two
things specific to this twin: a direct argv-shape comparison against the
.sh twin (shipping two wrappers is only worth it if they actually agree),
and the Windows prompt-delivery fix (a *.cmd shim's cmd.exe reparsing can
mangle |/</newlines in a bare-argument prompt -- vault-groom.ps1 now
prefers a *.ps1 shim when one is on PATH).

Stub scripts intercepting claude/codex/agy: the SAME stub sources as
test_vault_groom.py, imported rather than duplicated (two divergent copies
of "what the fake LLM does" is exactly the kind of twin-drift this whole
redesign exists to eliminate elsewhere). On POSIX (ubuntu-latest under
pwsh) an extensionless file with a shebang and the exec bit is directly
invocable from PATH, identically to how bash finds it. Windows has no
shebang-based exec, so there `claude`/`codex`/`agy` are instead a
`<name>.cmd` (matched by PATHEXT's default .CMD entry) that shells out to
a same-named `.py` holding the identical stub source.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR))
from test_vault_groom import (  # noqa: E402  (path insert must come first)
    FIXED_TRANCHE,
    DIRTY_TRANCHE,
    _empty_stub_source,
    _stub_source,
    _stub_source_merge_commit,
    _stub_source_out_of_scope,
)

PWSH = shutil.which("pwsh")
pytestmark = pytest.mark.skipif(PWSH is None, reason="pwsh is not installed on this machine")

REAL_UL = TESTS_DIR.parent
REAL_VAULT = REAL_UL.parent.parent
GROOM_PS1 = REAL_VAULT / "03-INFRA" / "scripts" / "vault-groom.ps1"
GROOM_SH = REAL_VAULT / "03-INFRA" / "scripts" / "vault-groom.sh"


def _git(vault, *args):
    return subprocess.run(
        ["git", "-C", str(vault), *args],
        capture_output=True, text=True, check=True,
    )


def _write_stub_source(bin_dir: Path, name: str, source: str) -> None:
    if os.name == "nt":
        (bin_dir / f"{name}.py").write_text(source, encoding="utf-8")
        # A *.ps1 shim ALONGSIDE the *.cmd, mirroring the launcher shape
        # agent-sync's utils() actually installs on Windows (it writes both a
        # .ps1 and a .cmd). vault-groom.ps1's Resolve-CliInvoker prefers the
        # .ps1, invoked in-process by the PowerShell engine, so the
        # -p/--prompt argument (a multi-line markdown tranche full of | and
        # <) arrives byte-intact instead of being re-parsed by cmd.exe
        # through the .cmd's %* -- without this the write pass never sees
        # "APPROVED TRANCHE" and the audit blocks as coverage-dirty. Same
        # forwarder as _write_direct_ps1_shim below.
        (bin_dir / f"{name}.ps1").write_text(
            "$pyScript = Join-Path $PSScriptRoot '" + name + ".py'\n"
            "$py = (Get-Command python3 -ErrorAction SilentlyContinue).Source\n"
            "if (-not $py) { $py = (Get-Command python -ErrorAction SilentlyContinue).Source }\n"
            "$piped = @($input) -join \"`n\"\n"
            "$piped | & $py $pyScript @args\n"
            "exit $LASTEXITCODE\n",
            encoding="utf-8",
        )
        (bin_dir / f"{name}.cmd").write_text(
            f'@echo off\r\npython "%~dp0{name}.py" %*\r\n', encoding="utf-8"
        )
    else:
        stub = bin_dir / name
        stub.write_text(source, encoding="utf-8")
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)


def _write_stub(bin_dir: Path, name: str, tranche: str = FIXED_TRANCHE) -> None:
    _write_stub_source(bin_dir, name, _stub_source(tranche))


def _write_empty_stub(bin_dir: Path, name: str) -> None:
    _write_stub_source(bin_dir, name, _empty_stub_source())


def _write_direct_ps1_shim(bin_dir: Path, name: str, tranche: str) -> None:
    # The dedicated fix for the 2026-07-13 review's Windows prompt-mangling
    # finding: vault-groom.ps1 now prefers a *.ps1 shim (invoked directly by
    # the PowerShell engine, no cmd.exe reparsing of |/</newlines) over a
    # bare-name resolution that could hit a *.cmd shim's own reparsing.
    # This stub is PowerShell itself, forwarding straight to the shared
    # Python stub source (python is a real .exe, likewise safe) so its
    # git-commit/record-writing behavior stays identical either way.
    py_stub = bin_dir / f"{name}.py"
    py_stub.write_text(_stub_source(tranche), encoding="utf-8")
    ps1_stub = bin_dir / f"{name}.ps1"
    # $input, NOT [Console]::In: invoking a *.ps1 via `&` runs it IN-PROCESS
    # (no new OS process, no real stdin redirection of its own) -- reading
    # [Console]::In here would drain the CALLER's real console stdin (the
    # confirmation gate's own "yes\n"), not the '' this specific pipeline
    # stage piped in. $input is the automatic variable that actually holds
    # what THIS invocation was piped, matching vault-groom.ps1's own
    # `'' | & $cli ...` intent (isolate stdin, don't block/consume the
    # real one) -- a test-stub-only concern: the real fix under test is the
    # -p ARGUMENT arriving byte-intact, which doesn't depend on this.
    ps1_stub.write_text(
        "$pyScript = Join-Path $PSScriptRoot '" + name + ".py'\n"
        "$py = (Get-Command python3 -ErrorAction SilentlyContinue).Source\n"
        "if (-not $py) { $py = (Get-Command python -ErrorAction SilentlyContinue).Source }\n"
        "$piped = @($input) -join \"`n\"\n"
        "$piped | & $py $pyScript @args\n"
        "exit $LASTEXITCODE\n",
        encoding="utf-8",
    )


@pytest.fixture
def groom_env(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "03-INFRA" / "scripts").mkdir(parents=True)
    (vault / "03-INFRA" / "vault-grooming-playbook.md").write_text("playbook\n", encoding="utf-8")
    _git(vault, "init", "-q", "-b", "main")
    _git(vault, "config", "user.email", "nexgen-tests.invalid")
    _git(vault, "config", "user.name", "Test")
    _git(vault, "add", "-A")
    _git(vault, "commit", "-q", "-m", "seed")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    record = tmp_path / "record.json"
    for name in ("claude", "codex", "agy"):
        _write_stub(bin_dir, name)

    state_dir = tmp_path / "state"

    env = dict(os.environ)
    env["VAULT"] = str(vault)
    path_sep = ";" if os.name == "nt" else ":"
    env["PATH"] = f"{bin_dir}{path_sep}{env.get('PATH', '')}"
    env["GROOM_TEST_RECORD"] = str(record)
    env["GROOM_STATE_DIR"] = str(state_dir)
    env["GROOM_NOPUSH"] = "1"  # no remote configured in these fixtures
    # The Python stub stands in for a real CLI runner (claude/codex/agy),
    # which emits UTF-8. On Windows a redirected Python stdout defaults to
    # the ANSI code page, so an accented tranche would reach vault-groom.ps1
    # as mojibake even after the wrapper's own [Console]::OutputEncoding fix.
    # PYTHONUTF8=1 makes the stand-in faithful to what it emulates; a no-op
    # on the already-UTF-8 POSIX runners.
    env["PYTHONUTF8"] = "1"
    isolated_home = tmp_path / "isolated-home"
    isolated_home.mkdir()
    env["HOME"] = str(isolated_home)
    env["USERPROFILE"] = str(isolated_home)
    env.pop("AGENT_ENGINE_ROOT", None)
    # Same reasoning as test_vault_groom.py's own fixture: the temp-clone
    # gate's clone has no local git config of its own, and HOME is isolated
    # above -- give the write-pass stub's commits an identity from env vars
    # instead.
    env["GIT_AUTHOR_NAME"] = "Test"
    env["GIT_AUTHOR_EMAIL"] = "nexgen-tests.invalid"
    env["GIT_COMMITTER_NAME"] = "Test"
    env["GIT_COMMITTER_EMAIL"] = "nexgen-tests.invalid"
    return {"vault": vault, "env": env, "record": record, "state_dir": state_dir, "bin_dir": bin_dir}


def _run(groom_env, *args, extra_env=None, stdin_input=None):
    env = dict(groom_env["env"])
    env.update(extra_env or {})
    return subprocess.run(
        [PWSH, "-NoProfile", "-File", str(GROOM_PS1), *args],
        env=env,
        input=stdin_input,
        capture_output=True,
        # Decode the wrapper's (now UTF-8) output deterministically, not with
        # the pytest process's platform-default code page -- on Windows that
        # default would turn an accented tranche into mojibake here even
        # though vault-groom.ps1 emitted it correctly.
        encoding="utf-8",
        text=True,
        timeout=60,
    )


def _records(groom_env):
    return json.loads(groom_env["record"].read_text(encoding="utf-8"))


def _audit_record(groom_env):
    records = list(groom_env["state_dir"].glob("*.json"))
    assert len(records) == 1
    return json.loads(records[0].read_text(encoding="utf-8"))


def _clone_dirs(groom_env):
    return list(groom_env["state_dir"].glob("*-clone-*"))


def test_bare_invocation_is_preview_read_only_and_never_prompts(groom_env):
    proc = _run(groom_env, stdin_input="")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    recs = _records(groom_env)
    assert len(recs) == 1, "bare invocation must invoke the runner exactly once (read-only pass)"
    tools = recs[0]["argv"][recs[0]["argv"].index("--allowedTools") + 1:]
    assert "Edit" not in tools and "Write" not in tools
    assert FIXED_TRANCHE.strip() in proc.stdout
    assert _clone_dirs(groom_env) == [], "preview must never create a temp-clone"


def test_preview_mode_is_read_only_and_never_prompts(groom_env):
    proc = _run(groom_env, "preview", stdin_input="")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    recs = _records(groom_env)
    assert len(recs) == 1
    tools = recs[0]["argv"][recs[0]["argv"].index("--allowedTools") + 1:]
    assert "Edit" not in tools and "Write" not in tools


def test_plan_mode_rejected_with_migration_hint(groom_env):
    proc = _run(groom_env, "plan", stdin_input="")
    assert proc.returncode == 2
    assert "preview" in proc.stderr
    assert not groom_env["record"].exists()


@pytest.mark.parametrize("retired_mode", ["run", "guarded"])
def test_run_and_guarded_modes_rejected_with_migration_hint(groom_env, retired_mode):
    proc = _run(groom_env, retired_mode, stdin_input="")
    assert proc.returncode == 2
    assert "apply" in proc.stderr
    assert not groom_env["record"].exists()


def test_apply_declined_does_not_execute_or_commit(groom_env):
    head_before = _git(groom_env["vault"], "rev-parse", "HEAD").stdout.strip()
    proc = _run(groom_env, "apply", stdin_input="no\n")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    recs = _records(groom_env)
    assert len(recs) == 1, "declining must never reach the write pass"
    assert "annullato" in (proc.stdout + proc.stderr)
    head_after = _git(groom_env["vault"], "rev-parse", "HEAD").stdout.strip()
    assert head_after == head_before
    assert _clone_dirs(groom_env) == []


def test_apply_confirmation_is_case_sensitive(groom_env):
    # -cne, NOT PowerShell's default case-insensitive -ne: the banner
    # promises a literal "yes" (2026-07-13 review -- matches vault-groom.sh,
    # where bash string comparison is always case-sensitive).
    head_before = _git(groom_env["vault"], "rev-parse", "HEAD").stdout.strip()
    proc = _run(groom_env, "apply", stdin_input="Yes\n")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    recs = _records(groom_env)
    assert len(recs) == 1, "'Yes' (capital Y) must be treated as declined, not confirmed"
    head_after = _git(groom_env["vault"], "rev-parse", "HEAD").stdout.strip()
    assert head_after == head_before


def test_apply_confirmed_executes_exact_approved_tranche_inside_the_clone(groom_env):
    # GROOM_KEEP_CLONE: the promoted clone is removed by default; keep it
    # so the write pass's recorded cwd can be compared against it below.
    proc = _run(groom_env, "apply", stdin_input="yes\n", extra_env={"GROOM_KEEP_CLONE": "1"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    recs = _records(groom_env)
    assert len(recs) == 2, "confirming must invoke propose, then write"

    propose_tools = recs[0]["argv"][recs[0]["argv"].index("--allowedTools") + 1:]
    assert "Edit" not in propose_tools

    write_argv = recs[1]["argv"]
    write_tools = write_argv[write_argv.index("--allowedTools") + 1:]
    assert "Edit" in write_tools

    write_prompt = write_argv[write_argv.index("-p") + 1]
    assert "APPROVED TRANCHE" in write_prompt
    assert FIXED_TRANCHE.strip() in write_prompt

    plan_records = list(groom_env["state_dir"].glob("*-plan.txt"))
    assert len(plan_records) == 1
    # Read the ACTUAL plan-record bytes this run wrote, not an assumed
    # byte-normalization -- Set-Content's own trailing-newline convention
    # need not match bash's `printf '%s\n'` byte-for-byte, only be
    # internally self-consistent (the same hash the wrapper embeds).
    expected_hash = hashlib.sha256(plan_records[0].read_bytes()).hexdigest()
    assert expected_hash in write_prompt
    assert str(plan_records[0]) in write_prompt
    assert "Do NOT push" in write_prompt

    clone_dirs = _clone_dirs(groom_env)
    assert len(clone_dirs) == 1
    assert recs[1]["cwd"] == str(clone_dirs[0]), "the write pass's cwd must be the temp-clone, never the vault"


def test_clone_has_no_remotes_after_setup(groom_env):
    # GROOM_KEEP_CLONE: keep the promoted clone (removed by default) so its
    # remote configuration can be inspected after the run.
    proc = _run(groom_env, "apply", stdin_input="yes\n", extra_env={"GROOM_KEEP_CLONE": "1"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    clone_dirs = _clone_dirs(groom_env)
    assert len(clone_dirs) == 1
    remotes = _git(clone_dirs[0], "remote").stdout.strip()
    assert remotes == ""


def test_clean_run_promotes_exactly_the_audited_oid(groom_env):
    # GROOM_KEEP_CLONE: keep the promoted clone (removed by default) so the
    # exact OID it audited can be read back and compared to what got promoted.
    proc = _run(groom_env, "apply", stdin_input="yes\n", extra_env={"GROOM_KEEP_CLONE": "1"})
    assert proc.returncode == 0, proc.stdout + proc.stderr

    clone_dirs = _clone_dirs(groom_env)
    assert len(clone_dirs) == 1
    audited_oid = _git(clone_dirs[0], "rev-parse", "HEAD").stdout.strip()

    vault = groom_env["vault"]
    vault_head = _git(vault, "rev-parse", "HEAD").stdout.strip()
    assert vault_head != audited_oid
    parent = _git(vault, "rev-parse", "HEAD~1").stdout.strip()
    assert parent == audited_oid

    record = _audit_record(groom_env)
    assert record["promoted"] is True
    assert record["promoted_oid"] == audited_oid


def test_apply_confirmed_produces_real_commit_and_audit_record(groom_env):
    proc = _run(groom_env, "apply", stdin_input="yes\n")
    assert proc.returncode == 0, proc.stdout + proc.stderr

    log = _git(groom_env["vault"], "log", "--oneline").stdout
    assert "stub: simulated grooming" in log
    assert "chore(groom): record run" in log

    record = _audit_record(groom_env)
    assert record["runner"] == "claude"
    assert record["pushed"] is False, "GROOM_NOPUSH=1 must omit --push-if-clean entirely"
    assert record["coverage_status"] == "clean"
    assert record["files_touched"] == ["stub-groomed.md"]
    plan_records = list(groom_env["state_dir"].glob("*-plan.txt"))
    assert len(plan_records) == 1
    expected_hash = hashlib.sha256(plan_records[0].read_bytes()).hexdigest()
    assert record["tranche_sha256"] == expected_hash


def test_apply_toctou_guard_aborts_if_plan_record_changes_before_write_pass(groom_env):
    proc = subprocess.Popen(
        [PWSH, "-NoProfile", "-File", str(GROOM_PS1), "apply"],
        env=groom_env["env"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        encoding="utf-8",
        text=True,
    )

    # A dedicated reader thread, not a post-hoc proc.stdout.read(): the
    # feeder thread below waits for the confirmation banner to actually
    # appear -- proof $TrancheHash has already been computed, since the
    # banner prints strictly after it, right before the blocking
    # Read-Host. Polling for the plan-record FILE's mere existence isn't
    # enough: under load, this thread can observe the file the instant
    # Set-Content creates it, before its content is fully written (a .NET
    # StreamWriter, not one atomic write() syscall) and before the
    # wrapper's own next line (the hash computation) has even run --
    # tampering into that window makes the wrapper hash the TAMPERED text
    # as if it were the original, missing the TOCTOU trigger entirely. See
    # the matching comment in test_vault_groom.py's own TOCTOU test.
    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    stdout_lock = threading.Lock()

    def _drain(stream, sink):
        # Character-by-character, NOT `for line in stream`: Read-Host's own
        # prompt text has no trailing newline -- a readline-based iterator
        # blocks forever waiting for one that never comes before the human
        # answers, deadlocking this exact banner-detection wait. Both pipes
        # are drained on DAEMON threads: on Windows a grandchild that
        # inherited a pipe's write end (the clone's git) can keep read(1)
        # from ever seeing EOF, and a non-daemon reader stuck there would
        # wedge the whole pytest process at interpreter exit -- a daemon one
        # is abandoned instead. stderr is drained concurrently for the same
        # reason, so the trailing assertions never do a blocking main-thread
        # read. See the matching comment in test_vault_groom.py's TOCTOU test.
        while True:
            ch = stream.read(1)
            if not ch:
                break
            with stdout_lock:
                sink.append(ch)

    reader = threading.Thread(target=_drain, args=(proc.stdout, stdout_chunks), daemon=True)
    err_reader = threading.Thread(target=_drain, args=(proc.stderr, stderr_chunks), daemon=True)
    reader.start()
    err_reader.start()

    def tamper_then_confirm():
        # Trigger on the Write-Host banner's last line, NOT the Read-Host
        # "Procedere?" prompt: on Windows PowerShell delivers a Read-Host
        # prompt to the console host, not the redirected stdout pipe, so it
        # never appears in what this thread drains and the feeder would time
        # out (the process then blocks on Read-Host forever). The banner is
        # printed strictly after $TrancheHash is computed and the plan record
        # is written, and right before Read-Host blocks -- the exact window
        # this TOCTOU test needs -- and Write-Host lands on stdout on both
        # platforms.
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            with stdout_lock:
                seen = "".join(stdout_chunks)
            if "Qualunque altra risposta annulla" in seen:
                break
            time.sleep(0.02)
        else:
            raise AssertionError("confirmation prompt never appeared")
        candidates = list(groom_env["state_dir"].glob("*-plan.txt"))
        assert candidates, "plan record never appeared"
        candidates[0].write_text("tampered after approval, before the write pass\n", encoding="utf-8")
        proc.stdin.write("yes\n")
        proc.stdin.close()

    # Not proc.communicate(): with input=None it closes stdin immediately on
    # entry, which would race the feeder thread's write -- see the matching
    # comment in test_vault_groom.py's own TOCTOU test.
    feeder = threading.Thread(target=tamper_then_confirm, daemon=True)
    feeder.start()
    feeder.join(timeout=30)
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        # The banner never appeared (feeder never fed stdin, Read-Host still
        # blocking) or the process wedged: kill it so the pipes close, the
        # daemon drains end, and the assertions below fail loudly instead of
        # the job hanging.
        proc.kill()
        proc.wait(timeout=10)
    reader.join(timeout=5)
    err_reader.join(timeout=5)
    with stdout_lock:
        stdout = "".join(stdout_chunks)
        stderr = "".join(stderr_chunks)

    assert proc.returncode == 1, stdout + stderr
    assert "plan record changed after approval" in stderr
    recs = _records(groom_env)
    assert len(recs) == 1, "a TOCTOU abort must never reach the write pass"
    assert _clone_dirs(groom_env) == []


def test_apply_dirty_vault_working_tree_aborts_before_any_clone(groom_env):
    (groom_env["vault"] / "uncommitted.md").write_text("oops\n", encoding="utf-8")
    proc = _run(groom_env, "apply", stdin_input="yes\n")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "not clean" in proc.stderr
    recs = _records(groom_env)
    assert len(recs) == 1
    assert _clone_dirs(groom_env) == []


def test_empty_proposal_aborts_before_any_confirmation_prompt(groom_env):
    empty_record = groom_env["record"].parent / "empty_record.json"
    for name in ("claude", "codex", "agy"):
        _write_empty_stub(groom_env["bin_dir"], name)
    env = dict(groom_env["env"])
    env["GROOM_TEST_RECORD"] = str(empty_record)

    proc = subprocess.run(
        [PWSH, "-NoProfile", "-File", str(GROOM_PS1), "apply"],
        env=env, input="yes\n", capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 1
    assert "empty proposal" in proc.stderr
    recs = json.loads(empty_record.read_text(encoding="utf-8"))
    assert len(recs) == 1, "an empty proposal must never reach the write pass either"


def test_claude_write_pass_always_blocks_git_push_hard(groom_env):
    proc = _run(groom_env, "apply", stdin_input="yes\n")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    recs = _records(groom_env)
    write_argv = recs[1]["argv"]
    assert "--disallowedTools" in write_argv
    disallowed = write_argv[write_argv.index("--disallowedTools") + 1:]
    assert "Bash(git push:*)" in disallowed


def test_unknown_runner_rejected(groom_env):
    proc = _run(groom_env, "preview", extra_env={"GROOM_RUNNER": "some-other-cli"}, stdin_input="")
    assert proc.returncode == 2
    assert "unknown GROOM_RUNNER" in proc.stderr


# --- The audit gate: out-of-scope edits, non-linear history, staleness,
# and a failing write-pass runner all must block promotion, quarantine the
# clone, and leave the real vault untouched. Mirrors test_vault_groom.py's
# own gate tests. ---

def test_apply_out_of_scope_edit_in_clone_quarantines_vault_head_unchanged(groom_env):
    for name in ("claude", "codex", "agy"):
        _write_stub_source(groom_env["bin_dir"], name, _stub_source_out_of_scope(FIXED_TRANCHE))
    head_before = _git(groom_env["vault"], "rev-parse", "HEAD").stdout.strip()

    proc = _run(groom_env, "apply", stdin_input="yes\n")
    assert proc.returncode == 4, proc.stdout + proc.stderr
    assert "UNTOUCHED" in proc.stderr

    head_after = _git(groom_env["vault"], "rev-parse", "HEAD").stdout.strip()
    assert head_after == head_before

    record = _audit_record(groom_env)
    assert record["promoted"] is False
    assert record["out_of_scope_targets"] == ["unplanned-extra.md"]

    clone_dirs = _clone_dirs(groom_env)
    assert len(clone_dirs) == 1
    assert (clone_dirs[0] / ".GROOM_QUARANTINE.json").is_file()


def test_apply_merge_commit_in_clone_quarantines_for_non_linear_history(groom_env):
    for name in ("claude", "codex", "agy"):
        _write_stub_source(groom_env["bin_dir"], name, _stub_source_merge_commit(FIXED_TRANCHE))
    head_before = _git(groom_env["vault"], "rev-parse", "HEAD").stdout.strip()

    proc = _run(groom_env, "apply", stdin_input="yes\n")
    assert proc.returncode == 4, proc.stdout + proc.stderr

    head_after = _git(groom_env["vault"], "rev-parse", "HEAD").stdout.strip()
    assert head_after == head_before

    record = _audit_record(groom_env)
    assert record["promoted"] is False
    assert record["history_linear"] is False
    clone_dirs = _clone_dirs(groom_env)
    assert (clone_dirs[0] / ".GROOM_QUARANTINE.json").is_file()


def test_apply_runner_non_zero_exit_still_writes_audit_record_and_quarantines(groom_env):
    env = dict(groom_env["env"])
    env["GROOM_TEST_FAIL_WRITE"] = "17"
    head_before = _git(groom_env["vault"], "rev-parse", "HEAD").stdout.strip()

    proc = subprocess.run(
        [PWSH, "-NoProfile", "-File", str(GROOM_PS1), "apply"],
        env=env, input="yes\n", capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode != 0, proc.stdout + proc.stderr

    head_after = _git(groom_env["vault"], "rev-parse", "HEAD").stdout.strip()
    assert head_after == head_before

    record = _audit_record(groom_env)
    assert record["write_exit_code"] == 17
    assert record["promoted"] is False
    clone_dirs = _clone_dirs(groom_env)
    assert len(clone_dirs) == 1
    assert (clone_dirs[0] / ".GROOM_QUARANTINE.json").is_file()


# --- Windows prompt delivery (2026-07-13 review, critical finding): a
# *.cmd shim's cmd.exe reparsing can mangle |, <, and embedded newlines in
# a bare-argument prompt. This asserts a tricky prompt arrives byte-intact
# via the *.ps1-shim-preferred path. ---

@pytest.mark.parametrize("target_runner", ["claude", "agy"])
def test_prompt_special_characters_survive_the_ps1_shim_byte_intact(groom_env, target_runner):
    tricky_tranche = (
        "| Nota | Azione | Perch\u00e9 |\n"
        "|---|---|---|\n"
        "| `stub-groomed.md` | **archive** | a < b, superato |\n"
    )
    for name in ("claude", "codex", "agy"):
        _write_direct_ps1_shim(groom_env["bin_dir"], name, tricky_tranche)

    proc = _run(groom_env, "apply", extra_env={"GROOM_RUNNER": target_runner}, stdin_input="yes\n")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    recs = _records(groom_env)
    assert len(recs) == 2
    write_argv = recs[1]["argv"]
    write_prompt = write_argv[write_argv.index("-p") + 1] if target_runner == "claude" else write_argv[write_argv.index("--prompt") + 1]
    assert tricky_tranche.strip() in write_prompt
    assert "a < b, superato" in write_prompt


# --- Push: only reached after a successful promotion. ---

def _isolated_push_env(groom_env, tmp_path, *, remote="local"):
    env = dict(groom_env["env"])
    env.pop("GROOM_NOPUSH", None)
    home_dir = tmp_path / "isolated-push-home"
    home_dir.mkdir()
    env["HOME"] = str(home_dir)
    env["USERPROFILE"] = str(home_dir)
    env["KNOWLEDGE_VAULT_REMOTE"] = remote
    return env, home_dir


def test_apply_clean_coverage_without_nopush_invokes_publish_and_reports_pushed(groom_env, tmp_path):
    env, home_dir = _isolated_push_env(groom_env, tmp_path)

    proc = subprocess.run(
        [PWSH, "-NoProfile", "-File", str(GROOM_PS1), "apply"],
        env=env, input="yes\n", capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr

    record = _audit_record(groom_env)
    assert record["coverage_status"] == "clean"
    assert record["promoted"] is True
    assert record["pushed"] is True

    log_path = home_dir / ".local" / "state" / "agent-sync.log"
    assert log_path.is_file()
    assert "push: skipped (Local-Only mode)" in log_path.read_text(encoding="utf-8")


def test_apply_dirty_coverage_blocks_push_and_exits_4(groom_env, tmp_path):
    for name in ("claude", "codex", "agy"):
        _write_stub(groom_env["bin_dir"], name, tranche=DIRTY_TRANCHE)
    env, _home_dir = _isolated_push_env(groom_env, tmp_path)

    proc = subprocess.run(
        [PWSH, "-NoProfile", "-File", str(GROOM_PS1), "apply"],
        env=env, input="yes\n", capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 4, proc.stdout + proc.stderr
    assert "UNTOUCHED" in proc.stderr

    record = _audit_record(groom_env)
    assert record["coverage_status"] == "dirty"
    assert record["promoted"] is False
    assert record["pushed"] is False


# --- Runner argv parity with the .sh twin: same mode, same runner, same
# fixture shape -> both wrappers must hand the runner CLI the same argv
# (module-relative differences like the interpreter itself aside). Shipping
# two wrappers only pays for itself if they actually agree; this is the
# regression net for that promise. Skipped on Windows CI, where `bash`
# resolves to WSL and can't run the .sh twin at all (same reasoning as
# test_vault_groom.py's own module skip). ---

@pytest.mark.skipif(os.name == "nt", reason="the .sh twin needs a real POSIX bash, not WSL.")
def test_write_pass_argv_shape_matches_the_sh_twin(tmp_path):
    def _make_env(bin_dir, record, vault, state_dir):
        env = dict(os.environ)
        env["VAULT"] = str(vault)
        env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
        env["GROOM_TEST_RECORD"] = str(record)
        env["GROOM_STATE_DIR"] = str(state_dir)
        env["GROOM_NOPUSH"] = "1"
        isolated_home = vault.parent / "isolated-home"
        isolated_home.mkdir(exist_ok=True)
        env["HOME"] = str(isolated_home)
        env["USERPROFILE"] = str(isolated_home)
        env.pop("AGENT_ENGINE_ROOT", None)
        env["GIT_AUTHOR_NAME"] = "Test"
        env["GIT_AUTHOR_EMAIL"] = "nexgen-tests.invalid"
        env["GIT_COMMITTER_NAME"] = "Test"
        env["GIT_COMMITTER_EMAIL"] = "nexgen-tests.invalid"
        return env

    def _seed_vault(root):
        vault = root / "vault"
        vault.mkdir()
        (vault / "03-INFRA" / "scripts").mkdir(parents=True)
        (vault / "03-INFRA" / "vault-grooming-playbook.md").write_text("playbook\n", encoding="utf-8")
        _git(vault, "init", "-q", "-b", "main")
        _git(vault, "config", "user.email", "nexgen-tests.invalid")
        _git(vault, "config", "user.name", "Test")
        _git(vault, "add", "-A")
        _git(vault, "commit", "-q", "-m", "seed")
        return vault

    sh_root = tmp_path / "sh"
    sh_root.mkdir()
    sh_bin = sh_root / "bin"
    sh_bin.mkdir()
    sh_record = sh_root / "record.json"
    for name in ("claude", "codex", "agy"):
        stub = sh_bin / name
        stub.write_text(_stub_source(FIXED_TRANCHE), encoding="utf-8")
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    sh_vault = _seed_vault(sh_root)
    sh_state = sh_root / "state"
    sh_env = _make_env(sh_bin, sh_record, sh_vault, sh_state)
    sh_proc = subprocess.run(
        ["bash", str(GROOM_SH), "apply"], env=sh_env, input="yes\n",
        capture_output=True, text=True, timeout=30,
    )
    assert sh_proc.returncode == 0, sh_proc.stdout + sh_proc.stderr
    sh_write_argv = json.loads(sh_record.read_text(encoding="utf-8"))[1]["argv"]

    ps1_root = tmp_path / "ps1"
    ps1_root.mkdir()
    ps1_bin = ps1_root / "bin"
    ps1_bin.mkdir()
    ps1_record = ps1_root / "record.json"
    for name in ("claude", "codex", "agy"):
        _write_stub(ps1_bin, name)
    ps1_vault = _seed_vault(ps1_root)
    ps1_state = ps1_root / "state"
    ps1_env = _make_env(ps1_bin, ps1_record, ps1_vault, ps1_state)
    ps1_proc = subprocess.run(
        [PWSH, "-NoProfile", "-File", str(GROOM_PS1), "apply"],
        env=ps1_env, input="yes\n", capture_output=True, text=True, timeout=60,
    )
    assert ps1_proc.returncode == 0, ps1_proc.stdout + ps1_proc.stderr
    ps1_write_argv = json.loads(ps1_record.read_text(encoding="utf-8"))[1]["argv"]

    # Same flags, same tool lists, same disallowed list -- the model name,
    # the plan-record path, and the working directory it forwards differ
    # only in per-run tmp paths, so compare everything except -p's value
    # and codex's -C value.
    def _normalize(argv):
        argv = list(argv)
        if "-p" in argv:
            argv[argv.index("-p") + 1] = "<prompt>"
        if "-C" in argv:
            argv[argv.index("-C") + 1] = "<workdir>"
        return argv

    assert _normalize(sh_write_argv) == _normalize(ps1_write_argv)
