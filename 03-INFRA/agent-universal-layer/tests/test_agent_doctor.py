"""Test 14 su agent-doctor.sh: smoke in sandbox.

Nota di adattamento (dichiarata, non nascosta): agent-doctor.sh fa MOLTI check
contro infrastruttura reale hardcoded (porte di servizi locali, un backend
remoto raggiunto via SSH, variabili d'ambiente con URL/token, nomi di skill
specifiche dell'installazione) che NON possono mai passare in una
sandbox sintetica, a prescindere da quanto sia "sana" — non e' questo che il
test #14 deve provare. Il comportamento davvero testabile e specifico della
sandbox e' il meccanismo di drift-detection: iniettare un drift controllabile
(qui: un symlink rotto sotto ~/.agents/skills, esattamente l'esempio del
design) deve far AUMENTARE il numero di FAIL rispetto a una baseline nella
STESSA sandbox. Confrontiamo baseline vs drift, non l'exit code assoluto.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import run_agent_doctor, run_agent_sync

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_engine_upgrade import _make_consumer_engine_clone  # noqa: E402  (shared fixture helper, see that module)

pytestmark = pytest.mark.skipif(
    os.name == "nt",
    reason="agent-doctor.sh is POSIX-only; B2.5 Windows coverage is agent_sync.py smoke.",
)


def _parse_summary(stdout: str) -> tuple[int, int, int]:
    m = re.search(r"PASS=(\d+)\s+WARN=(\d+)\s+FAIL=(\d+)", stdout)
    assert m, f"riga di riepilogo --summary non trovata:\n{stdout}"
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def test_doctor_smoke_detects_injected_broken_symlink(sandbox):
    sb = sandbox
    for rt in (".claude/skills", ".codex/skills"):
        (sb.home / rt).mkdir(parents=True, exist_ok=True)
    priming = run_agent_sync(sb, "apply")
    assert priming.returncode == 0, priming.stdout + priming.stderr

    baseline = run_agent_doctor(sb, "--summary")
    base_pass, base_warn, base_fail = _parse_summary(baseline.stdout)

    # drift iniettato: un symlink rotto nella library non scoperta.
    library_link = sb.skill_library / "fake-skill-a"
    assert library_link.is_symlink(), "precondizione: la library deve gia' avere il link creato da agent-sync"
    library_link.unlink()
    library_link.symlink_to(sb.home / "questo-target-non-esiste-affatto")

    drifted = run_agent_doctor(sb, "--summary")
    drift_pass, drift_warn, drift_fail = _parse_summary(drifted.stdout)

    assert drift_fail > base_fail, (
        f"il drift iniettato non ha aumentato i FAIL (baseline={base_fail}, dopo drift={drift_fail})\n"
        f"baseline: {baseline.stdout}\ndrift: {drifted.stdout}"
    )
    assert "FAIL:" in drifted.stdout
    assert "fake-skill-a" in drifted.stdout or "ROTTE" in drifted.stdout, drifted.stdout


def test_doctor_warns_when_opencode_loads_canonical_instructions_twice(sandbox_with_live_configs):
    sandbox = sandbox_with_live_configs
    config_path = sandbox.live_config_path("opencode")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    canonical = "~/KnowledgeVault/03-INFRA/agent-universal-layer/instructions/AGENTS.md"
    config["instructions"] = [canonical, canonical.replace("/", "\\")]
    config_path.write_text(json.dumps(config), encoding="utf-8")

    result = run_agent_doctor(sandbox)

    assert "OpenCode loads the canonical AGENTS.md 2 times" in result.stdout


def test_doctor_reads_current_opencode_jsonc_and_preserves_runtime_visibility(sandbox):
    config_path = sandbox.home / ".config" / "opencode" / "opencode.jsonc"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        '{\n'
        '  // Current OpenCode creates JSONC by default.\n'
        '  "instructions": [\n'
        '    "~/KnowledgeVault/03-INFRA/agent-universal-layer/instructions/AGENTS.md",\n'
        '  ],\n'
        '}\n',
        encoding="utf-8",
    )

    result = run_agent_doctor(sandbox)

    assert "OpenCode instructions" in result.stdout
    assert "one canonical AGENTS.md" in result.stdout
    assert "opencode.jsonc: valid JSONC" in result.stdout
    assert f"missing {config_path}" not in result.stdout


def test_doctor_strict_finds_opencode_in_its_standard_user_install_path(sandbox):
    config_path = sandbox.home / ".config" / "opencode" / "opencode.jsonc"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        '{\n'
        '  "instructions": [\n'
        '    "~/KnowledgeVault/03-INFRA/agent-universal-layer/instructions/AGENTS.md"\n'
        '  ]\n'
        '}\n',
        encoding="utf-8",
    )
    opencode = sandbox.home / ".opencode" / "bin" / "opencode"
    opencode.parent.mkdir(parents=True, exist_ok=True)
    opencode.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' "
        "'fake-stdio-tool connected' "
        "'fake-http-api connected' "
        "'fake-cross-os-tool connected'\n",
        encoding="utf-8",
    )
    opencode.chmod(0o755)
    agy = sandbox.bin_stubs / "agy"
    agy.write_text(
        "#!/bin/sh\nprintf '%s\\n' 'fake-stdio-tool' 'fake-http-api' 'fake-cross-os-tool'\n",
        encoding="utf-8",
    )
    agy.chmod(0o755)
    env = sandbox.env()
    env["AGENT_ENGINE_ROOT"] = str(sandbox.vault / "03-INFRA")
    env["PATH"] = os.pathsep.join(
        entry for entry in env["PATH"].split(os.pathsep)
        if entry != str(sandbox.home / ".opencode" / "bin")
    )

    result = subprocess.run(
        ["bash", str(sandbox.scripts_dir / "agent-doctor.sh"), "--strict"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert "OpenCode mcp list" in result.stdout
    assert "opencode not found in PATH or ~/.opencode/bin" not in result.stdout


# ── A genuinely-absent OpenCode used to be a permanent, unfixable FAIL
# (missing $OCJSON, unconditionally) while Codex/Antigravity absent stayed a
# silent no-op elsewhere in the same script -- pure inconsistency that hits
# exactly the "third-party user with only one CLI installed" case. Confirmed
# bug: no code path anywhere ever creates opencode.jsonc for a CLI that was
# never installed, so the FAIL could never be cleared.

def test_opencode_genuinely_absent_is_a_warning_not_a_permanent_fail(sandbox):
    env = sandbox.env()
    # This suite may itself run on a machine with a real OpenCode install
    # (its bin dir would otherwise leak in via the real PATH sandbox.env()
    # appends after the stub dir) -- strip it so "genuinely absent" is
    # actually genuine, not accidentally masked by the host running pytest.
    env["PATH"] = os.pathsep.join(
        entry for entry in env["PATH"].split(os.pathsep)
        if "opencode" not in entry.lower()
    )

    result = subprocess.run(
        ["bash", str(sandbox.scripts_dir / "agent-doctor.sh")],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert _lines_with(result.stdout, "⚠", "OpenCode not installed here?"), result.stdout
    assert not _lines_with(result.stdout, "✗", "missing"), result.stdout


def test_opencode_installed_but_missing_config_still_fails(sandbox):
    """Guard against over-fixing: OpenCode IS on PATH but its config is
    missing is a real, fixable problem (render.py already bootstraps it),
    so it must stay a FAIL, not soften into a WARN."""
    opencode = sandbox.bin_stubs / "opencode"
    opencode.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    opencode.chmod(0o755)

    result = run_agent_doctor(sandbox)

    assert _lines_with(result.stdout, "✗", "missing"), result.stdout
    assert "OpenCode not installed here?" not in result.stdout, result.stdout


def test_opencode_absence_asymmetry_fix_present_in_both_twins():
    repo = Path(__file__).resolve().parents[3]
    bash = (repo / "03-INFRA/scripts/agent-doctor.sh").read_text(encoding="utf-8")
    powershell = (repo / "03-INFRA/scripts/agent-doctor.ps1").read_text(encoding="utf-8")
    assert "OpenCode not installed here?" in bash
    assert "OpenCode not installed here?" in powershell


def test_doctor_does_not_judge_host_local_claude_permissions(sandbox):
    # Permission posture is a host-local choice the engine must not grade:
    # bypassPermissions, a suppressed dangerous-mode prompt, and persistent
    # allow rules are all legitimate depending on the machine. The doctor
    # never comments on them (0.91.3 dropped the Claude-only judgement).
    claude_dir = sandbox.home / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    (claude_dir / "settings.json").write_text(
        json.dumps({
            "permissions": {"defaultMode": "bypassPermissions"},
            "skipDangerousModePermissionPrompt": True,
        }),
        encoding="utf-8",
    )
    (claude_dir / "settings.local.json").write_text(
        json.dumps({"permissions": {"allow": ["Bash(git:*)", "Bash(docker:*)"]}}),
        encoding="utf-8",
    )

    result = run_agent_doctor(sandbox)

    assert "bypassPermissions" not in result.stdout
    assert "dangerous-mode" not in result.stdout
    assert "unmanaged persistent allow rule" not in result.stdout
    assert "Claude security posture" not in result.stdout


def test_vault_library_probe_uses_mcp_protocol_headers():
    repo = Path(__file__).resolve().parents[3]
    bash = (repo / "03-INFRA/scripts/agent-doctor.sh").read_text(encoding="utf-8")
    powershell = (repo / "03-INFRA/scripts/agent-doctor.ps1").read_text(encoding="utf-8")

    assert "code -X OPTIONS" in bash
    assert "--server-url vault-library" in bash
    assert "Accept: application/json, text/event-stream" in bash
    assert "httpcode $VaultLibraryUrl" in powershell
    assert "--server-url vault-library" in powershell
    assert "Accept = \"application/json, text/event-stream\"" in powershell
    assert '"Options"' in powershell


def test_doctor_resolves_the_authoritative_remote_from_agent_sync():
    repo = Path(__file__).resolve().parents[3]
    bash = (repo / "03-INFRA/scripts/agent-doctor.sh").read_text(encoding="utf-8")
    powershell = (repo / "03-INFRA/scripts/agent-doctor.ps1").read_text(encoding="utf-8")

    assert "config authoritative_remote" in bash
    assert "config authoritative_remote" in powershell
    assert 'KNOWLEDGE_VAULT_REMOTE:-origin' not in bash
    assert 'else { "origin" }' not in powershell


def test_antigravity_quota_is_a_warning_not_a_false_mcp_failure(sandbox):
    agy = sandbox.bin_stubs / "agy"
    agy.write_text(
        "#!/bin/sh\nprintf '%s\\n' 'Error: Individual quota reached. Please upgrade your subscription.'\nexit 1\n",
        encoding="utf-8",
    )
    agy.chmod(0o755)

    result = run_agent_doctor(sandbox, "--strict")

    assert "Antigravity behavioral probe skipped: the selected model quota is unavailable" in result.stdout
    assert "Antigravity behavioral probe does not confirm" not in result.stdout


def test_doctor_sandbox_gate_skips_live_consumer_processes(sandbox):
    agy_marker = sandbox.home / "agy-was-run"
    opencode_marker = sandbox.home / "opencode-was-run"
    for name, marker in (("agy", agy_marker), ("opencode", opencode_marker)):
        stub = sandbox.bin_stubs / name
        stub.write_text(
            f"#!/bin/sh\n: > {marker}\nexit 0\n",
            encoding="utf-8",
        )
        stub.chmod(0o755)

    env = sandbox.env(NEXGEN_SKIP_LIVE_CONSUMER_PROBES="1")
    result = subprocess.run(
        ["bash", str(sandbox.scripts_dir / "agent-doctor.sh"), "--strict"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert "Antigravity behavioral probe skipped by the sandbox safety gate" in result.stdout
    assert "OpenCode consumer test skipped by the sandbox safety gate" in result.stdout
    assert not agy_marker.exists()
    assert not opencode_marker.exists()


# ── Local-Only vault-remote sentinel ("local"/"none") ────────────────────
# Architectural-review finding: agent_sync.py's pull()/publish() already
# special-case `env.remote in ("local", "none")` (the Local-Only sentinel
# from USER-PROFILE.md's "Environment variable: KNOWLEDGE_VAULT_REMOTE=local").
# agent-doctor.sh/.ps1 did NOT: the vault section tried a real `git fetch`
# against a remote literally named "local"/"none" (which never exists), and
# the resulting "?" ahead/behind comparison hard-FAILed a correctly
# configured Local-Only install every single run.

def _init_vault_git_repo(sandbox) -> None:
    subprocess.run(["git", "init", "-b", "main", str(sandbox.vault)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(sandbox.vault), "config", "user.email", "nexgen-tests.invalid"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(sandbox.vault), "config", "user.name", "NeXgen tests"],
        check=True, capture_output=True,
    )
    subprocess.run(["git", "-C", str(sandbox.vault), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(sandbox.vault), "commit", "-m", "fixture"], check=True, capture_output=True)


# These are exactly the connector-gating vars agent-doctor.sh's Mode-gating
# reads (N8N_MCP_TOKEN, and vault-library's own two, kept isolated too for
# the same reason). A dev machine actually running this project's own
# Cloud-Server stack can have these set for real in the ambient shell
# environment that sandbox.env() inherits via dict(os.environ); the
# Mode-gating tests below need deterministic control over them regardless
# of what the host running the suite happens to have configured.
_CONNECTOR_ENV_VARS = ("N8N_MCP_TOKEN", "VAULT_LIBRARY_TOKEN", "VAULT_LIBRARY_URL",
                       "FIRECRAWL_TUNNEL_PORT", "OCR_TUNNEL_PORT")


def _run_doctor(sandbox, *args: str, env_overrides: dict | None = None, timeout: int = 60,
                verbose: bool = True):
    """Like conftest.run_agent_doctor, but lets a test override env vars
    (KNOWLEDGE_VAULT_REMOTE, connector tokens, ...) -- run_agent_doctor()
    itself calls sandbox.env() with no extra kwargs, and sandbox.env()
    hardcodes KNOWLEDGE_VAULT_REMOTE=local, so overriding it (e.g. to
    "none", or to a real remote name for the Mode-gating tests) requires
    going through sandbox.env() directly. Always starts from a clean slate
    for _CONNECTOR_ENV_VARS (see above), then applies env_overrides on top."""
    sandbox.assert_is_sandbox()
    env = sandbox.env()
    for var in _CONNECTOR_ENV_VARS:
        env.pop(var, None)
    env.update(env_overrides or {})
    if verbose and "--verbose" not in args and "--summary" not in args:
        args = ("--verbose", *args)
    return subprocess.run(
        ["bash", str(sandbox.scripts_dir / "agent-doctor.sh"), *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


@pytest.mark.parametrize("sentinel", ["local", "none"])
def test_local_only_remote_sentinel_skips_fetch_and_never_fails(sandbox, sentinel):
    _init_vault_git_repo(sandbox)

    result = _run_doctor(sandbox, env_overrides={"KNOWLEDGE_VAULT_REMOTE": sentinel})

    assert "commits behind the cloud" not in result.stdout, result.stdout
    assert "the vault is not a git repo" not in result.stdout, result.stdout
    assert f"Local-Only mode ({sentinel})" in result.stdout, result.stdout


def test_non_local_remote_still_runs_the_real_fetch_ahead_behind_checks(sandbox):
    """Guard against over-fixing: a REAL remote name must still go through
    the fetch/ahead/behind path (and, since no such remote is configured in
    this sandbox, still legitimately FAIL) -- the Local-Only branch must not
    swallow genuine misconfiguration for non-local/non-none remotes."""
    _init_vault_git_repo(sandbox)

    result = _run_doctor(sandbox, env_overrides={"KNOWLEDGE_VAULT_REMOTE": "oracle"})

    assert "Local-Only mode" not in result.stdout, result.stdout
    assert "commits behind the cloud" in result.stdout, result.stdout


# ── A failed fetch used to discard git's real stderr ("(offline?)" is a
# guess, not a diagnosis) and let rev-list's own '?' fallback leak into the
# FAIL line as a literal, unexplained question mark ("✗ ? commits behind the
# cloud"). Confirmed bug: neither symptom tells the reader what actually
# happened or what to do about it.

def test_fetch_failure_surfaces_gits_real_stderr_not_a_guess(sandbox):
    _init_vault_git_repo(sandbox)

    result = _run_doctor(sandbox, env_overrides={"KNOWLEDGE_VAULT_REMOTE": "oracle"})

    assert "fetch oracle failed (offline?)" not in result.stdout, result.stdout
    assert "fetch oracle failed:" in result.stdout, result.stdout
    # git's real reason ("does not appear to be a git repository", or the
    # locale-equivalent) must show up verbatim, not be swallowed by 2>&1.
    assert "oracle" in result.stdout and "repository" in result.stdout, result.stdout


def test_fetch_failure_never_prints_a_literal_question_mark_as_a_commit_count(sandbox):
    _init_vault_git_repo(sandbox)

    result = _run_doctor(sandbox, env_overrides={"KNOWLEDGE_VAULT_REMOTE": "oracle"})

    assert "✗ ? commits behind the cloud" not in result.stdout, result.stdout
    assert "cannot tell how many commits behind the cloud" in result.stdout, result.stdout


def test_fetch_failure_message_also_present_in_summary_mode(sandbox):
    """The '?' placeholder used to leak into --summary output too (the text
    consumed by digests/alerts), not just the colored interactive view."""
    _init_vault_git_repo(sandbox)

    result = _run_doctor(sandbox, "--summary", env_overrides={"KNOWLEDGE_VAULT_REMOTE": "oracle"})

    assert "FAIL: ? commits behind the cloud" not in result.stdout, result.stdout
    assert "cannot tell how many commits behind the cloud" in result.stdout, result.stdout


def test_fetch_stderr_capture_present_in_both_twins():
    repo = Path(__file__).resolve().parents[3]
    bash = (repo / "03-INFRA/scripts/agent-doctor.sh").read_text(encoding="utf-8")
    powershell = (repo / "03-INFRA/scripts/agent-doctor.ps1").read_text(encoding="utf-8")
    assert "fetch $REMOTE failed (offline?)" not in bash
    assert "fetch $Remote failed (offline?)" not in powershell
    for content in (bash, powershell):
        assert "cannot tell how many commits behind the cloud" in content


# ── Mid-rebase/merge conflict must be named, not mislabeled as ordinary
# "commits behind" / "not committed" drift. Confirmed bug: a `git pull
# --rebase` that hits a conflict (the exact recovery agent_sync.py's own
# vault-push publish path tells the user to run by hand on a conflicting
# divergence) left main/HEAD mid-rebase; the old ahead/behind/dirty checks
# below could not tell that apart from ordinary unpushed commits or a
# routine dirty tree, and printed generic wording that invites a naive
# `git add -A && git commit` baking conflict markers straight into the vault.

def test_vault_mid_rebase_conflict_gets_a_dedicated_fail_not_generic_warnings(sandbox):
    _init_vault_git_repo(sandbox)
    rebase_merge = sandbox.vault / ".git" / "rebase-merge"
    rebase_merge.mkdir()
    (rebase_merge / "head-name").write_text("refs/heads/main\n", encoding="utf-8")

    result = _run_doctor(sandbox, env_overrides={"KNOWLEDGE_VAULT_REMOTE": "oracle"})

    assert "mid-rebase" in result.stdout, result.stdout
    assert "rebase --abort" in result.stdout, result.stdout
    assert "commits behind the cloud" not in result.stdout, result.stdout
    assert "unpublished local commits" not in result.stdout, result.stdout
    assert "tracked files not committed" not in result.stdout, result.stdout


@pytest.mark.parametrize("sentinel", ["local", "none"])
def test_vault_mid_rebase_conflict_detected_in_local_only_mode_too(sandbox, sentinel):
    """The guard sits before the Local-Only/remote-configured split, so a
    Local-Only install caught mid-rebase (e.g. from a manual `git rebase`
    against a mirror) must also get the dedicated fail, not the Local-Only
    branch's own generic "tracked files not committed" warning."""
    _init_vault_git_repo(sandbox)
    (sandbox.vault / ".git" / "rebase-apply").mkdir()

    result = _run_doctor(sandbox, env_overrides={"KNOWLEDGE_VAULT_REMOTE": sentinel})

    assert "mid-rebase" in result.stdout, result.stdout
    assert "tracked files not committed" not in result.stdout, result.stdout
    assert f"Local-Only mode ({sentinel})" not in result.stdout, result.stdout


