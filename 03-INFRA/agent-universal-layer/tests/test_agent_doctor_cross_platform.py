"""Cross-platform source-parity guards for the doctor twins."""

from pathlib import Path


def test_claude_authentication_guard_present_in_both_twins():
    repo = Path(__file__).resolve().parents[3]
    bash = (repo / "03-INFRA/scripts/agent-doctor.sh").read_text(encoding="utf-8")
    powershell = (repo / "03-INFRA/scripts/agent-doctor.ps1").read_text(encoding="utf-8")
    for content in (bash, powershell):
        assert "Claude authentication" in content
        assert "claude auth status" in content
        assert "claude auth login" in content
        # Gated on real use of Claude on this host (layer-managed
        # settings.json), so a non-Claude user never sees a logged-out FAIL
        # they cannot act on. A logged-out Claude is a host-local state, not
        # a product defect: WARN in both twins since 2026-08-16, matching the
        # OpenCode/Codex/Antigravity treatment of genuinely-absent CLIs.
        assert "Claude is configured on this host but" in content
    assert 'warn "Claude is not authenticated' in bash
    assert 'warn "Claude is not authenticated' in powershell
    assert 'fail "Claude is not authenticated' not in bash
    assert 'bad "Claude is not authenticated' not in powershell


def test_doctor_does_not_judge_claude_permission_posture_in_either_twin():
    repo = Path(__file__).resolve().parents[3]
    bash = (repo / "03-INFRA/scripts/agent-doctor.sh").read_text(encoding="utf-8")
    powershell = (repo / "03-INFRA/scripts/agent-doctor.ps1").read_text(encoding="utf-8")
    # 0.91.3 dropped the Claude-only permission judgement: permission posture
    # is a host-local choice, not product policy.
    for content in (bash, powershell):
        assert "Claude security posture" not in content
        assert "bypassPermissions" not in content
        assert "unmanaged persistent allow rule" not in content


def test_config_ownership_guards_are_present_in_both_twins():
    repo = Path(__file__).resolve().parents[3]
    bash = (repo / "03-INFRA/scripts/agent-doctor.sh").read_text(encoding="utf-8")
    powershell = (repo / "03-INFRA/scripts/agent-doctor.ps1").read_text(encoding="utf-8")
    for content in (bash, powershell):
        assert "OpenCode model/provider profile is host-local" in content
        assert "legacy shared OpenCode model/provider profile" in content
        assert "OpenCode loads the canonical AGENTS.md" in content


def test_onboarding_out_of_manifest_skill_nudge_in_both_twins():
    repo = Path(__file__).resolve().parents[3]
    bash = (repo / "03-INFRA/scripts/agent-doctor.sh").read_text(encoding="utf-8")
    powershell = (repo / "03-INFRA/scripts/agent-doctor.ps1").read_text(encoding="utf-8")
    # gentle onboarding nudge for stray (out-of-manifest) skills, both twins
    for content in (bash, powershell):
        assert "materialized but not in the manifest" in content
        assert "agent-sync inventory" in content


def test_fetch_failure_never_masks_gits_real_reason_in_either_twin():
    repo = Path(__file__).resolve().parents[3]
    bash = (repo / "03-INFRA/scripts/agent-doctor.sh").read_text(encoding="utf-8")
    powershell = (repo / "03-INFRA/scripts/agent-doctor.ps1").read_text(encoding="utf-8")
    # A failed fetch must surface git's own stderr, not a guessed
    # "(offline?)", and rev-list's own '?' fallback must never leak into a
    # FAIL/bad line as a literal, unexplained question mark.
    for content in (bash, powershell):
        assert "cannot tell how many commits behind the cloud" in content
    assert 'warn "fetch $REMOTE failed (offline?)"' not in bash
    assert 'warn "fetch $Remote failed (offline?)"' not in powershell


