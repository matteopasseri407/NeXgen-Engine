"""The maintainer registration: both halves, the tools it unlocks, and the ones it must not touch.

A machine is a maintainer when this installation's private config says `maintainer: true` AND the
key its git would sign with is listed in `trust/release-signers.txt`. The tests below cover the
four combinations, the commands that must refuse without it, and the commands that must keep
working for a person who is not a maintainer: the discipline workflow in their own project, and
the Council above all. No test reads this machine's real git config or real Vault: the flag and
the key are injected, so a maintainer's own machine answers nothing here.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

TESTS_DIR = Path(__file__).resolve().parent
REAL_VAULT = TESTS_DIR.parents[2]
SCRIPTS = REAL_VAULT / "03-INFRA" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from nexgen_core import maintainer  # noqa: E402
from nexgen_core.discipline import cli as discipline_cli  # noqa: E402

#: One of the fingerprints the shipped anchor pins, as test data.
LISTED_KEY = "5D061CE9626C9CC8BD88761F4399A81E895EB96F"
#: A well-formed key that no release was signed with.
UNLISTED_KEY = "0123456789ABCDEF0123456789ABCDEF01234567"
SSH_BODY = "AAAAC3NzaC1lZDI1NTE5AAAAIJI0QgCVCvqEoZe3UvTAn9vBV3KN3O/8iNrH4hjVR537"
REFUSAL = "this is a maintainer tool; this installation is not registered as maintainer"


@pytest.fixture(autouse=True)
def _english():
    """These tests pin one sentence's wording: the language is chosen, never inherited.

    The engine speaks the language the system reports, and the suite runs on machines set up in
    Italian as well as English. Without this fixture the same assertion changes with the host.
    """
    from nexgen_core import i18n

    i18n.set_language("en")
    yield
    i18n.set_language(None)


def _config(tmp_path: Path, body: str | None = None) -> Path:
    """The installation's private config, at the path the channel gate reads."""
    path = tmp_path / "sync" / "remotes.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        body if body is not None else "schema_version: 1\nauthoritative_remote: origin\nmirrors: []\n",
        encoding="utf-8",
    )
    return path


def _anchor(tmp_path: Path, *entries: str) -> Path:
    """The trust anchor an installed copy carries: a signer list, and the key material beside it."""
    folder = tmp_path / "trust"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "release-signers.txt").write_text("\n".join(entries) + "\n", encoding="utf-8")
    (folder / "release-signing-keys.asc").write_text("placeholder\n", encoding="utf-8")
    return folder


OPENPGP_ENTRY = f"openpgp {LISTED_KEY}"
SSH_ENTRY = f"ssh ssh-ed25519 {SSH_BODY}"


def _status(tmp_path: Path, *, config: Path | None, key: str, anchor: Path | None) -> maintainer.MaintainerStatus:
    return maintainer.is_maintainer(
        tmp_path,
        config=config,
        anchor_dir=anchor,
        resolve_key=lambda: key,
    )


# --------------------------------------------------------------------------------------------------
# The four combinations
# --------------------------------------------------------------------------------------------------


def test_flag_and_a_listed_key_are_a_maintainer(tmp_path):
    status = _status(tmp_path, config=_config(tmp_path, "maintainer: true\n"), key=LISTED_KEY,
                     anchor=_anchor(tmp_path, OPENPGP_ENTRY))
    assert status.ok is True
    assert status.flag is True
    assert status.key is True
    assert "the private config says maintainer: true" in status.flag_reason
    assert "is a listed release signer" in status.key_reason
    assert status.reasons == (status.flag_reason, status.key_reason)


def test_the_flag_without_a_listed_key_is_not_a_maintainer(tmp_path):
    status = _status(tmp_path, config=_config(tmp_path, "maintainer: true\n"), key=UNLISTED_KEY,
                     anchor=_anchor(tmp_path, OPENPGP_ENTRY))
    assert status.ok is False
    assert status.flag is True
    assert status.key is False
    assert "is not in release-signers.txt" in status.key_reason