def test_vault_mid_merge_conflict_gets_a_dedicated_fail(sandbox):
    _init_vault_git_repo(sandbox)
    (sandbox.vault / ".git" / "MERGE_HEAD").write_text(
        "0000000000000000000000000000000000000000\n", encoding="utf-8"
    )

    result = _run_doctor(sandbox, env_overrides={"KNOWLEDGE_VAULT_REMOTE": "oracle"})

    assert "mid-merge" in result.stdout, result.stdout
    assert "merge --abort" in result.stdout, result.stdout
    assert "commits behind the cloud" not in result.stdout, result.stdout


def test_vault_mid_rebase_guard_present_in_both_twins():
    repo = Path(__file__).resolve().parents[3]
    bash = (repo / "03-INFRA/scripts/agent-doctor.sh").read_text(encoding="utf-8")
    powershell = (repo / "03-INFRA/scripts/agent-doctor.ps1").read_text(encoding="utf-8")
    for content in (bash, powershell):
        assert "rebase-merge" in content
        assert "rebase-apply" in content
        assert "MERGE_HEAD" in content
        assert "mid-rebase" in content
        assert "mid-merge" in content
    assert 'fail "vault is mid-rebase' in bash
    assert 'fail "vault is mid-merge' in bash
    assert 'bad "vault is mid-rebase' in powershell
    assert 'bad "vault is mid-merge' in powershell


