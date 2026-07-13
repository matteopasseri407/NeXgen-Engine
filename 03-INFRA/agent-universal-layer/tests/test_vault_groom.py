"""Behavioral tests for vault-groom.sh (the gardener's hand).

The 2026-07-13 architect review, after an external REVISE verdict ("the
audit must be the only technical route a write can take to main and the
remotes"), redesigned the write path again: `apply`'s write pass no longer
runs against the real vault at all. After the typed "yes" and the TOCTOU
re-hash, the wrapper clones the vault into a throwaway dir under the state
dir and immediately removes that clone's `origin` remote -- `git push`
becomes mechanically impossible for the write pass, for any runner, not
just a prompt-level "don't push". The write pass's working directory is
inside that clone. `vault_groom_audit.py` then audits the clone (clean
working tree, linear history, path-exact coverage) and, only if that's
clean AND the real vault hasn't moved since the clone was made, PROMOTES
the clone's exact audited commit into the real vault (fetch + ff-only
merge) before appending the backlog line and optionally publishing. Any
audit failure leaves the real vault untouched and quarantines the clone.

.ps1 is not covered here (no pwsh on this runner); see test_vault_groom_ps1.py
for its own (skip-if-no-pwsh) behavioral tests, and mirror any finding here
into vault-groom.ps1 by hand for anything that test file doesn't cover.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import threading
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.name == "nt",
    reason="vault-groom.sh is the POSIX gardener launcher; Windows CI's `bash` "
           "resolves to WSL, which has no distribution installed there and fails "
           "immediately -- vault-groom.ps1 is covered separately in "
           "test_vault_groom_ps1.py (pwsh, not WSL bash).",
)

TESTS_DIR = Path(__file__).resolve().parent
REAL_UL = TESTS_DIR.parent
REAL_VAULT = REAL_UL.parent.parent
GROOM_SH = REAL_VAULT / "03-INFRA" / "scripts" / "vault-groom.sh"

# A proper markdown table -- PROPOSE_PROMPT requires "| Nota | Azione |
# Perché |" as the tranche's contract (this is what makes coverage checking
# possible at all). The target here (`stub-groomed.md`) matches exactly
# what the write-pass stub below actually commits, so the default apply
# flow in most of these tests has CLEAN coverage end to end -- a dedicated
# mismatched tranche is used separately for the dirty-coverage test.
FIXED_TRANCHE = (
    "| Nota | Azione | Perch\u00e9 |\n"
    "|---|---|---|\n"
    "| `stub-groomed.md` | **archive** | superseded by new-note.md |\n"
)

# Names a file the write-pass stub never touches -- guarantees dirty
# coverage (unaddressed target + the stub's real commit becomes unplanned).
DIRTY_TRANCHE = (
    "| Nota | Azione | Perch\u00e9 |\n"
    "|---|---|---|\n"
    "| `never-touched.md` | **archive** | ok |\n"
)


def _plan_bytes(tranche: str) -> bytes:
    """What the wrapper actually writes to the plan-record file: bash's
    $(cat ...) command substitution strips ALL trailing newlines from the
    propose log, then `printf '%s\\n'` adds exactly one back."""
    return (tranche.rstrip("\n") + "\n").encode()


def _git(vault, *args):
    return subprocess.run(
        ["git", "-C", str(vault), *args],
        capture_output=True, text=True, check=True,
    )


def _write_stub(bin_dir: Path, name: str, record_path: Path, tranche: str = FIXED_TRANCHE) -> None:
    # A plain Python script with its own shebang, run directly (not piped
    # through `python3 - <<HEREDOC`, which would consume the stub's OWN
    # stdin instead of the caller's real piped prompt).
    #
    # The stub plays BOTH roles the wrapper now invokes in one apply run:
    # the read-only propose pass (prints the given tranche so the test can
    # assert its sha256) and the write pass (detected by the presence of
    # "APPROVED TRANCHE" in the prompt it received, either via -p/--prompt
    # argv or piped stdin) -- which performs a REAL git commit in ITS OWN
    # CURRENT WORKING DIRECTORY, not a fixed vault path. That matters:
    # the temp-clone gate runs the write pass with its cwd inside the
    # throwaway clone (see vault-groom.sh), so this stub committing to
    # os.getcwd() is exactly "point the stub runners at the clone via cwd
    # and they keep working" -- the same stub source works unmodified for
    # the propose pass (real vault cwd, but read-only, never commits).
    #
    # Source factored out as _stub_source (below), not inlined here: the
    # .ps1 twin's own tests (test_vault_groom_ps1.py) import it too, on
    # Windows wrapping the identical source in a <name>.cmd/<name>.py pair
    # instead of an extensionless shebang file -- one stub implementation
    # for both wrapper twins' tests, not two copies that can drift apart.
    stub = bin_dir / name
    stub.write_text(_stub_source(tranche), encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)


def _stub_source(tranche: str) -> str:
    return (
        "#!/usr/bin/env python3\n"
        "import json, os, subprocess, sys, time\n"
        "argv = sys.argv[1:]\n"
        "stdin_data = ''\n"
        "try:\n"
        "    if not sys.stdin.isatty():\n"
        "        stdin_data = sys.stdin.read()\n"
        "except Exception:\n"
        "    pass\n"
        "prompt = stdin_data\n"
        "for flag in ('-p', '--prompt'):\n"
        "    if flag in argv:\n"
        "        prompt = argv[argv.index(flag) + 1]\n"
        "record_path = os.environ['GROOM_TEST_RECORD']\n"
        "try:\n"
        "    with open(record_path) as f:\n"
        "        records = json.load(f)\n"
        "except (FileNotFoundError, json.JSONDecodeError):\n"
        "    records = []\n"
        "records.append({'argv': argv, 'stdin': stdin_data, 'cwd': os.getcwd()})\n"
        "with open(record_path, 'w') as f:\n"
        "    json.dump(records, f)\n"
        "if 'APPROVED TRANCHE' in prompt:\n"
        "    vault = os.getcwd()\n"
        "    with open(os.path.join(vault, 'stub-groomed.md'), 'w') as f:\n"
        "        f.write('groomed by stub\\n')\n"
        "    subprocess.run(['git', '-C', vault, 'add', 'stub-groomed.md'], check=True)\n"
        "    subprocess.run(['git', '-C', vault, 'commit', '-m', 'stub: simulated grooming'], check=True)\n"
        "    fail_exit = os.environ.get('GROOM_TEST_FAIL_WRITE')\n"
        "    sync_file = os.environ.get('GROOM_TEST_SYNC_FILE')\n"
        "    if sync_file:\n"
        "        deadline = time.monotonic() + 10\n"
        "        while not os.path.exists(sync_file) and time.monotonic() < deadline:\n"
        "            time.sleep(0.05)\n"
        "    if fail_exit:\n"
        "        sys.exit(int(fail_exit))\n"
        "    print('stub-write-output')\n"
        "else:\n"
        "    print(" + repr(tranche) + ", end='')\n"
    )


def _stub_source_out_of_scope(tranche: str) -> str:
    """Same write-pass role as _stub_source, but ALSO commits a second file
    the tranche never named -- guarantees out_of_scope coverage without
    also leaving the tranche's own target unaddressed."""
    return (
        "#!/usr/bin/env python3\n"
        "import json, os, subprocess, sys\n"
        "argv = sys.argv[1:]\n"
        "stdin_data = ''\n"
        "try:\n"
        "    if not sys.stdin.isatty():\n"
        "        stdin_data = sys.stdin.read()\n"
        "except Exception:\n"
        "    pass\n"
        "prompt = stdin_data\n"
        "for flag in ('-p', '--prompt'):\n"
        "    if flag in argv:\n"
        "        prompt = argv[argv.index(flag) + 1]\n"
        "record_path = os.environ['GROOM_TEST_RECORD']\n"
        "try:\n"
        "    with open(record_path) as f:\n"
        "        records = json.load(f)\n"
        "except (FileNotFoundError, json.JSONDecodeError):\n"
        "    records = []\n"
        "records.append({'argv': argv, 'stdin': stdin_data, 'cwd': os.getcwd()})\n"
        "with open(record_path, 'w') as f:\n"
        "    json.dump(records, f)\n"
        "if 'APPROVED TRANCHE' in prompt:\n"
        "    vault = os.getcwd()\n"
        "    with open(os.path.join(vault, 'stub-groomed.md'), 'w') as f:\n"
        "        f.write('groomed by stub\\n')\n"
        "    with open(os.path.join(vault, 'unplanned-extra.md'), 'w') as f:\n"
        "        f.write('scope creep\\n')\n"
        "    subprocess.run(['git', '-C', vault, 'add', 'stub-groomed.md', 'unplanned-extra.md'], check=True)\n"
        "    subprocess.run(['git', '-C', vault, 'commit', '-m', 'stub: simulated grooming plus scope creep'], check=True)\n"
        "    print('stub-write-output')\n"
        "else:\n"
        "    print(" + repr(tranche) + ", end='')\n"
    )


