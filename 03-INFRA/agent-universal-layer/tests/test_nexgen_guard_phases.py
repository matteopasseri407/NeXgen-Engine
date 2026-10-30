"""The guard cycle runs every phase, each isolated, and says which ones failed.

It used to run as one chain: a skill whose GitHub fetch timed out stopped the MCP
render, the permission posture and the guardrail hook behind it, every 30 minutes,
for as long as the network stayed down. A failure in one phase has to be a
failure of that phase.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from nexgen_core import action_notes
from nexgen_core.checks.takeover_checks import check_last_cycle_phases
from nexgen_core.guard import GuardMode, GuardRunner
from nexgen_core.report import Severity

PHASES = ("skills", "mcp", "permissions", "instructions", "launchers", "scheduler", "modules", "runtime", "openclaw")


def _runner(tmp_path: Path, monkeypatch) -> tuple[GuardRunner, list[str]]:
    """A runner whose phases only record that they ran: nothing touches the host."""
    vault = tmp_path / "vault"
    vault.mkdir()
    runner = GuardRunner(vault_data=vault, home=tmp_path / "home")
    monkeypatch.setattr(runner, "_phase_git", lambda *_a: None)
    monkeypatch.setattr(runner, "_phase_preflight", lambda *_a: None)
    ran: list[str] = []
    for name in PHASES:
        monkeypatch.setattr(runner, f"_phase_{name}", lambda *_a, _n=name: ran.append(_n))
    return runner, ran


@pytest.mark.parametrize("broken", PHASES)
def test_one_phase_failing_never_stops_the_others(tmp_path, monkeypatch, broken):
    runner, ran = _runner(tmp_path, monkeypatch)

    def explode(*_a):
        raise RuntimeError(f"{broken} exploded")

    monkeypatch.setattr(runner, f"_phase_{broken}", explode)

    result = runner.run(GuardMode.APPLY)

    assert ran == [name for name in PHASES if name != broken]  # all the others, in order
    assert result.success is False and result.exit_code == 1  # still a failed cycle: the unit alerts
    # Not asserting on translated wording: the suite runs under whatever language the machine speaks.
    assert broken in result.message
    assert any(action_notes.is_error(a) and f"'{broken}'" in a and f"{broken} exploded" in a
               for a in result.actions_taken)
    assert runner.heartbeat.recorded_failed_phases() == [broken]


def test_a_cycle_where_everything_works_records_no_failed_phase(tmp_path, monkeypatch):
    runner, ran = _runner(tmp_path, monkeypatch)

    result = runner.run(GuardMode.APPLY)

    assert result.success is True and ran == list(PHASES)
    assert runner.heartbeat.recorded_failed_phases() == []
    assert "FAIL=" not in runner.heartbeat.liveness_file.read_text(encoding="utf-8")


def test_several_failures_are_all_reported(tmp_path, monkeypatch):
    runner, ran = _runner(tmp_path, monkeypatch)
    for name in ("skills", "launchers"):
        monkeypatch.setattr(runner, f"_phase_{name}", lambda *_a: (_ for _ in ()).throw(OSError("denied")))

    result = runner.run(GuardMode.APPLY)

    assert runner.heartbeat.recorded_failed_phases() == ["skills", "launchers"]
    assert "skills, launchers" in result.message
    assert ran == ["mcp", "permissions", "instructions", "scheduler", "modules", "runtime", "openclaw"]


def test_the_guardrail_phase_runs_even_when_skills_cannot_be_fetched(tmp_path, monkeypatch):
    """The point of the whole change: a network error in a skill fetch must not
    leave the safety controls unapplied."""
    runner, ran = _runner(tmp_path, monkeypatch)
    monkeypatch.setattr(runner, "_phase_skills", lambda *_a: (_ for _ in ()).throw(TimeoutError("github unreachable")))

    runner.run(GuardMode.GUARD)

    assert "permissions" in ran


def test_a_crash_inside_the_permissions_phase_is_a_failed_phase_not_a_warning(tmp_path, monkeypatch):
    """The guardrail hook may not have been applied: that is a failed cycle, not
    "completed with warnings" (it used to be caught and downgraded to a warning)."""
    runner, ran = _runner(tmp_path, monkeypatch)
    monkeypatch.setattr(runner, "_phase_permissions", GuardRunner._phase_permissions.__get__(runner))
    monkeypatch.setattr(runner, "apply_runtime_permissions", lambda: (_ for _ in ()).throw(RuntimeError("boom")))

    result = runner.run(GuardMode.APPLY)

    assert result.success is False and result.exit_code == 1
    assert runner.heartbeat.recorded_failed_phases() == ["permissions"]
    assert "scheduler" in ran and "modules" in ran  # and the cycle went on


def test_a_crash_inside_the_launchers_phase_is_a_failed_phase_not_a_warning(tmp_path, monkeypatch):
    """MUST-02: install_shims raising was caught inside the helper and turned
    into a WARN; the phase never reached `failed` and the guard exited 0
    while the commands stayed broken."""
    from nexgen_core import shims

    runner, ran = _runner(tmp_path, monkeypatch)
    monkeypatch.setattr(runner, "_phase_launchers", GuardRunner._phase_launchers.__get__(runner))
    monkeypatch.setattr(shims, "install_shims", lambda **_kw: (_ for _ in ()).throw(OSError("shims denied")))

    result = runner.run(GuardMode.APPLY)

    assert result.success is False and result.exit_code == 1
    assert runner.heartbeat.recorded_failed_phases() == ["launchers"]
    assert "modules" in ran  # and the cycle went on


def test_a_crash_inside_the_scheduler_phase_is_a_failed_phase_not_a_warning(tmp_path, monkeypatch):
    """MUST-02: a scheduler that raises must fail the cycle with the phase named."""
    from nexgen_core import guard as guard_module

    runner, ran = _runner(tmp_path, monkeypatch)
    monkeypatch.setattr(runner, "_phase_scheduler", GuardRunner._phase_scheduler.__get__(runner))
    monkeypatch.setattr(guard_module, "install_scheduler", lambda **_kw: (_ for _ in ()).throw(OSError("schtasks denied")))

    result = runner.run(GuardMode.APPLY)

    assert result.success is False and result.exit_code == 1
    assert runner.heartbeat.recorded_failed_phases() == ["scheduler"]
    assert "modules" in ran  # and the cycle went on


def test_a_crash_inside_the_modules_phase_is_a_failed_phase_not_a_warning(tmp_path, monkeypatch):
    """MUST-02: modules that cannot be realigned used to be a WARN only."""
    from nexgen_core import module_install

    runner, ran = _runner(tmp_path, monkeypatch)
    monkeypatch.setattr(runner, "_phase_modules", GuardRunner._phase_modules.__get__(runner))
    monkeypatch.setattr(module_install, "install_declared_modules",
                        lambda *_a, **_kw: (_ for _ in ()).throw(OSError("modules denied")))

    result = runner.run(GuardMode.APPLY)

    assert result.success is False and result.exit_code == 1
    assert runner.heartbeat.recorded_failed_phases() == ["modules"]
    assert "runtime" in ran  # and the cycle went on


def test_warnings_are_counted_whatever_spelling_the_emitter_used(tmp_path, monkeypatch):
    """skill_sources wrote "[WARNING]" while the guard counted "[WARN]" and "[AVVISO]":
    a skill warning was not counted and the cycle was reported as a plain success."""
    runner, _ran = _runner(tmp_path, monkeypatch)
    monkeypatch.setattr(runner, "_phase_skills", lambda actions: actions.append("[WARNING] pin drifted"))

    result = runner.run(GuardMode.APPLY)

    assert result.success is True
    assert "warn" in result.message.lower() or "avvis" in result.message.lower()
    assert runner.heartbeat.recorded_warnings() == 1


# ----------------------------------------------------------------- liveness


def test_liveness_keeps_its_old_format_and_adds_the_failed_phases(tmp_path):
    from nexgen_core.beat import Heartbeat

    beat = Heartbeat(state_dir=tmp_path / "state")
    beat.record_liveness(warnings=2, failed_phases=["skills", "mcp"])

    lines = beat.liveness_file.read_text(encoding="utf-8").splitlines()
    float(lines[0])  # a previous release reads only the first line and must still parse it
    assert lines[2] == "WARN=2" and lines[3] == "FAIL=skills,mcp"
    assert beat.recorded_warnings() == 2
    assert beat.recorded_failed_phases() == ["skills", "mcp"]
    ok, message = beat.check_liveness()
    assert ok is True  # alive: the unit's own failure is what alerts
    assert "skills, mcp" in message


def test_liveness_file_is_replaced_atomically(tmp_path, monkeypatch):
    from nexgen_core import files
    from nexgen_core.beat import Heartbeat

    beat = Heartbeat(state_dir=tmp_path / "state")
    beat.record_liveness()
    before = beat.liveness_file.read_text(encoding="utf-8")
    monkeypatch.setattr(files.os, "replace", lambda *_a: (_ for _ in ()).throw(OSError("synthetic")))

    with pytest.raises(OSError):
        beat.record_liveness(warnings=9)

    assert beat.liveness_file.read_text(encoding="utf-8") == before


def test_doctor_says_which_phases_the_last_cycle_could_not_complete(tmp_path):
    from nexgen_core.beat import Heartbeat

    state = tmp_path / "state"
    beat = Heartbeat(state_dir=state)
    beat.record_liveness()
    assert check_last_cycle_phases(state).severity == Severity.OK

    beat.record_liveness(failed_phases=["skills"])
    outcome = check_last_cycle_phases(state)
    assert outcome.severity == Severity.BROKEN and "skills" in outcome.message and outcome.action


# --------------------------------------------------------------- one definition


def test_note_severity_is_written_and_read_in_one_module():
    assert action_notes.is_warning(action_notes.WARN + "x")
    assert action_notes.is_error(action_notes.ERROR + "x")
    for spelling in ("[WARNING] x", "[AVVISO] x"):
        assert action_notes.is_warning(spelling) and not action_notes.is_error(spelling)
    assert action_notes.is_error("[ERRORE] x") and not action_notes.is_warning("[ERRORE] x")
    assert not action_notes.is_warning("plain") and not action_notes.is_error("plain")


def test_no_other_module_spells_the_severity_prefixes_by_hand():
    """69 places wrote or matched these by hand and drifted apart. A new emitter has
    to use action_notes, or the guard will not count what it says. Only a literal that
    *starts* with the marker counts: a sentence that mentions "[ERROR]" in passing is prose."""
    root = Path(action_notes.__file__).resolve().parent
    by_hand = re.compile(r"""["']\[(?:WARN|WARNING|ERROR|AVVISO|ERRORE)\]""")
    offenders = [
        f"{path.relative_to(root)}:{n}"
        for path in root.rglob("*.py")
        if path.name != "action_notes.py"
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if by_hand.search(line)
    ]
    assert offenders == []


