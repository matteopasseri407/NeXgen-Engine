"""The moving channels (nightly, beta): which commit they point at, and what they refuse.

A stable target is a version; a moving-channel target is a commit, and the only thing that
makes it installable is a signature the installed tree already pins (never a
`--allow-unverified` door), plus, for nightly, the CI gates the branch itself requires and,
for beta, a full nightly suite that finished green on Windows and Linux. These tests build real
real ssh signatures: a faked verdict would prove nothing about the lock.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
REAL_VAULT = TESTS_DIR.parents[2]
SCRIPTS = REAL_VAULT / "03-INFRA" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from nexgen_core import update_channel as uc  # noqa: E402
from nexgen_core import update_window, updater  # noqa: E402

SSH_KEYGEN = shutil.which("ssh-keygen")

#: The command entry every fixture release drives, exactly as the update
#: fixtures do: doctor answers a readable summary, apply succeeds.
_ENTRY = (
    '"""Fixture command entry (minimal honest target, never the real CLI)."""\n'
    "import sys\n"
    "verbs = sys.argv[1:]\n"
    "if verbs[:2] == ['doctor', '--summary']:\n"
    "    print('FAIL=0 OK=1 WARN=0 UNDETERMINED=0')\n"
    "elif verbs[:1] == ['apply']:\n"
    "    pass\n"
    "else:\n"
    "    sys.exit(2)\n"
)


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "channel test",
        "GIT_AUTHOR_EMAIL": "channel-test-identity",
        "GIT_COMMITTER_NAME": "channel test",
        "GIT_COMMITTER_EMAIL": "channel-test-identity",
    }
    return subprocess.run(["git", *args], cwd=cwd, check=check, capture_output=True, text=True, env=env)


def _key(path: Path) -> Path:
    subprocess.run([SSH_KEYGEN, "-q", "-t", "ed25519", "-N", "", "-f", str(path), "-C", "channel-test"], check=True)
    return path.with_suffix(".pub")


def _signed(repo: Path, pub: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "-c", "gpg.format=ssh", "-c", f"user.signingkey={pub.as_posix()}", "commit", "-q", "-S", "-m", message)
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def _tree(repo: Path, version: str) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "VERSION").write_text(f"{version}\n", encoding="utf-8")
    (repo / "CHANGELOG.md").write_text(f"## [{version}] - 2026-10-01\n\n### Changed\n\n- Fixture {version}.\n",
                                       encoding="utf-8")
    entry = repo / "03-INFRA" / "scripts" / "nexgen_core" / "cli"
    entry.mkdir(parents=True, exist_ok=True)
    (entry / "__init__.py").write_text(_ENTRY, encoding="utf-8")


def _pin(repo: Path, pub: Path) -> None:
    """Pins the ssh key in the tree, where an install carries its trust anchor."""
    kind, body, *_ = pub.read_text(encoding="utf-8").split()
    trust = repo / "03-INFRA" / "agent-universal-layer" / "trust"
    trust.mkdir(parents=True, exist_ok=True)
    (trust / "release-signers.txt").write_text(f"ssh {kind} {body} channel-test\n", encoding="utf-8")


@dataclass
class Machine:
    origin: Path
    engine: Path
    state: Path
    pub: Path


def _instance_config(machine_engine: Path, *, allow: tuple[str, ...]) -> None:
    """The installation's private config, the one the engine loads from the vault."""
    config = machine_engine / "03-INFRA" / "agent-universal-layer" / "sync" / "remotes.yaml"
    config.parent.mkdir(parents=True, exist_ok=True)
    listed = ", ".join(allow)
    config.write_text(
        "schema_version: 1\n"
        "authoritative_remote: origin\n"
        "mirrors: []\n"
        f"update:\n  allow_channels: [{listed}]\n",
        encoding="utf-8",
    )


