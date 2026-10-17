"""Already-published leak-scan findings are pinned one by one, never by value.

A release re-scans every commit since the last release, so a value in a
commit that is already public blocked every later release. The exception file
lets that one finding through and nothing else: the same value in another
commit still blocks, and a malformed entry aborts instead of matching loosely.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

LEAK_SCAN = Path(__file__).resolve().parents[1] / "leak-scan" / "leak_scan.py"
TOKEN = "PIN" + "LEAK" + "QWERTZUI"  # built at runtime: this file must not look like a leak


def _git(repo: Path, *args: str) -> str:
    mail = "t" + "@" + "example.invalid"  # joined at runtime: a literal address reads as a leak
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": mail, "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": mail, "PATH": os.environ["PATH"], "SYSTEMROOT": os.environ.get("SYSTEMROOT", "")}
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True,
                          env=env).stdout.strip()


def _repo(tmp_path: Path) -> tuple[Path, str, str, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "a.txt").write_text(f"value = {TOKEN}\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "published")
    published = _git(repo, "rev-parse", "HEAD")
    (repo / "b.txt").write_text(f"value = {TOKEN}\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "new")
    return repo, base, published, _git(repo, "rev-parse", "HEAD")


def _scan(tmp_path: Path, repo: Path, base: str, exceptions: str | None) -> subprocess.CompletedProcess:
    patterns = tmp_path / "patterns.yaml"
    patterns.write_text("leak_hard:\n  - 'PINLEAK[A-Z]{8}'\n", encoding="utf-8")
    args = [sys.executable, str(LEAK_SCAN), "--patterns", str(patterns), "--repo", str(repo),
            "--commit-range", f"{base}..HEAD"]
    if exceptions is not None:
        pinned = tmp_path / "exceptions.yaml"
        pinned.write_text(exceptions, encoding="utf-8")
        args += ["--exceptions", str(pinned)]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}  # Windows runners print in the ANSI codepage otherwise
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8", env=env)


def _entry(commit: str, path: str) -> str:
    return (f"  - rev: \"{commit}\"\n    path: {path}\n    line: 1\n    kind: pattern:h0\n"
            f"    match: \"PI…UI (15 chars)\"\n    reason: published test value\n")


def test_a_pinned_finding_passes_and_the_same_value_elsewhere_still_blocks(tmp_path):
    repo, base, published, new = _repo(tmp_path)
    assert _scan(tmp_path, repo, base, None).returncode == 1

    only_published = _scan(tmp_path, repo, base, "exceptions:\n" + _entry(published, "a.txt"))
    assert only_published.returncode == 1, "the same value in a commit nobody pinned must still block"
    assert f"commit {new[:9]}: b.txt" in only_published.stdout
    assert "a.txt" not in only_published.stdout.split("BLOCKING", 1)[-1]

    both = _scan(tmp_path, repo, base, "exceptions:\n" + _entry(published, "a.txt") + _entry(new, "b.txt"))
    assert both.returncode == 0, both.stdout


def test_an_exception_for_another_file_or_a_short_commit_does_not_match(tmp_path):
    repo, base, published, _new = _repo(tmp_path)
    wrong_file = _scan(tmp_path, repo, base, "exceptions:\n" + _entry(published, "other.txt"))
    assert "a.txt" in wrong_file.stdout and wrong_file.returncode == 1

    short = _scan(tmp_path, repo, base, "exceptions:\n" + _entry(published[:9], "a.txt"))
    assert short.returncode != 0 and "full commit" in (short.stdout + short.stderr)


def test_the_list_beside_the_patterns_is_used_without_being_named(tmp_path):
    repo, base, published, new = _repo(tmp_path)
    (tmp_path / "published_exceptions.yaml").write_text(
        "exceptions:\n" + _entry(published, "a.txt") + _entry(new, "b.txt"), encoding="utf-8")
    assert _scan(tmp_path, repo, base, None).returncode == 0


def test_a_malformed_exception_file_aborts_instead_of_matching_loosely(tmp_path):
    repo, base, _published, _new = _repo(tmp_path)
    broken = _scan(tmp_path, repo, base, "exceptions:\n  - rev: abc\n")
    assert broken.returncode != 0 and "ABORT" in (broken.stdout + broken.stderr)