def test_the_key_without_the_flag_is_not_a_maintainer(tmp_path):
    status = _status(tmp_path, config=_config(tmp_path), key=LISTED_KEY, anchor=_anchor(tmp_path, OPENPGP_ENTRY))
    assert status.ok is False
    assert status.flag is False
    assert status.key is True
    assert "does not say maintainer: true" in status.flag_reason


def test_neither_half_is_not_a_maintainer(tmp_path):
    status = _status(tmp_path, config=None, key="", anchor=tmp_path / "no-trust")
    assert status.ok is False
    assert status.flag is False and status.key is False
    assert "does not say maintainer: true" in status.flag_reason
    assert "release-signers.txt is missing" in status.key_reason


def test_a_flag_that_is_not_true_does_not_register_anything(tmp_path):
    status = _status(tmp_path, config=_config(tmp_path, 'maintainer: "yes"\n'), key=LISTED_KEY,
                     anchor=_anchor(tmp_path, OPENPGP_ENTRY))
    assert status.ok is False
    assert status.flag is False


def test_a_config_that_cannot_be_parsed_names_the_file(tmp_path):
    config = _config(tmp_path, "maintainer: [unclosed\n")
    status = _status(tmp_path, config=config, key=LISTED_KEY, anchor=_anchor(tmp_path, OPENPGP_ENTRY))
    assert status.ok is False
    assert status.flag is False
    assert str(config) in status.flag_reason


@pytest.mark.parametrize("configured", [LISTED_KEY, LISTED_KEY[-16:], f"0x{LISTED_KEY}"])
def test_git_accepts_a_full_fingerprint_a_key_id_and_a_0x_prefix(tmp_path, configured):
    status = _status(tmp_path, config=_config(tmp_path, "maintainer: true\n"), key=configured,
                     anchor=_anchor(tmp_path, OPENPGP_ENTRY))
    assert status.ok is True


def test_an_ssh_key_is_matched_by_its_body_however_git_names_it(tmp_path):
    anchor = _anchor(tmp_path, SSH_ENTRY)
    config = _config(tmp_path, "maintainer: true\n")
    for configured in (f"ssh-ed25519 {SSH_BODY} who@host", f"key::ssh-ed25519 {SSH_BODY}"):
        assert _status(tmp_path, config=config, key=configured, anchor=anchor).ok is True


def test_an_ssh_key_file_is_read_from_disk(tmp_path):
    public = tmp_path / "id_ed25519.pub"
    public.write_text(f"ssh-ed25519 {SSH_BODY} who@host\n", encoding="utf-8")
    status = _status(tmp_path, config=_config(tmp_path, "maintainer: true\n"), key=str(public),
                     anchor=_anchor(tmp_path, SSH_ENTRY))
    assert status.ok is True


@pytest.mark.skipif(shutil.which("ssh-keygen") is None, reason="ssh-keygen is how git derives the public half")
def test_an_ssh_private_key_matches_the_public_key_the_anchor_pins(tmp_path):
    """git is configured with the private key: the anchor pins its public half, not the file."""
    private = tmp_path / "signing"
    subprocess.run([shutil.which("ssh-keygen"), "-q", "-t", "ed25519", "-N", "", "-f", str(private)],
                   check=True)
    public_line = (tmp_path / "signing.pub").read_text(encoding="utf-8")
    kind, body, *_ = public_line.split()
    status = _status(tmp_path, config=_config(tmp_path, "maintainer: true\n"), key=str(private),
                     anchor=_anchor(tmp_path, f"ssh {kind} {body}"))
    assert status.ok is True


# --------------------------------------------------------------------------------------------------
# The commands that refuse
# --------------------------------------------------------------------------------------------------


@pytest.fixture
def not_a_maintainer(monkeypatch, tmp_path):
    """The machine the engine is installed on for most people: a status with nothing satisfied."""
    status = maintainer.MaintainerStatus(False, "no flag", False, "no key")
    monkeypatch.setattr(maintainer, "is_maintainer", lambda home=None: status)
    return status