def _machine(tmp_path: Path, *, tip_signed: bool, extra_unsigned: bool = False, unlocked: bool = True) -> Machine:
    """A machine at v0.1.0 and an origin whose developer branch the clone does not have yet."""
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q", "-b", "main")
    pub = _key(tmp_path / "signing")
    _tree(origin, "0.1.0")
    _git(origin, "add", "-A")
    _git(origin, "commit", "-q", "-m", "release 0.1.0")
    _git(origin, "tag", "v0.1.0")
    _pin(origin, pub)
    if unlocked:
        # The moving channels are closed on a fresh installation; every test that
        # uses them states the unlock it depends on, so the closed default is
        # never an accident of the fixture. It travels with the repository the
        # clone is made from, exactly as a vault config would.
        _instance_config(origin, allow=("nightly", "beta"))
    _git(origin, "add", "-A")
    _git(origin, "commit", "-q", "-m", "pin the signer and the installation config")

    engine = tmp_path / "engine"
    _git(tmp_path, "clone", "-q", str(origin), str(engine))

    # The branch comes after the clone on purpose: a stale local branch must not
    # decide the target, the remote tip must.
    _git(origin, "checkout", "-q", "-b", "developer")
    _tree(origin, "0.2.0")
    if tip_signed:
        _signed(origin, pub, "dev work")
    else:
        _git(origin, "add", "-A")
        _git(origin, "commit", "-q", "-m", "dev work")
    if extra_unsigned:
        (origin / "more.txt").write_text("more\n", encoding="utf-8")
        _git(origin, "add", "-A")
        _git(origin, "commit", "-q", "-m", "more dev work")
    _git(origin, "checkout", "-q", "main")
    return Machine(origin=origin, engine=engine, state=tmp_path / "state", pub=pub)


def _env(machine: Machine) -> dict[str, str]:
    return {
        **os.environ,
        "AGENT_ENGINE_ROOT": str(machine.engine / "03-INFRA"),
        "AGENT_VAULT_DATA": str(machine.engine),
        "AGENT_STATE_DIR": str(machine.state),
    }


def _gates_green(monkeypatch) -> None:
    """Stubs the CI-gate lookup: the gate itself has its own tests below."""
    monkeypatch.setattr(uc, "checks_missing", lambda *_a, **_k: [])


def _with_git_identity(monkeypatch) -> None:
    """A real machine has a Git identity; a divergent merge needs one.

    The suite deliberately runs with git's global and system config isolated,
    so an update that has to write a merge commit needs the identity in the
    process environment, which is where a machine that can run one has it.
    """
    monkeypatch.setenv("GIT_AUTHOR_NAME", "channel test")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "channel-test-identity")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "channel test")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "channel-test-identity")