def _stub_source_merge_commit(tranche: str) -> str:
    """Write-pass role that leaves a merge commit in the clone -- exercises
    vault_groom_audit.py's linear-history check end to end through the real
    wrapper, not just the audit script directly."""
    return (
        "#!/usr/bin/env python3\n"
        "import json, os, subprocess, sys\n"
        "argv = sys.argv[1:]\n"
        "stdin_data = ''\n"
        "try:\n"
        "    if not sys.stdin.isatty():\n"
        "        stdin_data = sys.stdin.read()\n"
        "except Exception:\n"
        "    pass\n"
        "prompt = stdin_data\n"
        "for flag in ('-p', '--prompt'):\n"
        "    if flag in argv:\n"
        "        prompt = argv[argv.index(flag) + 1]\n"
        "record_path = os.environ['GROOM_TEST_RECORD']\n"
        "try:\n"
        "    with open(record_path) as f:\n"
        "        records = json.load(f)\n"
        "except (FileNotFoundError, json.JSONDecodeError):\n"
        "    records = []\n"
        "records.append({'argv': argv, 'stdin': stdin_data, 'cwd': os.getcwd()})\n"
        "with open(record_path, 'w') as f:\n"
        "    json.dump(records, f)\n"
        "if 'APPROVED TRANCHE' in prompt:\n"
        "    vault = os.getcwd()\n"
        "    subprocess.run(['git', '-C', vault, 'checkout', '-q', '-b', 'side'], check=True)\n"
        "    with open(os.path.join(vault, 'side.md'), 'w') as f:\n"
        "        f.write('side\\n')\n"
        "    subprocess.run(['git', '-C', vault, 'add', 'side.md'], check=True)\n"
        "    subprocess.run(['git', '-C', vault, 'commit', '-q', '-m', 'side commit'], check=True)\n"
        "    subprocess.run(['git', '-C', vault, 'checkout', '-q', 'main'], check=True)\n"
        "    with open(os.path.join(vault, 'stub-groomed.md'), 'w') as f:\n"
        "        f.write('groomed by stub\\n')\n"
        "    subprocess.run(['git', '-C', vault, 'add', 'stub-groomed.md'], check=True)\n"
        "    subprocess.run(['git', '-C', vault, 'commit', '-q', '-m', 'stub: simulated grooming'], check=True)\n"
        "    subprocess.run(['git', '-C', vault, 'merge', '-q', '--no-ff', '-m', 'merge side', 'side'], check=True)\n"
        "    print('stub-write-output')\n"
        "else:\n"
        "    print(" + repr(tranche) + ", end='')\n"
    )


