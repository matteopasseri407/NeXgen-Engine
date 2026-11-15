"""The `skills` CLI backend: what it fetches, and every way it refuses to.

The CLI itself is never run here: `_run` is replaced, and the fake writes the
files the real one writes (`skills-lock.json` version 2 and
`.agents/skills/<name>/SKILL.md`), so the assertions are about this engine's
rules — the pinned version, the telemetry switches, and a lock that must prove
the pin — and not about the tool's own behavior.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path


from nexgen_core import skills_tool

PIN = "96608e078b2d5ea607c5" "c3b8b5a54d1c1fba6846"
OTHER = "b48c832f0f80a0e5db0b" "4c0bd4b2a3a1e5b0c7d9"


def _lock(tmp_path: Path, *, ref: str | None = PIN, content_hash: str | None = "fe7b781d") -> None:
    entry = {"source": "owner/repo", "sourceType": "github", "skillPath": "s/SKILL.md"}
    if ref is not None:
        entry["ref"] = ref
    if content_hash is not None:
        entry["computedHash"] = content_hash
    (tmp_path / skills_tool.LOCK_FILE).write_text(
        json.dumps({"version": 2, "skills": {"demo": entry}}), encoding="utf-8")


def _installed(tmp_path: Path) -> None:
    folder = tmp_path / skills_tool.INSTALLED_ROOT / "demo"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text("---\nname: demo\n---\n", encoding="utf-8")


def _fake_run(tmp_path: Path, monkeypatch, code: int = 0, stdout: str = "", stderr: str = ""):
    """Answer like the CLI does, and record the argv it was called with."""
    seen: list[list[str]] = []

    def run(argv, cwd):
        seen.append(list(argv))
        return subprocess.CompletedProcess(argv, code, stdout, stderr)

    monkeypatch.setattr(skills_tool, "_run", run)
    monkeypatch.setattr(skills_tool, "available", lambda which=None: True)
    return seen


def test_available_needs_both_node_and_npx():
    """`npx` alone is not enough: the shim is started through node."""
    assert skills_tool.available(which=lambda name: "/bin/" + name) is True
    assert skills_tool.available(which=lambda name: "/bin/npx" if name == "npx" else None) is False


def test_a_pinned_fetch_asks_for_the_pinned_version_and_the_one_skill(tmp_path, monkeypatch):
    _installed(tmp_path)
    _lock(tmp_path)
    seen = _fake_run(tmp_path, monkeypatch)

    folder, problem = skills_tool.fetch("owner/repo", PIN, "demo", tmp_path)

    assert problem is None
    assert folder == tmp_path / ".agents" / "skills" / "demo"
    assert seen == [["npx", "--yes", "skills@1.8.1", "add", f"owner/repo@{PIN}",
                     "--skill", "demo", "-y", "--copy"]]


def test_the_telemetry_switches_are_always_set(monkeypatch):
    """The CLI collects anonymous usage data unless told not to, on every run."""
    captured: dict = {}

    def fake_run(argv, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(skills_tool.subprocess, "run", fake_run)
    skills_tool._run(["npx", "--version"], Path("."))

    env = captured["env"]
    assert (env["DISABLE_TELEMETRY"], env["DO_NOT_TRACK"]) == ("1", "1")
    assert captured["timeout"] == skills_tool.RUN_TIMEOUT_SECONDS


def test_a_lock_that_names_another_commit_is_refused(tmp_path, monkeypatch):
    """The bytes that arrived are not the bytes the pin names: nothing is used."""
    _installed(tmp_path)
    _lock(tmp_path, ref=OTHER)
    _fake_run(tmp_path, monkeypatch)

    folder, problem = skills_tool.fetch("owner/repo", PIN, "demo", tmp_path)

    assert folder is None
    assert problem == f"the fetch resolved {OTHER} instead of the pinned commit"


def test_a_lock_without_a_content_hash_is_refused(tmp_path, monkeypatch):
    _installed(tmp_path)
    _lock(tmp_path, content_hash=None)
    _fake_run(tmp_path, monkeypatch)

    folder, problem = skills_tool.fetch("owner/repo", PIN, "demo", tmp_path)

    assert (folder, problem) == (None, "the fetch recorded no content hash")


def test_a_skill_the_lock_does_not_list_is_refused(tmp_path, monkeypatch):
    _installed(tmp_path)
    (tmp_path / skills_tool.LOCK_FILE).write_text('{"version": 2, "skills": {}}', encoding="utf-8")
    _fake_run(tmp_path, monkeypatch)

    folder, problem = skills_tool.fetch("owner/repo", PIN, "demo", tmp_path)

    assert (folder, problem) == (None, "the fetched skill is not in skills-lock.json")


def test_an_unreadable_lock_is_refused_whole(tmp_path, monkeypatch):
    _installed(tmp_path)
    (tmp_path / skills_tool.LOCK_FILE).write_text("not a lock", encoding="utf-8")
    _fake_run(tmp_path, monkeypatch)

    folder, problem = skills_tool.fetch("owner/repo", PIN, "demo", tmp_path)

    assert folder is None
    assert problem == "the fetched skill is not in skills-lock.json"


def test_a_failing_cli_reports_its_own_last_line(tmp_path, monkeypatch):
    _fake_run(tmp_path, monkeypatch, code=1, stderr="fatal: repository not found\n")

    folder, problem = skills_tool.fetch("owner/repo", PIN, "demo", tmp_path)

    assert folder is None
    assert problem == "the skills CLI failed: fatal: repository not found"


def test_a_run_that_leaves_no_skill_says_so(tmp_path, monkeypatch):
    _lock(tmp_path)
    _fake_run(tmp_path, monkeypatch)

    folder, problem = skills_tool.fetch("owner/repo", PIN, "demo", tmp_path)

    assert (folder, problem) == (None, "the skills CLI ran but left no demo skill to take")


def test_a_machine_without_node_keeps_todays_path(tmp_path, monkeypatch):
    monkeypatch.setattr(skills_tool, "available", lambda which=None: False)

    folder, problem = skills_tool.fetch("owner/repo", PIN, "demo", tmp_path)

    assert (folder, problem) == (None, "node and npx are not on this machine")


def test_a_timeout_is_one_line_not_an_exception(tmp_path, monkeypatch):
    def boom(argv, cwd):
        raise subprocess.TimeoutExpired(argv, skills_tool.RUN_TIMEOUT_SECONDS)

    monkeypatch.setattr(skills_tool, "_run", boom)
    monkeypatch.setattr(skills_tool, "available", lambda which=None: True)

    folder, problem = skills_tool.fetch("owner/repo", PIN, "demo", tmp_path)

    assert folder is None
    assert problem == f"the skills CLI did not finish within {skills_tool.RUN_TIMEOUT_SECONDS}s"