# ── Mode-based gating of MCP connector checks ─────────────────────────────
# Architectural-review finding: the "MCP connectors — reachability" and
# "Tokens in env" sections hard-FAILed unconditionally on n8n/firecrawl/
# vault-ocr, with no gating on the Mode declared in USER-PROFILE.md or on
# what the user actually configured -- unlike vault-library, which already
# WARNs (not FAILs) when VAULT_LIBRARY_URL is absent. A Cloud-Server user
# with a real missing connector and a Local-Only user who never needed one
# got the identical FAIL either way.

def _lines_with(stdout: str, marker: str, needle: str) -> list[str]:
    return [line for line in stdout.splitlines() if marker in line and needle in line]


def _stub_curl_always_unreachable(sandbox) -> None:
    """Deterministic http_code stub for agent-doctor's `code()` helper (which
    shells out to curl against hardcoded 127.0.0.1 ports). Some dev machines
    legitimately run real n8n/firecrawl/vault-ocr services on those exact
    ports (5678/33002/33003), which would otherwise make these Mode-gating
    tests flaky depending on what happens to be running locally. Forces
    every reachability probe to read as unreachable (000) regardless of the
    host's real local services, so the tests exercise only the gating LOGIC
    (Mode / env-var presence), never real network state."""
    curl_stub = sandbox.bin_stubs / "curl"
    curl_stub.write_text("#!/bin/sh\nprintf '000'\nexit 0\n", encoding="utf-8")
    curl_stub.chmod(0o755)


def _write_user_profile_mode(sandbox, mode_value: str) -> Path:
    profile = sandbox.vault / "99-INDEX" / "USER-PROFILE.md"
    profile.parent.mkdir(parents=True, exist_ok=True)
    profile.write_text(
        "# User Profile and Host Awareness\n\n"
        "## Architecture: Local-Only or Cloud-Server\n\n"
        f"- **Mode**: `{mode_value}`\n",
        encoding="utf-8",
    )
    return profile


def test_no_user_profile_treats_missing_connectors_as_ok_not_fail(sandbox):
    """Fresh sandbox has no USER-PROFILE.md at all: Mode must be treated as
    unknown (WARN, never a crash), and n8n/firecrawl/vault-ocr -- genuinely
    unreachable in this sandbox, with no token ever set -- must read as
    correct-by-design (OK), not FAIL."""
    _stub_curl_always_unreachable(sandbox)

    result = _run_doctor(sandbox)

    assert "Mode not found or not parseable" in result.stdout, result.stdout
    assert not _lines_with(result.stdout, "✗", "n8n-mcp (5678)"), result.stdout
    assert not _lines_with(result.stdout, "✗", "firecrawl (33002)"), result.stdout
    assert not _lines_with(result.stdout, "✗", "vault-ocr (33003)"), result.stdout
    assert not _lines_with(result.stdout, "✗", "N8N_MCP_TOKEN"), result.stdout
    assert _lines_with(result.stdout, "✓", "not expected in current Mode"), result.stdout


