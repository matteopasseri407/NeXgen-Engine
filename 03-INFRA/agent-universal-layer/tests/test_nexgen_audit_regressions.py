"""Regression cases found by the 2026-10 foundations audit.

Each case reproduces a defect that was confirmed by running the code, not by
reading it. They sit together because they share one lesson: the suite proved
functions in isolation, and these defects only appear over time, under another
process's environment, or on the shape of data the happy path never produced.
"""
from __future__ import annotations

import importlib.util
import os
import random
import re
import string
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
LEAK_DIR = REPO / "03-INFRA" / "agent-universal-layer" / "leak-scan"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=False)


# --------------------------------------------------------------- systemd unit


def _unit_path(unit_text: str) -> str:
    match = re.search(r'^Environment="PATH=(.*)"$', unit_text, re.MULTILINE)
    assert match, unit_text
    return match.group(1)


@pytest.mark.skipif(sys.platform == "win32", reason="the systemd unit is Linux-only; Windows paths are escaped in it")
@pytest.mark.parametrize("builder", ["_systemd_service_content", "_systemd_heartbeat_content"])
def test_systemd_unit_is_a_fixed_point_of_its_own_environment(tmp_path, monkeypatch, builder):
    """The guard runs with the PATH its own unit declares. Writing the unit
    again from that environment must reproduce it exactly, or the unit grows
    and is reloaded on every cycle (it grew 51 bytes per cycle in the field)."""
    from nexgen_core import scheduler

    home = tmp_path / "home"
    engine = tmp_path / "engine" / "03-INFRA"
    vault = tmp_path / "vault"
    build = getattr(scheduler, builder)
    monkeypatch.setenv("PATH", os.pathsep.join(["/usr/local/bin", "/usr/bin", "/bin"]))
    first = build(home, engine, vault, vault)
    for _ in range(5):
        monkeypatch.setenv("PATH", _unit_path(first))
        assert build(home, engine, vault, vault) == first


@pytest.mark.skipif(sys.platform == "win32", reason="the systemd unit is Linux-only; Windows paths are escaped in it")
def test_an_already_bloated_unit_path_collapses_to_unique_entries(tmp_path, monkeypatch):
    """Machines already carrying the repeated entries repair themselves."""
    from nexgen_core import scheduler

    home = tmp_path / "home"
    local_bin, opencode_bin = str(home / ".local" / "bin"), str(home / ".opencode" / "bin")
    monkeypatch.setenv("PATH", os.pathsep.join([local_bin, opencode_bin] * 6 + ["/usr/bin"]))
    entries = _unit_path(
        scheduler._systemd_service_content(home, tmp_path / "e" / "03-INFRA", tmp_path / "v", tmp_path / "v")
    ).split(os.pathsep)
    assert len(entries) == len(set(entries))
    assert entries[:2] == [local_bin, opencode_bin]
    assert "/usr/bin" in entries


# ------------------------------------------------------------------ git paths


@pytest.mark.parametrize("path", [
    ".sync/x.yaml", ".hooks/pre-commit", ".skills/a", ".mcp/server.json", ".permissions/p", "...skills/x",
])
def test_hidden_directories_are_not_infrastructure(path):
    from nexgen_core.git_ops import is_infra_file

    assert is_infra_file(path) is False


@pytest.mark.parametrize("path", ["./AGENTS.md", "./03-INFRA/x.yaml", "03-INFRA/x.yaml", "mcp/manifest.yaml"])
def test_real_infrastructure_paths_still_match(path):
    from nexgen_core.git_ops import is_infra_file

    assert is_infra_file(path) is True