@pytest.fixture
def a_maintainer(monkeypatch):
    status = maintainer.MaintainerStatus(True, "flag", True, "key")
    monkeypatch.setattr(maintainer, "is_maintainer", lambda home=None: status)
    return status


def test_release_preflight_refuses_and_never_runs(not_a_maintainer, monkeypatch, capsys):
    from nexgen_core import release

    monkeypatch.setattr(release, "_preflight", lambda: pytest.fail("a refused preflight must not run"))
    assert release.main(["preflight"]) == 1
    assert REFUSAL in capsys.readouterr().err


def test_release_ship_refuses_before_it_parses_anything(not_a_maintainer, capsys):
    from nexgen_core import release_ship

    assert release_ship.main(["patch"]) == 1
    assert REFUSAL in capsys.readouterr().err


def test_a_maintainer_reaches_the_release_preflight(a_maintainer, monkeypatch):
    from nexgen_core import release

    monkeypatch.setattr(release, "_preflight", lambda: 0)
    assert release.main(["preflight"]) == 0


def test_the_engine_repo_gate_refuses_a_non_maintainer(not_a_maintainer, capsys):
    assert discipline_cli.main(["gate"]) == 1
    assert REFUSAL in capsys.readouterr().err


def test_discipline_init_refuses_inside_the_engine_repo(not_a_maintainer, capsys):
    assert discipline_cli.main(["init", "--smoke", "python -c pass"]) == 1
    assert REFUSAL in capsys.readouterr().err


# --------------------------------------------------------------------------------------------------
# The commands that must not be gated
# --------------------------------------------------------------------------------------------------


def test_discipline_init_still_works_in_a_persons_own_project(not_a_maintainer, tmp_path, monkeypatch):
    project = tmp_path / "their-project"
    project.mkdir()
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    monkeypatch.chdir(project)
    assert discipline_cli.main(["init", "--smoke", "python -c pass"]) == 0
    assert (project / ".nexgen-discipline.toml").is_file()
    assert (project / ".git" / "hooks" / "pre-push").is_file()


def test_discipline_check_is_never_gated_inside_the_engine_repo(not_a_maintainer, monkeypatch):
    """`check` runs on every commit of every project, the engine's included: it is not a publish step."""
    from nexgen_core.discipline import cli

    monkeypatch.setattr(cli.gates, "run", lambda *a, **k: SimpleNamespace(ok=True))
    monkeypatch.setattr(cli.gates, "render", lambda *a, **k: "ok")
    assert discipline_cli.main(["check", "--base", "HEAD", "--head", "HEAD"]) == 0


def test_a_project_outside_the_engine_is_never_a_maintainer_surface(tmp_path):
    """The refusal is about the engine's own tree; a clone of something else is nobody's"""
    assert maintainer.engine_repo_refusal(tmp_path) is None


# --------------------------------------------------------------------------------------------------
# `nexgen maintainer status` / `register`
# --------------------------------------------------------------------------------------------------


def test_status_prints_both_halves(a_maintainer, capsys):
    from nexgen_core.cli import maintainer_cmds

    assert maintainer_cmds.cmd_status(SimpleNamespace(json=False)) == 0
    printed = capsys.readouterr().out
    assert "maintainer: yes (this installation may run the maintainer tools)" in printed
    assert "flag" in printed and "key" in printed


def test_status_as_json_carries_the_reasons(a_maintainer, capsys):
    from nexgen_core.cli import maintainer_cmds

    maintainer_cmds.cmd_status(SimpleNamespace(json=True))
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"ok": True, "flag": True, "key": True, "signing_key": "", "reasons": ["flag", "key"]}