# ------------------------------------------------------------- mcp render result


def test_mcp_phase_claims_success_only_when_every_cli_rendered_and_names_the_failure(tmp_path, monkeypatch):
    """The phase used to append "regenerated for every CLI" before it looked at the
    results, so a codex config that could not be written was reported as a success.
    A failed target must be named together with the error that stopped it."""
    from nexgen_core import guard as guard_module
    from nexgen_core import mcp_trials
    from nexgen_core.i18n import t

    class FailingRenderer:
        def __init__(self, **_kw):
            self.last_errors: dict[str, str] = {}

        def render_all(self, write: bool = False) -> dict[str, bool]:
            self.last_errors = {"codex": "line 3 is not valid TOML"}
            return {"claude": True, "claude-desktop": True, "antigravity": True, "opencode": True, "codex": False}

    monkeypatch.setattr(guard_module, "McpRenderer", FailingRenderer)
    monkeypatch.setattr(mcp_trials, "purge_expired", lambda: [])
    vault = tmp_path / "vault"
    vault.mkdir()
    runner = GuardRunner(vault_data=vault, home=tmp_path / "home")
    actions: list[str] = []

    runner._phase_mcp(actions, skip_mcp=False)

    assert t("MCP configurations regenerated for every CLI") not in actions
    assert any(action_notes.is_warning(a) and "codex" in a and "line 3 is not valid TOML" in a for a in actions)


