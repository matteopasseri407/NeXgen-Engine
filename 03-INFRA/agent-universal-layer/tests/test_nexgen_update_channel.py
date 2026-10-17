"""The dev channels: which commit they point at, and what they refuse.

A stable target is a version; a dev target is a commit, and the only thing that
makes it installable is a signature the installed tree already pins (never a
`--allow-unverified` door), plus, for `dev-verified`, a nightly CI run that
finished green on Windows and Linux. These tests build real repositories and
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
from nexgen_core import updater  # noqa: E402

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
        # The dev channels are closed on a fresh installation; every test that
        # uses them states the unlock it depends on, so the closed default is
        # never an accident of the fixture. It travels with the repository the
        # clone is made from, exactly as a vault config would.
        _instance_config(origin, allow=("dev", "dev-verified"))
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


def _head(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


# --------------------------------------------------------------------------
# The installation unlocks the dev channels; the public default is closed.
# --------------------------------------------------------------------------

def test_a_dev_channel_is_closed_on_an_installation_that_did_not_open_it(tmp_path, capsys):
    machine = _machine(tmp_path, tip_signed=True, unlocked=False)
    before = _head(machine.engine)

    result = updater.main(["--yes", "--channel", "dev"], environ=_env(machine))

    assert result == updater.EXIT_REFUSED
    assert _head(machine.engine) == before
    assert "not enabled on this installation" in capsys.readouterr().err


def test_a_remembered_dev_channel_is_ignored_when_the_unlock_is_gone(tmp_path, capsys):
    """Removing the private key closes the door again instead of failing every cycle."""
    machine = _machine(tmp_path, tip_signed=True, unlocked=False)
    machine.state.mkdir(parents=True, exist_ok=True)
    (machine.state / uc.CHANNEL_FILE).write_text("dev\n", encoding="utf-8")
    before = _head(machine.engine)

    result = updater.main(["--yes"], environ=_env(machine))

    assert result == 0
    assert _head(machine.engine) == before  # nothing to do on stable: it is already at v0.1.0
    assert (machine.engine / "VERSION").read_text(encoding="utf-8").strip() == "0.1.0"


def test_the_unlock_opens_exactly_the_channels_it_names(tmp_path):
    config = tmp_path / "remotes.yaml"
    config.write_text("schema_version: 1\nupdate:\n  allow_channels: [dev]\n", encoding="utf-8")
    assert uc.allowed_channels(config_path=config) == frozenset({"dev"})
    assert uc.resolve_channel(None, tmp_path, allowed=uc.allowed_channels(config_path=config)) == "stable"
    assert uc.resolve_channel("dev", tmp_path, allowed=uc.allowed_channels(config_path=config)) == "dev"
    with pytest.raises(uc.ChannelRefused):
        uc.resolve_channel("dev-verified", tmp_path, allowed=uc.allowed_channels(config_path=config))


@pytest.mark.parametrize("body", ["", "not: [a, config\n", "- a list\n", "update: dev\n", "update:\n  allow_channels: nope\n",
                                  "update:\n  allow_channels: [stable, dev-verified, silly]\n"])
def test_a_broken_or_unknown_unlock_closes_the_door(tmp_path, body):
    config = tmp_path / "remotes.yaml"
    config.write_text(body, encoding="utf-8")

    allowed = uc.allowed_channels(config_path=config)

    assert allowed <= frozenset(uc.UNLOCKABLE)
    assert "stable" not in allowed


def test_a_missing_unlock_file_is_the_closed_default(tmp_path):
    assert uc.allowed_channels(config_path=tmp_path / "absent.yaml") == frozenset()


# --------------------------------------------------------------------------
# dev: the tip of developer, and only when it is signed by a pinned key.
# --------------------------------------------------------------------------

@pytest.mark.skipif(SSH_KEYGEN is None, reason="needs ssh-keygen")
def test_dev_channel_installs_the_signed_developer_tip(tmp_path):
    machine = _machine(tmp_path, tip_signed=True)
    tip = _git(machine.origin, "rev-parse", "developer").stdout.strip()

    assert updater.main(["--yes", "--channel", "dev"], environ=_env(machine)) == 0

    assert _head(machine.engine) == tip
    assert (machine.engine / "VERSION").read_text(encoding="utf-8").strip() == "0.2.0"


@pytest.mark.skipif(SSH_KEYGEN is None, reason="needs ssh-keygen")
@pytest.mark.parametrize("flags", [["--yes"], ["--yes", "--allow-unverified"], ["--unattended"]])
def test_an_unsigned_developer_tip_is_refused_in_every_mode(tmp_path, flags, capsys):
    machine = _machine(tmp_path, tip_signed=False)
    before = _head(machine.engine)

    result = updater.main([*flags, "--channel", "dev"], environ=_env(machine))

    assert result == updater.EXIT_REFUSED
    assert _head(machine.engine) == before
    assert "signed" in capsys.readouterr().err


@pytest.mark.skipif(SSH_KEYGEN is None, reason="needs ssh-keygen")
def test_a_developer_tip_signed_by_a_stranger_is_refused(tmp_path, capsys):
    machine = _machine(tmp_path, tip_signed=True)
    stranger = _key(tmp_path / "stranger")
    _git(machine.origin, "checkout", "-q", "developer")
    (machine.origin / "foreign.txt").write_text("foreign\n", encoding="utf-8")
    _signed(machine.origin, stranger, "dev work by a stranger")
    before = _head(machine.engine)

    result = updater.main(["--yes", "--channel", "dev"], environ=_env(machine))

    assert result == updater.EXIT_REFUSED
    assert _head(machine.engine) == before
    assert "is not signed by a trusted release key" in capsys.readouterr().err


# --------------------------------------------------------------------------
# dev-verified: the newest signed commit a green nightly actually covered.
# --------------------------------------------------------------------------

@pytest.mark.skipif(SSH_KEYGEN is None, reason="needs ssh-keygen")
def test_dev_verified_takes_the_newest_signed_commit_with_a_green_nightly(tmp_path, monkeypatch):
    machine = _machine(tmp_path, tip_signed=True, extra_unsigned=True)
    signed = _git(machine.origin, "rev-parse", "developer~1").stdout.strip()
    unsigned = _git(machine.origin, "rev-parse", "developer").stdout.strip()
    # The newest nightly is green on an unsigned commit: the answer is the
    # newest SIGNED one the nightly covered, never the unsigned tip.
    monkeypatch.setattr(uc, "green_nightly_shas", lambda **_kw: [unsigned, signed])

    assert updater.main(["--yes", "--channel", "dev-verified"], environ=_env(machine)) == 0

    assert _head(machine.engine) == signed


@pytest.mark.skipif(SSH_KEYGEN is None, reason="needs ssh-keygen")
def test_dev_verified_never_installs_a_commit_without_a_green_nightly(tmp_path, monkeypatch, capsys):
    machine = _machine(tmp_path, tip_signed=True)
    before = _head(machine.engine)
    monkeypatch.setattr(uc, "green_nightly_shas", lambda **_kw: [])

    result = updater.main(["--yes", "--channel", "dev-verified"], environ=_env(machine))

    assert result != 0
    assert _head(machine.engine) == before
    assert "nightly" in capsys.readouterr().err


@pytest.mark.skipif(SSH_KEYGEN is None, reason="needs ssh-keygen")
def test_dev_verified_refuses_a_green_nightly_whose_commit_is_not_signed(tmp_path, monkeypatch, capsys):
    machine = _machine(tmp_path, tip_signed=True, extra_unsigned=True)
    unsigned = _git(machine.origin, "rev-parse", "developer").stdout.strip()
    before = _head(machine.engine)
    monkeypatch.setattr(uc, "green_nightly_shas", lambda **_kw: [unsigned])

    result = updater.main(["--yes", "--channel", "dev-verified"], environ=_env(machine))

    assert result != 0
    assert _head(machine.engine) == before


# --------------------------------------------------------------------------
# The nightly lookup itself: schedule runs only, and both platforms in them.
# --------------------------------------------------------------------------

def _runs(*entries: tuple[int, str, str, str]) -> dict:
    return {
        "workflow_runs": [
            {"id": run_id, "head_sha": sha, "conclusion": conclusion, "event": event,
             "head_branch": "developer"}
            for run_id, sha, conclusion, event in entries
        ]
    }


def _jobs(*labels: tuple[str, ...]) -> dict:
    return {"jobs": [{"labels": list(label), "conclusion": "success"} for label in labels]}


def test_only_successful_schedule_runs_on_developer_are_candidates(monkeypatch):
    payloads = {
        "repos/o/r/actions/workflows/ci.yml/runs?branch=developer&event=schedule&status=success&per_page=30":
            _runs((1, "a" * 40, "success", "schedule"), (2, "b" * 40, "failure", "schedule"),
                  (3, "c" * 40, "success", "push")),
        "repos/o/r/actions/runs/1/jobs?per_page=100": _jobs(("ubuntu-latest",), ("windows-latest",)),
        "repos/o/r/actions/runs/3/jobs?per_page=100": _jobs(("ubuntu-latest",), ("windows-latest",)),
    }
    monkeypatch.setattr(uc, "_api_get", lambda path: payloads[path])

    assert uc.green_nightly_shas("o/r") == ["a" * 40]


def test_a_run_without_the_windows_leg_proves_nothing(monkeypatch):
    """Windows and Linux, or the nightly did not judge what dev-verified claims."""
    payloads = {
        "repos/o/r/actions/workflows/ci.yml/runs?branch=developer&event=schedule&status=success&per_page=30":
            _runs((1, "a" * 40, "success", "schedule")),
        "repos/o/r/actions/runs/1/jobs?per_page=100": _jobs(("ubuntu-latest",)),
    }
    monkeypatch.setattr(uc, "_api_get", lambda path: payloads[path])

    assert uc.green_nightly_shas("o/r") == []


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