def test_register_writes_the_flag_and_keeps_the_rest_of_the_config(tmp_path, monkeypatch, capsys):
    from nexgen_core.cli import maintainer_cmds

    config = _config(tmp_path, "schema_version: 1\nauthoritative_remote: origin\nmirrors: []\n"
                               "update:\n  allow_channels: [nightly]\n")
    monkeypatch.setattr(maintainer, "config_path", lambda home=None: config)
    monkeypatch.setattr(maintainer, "is_maintainer", lambda home=None: maintainer.MaintainerStatus(
        True, "flag", True, "key"))
    assert maintainer_cmds.cmd_register(SimpleNamespace()) == 0
    written = config.read_text(encoding="utf-8")
    assert "maintainer: true" in written
    kept = yaml.safe_load(written)
    assert kept["authoritative_remote"] == "origin"
    assert kept["update"] == {"allow_channels": ["nightly"]}
    assert maintainer.read_flag(config)[0] is True


def test_register_reports_a_key_half_that_is_missing(tmp_path, monkeypatch, capsys):
    from nexgen_core.cli import maintainer_cmds

    config = _config(tmp_path)
    monkeypatch.setattr(maintainer, "config_path", lambda home=None: config)
    monkeypatch.setattr(maintainer, "is_maintainer", lambda home=None: maintainer.MaintainerStatus(
        True, "flag", False, "the signing key is not in release-signers.txt"))
    assert maintainer_cmds.cmd_register(SimpleNamespace()) == 1
    assert "not in release-signers.txt" in capsys.readouterr().out


def test_register_with_no_private_config_prints_the_line_and_writes_nothing(tmp_path, monkeypatch, capsys):
    from nexgen_core.cli import maintainer_cmds

    absent = tmp_path / "no-vault" / "sync" / "remotes.yaml"
    monkeypatch.setattr(maintainer, "config_path", lambda home=None: absent)
    assert maintainer_cmds.cmd_register(SimpleNamespace()) == 1
    assert "maintainer: true" in capsys.readouterr().out
    assert not absent.exists()


# --------------------------------------------------------------------------------------------------
# The doctor's line
# --------------------------------------------------------------------------------------------------


def test_the_doctor_is_green_on_an_installation_that_never_asked(tmp_path):
    from nexgen_core.checks.maintainer_checks import check_maintainer_registration
    from nexgen_core.report import Severity

    vault = tmp_path / "vault"
    (vault / "03-INFRA" / "agent-universal-layer" / "sync").mkdir(parents=True)
    outcome = check_maintainer_registration(vault, tmp_path / "engine" / "03-INFRA")
    assert outcome.severity == Severity.OK
    assert outcome.message == "Not a maintainer machine: the publishing tools are hidden here"


def test_the_doctor_warns_about_a_registration_that_cannot_work(tmp_path):
    from nexgen_core.checks.maintainer_checks import check_maintainer_registration
    from nexgen_core.report import Severity

    vault = tmp_path / "vault"
    config = vault / "03-INFRA" / "agent-universal-layer" / "sync" / "remotes.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("maintainer: true\n", encoding="utf-8")
    engine = tmp_path / "engine" / "03-INFRA"
    _anchor(tmp_path, SSH_ENTRY)  # a key this machine does not sign with
    outcome = check_maintainer_registration(vault, engine)
    assert outcome.severity == Severity.WARN
    assert outcome.message.startswith("Registered as maintainer but half of it is missing: ")
    assert "nexgen maintainer status" in outcome.action


# --------------------------------------------------------------------------------------------------
# What `--help` shows
# --------------------------------------------------------------------------------------------------


def _help(monkeypatch, *, available: bool) -> str:
    from nexgen_core import cli

    monkeypatch.setattr(maintainer, "tools_available", lambda: available)
    return cli.build_parser().format_help()


def test_help_hides_the_maintainer_commands_from_a_non_maintainer(monkeypatch):
    text = _help(monkeypatch, available=False)
    assert "Maintainers: ship a release" not in text
    assert "registered as maintainer" not in text


def test_help_lists_them_for_a_maintainer(monkeypatch):
    text = _help(monkeypatch, available=True)
    assert "Maintainers: ship a release" in text
    assert "registered as maintainer" in text