def test_opencode_genuinely_absent_is_a_warning_in_either_twin():
    repo = Path(__file__).resolve().parents[3]
    bash = (repo / "03-INFRA/scripts/agent-doctor.sh").read_text(encoding="utf-8")
    powershell = (repo / "03-INFRA/scripts/agent-doctor.ps1").read_text(encoding="utf-8")
    # OpenCode genuinely not installed must warn, like the other three CLIs,
    # not permanently FAIL with no code path that could ever clear it.
    for content in (bash, powershell):
        assert "OpenCode not installed here?" in content
    # The still-a-real-problem path (installed but config missing) must stay
    # a hard failure in both twins, gated behind the presence probe.
    assert 'command -v opencode >/dev/null 2>&1 || [ -x "$HOME/.opencode/bin/opencode" ]' in bash
    assert 'fail "missing $OCJSON"' in bash
    assert '$OcInstalled = [bool](Get-Command opencode -ErrorAction SilentlyContinue)' in powershell
    assert 'bad "missing $OcJson"' in powershell


def test_consumer_engine_dirty_working_tree_check_in_either_twin():
    repo = Path(__file__).resolve().parents[3]
    bash = (repo / "03-INFRA/scripts/agent-doctor.sh").read_text(encoding="utf-8")
    powershell = (repo / "03-INFRA/scripts/agent-doctor.ps1").read_text(encoding="utf-8")
    # S2's pin-freshness check must also flag a dirty working tree, not only
    # a HEAD-sha mismatch -- a hand-edited tracked file at an otherwise
    # correctly pinned HEAD used to report a clean "OK".
    for content in (bash, powershell):
        assert "consumer engine working tree clean" in content
        assert "tracked file(s) modified in the consumer engine clone" in content


def test_starter_command_visibility_in_both_twins():
    repo = Path(__file__).resolve().parents[3]
    bash = (repo / "03-INFRA/scripts/agent-doctor.sh").read_text(encoding="utf-8")
    powershell = (repo / "03-INFRA/scripts/agent-doctor.ps1").read_text(encoding="utf-8")
    # A machine with no skills manifest has none of the seven commands README
    # promises, and the only signal used to be the "no managed skill (fresh
    # install)" WARN -- which reads as normal. Both twins now name the state.
    for content in (bash, powershell):
        assert "no skills manifest yet, so none of the starter commands" in content
        assert "starter commands not installed" in content
        assert "all 7 starter commands installed" in content
        # WARN, never FAIL: an emptied manifest is a deliberate opt-out.
        assert 'warn "starter commands not installed' in content


def test_remote_backend_version_skew_is_reported_in_both_twins():
    repo = Path(__file__).resolve().parents[3]
    bash = (repo / "03-INFRA/scripts/agent-doctor.sh").read_text(encoding="utf-8")
    powershell = (repo / "03-INFRA/scripts/agent-doctor.ps1").read_text(encoding="utf-8")
    # Upgrading the workstation clone changes 03-INFRA/deploy, but the VPS
    # keeps running the containers it was last deployed with, and nothing
    # said so. Both twins now compare the two.
    for content in (bash, powershell):
        assert "vault-mcp on the server is" in content
        assert "03-INFRA/deploy/README.md" in content


def test_probe_timeout_names_the_stuck_mcp_server_in_both_twins():
    """A bare "the probe timed out" says the CLI hung, never which server hung
    it -- the one fact needed to fix it. agy's own log names them ("still
    connecting after 30s: <names>"), so both twins pass --log-file and read the
    names out of it. Signal, not phrasing: the log flag, the marker they grep
    for, and the enriched failure line."""
    repo = Path(__file__).resolve().parents[3]
    bash = (repo / "03-INFRA/scripts/agent-doctor.sh").read_text(encoding="utf-8")
    powershell = (repo / "03-INFRA/scripts/agent-doctor.ps1").read_text(encoding="utf-8")
    for content in (bash, powershell):
        assert "--log-file" in content
        assert "still connecting after" in content
        assert "MCP server(s) still connecting:" in content