def test_mode_cloud_server_makes_a_really_missing_connector_fail(sandbox):
    """The bug this closes: a Cloud-Server install with a genuinely missing
    connector (nothing running/configured in this sandbox) must now produce
    a real FAIL instead of the same silent pass a Local-Only install gets."""
    _stub_curl_always_unreachable(sandbox)
    _write_user_profile_mode(sandbox, "CLOUD-SERVER")

    result = _run_doctor(sandbox)

    assert "USER-PROFILE.md declares Mode: CLOUD-SERVER" in result.stdout, result.stdout
    assert _lines_with(result.stdout, "✗", "n8n-mcp (5678)"), result.stdout
    assert _lines_with(result.stdout, "✗", "N8N_MCP_TOKEN missing"), result.stdout


def test_configured_env_var_is_checked_even_when_mode_says_local_only(sandbox):
    """Hard product requirement: Mode is a floor, never a ceiling. A user who
    declares LOCAL-ONLY but has already set N8N_MCP_TOKEN (mid-upgrade, or a
    single cloud connector added while staying local for the rest) must have
    that connector actually checked, not silently waved through just
    because Mode says Local-Only."""
    _stub_curl_always_unreachable(sandbox)
    _write_user_profile_mode(sandbox, "LOCAL-ONLY")

    result = _run_doctor(sandbox, env_overrides={"N8N_MCP_TOKEN": "fake-token-value"})

    assert "USER-PROFILE.md declares Mode: LOCAL-ONLY" in result.stdout, result.stdout
    assert "N8N_MCP_TOKEN present" in result.stdout, result.stdout
    # n8n itself is still unreachable in the sandbox: now expected (the var is
    # set), so it must FAIL for real instead of being waved through by Mode.
    assert _lines_with(result.stdout, "✗", "n8n-mcp (5678)"), result.stdout


# ── vault-library Mode-gating in "Tokens in env" (beta-readiness review,
# 2026-07-13) ───────────────────────────────────────────────────────────
# The bug this closes: unlike N8N_MCP_TOKEN two lines above it, the
# VAULT_LIBRARY_TOKEN/VAULT_LIBRARY_URL loop checked presence
# unconditionally, with no connector_expected() gating at all. A Local-Only
# install configured exactly as vault-write-architecture.md prescribes ("no
# VPS, no vault-library MCP container") got 2 permanent, unfixable FAILs on
# a component the architecture itself declares absent for that Mode.

def test_local_only_vault_library_tokens_not_expected(sandbox):
    _stub_curl_always_unreachable(sandbox)
    _write_user_profile_mode(sandbox, "LOCAL-ONLY")

    result = _run_doctor(sandbox)

    assert "USER-PROFILE.md declares Mode: LOCAL-ONLY" in result.stdout, result.stdout
    assert not _lines_with(result.stdout, "✗", "VAULT_LIBRARY_TOKEN missing"), result.stdout
    assert not _lines_with(result.stdout, "✗", "VAULT_LIBRARY_URL missing"), result.stdout
    assert _lines_with(result.stdout, "✓", "VAULT_LIBRARY_TOKEN not set"), result.stdout
    assert _lines_with(result.stdout, "✓", "VAULT_LIBRARY_URL not set"), result.stdout


def test_cloud_server_missing_vault_library_tokens_really_fail(sandbox):
    """Guard against over-fixing: a genuine Cloud-Server misconfiguration
    (tokens unset, Mode declares CLOUD-SERVER) must still FAIL for real,
    not be silently waved through by the same fix that frees Local-Only."""
    _stub_curl_always_unreachable(sandbox)
    _write_user_profile_mode(sandbox, "CLOUD-SERVER")

    result = _run_doctor(sandbox)

    assert "USER-PROFILE.md declares Mode: CLOUD-SERVER" in result.stdout, result.stdout
    assert _lines_with(result.stdout, "✗", "VAULT_LIBRARY_TOKEN missing"), result.stdout
    assert _lines_with(result.stdout, "✗", "VAULT_LIBRARY_URL missing"), result.stdout


# ── Bearer token off curl's argv (security audit finding, LOW) ───────────
# The vault-library reachability probe used to pass
# `-H "Authorization: Bearer $VAULT_LIBRARY_TOKEN"` straight on curl's argv,
# which any other local user can read via `ps` or /proc/<pid>/cmdline. The
# fix routes it through a curl config file (bearer_cfg(), mode 600) instead.
# This stub curl records every invocation's argv (to prove no argv element
# ever contains "Bearer ...") and, when it sees -K, copies that file's
# content out (to prove the header still actually reaches curl).

_CURL_STUB_PY = """#!/usr/bin/env python3
import os
import sys

args = sys.argv[1:]

argv_log = os.environ.get("CURL_STUB_ARGV_LOG")
if argv_log:
    with open(argv_log, "a", encoding="utf-8") as f:
        f.write(repr(args) + "\\n")

cfg_log = os.environ.get("CURL_STUB_CFG_LOG")
if cfg_log and "-K" in args:
    cfg_path = args[args.index("-K") + 1]
    try:
        with open(cfg_path, encoding="utf-8") as cf:
            content = cf.read()
    except OSError as exc:
        content = f"<unreadable: {exc}>"
    with open(cfg_log, "a", encoding="utf-8") as f:
        f.write(content)

sys.stdout.write("200" if ("-X" in args and "OPTIONS" in args) else "000")
"""


def _stub_curl_capture_bearer_cfg(sandbox) -> tuple[Path, Path]:
    curl_stub = sandbox.bin_stubs / "curl"
    curl_stub.write_text(_CURL_STUB_PY, encoding="utf-8")
    curl_stub.chmod(0o755)
    return sandbox.home / "curl-argv.log", sandbox.home / "curl-cfg.log"


def test_vault_library_bearer_token_never_appears_in_curl_argv(sandbox):
    argv_log, cfg_log = _stub_curl_capture_bearer_cfg(sandbox)

    result = _run_doctor(
        sandbox,
        env_overrides={
            "VAULT_LIBRARY_URL": "https://vault.example.invalid/mcp",
            "VAULT_LIBRARY_TOKEN": "super-secret-argv-must-not-see-this",
            "CURL_STUB_ARGV_LOG": str(argv_log),
            "CURL_STUB_CFG_LOG": str(cfg_log),
        },
    )

    assert "vault-library: 200 (up)" in result.stdout, result.stdout

    all_argv = argv_log.read_text(encoding="utf-8") if argv_log.exists() else ""
    assert "super-secret-argv-must-not-see-this" not in all_argv, (
        f"bearer token leaked into curl argv:\n{all_argv}"
    )
    assert "-K" in all_argv, f"expected the vault-library probe to use curl's -K/--config:\n{all_argv}"

    cfg_content = cfg_log.read_text(encoding="utf-8") if cfg_log.exists() else ""
    assert "Authorization: Bearer super-secret-argv-must-not-see-this" in cfg_content, (
        f"the -K config file did not carry the expected Authorization header:\n{cfg_content}"
    )


# ── --strict expected-server set now derived from the manifest, not
# hardcoded (beta-readiness review, 2026-07-13) ───────────────────────────
# The bug this closes: agent-doctor.sh (5 spots) and agent-doctor.ps1 (1
# shared literal, reused in 3 places) both hardcoded
# {firecrawl, n8n-mcp, vault-library, vault-ocr} for the --strict consumer
# checks. A manifest change (a server added/removed, a require_env gate
# flipped) silently went stale: the doctor kept checking for a set that no
# longer matched what agent-sync actually writes. Both twins now derive it
# at runtime via render.py --expected-servers, computed once near the top
# of the --strict block.

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(s: str) -> str:
    return ANSI_RE.sub("", s)


def test_strict_block_source_no_longer_hardcodes_the_4_server_set():
    """Content assertion on the SOURCE (not a run): the literal set/loop
    that used to enumerate {firecrawl, n8n-mcp, vault-library, vault-ocr}
    for the --strict checks must be gone from both twins, replaced by a
    render.py --expected-servers call."""
    repo = Path(__file__).resolve().parents[3]
    bash = (repo / "03-INFRA/scripts/agent-doctor.sh").read_text(encoding="utf-8")
    powershell = (repo / "03-INFRA/scripts/agent-doctor.ps1").read_text(encoding="utf-8")

    assert '{"firecrawl", "n8n-mcp", "vault-library", "vault-ocr"}' not in bash
    assert "for srv in firecrawl n8n-mcp vault-library vault-ocr" not in bash
    assert 'ag_probe_best_missing="firecrawl, n8n-mcp, vault-library, vault-ocr"' not in bash
    assert '@("firecrawl", "n8n-mcp", "vault-library", "vault-ocr")' not in powershell

    assert "--expected-servers antigravity" in bash
    assert "--expected-servers opencode" in bash
    assert "--expected-servers antigravity" in powershell
    assert "--expected-servers opencode" in powershell