def test_a_failed_mcp_render_is_a_failed_phase_not_a_warning(tmp_path, monkeypatch):
    """Ricognizione 8: la fase MCP avvisava soltanto, quindi `failed` restava
    vuoto e il guard usciva 0 mentre le configurazioni di una CLI restavano
    rotte. Deve fallire come le altre fasi: nome in `failed`, exit 1."""
    from nexgen_core import guard as guard_module
    from nexgen_core import mcp_trials

    class FailingRenderer:
        def __init__(self, **_kw):
            self.last_errors: dict[str, str] = {}

        def render_all(self, write: bool = False) -> dict[str, bool]:
            self.last_errors = {"codex": "line 3 is not valid TOML"}
            return {"claude": True, "codex": False}

    monkeypatch.setattr(guard_module, "McpRenderer", FailingRenderer)
    monkeypatch.setattr(mcp_trials, "purge_expired", lambda: [])
    runner, ran = _runner(tmp_path, monkeypatch)
    monkeypatch.setattr(runner, "_phase_mcp", GuardRunner._phase_mcp.__get__(runner))

    result = runner.run(GuardMode.APPLY)

    assert result.success is False and result.exit_code == 1
    assert runner.heartbeat.recorded_failed_phases() == ["mcp"]
    assert "runtime" in ran  # and the cycle went on