def test_the_hidden_verbs_are_still_reachable(monkeypatch):
    from nexgen_core import cli

    monkeypatch.setattr(maintainer, "tools_available", lambda: False)
    parsed = cli.build_parser().parse_args(["maintainer", "status"])
    assert parsed.func.__name__ == "cmd_status"
    assert cli.build_parser().parse_known_args(["release", "preflight"])[0].command == "release"


# --------------------------------------------------------------------------------------------------
# The engine's own leak gate: the entry point the scanner left behind
# --------------------------------------------------------------------------------------------------


SCANNER = REAL_VAULT / "03-INFRA" / "agent-universal-layer" / "leak-scan" / "leak_scan.py"
ENGINE_CHECKOUT = maintainer.engine_checkout_root()

#: A gitleaks stand-in that always reports one finding: the test is about whether the scan ran,
#: never about the shapes gitleaks really carries, and the real binary is not installed here.
FAKE_GITLEAKS = '''import json, sys
print(json.dumps([{"RuleID": "fake-rule", "File": "a.py", "StartLine": 3,
                   "Match": "synthetic", "Secret": "synthetic-value"}]))
sys.exit(1)
'''


def _fake_gitleaks(tmp_path: Path) -> Path:
    """A `gitleaks` on PATH that answers with one finding, whatever it was asked."""
    folder = tmp_path / "fake-bin"
    folder.mkdir()
    script = folder / "fake_gitleaks.py"
    script.write_text(FAKE_GITLEAKS, encoding="utf-8")
    if sys.platform == "win32":
        shim = folder / "gitleaks.bat"
        shim.write_text('@echo off\r\n' + f'"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
    else:
        shim = folder / "gitleaks"
        shim.write_text('#!/bin/sh\n' + f'exec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
        shim.chmod(0o755)
    return folder


def _scan(env_extra: dict[str, str], *args: str, repo: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run the engine's scanner as its own program, in the environment a machine would give it."""
    env = dict(os.environ)
    for inherited in ("GITHUB_ACTIONS", "KNOWLEDGE_VAULT_PATH", "AGENT_VAULT_DATA"):
        env.pop(inherited, None)
    env["NEXGEN_LANG"] = "en"  # the assertions below read the English sentence, whatever the host speaks
    env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(SCANNER), "--repo", str(repo or ENGINE_CHECKOUT), *args],
        capture_output=True, text=True, check=False, timeout=120, env=env,
    )


@pytest.mark.skipif(ENGINE_CHECKOUT is None, reason="the engine is installed as a package here")
def test_the_commit_gate_refuses_on_a_machine_that_is_not_a_maintainer(tmp_path):
    result = _scan({"KNOWLEDGE_VAULT_PATH": str(tmp_path / "empty-vault")}, "--staged")
    assert result.returncode == 1
    assert REFUSAL in result.stderr
    assert "BLOCKING" not in result.stdout  # nothing was scanned


@pytest.mark.skipif(ENGINE_CHECKOUT is None, reason="the engine is installed as a package here")
def test_the_commit_gate_runs_in_ci_where_github_holds_the_lock(tmp_path):
    scanner_dir = _fake_gitleaks(tmp_path)
    result = _scan({"KNOWLEDGE_VAULT_PATH": str(tmp_path / "empty-vault"), "GITHUB_ACTIONS": "true",
                    "PATH": f"{scanner_dir}{os.pathsep}{os.environ.get('PATH', '')}"}, "--staged")
    assert REFUSAL not in result.stderr
    assert result.returncode == 1
    assert "[rule:fake-rule]" in result.stdout


def test_a_scan_of_somebody_elses_repository_is_not_gated(tmp_path):
    project = tmp_path / "not-the-engine"
    project.mkdir()
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    scanner_dir = _fake_gitleaks(tmp_path)
    result = _scan({"KNOWLEDGE_VAULT_PATH": str(tmp_path / "empty-vault"),
                    "PATH": f"{scanner_dir}{os.pathsep}{os.environ.get('PATH', '')}"},
                   "--staged", repo=project)
    assert REFUSAL not in result.stderr
    assert result.returncode == 1
    assert "[rule:fake-rule]" in result.stdout