def test_strict_block_skips_explicitly_when_the_expected_set_cant_be_derived():
    """Empty/undeliverable expected-server set (python3/python missing,
    render.py missing, or a genuinely empty manifest result) must produce an
    explicit skip/warn in both twins, never a silent pass -- comparing
    live config content against an EMPTY expected set would otherwise
    trivially "match" and read as a false green."""
    repo = Path(__file__).resolve().parents[3]
    bash = (repo / "03-INFRA/scripts/agent-doctor.sh").read_text(encoding="utf-8")
    powershell = (repo / "03-INFRA/scripts/agent-doctor.ps1").read_text(encoding="utf-8")

    assert "python3 not found -- cannot derive the expected MCP server set" in bash
    assert "Python 3 with PyYAML not found -- cannot derive the expected MCP server set" in powershell
    assert "render.py not found" in bash
    assert "render.py not found" in powershell
    for text in (bash, powershell):
        assert "no expected Antigravity MCP servers derived from the manifest" in text
        assert "no expected OpenCode MCP servers derived from the manifest" in text


def test_ps1_has_windows_path_persistence_check():
    """Release-critical check (Task 5, external-architect review accepted
    2026-07-13): the PRIMARY signal is actually resolving 'agent-sync' as a
    bare command in a FRESH process (execution-policy/PATHEXT problems
    surface this way, a PATH string substring match does not); the
    registry value (HKCU:\\Environment) and this process's own $env:Path
    remain as fallback/diagnostic detail only, not an alternate pass path.
    The plain remediation text stays the same either way."""
    repo = Path(__file__).resolve().parents[3]
    powershell = (repo / "03-INFRA/scripts/agent-doctor.ps1").read_text(encoding="utf-8")

    start = powershell.index("Windows PATH persistence")
    end = powershell.index("Architecture Mode", start)
    section = powershell[start:end]

    assert "Get-Command agent-sync" in section, "the primary probe must actually resolve the bare command"
    assert "-NoProfile" in section
    assert "HKCU:\\Environment" in section
    assert "Get-ItemProperty" in section
    assert ".local\\bin" in section
    assert "$env:Path" in section
    assert "run agent-sync apply, then open a NEW terminal" in section
    # the fresh-process resolution result must gate the pass/fail, not the
    # registry/process PATH detail alone.
    assert powershell.index("$freshProbeOk = ($LASTEXITCODE") < powershell.index('ok "agent-sync resolves')


def test_vault_push_remediation_text_matches_between_twins():
    """Task 6: both twins must say the same thing for the dangling-commits
    remediation, and it must stay nested under the non-Local-Only branch
    (Local-Only has no authoritative remote to be 'ahead' of)."""
    repo = Path(__file__).resolve().parents[3]
    bash = (repo / "03-INFRA/scripts/agent-doctor.sh").read_text(encoding="utf-8")
    powershell = (repo / "03-INFRA/scripts/agent-doctor.ps1").read_text(encoding="utf-8")

    assert "RUN vault-push!" in bash
    assert "RUN vault-push!" in powershell
    # sanity: the remediation line sits after the Local-Only sentinel branch
    # in both files, not before it (the ordering is what keeps Local-Only
    # from ever hitting this remediation).
    assert bash.index("Local-Only mode (") < bash.index("RUN vault-push!")
    assert powershell.index("Local-Only mode (") < powershell.index("RUN vault-push!")


def test_doctor_strict_derives_expected_servers_from_manifest_not_hardcoded(sandbox):
    """Behavioral proof (not just a source grep): the --strict OpenCode
    check must report the SANDBOX's synthetic manifest server names
    (fake-*), not a residual hardcoded {firecrawl, n8n-mcp, vault-library,
    vault-ocr} set that would happen to still look plausible."""
    opencode = sandbox.bin_stubs / "opencode"
    opencode.write_text("#!/bin/sh\necho 'nothing connected'\nexit 0\n", encoding="utf-8")
    opencode.chmod(0o755)
    # A real `agy` on the host running this test (this project's own dev
    # machine has one) would otherwise get invoked for real -- a live model
    # call with a 45s timeout, tried twice -- and blow the test's own
    # subprocess timeout. Stub it out fast and deterministic, same as
    # test_antigravity_quota_is_a_warning_not_a_false_mcp_failure above.
    agy = sandbox.bin_stubs / "agy"
    agy.write_text("#!/bin/sh\nprintf '%s\\n' 'fake-stdio-tool' 'fake-http-api' 'fake-cross-os-tool'\nexit 0\n", encoding="utf-8")
    agy.chmod(0o755)

    result = run_agent_doctor(sandbox, "--strict")
    clean = _strip_ansi(result.stdout)
    start = clean.index("CLI consumer conformance (--strict)")
    end = clean.index("Shared browser and defaults", start)
    strict_section = clean[start:end]

    assert "OpenCode mcp list does not confirm:" in strict_section, strict_section
    assert "fake-stdio-tool" in strict_section or "fake-http-api" in strict_section, strict_section
    assert "firecrawl" not in strict_section, strict_section
    assert "n8n-mcp" not in strict_section, strict_section


# ── render.py failure detail (2026-07-13 adversarial review) ─────────────
# cmd_diff() now isolates a broken CLI PER SECTION (a '>>> STOP: ...' line
# inside that CLI's own section) instead of aborting on the first one --
# agent-doctor.sh used to show only the LAST line of render.py's captured
# output, which is the trailing summary ("N CLI(s) STOPPED, see above"), not
# the actual reason. The fail message must surface the real STOP line(s).

def test_doctor_surfaces_the_actual_stop_line_not_just_the_last_output_line(sandbox_with_live_configs):
    # No agent-sync priming needed: the "MCP configured in the runtimes"
    # section runs render.py straight against whatever live config files
    # already exist under HOME, independent of an apply/guard cycle.
    sb = sandbox_with_live_configs
    claude_config = sb.live_config_path("claude")
    claude_config.write_text('{"mcpServers": ', encoding="utf-8")

    result = run_agent_doctor(sb)

    assert "render.py failed to run" in result.stdout
    assert ">>> STOP:" in result.stdout
    assert ".claude.json" in result.stdout, (
        "the fail message must carry render.py's own STOP line (which names "
        "the broken file), not just the trailing 'N CLI(s) STOPPED' summary"
    )


def test_doctor_sh_quotes_the_expected_server_argv_passing():
    """Source assertion (Task 4 follow-up): the unquoted `$expected_ag`
    word-split/glob-risk expansion into python3's argv must be gone,
    replaced by an array built with mapfile so each server name reaches
    python3 as its own literal argv element."""
    repo = Path(__file__).resolve().parents[3]
    bash = (repo / "03-INFRA/scripts/agent-doctor.sh").read_text(encoding="utf-8")

    assert 'python3 - "$AG_GLOBAL" $expected_ag <<' not in bash
    assert "mapfile -t expected_ag_arr" in bash
    assert '"${expected_ag_arr[@]}"' in bash


def test_doctor_ps1_strict_python_resolution_probes_python3_too():
    """The common resolver must retain the python3-first strict-runtime path.

    The resolver is deliberately outside the --strict section so every doctor
    operation shares one validated Python with PyYAML, rather than each block
    probing an inconsistent runtime independently.
    """
    repo = Path(__file__).resolve().parents[3]
    powershell = (repo / "03-INFRA/scripts/agent-doctor.ps1").read_text(encoding="utf-8")

    resolver_start = powershell.index("function Resolve-NexgenPython")
    resolver_end = powershell.index("$NexgenPython = Resolve-NexgenPython", resolver_start)
    resolver = powershell[resolver_start:resolver_end]
    assert 'foreach ($name in @(\"python3\", \"python\"))' in resolver, resolver
    assert 'import sys, yaml' in resolver, resolver
    assert 'sys.version_info >= (3, 10)' in resolver, resolver