def test_dead_opencode_instructions_are_not_rewritten_when_their_safety_copy_fails(tmp_path, monkeypatch):
    """The backup of opencode.json was suppressed and the file rewritten anyway, so a
    failed copy removed the only record of the user's own lines. No copy, no rewrite."""
    import json

    from nexgen_core import files
    from nexgen_core.paths import canonical_instructions
    from nexgen_core.renderer import McpRenderer

    vault = tmp_path / "vault"
    vault.mkdir()
    runner = GuardRunner(vault_data=vault, home=tmp_path / "home")
    config = McpRenderer(vault_data=vault, home=tmp_path / "home").opencode_config_path()
    config.parent.mkdir(parents=True, exist_ok=True)
    original = json.dumps({"instructions": [str(canonical_instructions(vault)), "~/my-own-rules.md"]}, indent=2) + "\n"
    config.write_text(original, encoding="utf-8")

    def no_space(*_a, **_kw):
        raise OSError("disk full")

    monkeypatch.setattr(files, "backup_file", no_space)

    outcome = runner._drop_dead_opencode_instructions_array()

    assert outcome is not None and action_notes.is_warning(outcome)
    assert config.read_text(encoding="utf-8") == original


def test_claude_md_symlink_survives_the_pointer_being_rewritten(tmp_path, monkeypatch):
    """~/CLAUDE.md is often a link into a dotfiles repository. Rewriting the pointer
    replaced the link with a regular file, detaching it from its owner."""
    vault = tmp_path / "vault"
    canon = vault / "03-INFRA" / "agent-universal-layer" / "instructions" / "AGENTS.md"
    canon.parent.mkdir(parents=True)
    canon.write_text("# canon\n", encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    dotfiles = tmp_path / "dotfiles" / "CLAUDE.md"
    dotfiles.parent.mkdir()
    dotfiles.write_text("hand-written lines\n", encoding="utf-8")
    (home / "CLAUDE.md").symlink_to(dotfiles)
    runner = GuardRunner(vault_data=vault, home=home)

    runner.align_instructions()

    assert (home / "CLAUDE.md").is_symlink()
    assert dotfiles.read_text(encoding="utf-8").startswith("# Claude compatibility pointer")


def test_an_identical_copy_is_not_backed_up_again_every_cycle(tmp_path, monkeypatch):
    """Ricognizione 18: con il symlink ripiegato su copia (Windows senza
    Developer Mode) ogni ciclo faceva un backup e ricopiava. Se la copia è
    già identica alla sorgente non serve né l'uno né l'altra."""
    from nexgen_core import files

    vault = tmp_path / "vault"
    canon = vault / "03-INFRA" / "agent-universal-layer" / "instructions" / "AGENTS.md"
    canon.parent.mkdir(parents=True)
    canon.write_text("# canon\n", encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    target = home / "CLAUDE.md"
    target.write_text("# canon\n", encoding="utf-8")
    backups: list = []
    monkeypatch.setattr(files, "backup_file", lambda *a, **kw: backups.append(a) or None)
    monkeypatch.setattr(files, "publish_symlink",
                        lambda *_: (_ for _ in ()).throw(OSError("nessun privilegio di symlink")))
    runner = GuardRunner(vault_data=vault, home=home)

    changed, warning = runner._link_to_canonical(target, canon)

    assert (changed, warning) == (False, None)
    assert backups == [], "una copia identica non va salvata di nuovo"
    assert target.read_text(encoding="utf-8") == "# canon\n"