def _write_empty_stub(bin_dir: Path, name: str, record_path: Path) -> None:
    # Propose pass that produces nothing -- used to test the empty-proposal
    # abort path.
    stub = bin_dir / name
    stub.write_text(_empty_stub_source(), encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)


def _empty_stub_source() -> str:
    return (
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "argv = sys.argv[1:]\n"
        "record_path = os.environ['GROOM_TEST_RECORD']\n"
        "with open(record_path, 'w') as f:\n"
        "    json.dump([{'argv': argv}], f)\n"
    )


@pytest.fixture
def groom_env(tmp_path, monkeypatch):
    # $PLAYBOOK is genuinely vault-relative (README's install clones the
    # engine straight into ~/KnowledgeVault, and the playbook is meant to be
    # user-customizable content, so this fixture seeds a copy) -- but
    # $AUDIT_SCRIPT is resolved via vault-groom.sh's OWN real location
    # (SCRIPT_DIR, same pattern as vault-push.sh), never via $VAULT. GROOM_SH
    # below points at the real engine checkout, so the real
    # vault_groom_audit.py sitting right next to it is found automatically;
    # no copy needed here. (A $VAULT-relative AUDIT_SCRIPT was the actual bug
    # on the first live run, 2026-07-13: this fixture's OWN old copy-in trick
    # masked it in every test.)
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
        _write_stub(bin_dir, name, record)

    state_dir = tmp_path / "state"

    env = dict(os.environ)
    env["VAULT"] = str(vault)
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    env["GROOM_TEST_RECORD"] = str(record)
    env["GROOM_STATE_DIR"] = str(state_dir)
    env["GROOM_NOPUSH"] = "1"  # no remote configured in these fixtures
    # A real ~/.local/bin/agent-sync symlink would redirect ENGINE_SCRIPTS
    # elsewhere -- isolate HOME so these tests never pick up whatever the
    # machine actually running them has installed there.
    isolated_home = tmp_path / "isolated-home"
    isolated_home.mkdir()
    env["HOME"] = str(isolated_home)
    env.pop("AGENT_ENGINE_ROOT", None)
    # The temp-clone gate's clone is a fresh repo with no local git config
    # of its own (`git clone` never copies the source repo's LOCAL config),
    # and HOME is isolated above so there's no ~/.gitconfig to fall back on
    # either -- the write-pass stub's own commits need an identity from
    # somewhere. GIT_AUTHOR_*/GIT_COMMITTER_* override config entirely and
    # are inherited by every git subprocess this test tree spawns.
    env["GIT_AUTHOR_NAME"] = "Test"
    env["GIT_AUTHOR_EMAIL"] = "nexgen-tests.invalid"
    env["GIT_COMMITTER_NAME"] = "Test"
    env["GIT_COMMITTER_EMAIL"] = "nexgen-tests.invalid"
    return {"vault": vault, "env": env, "record": record, "state_dir": state_dir, "bin_dir": bin_dir}