# --- New-version alert on the DEFAULT single-clone install (2026-07-14) -----
#
# docs/upgrade.md used to say the update warning only existed for the split
# consumer-clone topology; these lock in the single-clone variant: a doctor
# run must TELL the user a newer released tag exists, informationally (warn,
# never fail), and must stay silent on a pure data vault that doesn't track
# the engine at all.


def _git(cwd, *args):
    subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True
    )


def _make_engine_tracking_vault(sb, running_version, released):
    """Turns the sandbox vault into a single-clone engine checkout: VERSION
    committed on main, plus a local bare `origin` whose main carries the
    given (version, tag) releases."""
    vault = sb.vault
    _git(vault, "init", "-q", "-b", "main")
    _git(vault, "config", "user.name", "sb")
    _git(vault, "config", "user.email", "sb@localhost")
    (vault / "VERSION").write_text(running_version + "\n", encoding="utf-8")
    _git(vault, "add", "-A")
    _git(vault, "commit", "-qm", f"sandbox at {running_version}")

    origin = vault.parent / "engine-origin.git"
    subprocess.run(
        ["git", "clone", "-q", "--bare", str(vault), str(origin)],
        check=True, capture_output=True,
    )
    work = vault.parent / "engine-work"
    subprocess.run(
        ["git", "clone", "-q", str(origin), str(work)],
        check=True, capture_output=True,
    )
    _git(work, "config", "user.name", "sb")
    _git(work, "config", "user.email", "sb@localhost")
    for version, tag in released:
        (work / "VERSION").write_text(version + "\n", encoding="utf-8")
        _git(work, "add", "-A")
        _git(work, "commit", "-qm", f"release {version}", "--allow-empty")
        _git(work, "tag", tag)
    _git(work, "push", "-q", "origin", "main", "--tags")
    _git(vault, "remote", "add", "origin", str(origin))


def test_single_clone_update_alert_warns_when_a_newer_tag_exists(sandbox):
    sb = sandbox
    _make_engine_tracking_vault(sb, "0.4.0", [("0.4.0", "v0.4.0"), ("0.5.0", "v0.5.0")])
    result = run_agent_doctor(sb)
    assert "Engine version (single-clone install)" in result.stdout
    assert "NeXgen Engine update available: v0.5.0 (this machine runs v0.4.0)" in result.stdout
    assert "run: nexgen-update" in result.stdout


def test_single_clone_update_alert_ok_at_latest(sandbox):
    sb = sandbox
    _make_engine_tracking_vault(sb, "0.5.0", [("0.5.0", "v0.5.0")])
    result = run_agent_doctor(sb)
    assert "engine at (or ahead of) the latest released version (v0.5.0" in result.stdout
    assert "NeXgen Engine update available" not in result.stdout


def test_single_clone_update_alert_skips_a_pure_data_vault(sandbox):
    # No VERSION file at the vault root -> the section must not run at all.
    result = run_agent_doctor(sandbox)
    assert "Engine version (single-clone install)" not in result.stdout


def test_single_clone_update_alert_parity_with_the_ps1_twin():
    repo = Path(__file__).resolve().parents[3]
    bash = (repo / "03-INFRA/scripts/agent-doctor.sh").read_text(encoding="utf-8")
    powershell = (repo / "03-INFRA/scripts/agent-doctor.ps1").read_text(encoding="utf-8")
    for content in (bash, powershell):
        assert "Engine version (single-clone install)" in content
        # Contract, not prose: the notice names the command to run, in both twins.
        assert "NeXgen Engine update available" in content
        assert "run: nexgen-update" in content
    # Informational-only contract: the alert is a warn, never a fail, in
    # both twins.
    assert 'fail "NeXgen Engine update available' not in bash
    assert 'bad "NeXgen Engine update available' not in powershell


# ── S2's pin-freshness check verified only HEAD sha, never whether the
# consumer engine clone's working tree is dirty -- a hand-edited tracked file
# at an otherwise correctly-pinned HEAD used to report a clean "OK" with no
# signal that the checkout no longer matches the release byte-for-byte.

def test_s2_pin_check_ok_on_a_genuinely_clean_pinned_clone(sandbox):
    consumer = _make_consumer_engine_clone(sandbox, "v0.2.0")
    result = run_agent_doctor(sandbox)

    assert "consumer engine at the pinned version" in result.stdout, result.stdout
    assert "consumer engine working tree clean (tracked files)" in result.stdout, result.stdout
    assert "tracked file(s) modified in the consumer engine clone" not in result.stdout, result.stdout
    assert consumer.exists()  # sanity: fixture actually created the clone


def test_s2_pin_check_warns_when_a_correctly_pinned_clone_is_dirty(sandbox):
    """The bug: HEAD sha still matches the pin, but a tracked file was
    hand-edited without committing -- this must no longer read as a clean
    OK."""
    consumer = _make_consumer_engine_clone(sandbox, "v0.2.0")
    (consumer / "VERSION").write_text("0.2.0-hand-edited\n", encoding="utf-8")

    result = run_agent_doctor(sandbox)

    assert "consumer engine at the pinned version" in result.stdout, result.stdout
    assert "1 tracked file(s) modified in the consumer engine clone" in result.stdout, result.stdout
    assert "consumer engine working tree clean" not in result.stdout, result.stdout


def test_s2_dirty_tree_check_independent_of_a_sha_mismatch(sandbox):
    """The dirty-tree warning must fire on top of a sha-mismatch FAIL too,
    not only on an otherwise-clean pin match -- they are separate signals."""
    consumer = _make_consumer_engine_clone(sandbox, "v0.1.0")
    (consumer / "VERSION").write_text("0.1.0-hand-edited\n", encoding="utf-8")
    # Force a genuine sha mismatch too (editing a tracked file without
    # committing never changes HEAD): point the pin at a sha this clone
    # never checked out.
    pin_file = sandbox.vault / "99-INDEX" / "ENGINE-PIN.txt"
    pin_file.write_text(("0" * 40) + "\n", encoding="utf-8")

    result = run_agent_doctor(sandbox)

    assert "silent drift" in result.stdout, result.stdout
    assert "1 tracked file(s) modified in the consumer engine clone" in result.stdout, result.stdout


def test_s2_dirty_tree_check_present_in_both_twins():
    repo = Path(__file__).resolve().parents[3]
    bash = (repo / "03-INFRA/scripts/agent-doctor.sh").read_text(encoding="utf-8")
    powershell = (repo / "03-INFRA/scripts/agent-doctor.ps1").read_text(encoding="utf-8")
    for content in (bash, powershell):
        assert "consumer engine working tree clean" in content
        assert "tracked file(s) modified in the consumer engine clone" in content


# ── Canonical bootstrap hygiene: size budget + load-on-demand pointer
# integrity (competitor-borrow Tier 1, 2026-07-17) ────────────────────────
# Two additive, read-only, WARN-only doctor checks. They must catch a bloated
# bootstrap and a dangling load-on-demand pointer, must skip the literal
# 03-INFRA/<topic>.md placeholder in the editing-discipline prose, and must
# NEVER turn a green doctor red (informational only). Mirrored in both twins.

def _write_canon(sandbox, text: str) -> Path:
    canon = sandbox.ul / "instructions" / "AGENTS.md"
    canon.parent.mkdir(parents=True, exist_ok=True)
    canon.write_text(text, encoding="utf-8")
    return canon


def test_bootstrap_size_budget_warns_over_and_never_fails(sandbox):
    _write_canon(sandbox, "# bootstrap\n" + ("x" * 200))
    result = _run_doctor(sandbox, env_overrides={"NEXGEN_BOOTSTRAP_MAX_BYTES": "10"})
    assert _lines_with(result.stdout, "⚠", "over the 10-byte budget"), result.stdout
    # informational-only: an oversized bootstrap must never be a FAIL.
    assert not _lines_with(result.stdout, "✗", "bootstrap AGENTS.md"), result.stdout


def test_bootstrap_size_budget_ok_when_under(sandbox):
    _write_canon(sandbox, "# small bootstrap\n")
    result = _run_doctor(sandbox)
    assert _lines_with(result.stdout, "✓", "bootstrap AGENTS.md within budget"), result.stdout


def test_load_on_demand_pointer_dangling_warns_not_fails(sandbox):
    _write_canon(
        sandbox,
        "Load details:\n\n- Ghost note: `03-INFRA/ghost-note-nonexistent.md`\n",
    )
    result = _run_doctor(sandbox)
    assert _lines_with(result.stdout, "⚠", "03-INFRA/ghost-note-nonexistent.md"), result.stdout
    assert not _lines_with(result.stdout, "✗", "ghost-note-nonexistent"), result.stdout