# --------------------------------------------------------------------- chrome


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX pkill path")
def test_heal_chrome_passes_a_pattern_pkill_cannot_read_as_an_option(tmp_path, monkeypatch):
    """`pkill -f --user-data-dir=...` exits 2 (unrecognized option) and
    check=False hid it: the hung browser was never stopped."""
    from nexgen_core.tools import chrome

    calls: list[list[str]] = []
    monkeypatch.setattr(chrome, "is_cdp_up", lambda *a, **k: False)
    monkeypatch.setattr(chrome, "get_profile_dir", lambda: tmp_path / "profile")
    monkeypatch.setattr(chrome, "singleton_owner_pid", lambda profile: None)
    monkeypatch.setattr(chrome.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(chrome, "launch_chrome", lambda extra_args=None: 0)
    monkeypatch.setattr(
        chrome.subprocess, "run",
        lambda argv, **kwargs: calls.append(list(argv)) or subprocess.CompletedProcess(argv, 1, "", ""),
    )
    chrome.heal_chrome()
    pkill = next(call for call in calls if call and call[0] == "pkill")
    pattern_at = next(i for i, arg in enumerate(pkill) if "user-data-dir" in arg)
    assert pkill[pattern_at - 1] == "--"


# ------------------------------------------------------------- vault-push


def _configured_clone(remote: Path, destination: Path) -> Path:
    subprocess.run(["git", "clone", str(remote), str(destination)], check=True, capture_output=True)
    for key, value in (("user.email", "t@example.com"), ("user.name", "T"), ("commit.gpgsign", "false")):
        _git(destination, "config", key, value)
    return destination


def _diverged_clone(tmp_path: Path) -> Path:
    """Machine B holds a committed note while machine A already moved origin."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True, capture_output=True)
    clone_a = _configured_clone(remote, tmp_path / "a")
    note_a = clone_a / "note.md"
    note_a.write_text("base\n", encoding="utf-8")
    _git(clone_a, "add", "-A")
    _git(clone_a, "commit", "-m", "base")
    _git(clone_a, "push", "origin", "HEAD:main")
    clone_b = _configured_clone(remote, tmp_path / "b")
    note_a.write_text("edit from machine A\n", encoding="utf-8")
    _git(clone_a, "commit", "-am", "A edit")
    _git(clone_a, "push", "origin", "HEAD:main")
    (clone_b / "note.md").write_text("durable note written on machine B\n", encoding="utf-8")
    _git(clone_b, "commit", "-am", "B durable note")
    return clone_b


def test_vault_push_exits_nonzero_when_the_work_was_moved_to_quarantine(tmp_path, monkeypatch):
    """The work is preserved, but it is no longer on the branch and it was not
    published. An agent closing a session on exit 0 would believe it was."""
    from nexgen_core.publisher import Publisher

    monkeypatch.setenv("AGENT_STATE_DIR", str(tmp_path / "state"))
    clone = _diverged_clone(tmp_path)
    code, message = Publisher(vault_data=clone).publish()
    assert code != 0
    assert "quarantine" in message.lower()
    assert "not published" in message.lower() or "non pubblicat" in message.lower()
    assert "durable note" not in _git(clone, "show", "origin/main:note.md").stdout


# ----------------------------------------------------------------- doctor


def test_doctor_reports_a_corrupt_manifest_instead_of_crashing(sandbox, tmp_path):
    from nexgen_core.doctor import Doctor
    from nexgen_core.report import Severity

    (sandbox.mcp_dir / "manifest.yaml").write_text("servers: [\n  broken: yaml\n", encoding="utf-8")
    doctor = Doctor(
        vault_data=sandbox.vault, home=sandbox.home, state_dir=tmp_path / "state",
        engine_root=sandbox.vault / "03-INFRA",
    )
    report = doctor.run_diagnostics()
    assert report.broken, "a corrupt manifest must be a finding"
    assert all(outcome.severity in Severity for outcome in report.outcomes)


def test_one_crashing_check_does_not_hide_the_others(sandbox, tmp_path, monkeypatch):
    from nexgen_core import doctor as doctor_module
    from nexgen_core.report import Severity

    def boom(*args, **kwargs):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(doctor_module, "check_secrets_materialized", boom)
    doctor = doctor_module.Doctor(
        vault_data=sandbox.vault, home=sandbox.home, state_dir=tmp_path / "state",
        engine_root=sandbox.vault / "03-INFRA",
    )
    report = doctor.run_diagnostics()
    crashed = [o for o in report.outcomes if "secrets_materialized" in o.id]
    assert crashed and crashed[0].severity == Severity.BROKEN
    assert "disk on fire" in crashed[0].message
    assert report.ok_count > 0, "the other checks still ran"


# ---------------------------------------------------------------- symlinks


def test_text_writer_edits_through_a_symlink_instead_of_replacing_it(tmp_path):
    """~/.bashrc is often a symlink into a dotfiles repository."""
    from nexgen_core.files import write_text_if_changed

    dotfiles = tmp_path / "dotfiles"
    dotfiles.mkdir()
    target = dotfiles / "bashrc"
    target.write_text("# mine\n", encoding="utf-8")
    link = tmp_path / ".bashrc"
    link.symlink_to(target)

    assert write_text_if_changed(link, "# mine\n# hook\n", tag="shell-hook") is True
    assert link.is_symlink()
    assert target.read_text(encoding="utf-8") == "# mine\n# hook\n"


def test_shell_hook_installation_keeps_a_dotfiles_symlink(tmp_path):
    from nexgen_core.tools.notifier_boot import _BASH_HOOK, _append_once

    target = tmp_path / "dotfiles" / "bashrc"
    target.parent.mkdir()
    target.write_text("export A=1\n", encoding="utf-8")
    link = tmp_path / ".bashrc"
    link.symlink_to(target)

    assert _append_once(link, _BASH_HOOK) is True
    assert link.is_symlink()
    assert "NeXgen Engine update notice" in target.read_text(encoding="utf-8")


def test_a_symlinked_instruction_pointer_never_overwrites_the_canonical_file(sandbox):
    """Guard rail on the fix above: ~/CLAUDE.md is a derivative the engine
    regenerates. If the user made it a symlink to the canonical bootstrap,
    regenerating it must replace the link, never write through it."""
    from nexgen_core.guard import GuardRunner

    canon = sandbox.ul / "instructions" / "AGENTS.md"
    original = canon.read_text(encoding="utf-8")
    claude_md = sandbox.home / "CLAUDE.md"
    claude_md.symlink_to(canon)

    GuardRunner(vault_data=sandbox.vault, home=sandbox.home).align_instructions()
    assert canon.read_text(encoding="utf-8") == original


# --------------------------------------------------------------- state dir


def test_every_caller_resolves_the_same_lock_file(tmp_path, monkeypatch):
    """HostLock() and the guard must name one lock for one host even when
    XDG_STATE_HOME is set; two files means two writers believing they are alone."""
    from nexgen_core.guard import GuardRunner
    from nexgen_core.lock import HostLock

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))
    runner = GuardRunner(vault_data=tmp_path / "vault", engine_root=tmp_path / "engine", home=Path.home())
    assert runner.state_dir / "agent-sync.lock" == HostLock().lock_path


def test_a_sandbox_home_keeps_its_state_even_when_xdg_is_set(tmp_path, monkeypatch):
    from nexgen_core.paths import resolve_state_dir

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))
    sandbox_home = tmp_path / "sandbox-home"
    assert resolve_state_dir(sandbox_home) == sandbox_home / ".local" / "state"


def test_a_relative_xdg_state_home_is_ignored(tmp_path, monkeypatch):
    """The XDG spec says relative paths are invalid; honoring one anchors
    state at whatever the working directory happens to be."""
    from nexgen_core.paths import resolve_state_dir

    monkeypatch.setenv("XDG_STATE_HOME", "relative/state")
    assert resolve_state_dir() == Path.home() / ".local" / "state"


# ---------------------------------------------------------------- leak scan


def _load_leak_scan():
    spec = importlib.util.spec_from_file_location("leak_scan_under_test", LEAK_DIR / "leak_scan.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _synthetic_tokens() -> dict[str, str]:
    """Built at runtime: this file must not itself look like a leak."""
    rng = random.Random(7)

    def body(length: int, alphabet: str = string.ascii_letters + string.digits) -> str:
        return "".join(rng.choice(alphabet) for _ in range(length))

    url_safe = string.ascii_letters + string.digits + "-_"
    return {
        "anthropic": "sk-" + "ant-api03-" + body(93, url_safe),
        "openai project": "sk-" + "proj-" + body(120, url_safe),
        "openai service account": "sk-" + "svcacct-" + body(80, url_safe),
        "github fine-grained": "github" + "_pat_" + body(22) + "_" + body(59),
        "github user token": "gh" + "u_" + body(36),
        "age secret key": "AGE-" + "SECRET-KEY-1" + body(58, string.ascii_uppercase + string.digits),
        "huggingface": "hf" + "_" + body(34),
        "npm": "npm" + "_" + body(36),
        "google api key": "AI" + "za" + body(35, url_safe),
    }


@pytest.mark.parametrize("provider", sorted(_synthetic_tokens()))
@pytest.mark.parametrize("template", ["API_KEY={tok}", '  "token": "{tok}",', "Authorization: Bearer {tok}"])
def test_leak_scan_recognizes_the_keys_of_common_providers(provider, template):
    leak_scan = _load_leak_scan()
    patterns, allow = leak_scan.load_patterns(LEAK_DIR / "leak_patterns.yaml")
    line = template.format(tok=_synthetic_tokens()[provider])
    findings = leak_scan.scan_units([leak_scan.Unit("sample", 1, line)], patterns, allow, [])
    assert any(f.blocking for f in findings), f"{provider} slipped through in: {template}"


# --------------------------------------------------------------- whole cycle


def test_applying_twice_changes_nothing_but_machine_state(sandbox, monkeypatch):
    """The cycle runs twice an hour: a second pass over an aligned machine
    must not rewrite anything (each rewrite is a backup, an mtime, a reload)."""
    from nexgen_core.guard import GuardMode, GuardRunner

    monkeypatch.setenv("KNOWLEDGE_VAULT_REMOTE", "local")

    def apply_once():
        return GuardRunner(vault_data=sandbox.vault, home=sandbox.home).run(mode=GuardMode.APPLY, allow_offline=True)

    def tree():
        return {
            key: value for key, value in sandbox.tree_snapshot().items()
            if not key.replace("\\", "/").startswith(".local/state")
        }

    assert apply_once().success
    after_first = tree()
    assert apply_once().success
    assert tree() == after_first
