"""The release workflow's own steps, run for real against throwaway repositories.

The steps are read out of release.yml, so what runs here is what runs on the
runner, not a copy that can drift from it.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
WORKFLOW = REPO / ".github" / "workflows" / "release.yml"
TRUST_REL = Path("03-INFRA/agent-universal-layer/trust")
SSH_KEYGEN = shutil.which("ssh-keygen")


def _git_version() -> tuple[int, ...]:
    out = subprocess.run(["git", "--version"], capture_output=True, text=True, check=True).stdout
    return tuple(int(p) for p in out.split()[2].split(".")[:2])


pytestmark = pytest.mark.skipif(
    os.name == "nt" or SSH_KEYGEN is None or _git_version() < (2, 34) or shutil.which("bash") is None,
    reason="needs bash, ssh-keygen and git >= 2.34",
)


def _steps() -> dict[str, dict]:
    wf = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return {s["name"]: s for s in wf["jobs"]["publish"]["steps"] if "name" in s}


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True, env=env)


def _key(path: Path) -> Path:
    subprocess.run([SSH_KEYGEN, "-q", "-t", "ed25519", "-N", "", "-f", str(path), "-C", "t"], check=True)
    return path.with_suffix(".pub")


def _sign_tag(repo: Path, tag: str, pub: Path) -> None:
    _git(repo, "-c", "gpg.format=ssh", "-c", f"user.signingkey={pub.as_posix()}", "tag", "-s", "-m", tag, tag)


def _write_anchor(repo: Path, pub: Path) -> None:
    trust = repo / TRUST_REL
    trust.mkdir(parents=True, exist_ok=True)
    (trust / "release-signers.txt").write_text(f"ssh {' '.join(pub.read_text().split()[:2])}\n", encoding="utf-8")


@pytest.fixture
def project(tmp_path):
    """A repo shaped like the real one: the release_trust tool, an origin, and main."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    repo = tmp_path / "work"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    tool = repo / "03-INFRA/scripts/nexgen_core"
    tool.mkdir(parents=True)
    shutil.copy(REPO / "03-INFRA/scripts/nexgen_core/release_trust.py", tool / "release_trust.py")
    _git(repo, "remote", "add", "origin", str(origin))
    ours = _key(tmp_path / "ours")
    return repo, ours, tmp_path


def _release(repo: Path, version: str, pub: Path, *, anchor: Path | None) -> str:
    if anchor is not None:
        _write_anchor(repo, anchor)
    (repo / "VERSION").write_text(version + "\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", f"release {version}")
    tag = f"v{version}"
    _sign_tag(repo, tag, pub)
    return tag


def _run_step(name: str, repo: Path, tag: str, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    runner_temp = tmp_path / "runner-temp"
    runner_temp.mkdir(exist_ok=True)
    env = {**os.environ, "TAG": tag, "RUNNER_TEMP": str(runner_temp), "GITHUB_REPOSITORY": "x/y"}
    script = _steps()[name]["run"]
    return subprocess.run(["bash", "-c", script], cwd=repo, env=env, capture_output=True, text=True, check=False)


TRUST_STEP = "Refuse a tag that installed copies would not accept"
MAIN_STEP = "Refuse a tag that is not on main"


def test_a_tag_signed_by_the_previous_releases_key_is_accepted(project):
    repo, ours, tmp = project
    _release(repo, "1.0.0", ours, anchor=ours)
    tag = _release(repo, "1.0.1", ours, anchor=None)
    result = _run_step(TRUST_STEP, repo, tag, tmp)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "trust anchor of v1.0.0" in result.stdout


def test_a_tag_signed_by_a_key_the_previous_release_does_not_pin_is_refused(project):
    repo, ours, tmp = project
    _release(repo, "1.0.0", ours, anchor=ours)
    theirs = _key(tmp / "theirs")
    # The new release also swaps the signers file to its own key: it must not get to vouch for itself.
    tag = _release(repo, "1.0.1", theirs, anchor=theirs)
    result = _run_step(TRUST_STEP, repo, tag, tmp)
    assert result.returncode != 0
    assert "untrusted" in result.stdout


def test_a_lightweight_tag_is_refused(project):
    repo, ours, tmp = project
    _release(repo, "1.0.0", ours, anchor=ours)
    _git(repo, "tag", "v1.0.1")
    result = _run_step(TRUST_STEP, repo, "v1.0.1", tmp)
    assert result.returncode != 0
    assert "not an annotated tag" in result.stdout + result.stderr


def test_the_first_release_with_an_anchor_says_it_is_self_attested(project):
    repo, ours, tmp = project
    tag = _release(repo, "1.0.0", ours, anchor=ours)
    result = _run_step(TRUST_STEP, repo, tag, tmp)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "self-attested" in result.stdout


def test_a_tag_on_main_passes_and_a_tag_off_main_does_not(project):
    repo, ours, tmp = project
    tag = _release(repo, "1.0.0", ours, anchor=ours)
    _git(repo, "push", "-q", "origin", "main")
    assert _run_step(MAIN_STEP, repo, tag, tmp).returncode == 0
    _git(repo, "checkout", "-q", "-b", "stray")
    (repo / "x").write_text("x", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "stray work")
    _sign_tag(repo, "v1.0.1", ours)
    result = _run_step(MAIN_STEP, repo, "v1.0.1", tmp)
    assert result.returncode != 0
    assert "not point at a commit on main" in result.stdout + result.stderr


def test_build_tools_are_pinned_and_ci_is_required():
    steps = _steps()
    assert "Refuse a commit that has not passed CI" in steps
    commands = "\n".join(s["run"] for s in steps.values() if isinstance(s.get("run"), str))
    assert "--upgrade" not in commands
    assert "build==" in commands and "twine==" in commands