def test_load_on_demand_pointer_resolves_ok(sandbox):
    note = sandbox.vault / "03-INFRA" / "real-note.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("real\n", encoding="utf-8")
    _write_canon(sandbox, "Load details:\n\n- Real note: `03-INFRA/real-note.md`\n")
    result = _run_doctor(sandbox)
    assert _lines_with(result.stdout, "✓", "load-on-demand pointers resolve"), result.stdout


def test_pointer_integrity_skips_the_topic_placeholder(sandbox):
    # The literal 03-INFRA/<topic>.md in the editing-discipline prose must be
    # skipped (angle brackets), never reported as a dangling pointer.
    _write_canon(sandbox, "create `03-INFRA/<topic>.md` and add a pointer.\n")
    result = _run_doctor(sandbox)
    assert not _lines_with(result.stdout, "⚠", "<topic>"), result.stdout
    assert _lines_with(result.stdout, "✓", "no vault-relative bootstrap pointers to verify"), result.stdout


def test_bootstrap_hygiene_present_and_warn_only_in_both_twins():
    repo = Path(__file__).resolve().parents[3]
    bash = (repo / "03-INFRA/scripts/agent-doctor.sh").read_text(encoding="utf-8")
    powershell = (repo / "03-INFRA/scripts/agent-doctor.ps1").read_text(encoding="utf-8")
    for content in (bash, powershell):
        assert "Canonical bootstrap hygiene" in content
        assert "NEXGEN_BOOTSTRAP_MAX_BYTES" in content
        assert "NEXGEN_NOTE_MAX_BYTES" in content
    # informational-only contract: never a fail/bad on these three checks.
    assert 'fail "bootstrap AGENTS.md' not in bash
    assert 'fail "oversized detail note' not in bash
    assert 'fail "bootstrap load-on-demand' not in bash
    assert 'bad "bootstrap AGENTS.md' not in powershell
    assert 'bad "oversized detail note' not in powershell
    assert 'bad "bootstrap load-on-demand' not in powershell


# ── Required invariant-rules drift guard (competitor-borrow #4, 2026-07-17) ──
# The doctor WARNs (never fails) when the canonical AGENTS.md is missing a
# non-negotiable rule declared in required-rules.txt. It skips silently when
# the rules file isn't present in the engine tree. CI enforces the same on the
# shipped public AGENTS.md (as a hard failure). Mirrored in both twins.

def test_required_rules_guard_warns_when_a_rule_is_missing(sandbox):
    (sandbox.ul / "instructions" / "required-rules.txt").write_text(
        "Alpha Invariant\nBeta Invariant\n", encoding="utf-8")
    (sandbox.ul / "instructions" / "AGENTS.md").write_text(
        "only Alpha Invariant here\n", encoding="utf-8")
    result = _run_doctor(sandbox)
    assert _lines_with(result.stdout, "⚠", "missing required invariant rule"), result.stdout
    assert "Beta Invariant" in result.stdout
    assert not _lines_with(result.stdout, "✗", "required invariant rule"), result.stdout


def test_required_rules_guard_ok_when_all_present(sandbox):
    (sandbox.ul / "instructions" / "required-rules.txt").write_text(
        "Alpha Invariant\nBeta Invariant\n", encoding="utf-8")
    (sandbox.ul / "instructions" / "AGENTS.md").write_text(
        "Alpha Invariant and Beta Invariant both present\n", encoding="utf-8")
    result = _run_doctor(sandbox)
    assert _lines_with(result.stdout, "✓", "carries all required invariant rules"), result.stdout


def test_required_rules_guard_present_and_warn_only_in_both_twins():
    repo = Path(__file__).resolve().parents[3]
    bash = (repo / "03-INFRA/scripts/agent-doctor.sh").read_text(encoding="utf-8")
    powershell = (repo / "03-INFRA/scripts/agent-doctor.ps1").read_text(encoding="utf-8")
    for content in (bash, powershell):
        assert "check_required_rules.py" in content
        assert "required invariant rule" in content
    assert 'fail "canonical AGENTS.md is missing required invariant' not in bash
    assert 'bad "canonical AGENTS.md is missing required invariant' not in powershell


# ── G1-contraddizione: vault-library reachability must agree with the later
# "Tokens in env" check for the SAME variable in the SAME run, not contradict
# it (review 2026-07-30). Before the fix, an unconditional warn here fired
# even under Local-Only/unknown Mode while "Tokens in env" said "ok, not
# expected" a few lines later for the identical unset VAULT_LIBRARY_URL.

def test_local_only_vault_library_reachability_agrees_with_tokens_section(sandbox):
    _stub_curl_always_unreachable(sandbox)
    _write_user_profile_mode(sandbox, "LOCAL-ONLY")

    result = _run_doctor(sandbox)

    assert "USER-PROFILE.md declares Mode: LOCAL-ONLY" in result.stdout, result.stdout
    assert "VAULT_LIBRARY_URL not in env" not in result.stdout, result.stdout
    assert _lines_with(result.stdout, "✓", "vault-library: not configured"), result.stdout


def test_cloud_server_vault_library_reachability_really_fails(sandbox):
    """Guard against over-fixing: Cloud-Server with no derivable vault-library
    endpoint at all must now FAIL in the reachability section too (it used to
    only warn there while the Tokens section correctly FAILed)."""
    _stub_curl_always_unreachable(sandbox)
    _write_user_profile_mode(sandbox, "CLOUD-SERVER")

    result = _run_doctor(sandbox)

    assert "USER-PROFILE.md declares Mode: CLOUD-SERVER" in result.stdout, result.stdout
    assert _lines_with(result.stdout, "✗", "vault-library: no endpoint resolved"), result.stdout


def test_vault_library_contradiction_fix_present_in_both_twins():
    repo = Path(__file__).resolve().parents[3]
    bash = (repo / "03-INFRA/scripts/agent-doctor.sh").read_text(encoding="utf-8")
    powershell = (repo / "03-INFRA/scripts/agent-doctor.ps1").read_text(encoding="utf-8")
    assert 'warn "VAULT_LIBRARY_URL not in env"' not in bash
    assert 'warn "VAULT_LIBRARY_URL not in env"' not in powershell
    assert "vault-library: not configured -- not expected in current Mode" in bash
    assert "vault-library: not configured - not expected in current Mode" in powershell
    for content in (bash, powershell):
        assert "vault-library: no endpoint resolved" in content


# ── G5-ollama: the check runs on any Linux host (server or laptop), so the
# message must not assume hardware it cannot know about.

def test_ollama_message_is_hardware_neutral():
    repo = Path(__file__).resolve().parents[3]
    bash = (repo / "03-INFRA/scripts/agent-doctor.sh").read_text(encoding="utf-8")
    assert "on the laptop" not in bash
    assert 'ok "Ollama running (emergency local fallback, not the routing worker)"' in bash


# ── G6-symlink: the ~/ANTIGRAVITY.md comment must describe what agent_sync.py
# actually does today (actively removes the dead symlink), not claim it
# "exists" in the present tense.

def test_antigravity_symlink_comment_reflects_current_removal_behavior():
    repo = Path(__file__).resolve().parents[3]
    bash = (repo / "03-INFRA/scripts/agent-doctor.sh").read_text(encoding="utf-8")
    assert "that symlink exists" not in bash
    assert "actively removes ~/ANTIGRAVITY.md" in bash


# ── BUG-B: a manifest server dropped by require_env must name the missing
# variable and its consequence, gated by the same Mode logic as every other
# connector check (incident 2026-07-30: N8N_MCP_TOKEN silently dropped
# n8n-mcp from all 4 CLIs and the doctor never named the variable).

def _add_require_env_server(sandbox, server_name: str, var_name: str, targets=("claude",)) -> None:
    """Appends one more server entry (require_env-gated) to the sandbox's
    already-copied synthetic fixture manifest, instead of replacing it, so
    the existing fake-* entries other checks may rely on stay intact."""
    manifest_path = sandbox.mcp_dir / "manifest.yaml"
    existing = manifest_path.read_text(encoding="utf-8")
    addition = (
        f"\n  {server_name}:\n"
        "    transport: stdio\n"
        "    command: fake-cmd\n"
        f"    require_env: {var_name}\n"
        f"    targets: [{', '.join(targets)}]\n"
    )
    manifest_path.write_text(existing + addition, encoding="utf-8")