def _head(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


# --------------------------------------------------------------------------
# The installation unlocks the moving channels; the public default is closed.
# --------------------------------------------------------------------------

def test_a_moving_channel_is_closed_on_an_installation_that_did_not_open_it(tmp_path, capsys):
    machine = _machine(tmp_path, tip_signed=True, unlocked=False)
    before = _head(machine.engine)

    result = updater.main(["--yes", "--channel", "nightly"], environ=_env(machine))

    assert result == updater.EXIT_REFUSED
    assert _head(machine.engine) == before
    assert "not enabled on this installation" in capsys.readouterr().err


def test_a_remembered_nightly_channel_is_ignored_when_the_unlock_is_gone(tmp_path, capsys):
    """Removing the private key closes the door again instead of failing every cycle."""
    machine = _machine(tmp_path, tip_signed=True, unlocked=False)
    machine.state.mkdir(parents=True, exist_ok=True)
    (machine.state / uc.CHANNEL_FILE).write_text("nightly\n", encoding="utf-8")
    before = _head(machine.engine)

    result = updater.main(["--yes"], environ=_env(machine))

    assert result == 0
    assert _head(machine.engine) == before  # nothing to do on stable: it is already at v0.1.0
    assert (machine.engine / "VERSION").read_text(encoding="utf-8").strip() == "0.1.0"


def test_the_unlock_opens_exactly_the_channels_it_names(tmp_path):
    config = tmp_path / "remotes.yaml"
    config.write_text("schema_version: 1\nupdate:\n  allow_channels: [nightly]\n", encoding="utf-8")
    assert uc.allowed_channels(config_path=config) == frozenset({"nightly"})
    assert uc.resolve_channel(None, tmp_path, allowed=uc.allowed_channels(config_path=config)) == "stable"
    assert uc.resolve_channel("nightly", tmp_path, allowed=uc.allowed_channels(config_path=config)) == "nightly"
    with pytest.raises(uc.ChannelRefused):
        uc.resolve_channel("beta", tmp_path, allowed=uc.allowed_channels(config_path=config))


@pytest.mark.parametrize("body", ["", "not: [a, config\n", "- a list\n", "update: nightly\n", "update:\n  allow_channels: nope\n",
                                  "update:\n  allow_channels: [stable, beta, silly]\n"])
def test_a_broken_or_unknown_unlock_closes_the_door(tmp_path, body):
    config = tmp_path / "remotes.yaml"
    config.write_text(body, encoding="utf-8")

    allowed = uc.allowed_channels(config_path=config)

    assert allowed <= frozenset(uc.UNLOCKABLE)
    assert "stable" not in allowed


def test_a_missing_unlock_file_is_the_closed_default(tmp_path):
    assert uc.allowed_channels(config_path=tmp_path / "absent.yaml") == frozenset()


# --------------------------------------------------------------------------
# The choice is remembered, and a switch that was refused is not.
# --------------------------------------------------------------------------

def _new_release(origin: Path, version: str, pub: Path) -> None:
    """A new signed release on origin/main, containing what developer already has.

    That is the real order: `sync-developer` brings main back into developer, so
    the release a machine returns to contains the commits it was already running.
    """
    _git(origin, "checkout", "-q", "main")
    _git(origin, "merge", "-q", "--no-edit", "developer")
    _tree(origin, version)
    _git(origin, "add", "-A")
    _git(origin, "commit", "-q", "-m", f"release {version}")
    _git(origin, "-c", "gpg.format=ssh", "-c", f"user.signingkey={pub.as_posix()}",
         "tag", "-s", "-m", f"Release {version}", f"v{version}")


def _advance_developer(origin: Path, pub: Path, text: str) -> str:
    """A new signed commit on origin/developer, the way the branch advances."""
    _git(origin, "checkout", "-q", "developer")
    (origin / f"{text}.txt").write_text(f"{text}\n", encoding="utf-8")
    return _signed(origin, pub, text)


@pytest.mark.skipif(SSH_KEYGEN is None, reason="needs ssh-keygen")
def test_the_chosen_channel_is_remembered_and_respected(tmp_path, monkeypatch):
    _gates_green(monkeypatch)
    machine = _machine(tmp_path, tip_signed=True)

    assert updater.main(["--yes", "--channel", "nightly"], environ=_env(machine)) == 0
    assert uc.read_channel(machine.state) == "nightly"

    # The branch advances; a bare `nexgen update` follows the remembered channel.
    moved = _advance_developer(machine.origin, machine.pub, "later dev work")
    assert updater.main(["--yes"], environ=_env(machine)) == 0

    assert _head(machine.engine) == moved


@pytest.mark.skipif(SSH_KEYGEN is None, reason="needs ssh-keygen")
def test_channel_stable_brings_a_nightly_machine_back_to_releases(tmp_path, monkeypatch):
    _with_git_identity(monkeypatch)
    _gates_green(monkeypatch)
    machine = _machine(tmp_path, tip_signed=True)
    assert updater.main(["--yes", "--channel", "nightly"], environ=_env(machine)) == 0
    assert uc.read_channel(machine.state) == "nightly"
    _new_release(machine.origin, "0.3.0", machine.pub)

    assert updater.main(["--yes", "--channel", "stable"], environ=_env(machine)) == 0

    assert uc.read_channel(machine.state) == "stable"
    assert (machine.engine / "VERSION").read_text(encoding="utf-8").strip() == "0.3.0"


@pytest.mark.skipif(SSH_KEYGEN is None, reason="needs ssh-keygen")
def test_a_refused_switch_is_not_remembered(tmp_path):
    machine = _machine(tmp_path, tip_signed=False)

    assert updater.main(["--yes", "--channel", "nightly"], environ=_env(machine)) == updater.EXIT_REFUSED

    assert uc.read_channel(machine.state) == "stable"
    assert not (machine.state / uc.CHANNEL_FILE).exists()


@pytest.mark.skipif(SSH_KEYGEN is None, reason="needs ssh-keygen")
def test_a_check_only_run_never_changes_the_remembered_channel(tmp_path, monkeypatch):
    _gates_green(monkeypatch)
    machine = _machine(tmp_path, tip_signed=True)

    assert updater.main(["--check", "--channel", "nightly"], environ=_env(machine)) == 0

    assert uc.read_channel(machine.state) == "stable"
    assert not (machine.state / uc.CHANNEL_FILE).exists()


# --------------------------------------------------------------------------
# The rollback guarantee holds on every channel.
# --------------------------------------------------------------------------

def test_a_failed_post_update_check_rolls_a_nightly_update_back(tmp_path, monkeypatch, capsys):
    """A nightly machine comes back to the commit it ran, not to a version number."""
    from nexgen_core.release_trust import VERIFIED, TagVerdict

    machine = _machine(tmp_path, tip_signed=False)
    # The rollback machinery is what this test is about, so the nightly target is
    # handed over already proved: the signature path has its own tests.
    fetched = _git(machine.engine, "fetch", "-q", "origin", "developer:refs/remotes/origin/developer")
    assert fetched.returncode == 0
    tip = _git(machine.origin, "rev-parse", "developer").stdout.strip()
    monkeypatch.setattr(uc, "nightly_target", lambda *_a, **_k: uc.ChannelTarget(
        channel="nightly", rev=tip, version="0.2.0", label=f"developer@{tip[:9]}",
        detail="synthetic verified signature", verdict=TagVerdict(VERIFIED, "synthetic verified signature"),
    ))
    before = _head(machine.engine)
    doctor_results = iter(((0, 0), (1, 1)))
    monkeypatch.setattr(updater, "_doctor", lambda *_args, **_kwargs: next(doctor_results))

    result = updater.main(["--yes", "--channel", "nightly"], environ=_env(machine))

    assert result == 1
    assert _head(machine.engine) == before
    assert (machine.engine / "VERSION").read_text(encoding="utf-8").strip() == "0.1.0"
    error = capsys.readouterr().err
    assert "introduced new failures: before=0, after=1" in error
    assert f"Rolled back to {before[:9]}" in error


@pytest.mark.skipif(SSH_KEYGEN is None, reason="needs ssh-keygen")
def test_an_unattended_nightly_failure_is_rolled_back_and_remembered(tmp_path, monkeypatch, capsys):
    _gates_green(monkeypatch)
    machine = _machine(tmp_path, tip_signed=True)
    before = _head(machine.engine)
    doctor_results = iter(((0, 0), (1, 1)))
    monkeypatch.setattr(updater, "_doctor", lambda *_args, **_kwargs: next(doctor_results))

    result = updater.main(["--unattended", "--channel", "nightly"], environ=_env(machine))

    assert result == updater.EXIT_ROLLED_BACK
    assert _head(machine.engine) == before
    assert uc.read_channel(machine.state) == "stable"  # the failed switch is not remembered
    assert f"Rolled back to {before[:9]}" in capsys.readouterr().err


def test_a_nightly_rollback_never_resets_away_a_commit_made_during_the_update(tmp_path, monkeypatch, capsys):
    """The anti-loss rev-list must name the commit the channel installs, not its display
    label: `developer@<sha>` is not a revision, so git used to answer with an error and an
    empty list, and the rollback reset the engine as if nothing had been committed."""
    _gates_green(monkeypatch)
    machine = _machine(tmp_path, tip_signed=True)
    doctor_results = iter(((0, 0), (1, 1)))
    monkeypatch.setattr(updater, "_doctor", lambda *_args, **_kwargs: next(doctor_results))
    real_run = updater._run
    committed: list[str] = []

    def commit_during_apply(args, **kwargs):
        if args[-1:] == ["apply"] and not committed:
            (machine.engine / "LOCAL-NOTE.md").write_text("work in progress\n", encoding="utf-8")
            _git(machine.engine, "add", "LOCAL-NOTE.md")
            _git(machine.engine, "commit", "-q", "-m", "local work")
            committed.append(_head(machine.engine))
        return real_run(args, **kwargs)

    monkeypatch.setattr(updater, "_run", commit_during_apply)

    result = updater.main(["--yes", "--channel", "nightly"], environ=_env(machine))

    assert committed, "the test needs a commit the rollback could erase"
    assert result == updater.EXIT_ROLLBACK_FAILED
    assert _head(machine.engine) == committed[0]
    assert "would be lost" in capsys.readouterr().err


def test_a_rollback_that_cannot_check_for_lost_commits_does_not_reset(tmp_path, monkeypatch, capsys):
    """A rev-list that fails proves nothing: the rollback leaves the tree as it is and says
    so instead of resetting on an answer it could not trust."""
    _gates_green(monkeypatch)
    machine = _machine(tmp_path, tip_signed=True)
    example = _git(machine.engine, "rev-parse", "HEAD").stdout.strip()
    tip = _git(machine.origin, "rev-parse", "developer").stdout.strip()
    doctor_results = iter(((0, 0), (1, 1)))
    monkeypatch.setattr(updater, "_doctor", lambda *_args, **_kwargs: next(doctor_results))
    real_git = updater._git

    def rev_list_fails(repo, *args, **kwargs):
        if args[:1] == ("rev-list",):
            return subprocess.CompletedProcess(["git", *args], 128, "", "fatal: bad revision")
        return real_git(repo, *args, **kwargs)

    monkeypatch.setattr(updater, "_git", rev_list_fails)

    result = updater.main(["--yes", "--channel", "nightly"], environ=_env(machine))

    assert result == updater.EXIT_ROLLBACK_FAILED
    assert _head(machine.engine) == tip != example
    assert "cannot be proven" in capsys.readouterr().err


# --------------------------------------------------------------------------
# `nexgen --version` says which channel this machine follows.
# --------------------------------------------------------------------------

def _version_line(monkeypatch, capsys, state_dir: Path, *, engine: Path | None = None) -> str:
    from nexgen_core.cli import main as cli_main

    monkeypatch.setenv("AGENT_STATE_DIR", str(state_dir))
    if engine is not None:
        monkeypatch.setenv("AGENT_ENGINE_ROOT", str(engine / "03-INFRA"))
    with pytest.raises(SystemExit) as exit_code:
        cli_main(["--version"])
    assert exit_code.value.code == 0
    return capsys.readouterr().out.strip()


def test_version_reports_the_channel_and_the_commit_on_nightly(tmp_path, monkeypatch, capsys):
    machine = _machine(tmp_path, tip_signed=True)
    machine.state.mkdir(parents=True, exist_ok=True)
    (machine.state / uc.CHANNEL_FILE).write_text("nightly\n", encoding="utf-8")
    short = _git(machine.engine, "rev-parse", "--short", "HEAD").stdout.strip()

    line = _version_line(monkeypatch, capsys, machine.state, engine=machine.engine)

    assert line.endswith(f"(nightly {short})")


def test_version_reports_the_stable_default(tmp_path, monkeypatch, capsys):
    line = _version_line(monkeypatch, capsys, tmp_path / "state")

    assert line.endswith("(stable)")


def test_version_keeps_the_plain_line_when_the_engine_is_not_there(tmp_path, monkeypatch, capsys):
    """A banner is not a place a missing checkout can break a command."""
    state = tmp_path / "state"
    state.mkdir()
    (state / uc.CHANNEL_FILE).write_text("nightly\n", encoding="utf-8")

    line = _version_line(monkeypatch, capsys, state, engine=tmp_path / "absent")

    assert line.endswith("(nightly)")


# --------------------------------------------------------------------------
# An automatic update onto a moving channel waits for a quiet machine.
# --------------------------------------------------------------------------

def test_an_unattended_nightly_update_waits_while_a_command_holds_the_host_lock(tmp_path, capsys):
    """The lock is the engine's own signal that something on this machine is running."""
    from nexgen_core.lock import host_mutation

    machine = _machine(tmp_path, tip_signed=True)
    before = _head(machine.engine)
    machine.state.mkdir(parents=True, exist_ok=True)
    holder = host_mutation("someone-elses-command", state_dir=machine.state, timeout=5)
    holder.acquire()
    try:
        result = updater.main(["--unattended", "--channel", "nightly"], environ=_env(machine))
    finally:
        holder.release()

    assert result == 0
    assert _head(machine.engine) == before
    assert "Postponed to the next cycle" in capsys.readouterr().out


def test_an_unattended_nightly_update_waits_right_after_a_guard_cycle(tmp_path, capsys):
    """A liveness file written moments ago means the machine's maintenance is active."""
    from nexgen_core.beat import LIVENESS_FILE_NAME

    machine = _machine(tmp_path, tip_signed=True)
    before = _head(machine.engine)
    machine.state.mkdir(parents=True, exist_ok=True)
    (machine.state / LIVENESS_FILE_NAME).write_text('{"warnings": 0}\n', encoding="utf-8")

    result = updater.main(["--unattended", "--channel", "nightly"], environ=_env(machine))

    assert result == 0
    assert _head(machine.engine) == before
    assert "Postponed to the next cycle" in capsys.readouterr().out


@pytest.mark.skipif(SSH_KEYGEN is None, reason="needs ssh-keygen")
def test_an_unattended_nightly_update_runs_when_the_machine_is_quiet(tmp_path, monkeypatch):
    _gates_green(monkeypatch)
    machine = _machine(tmp_path, tip_signed=True)
    tip = _git(machine.origin, "rev-parse", "developer").stdout.strip()

    result = updater.main(["--unattended", "--channel", "nightly"], environ=_env(machine))

    assert result == 0
    assert _head(machine.engine) == tip


def test_the_quiet_window_only_applies_to_automatic_dev_updates(tmp_path):
    """The rule protects a moving branch; a patch release keeps its own contract."""
    from nexgen_core.lock import host_mutation

    state = tmp_path / "state"
    state.mkdir()
    assert update_window.defer_if_busy(unattended=True, channel="stable", state_dir=state) is False

    holder = host_mutation("someone-elses-command", state_dir=state, timeout=5)
    holder.acquire()
    try:
        assert update_window.defer_if_busy(unattended=True, channel="nightly", state_dir=state) is True
        assert update_window.defer_if_busy(unattended=True, channel="stable", state_dir=state) is False
        # An interactive run is never postponed: the person asked for it now.
        assert update_window.defer_if_busy(unattended=False, channel="nightly", state_dir=state) is False
    finally:
        holder.release()


# --------------------------------------------------------------------------
# nightly: the tip of developer, signed and green on the gates developer requires.
# --------------------------------------------------------------------------

@pytest.mark.skipif(SSH_KEYGEN is None, reason="needs ssh-keygen")
def test_nightly_channel_installs_the_signed_developer_tip(tmp_path, monkeypatch):
    machine = _machine(tmp_path, tip_signed=True)
    tip = _git(machine.origin, "rev-parse", "developer").stdout.strip()
    _gates_green(monkeypatch)

    assert updater.main(["--yes", "--channel", "nightly"], environ=_env(machine)) == 0

    assert _head(machine.engine) == tip
    assert (machine.engine / "VERSION").read_text(encoding="utf-8").strip() == "0.2.0"


def test_nightly_refuses_a_signed_tip_that_has_not_passed_the_ci_gates(tmp_path, monkeypatch):
    """The gate lives between the signature and the merge: a red check is a refusal."""
    from nexgen_core.release_trust import VERIFIED, TagVerdict

    machine = _machine(tmp_path, tip_signed=False)
    monkeypatch.setattr(uc, "developer_tip", lambda repo: "a" * 40)
    monkeypatch.setattr(uc, "_signed_target", lambda *a, **k: uc.ChannelTarget(
        channel="nightly", rev="a" * 40, version="0.2.0", label="developer@aaaaaaaaa",
        detail="synthetic", verdict=TagVerdict(VERIFIED, "synthetic"),
    ))
    monkeypatch.setattr(uc, "checks_missing", lambda *_a, **_k: ["quick-windows=not finished"])

    with pytest.raises(uc.ChannelRefused) as refused:
        uc.nightly_target(machine.engine)

    assert "quick-windows=not finished" in str(refused.value)
    assert "CI gates" in str(refused.value)


@pytest.mark.skipif(SSH_KEYGEN is None, reason="needs ssh-keygen")
@pytest.mark.parametrize("flags", [["--yes"], ["--yes", "--allow-unverified"], ["--unattended"]])
def test_an_unsigned_developer_tip_is_refused_in_every_mode(tmp_path, flags, capsys):
    machine = _machine(tmp_path, tip_signed=False)
    before = _head(machine.engine)

    result = updater.main([*flags, "--channel", "nightly"], environ=_env(machine))

    assert result == updater.EXIT_REFUSED
    assert _head(machine.engine) == before
    assert "signed" in capsys.readouterr().err


@pytest.mark.skipif(SSH_KEYGEN is None, reason="needs ssh-keygen")
def test_a_developer_tip_signed_by_a_stranger_is_never_installed(tmp_path, monkeypatch, capsys):
    """The stranger's commit is passed over; the machine lands on the pinned-signed one below it."""
    machine = _machine(tmp_path, tip_signed=True)
    owner_signed = _git(machine.origin, "rev-parse", "developer").stdout.strip()
    stranger = _key(tmp_path / "stranger")
    _git(machine.origin, "checkout", "-q", "developer")
    (machine.origin / "foreign.txt").write_text("foreign\n", encoding="utf-8")
    _signed(machine.origin, stranger, "dev work by a stranger")
    _gates_green(monkeypatch)

    assert updater.main(["--yes", "--channel", "nightly"], environ=_env(machine)) == 0

    assert _head(machine.engine) == owner_signed
    assert not (machine.engine / "foreign.txt").exists()


@pytest.mark.skipif(SSH_KEYGEN is None, reason="needs ssh-keygen")
def test_a_developer_branch_only_a_stranger_signed_is_refused(tmp_path, capsys):
    machine = _machine(tmp_path, tip_signed=False)
    stranger = _key(tmp_path / "stranger")
    _git(machine.origin, "checkout", "-q", "developer")
    (machine.origin / "foreign.txt").write_text("foreign\n", encoding="utf-8")
    _signed(machine.origin, stranger, "dev work by a stranger")
    before = _head(machine.engine)

    result = updater.main(["--yes", "--channel", "nightly"], environ=_env(machine))

    assert result == updater.EXIT_REFUSED
    assert _head(machine.engine) == before
    assert "is not signed by a trusted release key" in capsys.readouterr().err


# --------------------------------------------------------------------------
# beta: the newest signed commit the full nightly suite proved.
# --------------------------------------------------------------------------

@pytest.mark.skipif(SSH_KEYGEN is None, reason="needs ssh-keygen")
def test_beta_takes_the_newest_signed_commit_the_full_suite_proved(tmp_path, monkeypatch):
    machine = _machine(tmp_path, tip_signed=True, extra_unsigned=True)
    signed = _git(machine.origin, "rev-parse", "developer~1").stdout.strip()
    unsigned = _git(machine.origin, "rev-parse", "developer").stdout.strip()
    # The newest full-suite run is green on an unsigned commit: the answer is the
    # newest SIGNED one the suite covered, never the unsigned tip.
    monkeypatch.setattr(uc, "full_suite_shas", lambda **_kw: [unsigned, signed])

    assert updater.main(["--yes", "--channel", "beta"], environ=_env(machine)) == 0

    assert _head(machine.engine) == signed


@pytest.mark.skipif(SSH_KEYGEN is None, reason="needs ssh-keygen")
def test_beta_never_installs_a_commit_without_the_full_suite(tmp_path, monkeypatch, capsys):
    machine = _machine(tmp_path, tip_signed=True)
    before = _head(machine.engine)
    monkeypatch.setattr(uc, "full_suite_shas", lambda **_kw: [])

    result = updater.main(["--yes", "--channel", "beta"], environ=_env(machine))

    assert result != 0
    assert _head(machine.engine) == before
    assert "full-suite" in capsys.readouterr().err


@pytest.mark.skipif(SSH_KEYGEN is None, reason="needs ssh-keygen")
def test_beta_refuses_a_proved_commit_that_is_not_signed(tmp_path, monkeypatch, capsys):
    machine = _machine(tmp_path, tip_signed=True, extra_unsigned=True)
    unsigned = _git(machine.origin, "rev-parse", "developer").stdout.strip()
    before = _head(machine.engine)
    monkeypatch.setattr(uc, "full_suite_shas", lambda **_kw: [unsigned])

    result = updater.main(["--yes", "--channel", "beta"], environ=_env(machine))

    assert result != 0
    assert _head(machine.engine) == before


# --------------------------------------------------------------------------
# The proofs: the `full-suite` status the workflow publishes (beta) and the
# branch CI gates (nightly), both read from the commit, never from the run.
# --------------------------------------------------------------------------

def _commits(*shas: str) -> dict:
    return {"commits": [{"sha": sha} for sha in shas]}


def _statuses(*entries: tuple[str, str]) -> dict:
    return {"statuses": [{"context": context, "state": state} for context, state in entries]}


def _check_runs(*entries: tuple[str, str]) -> dict:
    return {"check_runs": [{"name": name, "conclusion": conclusion} for name, conclusion in entries]}


def test_every_required_ci_gate_must_be_green_on_the_commit(monkeypatch):
    """nightly's gates: the leak scan, the Linux suite and the fast Windows one, on this commit."""
    sha = "a" * 40
    monkeypatch.setattr(uc, "_api_get", lambda path: _check_runs(
        ("self-check", "success"), ("engine-tests", "success"), ("quick-windows", "failure"),
    ))

    assert uc.checks_missing("o/r", sha) == ["quick-windows=failure"]


def test_a_gate_still_running_is_not_a_pass(monkeypatch):
    monkeypatch.setattr(uc, "_api_get", lambda path: _check_runs(("quick-windows", "")))

    assert uc.checks_missing("o/r", "b" * 40) == [
        "self-check=not finished", "engine-tests=not finished", "quick-windows=not finished",
    ]


def test_a_rerun_that_succeeded_is_the_one_that_counts(monkeypatch):
    """The first entry per name is the newest run; an old failure must not block a green re-run."""
    monkeypatch.setattr(uc, "_api_get", lambda path: _check_runs(
        ("quick-windows", "success"), ("quick-windows", "failure"),
        ("self-check", "success"), ("engine-tests", "success"),
    ))

    assert uc.checks_missing("o/r", "c" * 40) == []


def test_only_commits_with_a_green_full_suite_status_are_candidates(monkeypatch):
    green, red, other = "a" * 40, "b" * 40, "c" * 40
    payloads = {
        "repos/o/r/commits?sha=developer&per_page=12": _commits(green, red, other),
        f"repos/o/r/commits/{green}/status": _statuses(("full-suite", "success")),
        f"repos/o/r/commits/{red}/status": _statuses(("full-suite", "failure")),
        # A green status in ANOTHER context proves nothing about the full suite.
        f"repos/o/r/commits/{other}/status": _statuses(("ci", "success")),
    }
    monkeypatch.setattr(uc, "_api_get", lambda path: payloads[path])

    assert uc.full_suite_shas("o/r") == [green]


def test_a_commit_with_no_status_at_all_is_never_a_candidate(monkeypatch):
    """A rewritten history or a suite that never ran leaves no status: no green, no candidate."""
    monkeypatch.setattr(uc, "_api_get", lambda path: _commits("d" * 40) if "commits?" in path else _statuses())

    assert uc.full_suite_shas("o/r") == []


def test_the_status_lookup_reads_the_commit_not_the_run_event(monkeypatch):
    """The bug this replaces: schedule runs exist only on the default branch."""
    seen: list[str] = []
    monkeypatch.setattr(uc, "_api_get", lambda path: seen.append(path) or (
        _commits("e" * 40) if "commits?" in path else _statuses(("full-suite", "success"))
    ))

    assert uc.full_suite_shas("o/r") == ["e" * 40]
    assert seen[0].startswith("repos/o/r/commits?sha=developer")
    assert f"repos/o/r/commits/{'e' * 40}/status" == seen[1]
    assert not any("actions/" in path for path in seen)


def test_the_public_api_answers_when_gh_is_missing(monkeypatch):
    monkeypatch.setattr(uc.shutil, "which", lambda _name: None)
    seen: list[str] = []
    monkeypatch.setattr(uc, "_https_api", lambda path: seen.append(path) or {"runs": []})

    assert uc._api_get("repos/o/r/x") == {"runs": []}
    assert seen == ["repos/o/r/x"]


def test_gh_is_preferred_and_its_failure_still_reaches_the_public_api(monkeypatch):
    monkeypatch.setattr(uc.shutil, "which", lambda _name: "gh")
    monkeypatch.setattr(uc.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "not logged in"))
    seen: list[str] = []
    monkeypatch.setattr(uc, "_https_api", lambda path: seen.append(path) or {"runs": []})

    assert uc._api_get("repos/o/r/x") == {"runs": []}
    assert seen == ["repos/o/r/x"]


@pytest.mark.skipif(SSH_KEYGEN is None, reason="needs ssh-keygen")
def test_nightly_passes_over_a_tip_no_pinned_key_signed_for_the_signed_commit_below_it(tmp_path, monkeypatch):
    """After a release GitHub's own merge of main sits on top of developer, signed by
    GitHub; v2.16.3 refused it and nightly stayed shut until the owner pushed again."""
    machine = _machine(tmp_path, tip_signed=True, extra_unsigned=True)
    signed = _git(machine.origin, "rev-parse", "developer~1").stdout.strip()
    _gates_green(monkeypatch)

    assert updater.main(["--yes", "--channel", "nightly"], environ=_env(machine)) == 0

    assert _head(machine.engine) == signed