def _run(groom_env, *args, extra_env=None, stdin_input=None):
    env = dict(groom_env["env"])
    env.update(extra_env or {})
    return subprocess.run(
        ["bash", str(GROOM_SH), *args],
        env=env,
        input=stdin_input,
        capture_output=True,
        text=True,
        timeout=30,
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
    # No mode argument at all -- the invariant this redesign restores: a
    # bare run can never modify the vault, full stop.
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
    assert len(recs) == 1, "preview must invoke the runner exactly once (read-only pass)"
    tools = recs[0]["argv"][recs[0]["argv"].index("--allowedTools") + 1:]
    assert "Edit" not in tools and "Write" not in tools
    assert FIXED_TRANCHE.strip() in proc.stdout


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
    assert _clone_dirs(groom_env) == [], "declining must never create a temp-clone"


def test_apply_eof_on_stdin_is_treated_as_declined(groom_env):
    # No real caller ever gets asked for confirmation and simply has no
    # stdin at all (e.g. invoked with </dev/null) -- must fail SAFE, never
    # silently proceed to write.
    head_before = _git(groom_env["vault"], "rev-parse", "HEAD").stdout.strip()
    proc = _run(groom_env, "apply", stdin_input="")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    recs = _records(groom_env)
    assert len(recs) == 1
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
    expected_hash = hashlib.sha256(_plan_bytes(FIXED_TRANCHE)).hexdigest()
    assert expected_hash in write_prompt

    # The write pass's cwd is the temp-clone gate's clone, never the vault.
    clone_dirs = _clone_dirs(groom_env)
    assert len(clone_dirs) == 1
    assert recs[1]["cwd"] == str(clone_dirs[0])
    assert recs[0]["cwd"] == str(groom_env["vault"]), "the propose pass still runs read-only against the real vault"


def test_write_prompt_references_plan_record_path_and_never_conditions_push(groom_env):
    proc = _run(groom_env, "apply", stdin_input="yes\n")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    recs = _records(groom_env)
    write_argv = recs[1]["argv"]
    write_prompt = write_argv[write_argv.index("-p") + 1]

    plan_records = list(groom_env["state_dir"].glob("*-plan.txt"))
    assert len(plan_records) == 1
    assert str(plan_records[0]) in write_prompt, "write prompt must point at the plan-record file"
    assert "Do NOT push" in write_prompt
    assert "then push" not in write_prompt, "push must never be presented as something the write pass decides"


def test_clone_has_no_remotes_after_setup(groom_env):
    # GROOM_KEEP_CLONE: a promoted clone is removed by default -- keep it
    # here so its remote list can be inspected post-run.
    proc = _run(groom_env, "apply", stdin_input="yes\n", extra_env={"GROOM_KEEP_CLONE": "1"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    clone_dirs = _clone_dirs(groom_env)
    assert len(clone_dirs) == 1
    remotes = _git(clone_dirs[0], "remote").stdout.strip()
    assert remotes == "", "the temp-clone gate must remove origin before the write pass ever runs"


def test_promoted_clone_is_removed_by_default(groom_env):
    # Without GROOM_KEEP_CLONE the state dir must not accumulate one full
    # vault copy per successful apply run.
    proc = _run(groom_env, "apply", stdin_input="yes\n")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _clone_dirs(groom_env) == []
    record = _audit_record(groom_env)
    assert record["promoted"] is True


def test_clean_run_promotes_exactly_the_audited_oid(groom_env):
    proc = _run(groom_env, "apply", stdin_input="yes\n", extra_env={"GROOM_KEEP_CLONE": "1"})
    assert proc.returncode == 0, proc.stdout + proc.stderr

    clone_dirs = _clone_dirs(groom_env)
    assert len(clone_dirs) == 1
    audited_oid = _git(clone_dirs[0], "rev-parse", "HEAD").stdout.strip()

    vault = groom_env["vault"]
    vault_head = _git(vault, "rev-parse", "HEAD").stdout.strip()
    assert vault_head != audited_oid, "the backlog commit lands ON TOP of the promoted OID"
    parent = _git(vault, "rev-parse", "HEAD~1").stdout.strip()
    assert parent == audited_oid
    log = _git(vault, "log", "--oneline").stdout
    assert "stub: simulated grooming" in log
    assert "chore(groom): record run" in log

    record = _audit_record(groom_env)
    assert record["promoted"] is True
    assert record["promoted_oid"] == audited_oid


def test_apply_confirmed_produces_real_commit_and_audit_record(groom_env):
    proc = _run(groom_env, "apply", stdin_input="yes\n")
    assert proc.returncode == 0, proc.stdout + proc.stderr

    record = _audit_record(groom_env)
    assert record["runner"] == "claude"
    assert record["pushed"] is False, "GROOM_NOPUSH=1 must omit --push-if-clean entirely"
    assert record["coverage_status"] == "clean"
    assert len(record["commits"]) == 1
    assert record["files_touched"] == ["stub-groomed.md"]
    expected_hash = hashlib.sha256(_plan_bytes(FIXED_TRANCHE)).hexdigest()
    assert record["tranche_sha256"] == expected_hash

    plan_records = list(groom_env["state_dir"].glob("*-plan.txt"))
    assert len(plan_records) == 1
    assert plan_records[0].read_text(encoding="utf-8").strip() == FIXED_TRANCHE.strip()

    backlog = (groom_env["vault"] / "99-INDEX" / "vault-cleanup-backlog.md").read_text(encoding="utf-8")
    assert "runner=claude" in backlog
    assert "commits=1" in backlog
    assert "coverage=clean" in backlog


def test_apply_toctou_guard_aborts_if_plan_record_changes_before_write_pass(groom_env):
    # Tampers the plan-record file strictly BEFORE handing "yes" to the
    # confirmation prompt -- a background feeder thread guarantees that
    # ordering (the wrapper's `read -r ANSWER` blocks on stdin until we
    # write to it), so this exercises the real re-hash guard in
    # vault-groom.sh, not a simulation of it.
    proc = subprocess.Popen(
        ["bash", str(GROOM_SH), "apply"],
        env=groom_env["env"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True,
    )

    # A dedicated reader thread, not a post-hoc proc.stdout.read(): the
    # feeder thread below needs to observe the confirmation banner WHILE
    # the process is still running (that's the signal $TRANCHE_HASH has
    # already been computed -- the banner prints strictly after it, right
    # before the blocking `read -r ANSWER`). Polling for the plan-record
    # FILE's mere existence is not enough: under load, this thread can see
    # the file the instant `>` creates it, before the wrapper's OWN next
    # line (the hash computation) has even run -- tampering into that
    # window makes the wrapper hash the TAMPERED text as if it were the
    # original, and the later re-hash trivially "matches" it, missing the
    # TOCTOU trigger entirely. Waiting for the banner removes the race.
    stdout_chunks: list[str] = []
    stdout_lock = threading.Lock()

    def read_stdout():
        # Character-by-character, NOT `for line in proc.stdout`: the
        # confirmation prompt itself (`printf 'Procedere? > '`) has no
        # trailing newline -- a readline-based iterator blocks forever
        # waiting for one that never comes before the human answers,
        # deadlocking this exact banner-detection wait.
        while True:
            ch = proc.stdout.read(1)
            if not ch:
                break
            with stdout_lock:
                stdout_chunks.append(ch)

    reader = threading.Thread(target=read_stdout)
    reader.start()

    def tamper_then_confirm():
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            with stdout_lock:
                seen = "".join(stdout_chunks)
            if "Procedere?" in seen:
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
    # entry (CPython's own implementation), which would race the feeder
    # thread's write and turn this into the EOF/declined path instead of the
    # tampered-confirm path this test exists to exercise. Wait for the
    # feeder to actually deliver "yes\n" first, then wait for the process.
    feeder = threading.Thread(target=tamper_then_confirm)
    feeder.start()
    feeder.join(timeout=20)
    proc.wait(timeout=20)
    reader.join(timeout=5)
    stdout = "".join(stdout_chunks)
    stderr = proc.stderr.read()

    assert proc.returncode == 1, stdout + stderr
    assert "plan record changed after approval" in stderr
    recs = _records(groom_env)
    assert len(recs) == 1, "a TOCTOU abort must never reach the write pass"
    assert _clone_dirs(groom_env) == [], "a TOCTOU abort must never even create a clone"


def test_apply_dirty_vault_working_tree_aborts_before_any_clone(groom_env):
    # The temp-clone gate needs a clean HEAD to clone from -- an uncommitted
    # change sitting in the real vault when "yes" lands must abort loudly,
    # zero writes, before a clone is ever made.
    (groom_env["vault"] / "uncommitted.md").write_text("oops\n", encoding="utf-8")
    proc = _run(groom_env, "apply", stdin_input="yes\n")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "not clean" in proc.stderr
    recs = _records(groom_env)
    assert len(recs) == 1, "a dirty-vault abort must never reach the write pass"
    assert _clone_dirs(groom_env) == []


def test_empty_proposal_aborts_before_any_confirmation_prompt(groom_env):
    empty_record = groom_env["record"].parent / "empty_record.json"
    for name in ("claude", "codex", "agy"):
        _write_empty_stub(groom_env["bin_dir"], name, empty_record)
    env = dict(groom_env["env"])
    env["GROOM_TEST_RECORD"] = str(empty_record)

    proc = subprocess.run(
        ["bash", str(GROOM_SH), "apply"],
        env=env, input="yes\n", capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 1
    assert "empty proposal" in proc.stderr
    recs = json.loads(empty_record.read_text(encoding="utf-8"))
    assert len(recs) == 1, "an empty proposal must never reach the write pass either"


def test_invalid_mode_rejected_before_any_runner_call(groom_env):
    proc = _run(groom_env, "bogus", stdin_input="")
    assert proc.returncode == 2
    assert "usage:" in proc.stderr
    assert not groom_env["record"].exists()


def test_codex_runner_propose_uses_read_only_sandbox(groom_env):
    proc = _run(groom_env, "preview", extra_env={"GROOM_RUNNER": "codex"}, stdin_input="")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    recs = _records(groom_env)
    argv = recs[0]["argv"]
    assert "exec" in argv
    assert "-s" in argv
    assert argv[argv.index("-s") + 1] == "read-only"
    assert "read-only planning pass" in recs[0]["stdin"]


def test_codex_runner_write_uses_workspace_write_sandbox_and_the_clone_as_workdir(groom_env):
    proc = _run(
        groom_env, "apply",
        extra_env={"GROOM_RUNNER": "codex", "GROOM_KEEP_CLONE": "1"},
        stdin_input="yes\n",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    recs = _records(groom_env)
    assert len(recs) == 2
    write_argv = recs[1]["argv"]
    assert write_argv[write_argv.index("-s") + 1] == "workspace-write"
    assert "APPROVED TRANCHE" in recs[1]["stdin"]
    clone_dirs = _clone_dirs(groom_env)
    assert len(clone_dirs) == 1
    assert write_argv[write_argv.index("-C") + 1] == str(clone_dirs[0]), "codex's -C must point at the clone, not the vault"


def test_agy_runner_propose_uses_plan_mode_and_sandbox(groom_env):
    proc = _run(groom_env, "preview", extra_env={"GROOM_RUNNER": "agy"}, stdin_input="")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    recs = _records(groom_env)
    argv = recs[0]["argv"]
    assert argv[argv.index("--mode") + 1] == "plan"
    assert "--sandbox" in argv


def test_agy_runner_write_uses_accept_edits_without_sandbox(groom_env):
    proc = _run(groom_env, "apply", extra_env={"GROOM_RUNNER": "agy"}, stdin_input="yes\n")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    recs = _records(groom_env)
    write_argv = recs[1]["argv"]
    assert write_argv[write_argv.index("--mode") + 1] == "accept-edits"
    assert "--sandbox" not in write_argv


def test_claude_write_pass_always_blocks_git_push_hard(groom_env):
    # Unconditional now (no longer gated on GROOM_NOPUSH): push is never
    # the write pass's decision, full stop -- belt-and-suspenders on top of
    # the temp-clone gate's origin-less clone. The default fixture already
    # sets GROOM_NOPUSH=1; this asserts the block regardless of that.
    proc = _run(groom_env, "apply", stdin_input="yes\n")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    recs = _records(groom_env)
    write_argv = recs[1]["argv"]
    assert "--disallowedTools" in write_argv
    disallowed = write_argv[write_argv.index("--disallowedTools") + 1:]
    assert "Bash(git push:*)" in disallowed


def test_opencode_runner_fails_loud_before_any_invocation(groom_env):
    proc = _run(groom_env, "preview", extra_env={"GROOM_RUNNER": "opencode"}, stdin_input="")
    assert proc.returncode == 2
    assert "no per-invocation permission-scoping flag" in proc.stderr
    assert not groom_env["record"].exists()


def test_unknown_runner_rejected(groom_env):
    proc = _run(groom_env, "preview", extra_env={"GROOM_RUNNER": "some-other-cli"}, stdin_input="")
    assert proc.returncode == 2
    assert "unknown GROOM_RUNNER" in proc.stderr


# --- The audit gate: out-of-scope edits, non-linear history, staleness,
# and a failing write-pass runner all must block promotion, quarantine the
# clone, and leave the real vault untouched. These exercise the real
# wrapper -> real audit script path end to end, not the audit script
# directly (see test_vault_groom_audit.py for that). ---

def test_apply_out_of_scope_edit_in_clone_quarantines_vault_head_unchanged(groom_env, tmp_path):
    for name in ("claude", "codex", "agy"):
        stub = groom_env["bin_dir"] / name
        stub.write_text(_stub_source_out_of_scope(FIXED_TRANCHE), encoding="utf-8")
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
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


def test_apply_merge_commit_in_clone_quarantines_for_non_linear_history(groom_env, tmp_path):
    for name in ("claude", "codex", "agy"):
        stub = groom_env["bin_dir"] / name
        stub.write_text(_stub_source_merge_commit(FIXED_TRANCHE), encoding="utf-8")
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    head_before = _git(groom_env["vault"], "rev-parse", "HEAD").stdout.strip()

    proc = _run(groom_env, "apply", stdin_input="yes\n")
    assert proc.returncode == 4, proc.stdout + proc.stderr

    head_after = _git(groom_env["vault"], "rev-parse", "HEAD").stdout.strip()
    assert head_after == head_before

    record = _audit_record(groom_env)
    assert record["promoted"] is False
    assert record["history_linear"] is False

    clone_dirs = _clone_dirs(groom_env)
    assert len(clone_dirs) == 1
    assert (clone_dirs[0] / ".GROOM_QUARANTINE.json").is_file()


def test_apply_vault_moved_mid_run_exits_5_and_does_not_promote(groom_env, tmp_path):
    sync_file = tmp_path / "sync-marker"
    env = dict(groom_env["env"])
    env["GROOM_TEST_SYNC_FILE"] = str(sync_file)
    result = {}

    def run_wrapper():
        result["proc"] = subprocess.run(
            ["bash", str(GROOM_SH), "apply"], env=env, input="yes\n",
            capture_output=True, text=True, timeout=30,
        )

    thread = threading.Thread(target=run_wrapper)
    thread.start()

    # Wait for the temp-clone gate's clone to appear -- BASE has been
    # captured by then -- and simulate a concurrent write landing on the
    # real vault while the (stub) write pass is still "running" (parked on
    # sync_file). BASE is now stale.
    deadline = time.monotonic() + 15
    clone_dirs = []
    while time.monotonic() < deadline and not clone_dirs:
        clone_dirs = _clone_dirs(groom_env)
        time.sleep(0.05)
    assert clone_dirs, "temp-clone gate never created a clone"

    (groom_env["vault"] / "concurrent.md").write_text("meanwhile\n", encoding="utf-8")
    _git(groom_env["vault"], "add", "concurrent.md")
    _git(groom_env["vault"], "commit", "-q", "-m", "concurrent unrelated write")
    moved_head = _git(groom_env["vault"], "rev-parse", "HEAD").stdout.strip()

    sync_file.write_text("go\n", encoding="utf-8")
    thread.join(timeout=30)
    proc = result["proc"]

    assert proc.returncode == 5, proc.stdout + proc.stderr
    assert "vault moved during grooming" in proc.stderr
    head_after = _git(groom_env["vault"], "rev-parse", "HEAD").stdout.strip()
    assert head_after == moved_head, "the real vault must be completely untouched by a stale run"

    record = _audit_record(groom_env)
    assert record["promoted"] is False


def test_apply_runner_non_zero_exit_still_writes_audit_record_and_quarantines(groom_env):
    env = dict(groom_env["env"])
    env["GROOM_TEST_FAIL_WRITE"] = "17"
    head_before = _git(groom_env["vault"], "rev-parse", "HEAD").stdout.strip()

    proc = subprocess.run(
        ["bash", str(GROOM_SH), "apply"], env=env, input="yes\n",
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode != 0, proc.stdout + proc.stderr

    head_after = _git(groom_env["vault"], "rev-parse", "HEAD").stdout.strip()
    assert head_after == head_before, "a failing write-pass runner must never reach promotion"

    record = _audit_record(groom_env)
    assert record["write_exit_code"] == 17
    assert record["promoted"] is False

    clone_dirs = _clone_dirs(groom_env)
    assert len(clone_dirs) == 1
    assert (clone_dirs[0] / ".GROOM_QUARANTINE.json").is_file()


# --- Push moved out of the LLM's hands: vault_groom_audit.py decides it,
# deterministically, from coverage, and only after a successful promotion.
# These exercise the real wrapper -> real audit script path end to end
# (KNOWLEDGE_VAULT_REMOTE=local makes agent_sync.py's publish() a real,
# successful no-op with no network/remote needed -- see agent_sync.py's own
# Local-Only-mode branch). ---

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
        ["bash", str(GROOM_SH), "apply"],
        env=env, input="yes\n", capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr

    record = _audit_record(groom_env)
    assert record["coverage_status"] == "clean"
    assert record["promoted"] is True
    assert record["pushed"] is True, "clean coverage + no GROOM_NOPUSH must invoke publish and report it pushed"

    log_path = home_dir / ".local" / "state" / "agent-sync.log"
    assert log_path.is_file()
    assert "push: skipped (Local-Only mode)" in log_path.read_text(encoding="utf-8")


def test_apply_dirty_coverage_blocks_push_and_exits_4(groom_env, tmp_path):
    for name in ("claude", "codex", "agy"):
        _write_stub(groom_env["bin_dir"], name, groom_env["record"], tranche=DIRTY_TRANCHE)
    env, _home_dir = _isolated_push_env(groom_env, tmp_path)

    proc = subprocess.run(
        ["bash", str(GROOM_SH), "apply"],
        env=env, input="yes\n", capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 4, proc.stdout + proc.stderr
    assert "UNTOUCHED" in proc.stderr

    record = _audit_record(groom_env)
    assert record["coverage_status"] == "dirty"
    assert record["promoted"] is False
    assert record["pushed"] is False
    assert record["unaddressed_targets"] == ["never-touched.md"]
    assert record["out_of_scope_targets"] == ["stub-groomed.md"]