def test_bugb_require_env_skip_is_silent_when_not_expected_in_current_mode(sandbox):
    _add_require_env_server(sandbox, "fake-conditional-tool", "FAKE_CONDITIONAL_VAR")

    result = _run_doctor(sandbox)

    assert "FAKE_CONDITIONAL_VAR" not in result.stdout, result.stdout


def test_bugb_require_env_skip_warns_naming_the_variable_when_expected(sandbox):
    _add_require_env_server(sandbox, "fake-conditional-tool", "FAKE_CONDITIONAL_VAR")
    _write_user_profile_mode(sandbox, "CLOUD-SERVER")

    result = _run_doctor(sandbox)

    assert _lines_with(result.stdout, "⚠", "FAKE_CONDITIONAL_VAR missing"), result.stdout
    assert "fake-conditional-tool" in result.stdout, result.stdout
    assert "not mounted on any CLI" in result.stdout, result.stdout


def test_bugb_require_env_skip_is_silent_once_the_variable_is_set(sandbox):
    _add_require_env_server(sandbox, "fake-conditional-tool", "FAKE_CONDITIONAL_VAR")
    _write_user_profile_mode(sandbox, "CLOUD-SERVER")

    result = _run_doctor(sandbox, env_overrides={"FAKE_CONDITIONAL_VAR": "set"})

    assert "FAKE_CONDITIONAL_VAR" not in result.stdout, result.stdout


def test_require_env_skip_visibility_present_in_both_twins():
    repo = Path(__file__).resolve().parents[3]
    bash = (repo / "03-INFRA/scripts/agent-doctor.sh").read_text(encoding="utf-8")
    powershell = (repo / "03-INFRA/scripts/agent-doctor.ps1").read_text(encoding="utf-8")
    for content in (bash, powershell):
        assert "load_mcp_manifest_document" in content
        assert "is not mounted on any CLI" in content


# ── GUARDRAIL: a CLI running in "bypass" permission posture (no confirmation
# prompts) without a declared PreToolUse guardrail hook must be reported
# visibly -- as a WARN, never a FAIL. Nobody reaches that state by accident:
# it exists only because the instance manifest deliberately declares the
# posture and no hook targeting that CLI, so it is a standing choice rather
# than a fault, and a FAIL nobody can ever clear leaves the doctor permanently
# red and every alert channel firing until the reader stops reading them.
# A missing instance permissions manifest (the public-engine default) is a
# complete, silent no-op.

def _write_permissions_manifest(sandbox, *, posture: dict, hooks: list) -> Path:
    perms_dir = sandbox.ul / "permissions"
    perms_dir.mkdir(parents=True, exist_ok=True)
    lines = ["schema_version: 1", "posture:"]
    for cli, value in posture.items():
        lines.append(f"  {cli}: {value}")
    lines.append("hooks:")
    for h in hooks:
        lines.append(f"  - name: {h['name']}")
        lines.append(f"    file: {h['file']}")
        lines.append(f"    targets: [{', '.join(h['targets'])}]")
        lines.append(f"    event: {h['event']}")
    path = perms_dir / "manifest.yaml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_guardrail_check_is_a_silent_noop_without_a_permissions_manifest(sandbox):
    result = run_agent_doctor(sandbox)
    assert "Permission posture guardrail" not in result.stdout, result.stdout


def test_guardrail_check_ignores_non_bypass_postures(sandbox):
    _write_permissions_manifest(sandbox, posture={"claude": "accept-edits"}, hooks=[])

    result = run_agent_doctor(sandbox)

    assert "Permission posture guardrail" in result.stdout, result.stdout
    assert _lines_with(result.stdout, "✓", "no CLI declared in bypass posture"), result.stdout


def test_guardrail_check_ok_when_bypass_cli_has_a_pretooluse_hook(sandbox):
    _write_permissions_manifest(
        sandbox,
        posture={"claude": "bypass"},
        hooks=[{"name": "guardrail", "file": "hooks/guardrail.mjs", "targets": ["claude"], "event": "PreToolUse"}],
    )

    result = run_agent_doctor(sandbox)

    assert _lines_with(result.stdout, "✓", "claude runs in bypass posture"), result.stdout
    assert not _lines_with(result.stdout, "✗", "claude runs in bypass posture"), result.stdout


def test_guardrail_check_warns_visibly_when_bypass_cli_has_no_guardrail_hook(sandbox):
    """Also exercises forward-compatibility: 'codex' is not yet a valid
    permissions-manifest target in config_schema.py's strict validator, but
    this check must keep working as that set grows (another agent is
    extending bypass support to non-Claude CLIs concurrently) -- it must
    never depend on that strict schema."""
    _write_permissions_manifest(sandbox, posture={"codex": "bypass"}, hooks=[])

    result = run_agent_doctor(sandbox)

    assert _lines_with(result.stdout, "⚠", "codex runs in bypass posture"), result.stdout
    assert "WITHOUT a declared PreToolUse guardrail hook" in result.stdout, result.stdout
    assert not _lines_with(result.stdout, "✗", "codex runs in bypass posture"), (
        "a deliberately declared posture must not raise a FAIL nobody can clear"
    )


def test_guardrail_check_ignores_a_hook_declared_for_a_different_event(sandbox):
    _write_permissions_manifest(
        sandbox,
        posture={"claude": "bypass"},
        hooks=[{"name": "session-hook", "file": "hooks/x.mjs", "targets": ["claude"], "event": "SessionStart"}],
    )

    result = run_agent_doctor(sandbox)

    assert _lines_with(result.stdout, "⚠", "claude runs in bypass posture"), result.stdout


def test_guardrail_check_present_in_both_twins():
    repo = Path(__file__).resolve().parents[3]
    bash = (repo / "03-INFRA/scripts/agent-doctor.sh").read_text(encoding="utf-8")
    powershell = (repo / "03-INFRA/scripts/agent-doctor.ps1").read_text(encoding="utf-8")
    for content in (bash, powershell):
        assert "Permission posture guardrail" in content
        assert "PreToolUse" in content
        assert "WITHOUT a declared PreToolUse guardrail hook" in content


def test_doctor_reports_an_engine_owned_skill_this_engine_no_longer_ships(sandbox):
    """A release that renames or drops a command leaves any manifest still
    listing the old name pointing at nothing, and the command disappears from
    every CLI without a word. The user did not choose that, the release changed
    under them, so the doctor has to name the entry out loud."""
    (sandbox.skills_dir / "skills.manifest.yaml").write_text(
        "skills:\n  ghost-command:\n    origin: engine\n"
        "    targets: [claude]\n    exposure: core\n",
        encoding="utf-8",
    )

    result = _run_doctor(sandbox)

    assert "ghost-command" in result.stdout, result.stdout


def test_doctor_stays_quiet_when_every_engine_owned_skill_resolves(sandbox):
    """The common case after an upgrade is that nothing moved, or that a rename
    shipped a deprecated stub. Either way it resolves, and a guardian that
    congratulates itself on every run teaches people to skim past it."""
    body = sandbox.skills_dir / "still-here"
    body.mkdir(parents=True, exist_ok=True)
    (body / "SKILL.md").write_text(
        "---\nname: still-here\ndescription: ships with the engine.\n---\n\nbody\n",
        encoding="utf-8",
    )
    (sandbox.skills_dir / "skills.manifest.yaml").write_text(
        "skills:\n  still-here:\n    origin: engine\n"
        "    targets: [claude]\n    exposure: core\n",
        encoding="utf-8",
    )

    result = _run_doctor(sandbox)

    assert "no longer ships" not in result.stdout, result.stdout


def test_default_report_shows_what_is_wrong_and_hides_what_passed(sandbox):
    """A report that lists forty passing checks trains people to skim past the
    one line that mattered. The counts still prove the checks ran."""
    result = _run_doctor(sandbox, verbose=False)

    assert "PASS=" in result.stdout, "the summary must still prove the checks ran"
    assert "✓" not in result.stdout, result.stdout


def test_verbose_still_lists_every_check_that_passed(sandbox):
    """Hiding them by default is a reading aid, not a loss of information."""
    result = _run_doctor(sandbox, "--verbose")

    assert "✓" in result.stdout, result.stdout
