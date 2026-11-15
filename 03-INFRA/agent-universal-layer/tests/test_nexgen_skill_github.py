"""The github skill lane: whichever backend fetched, the same verified tree arrives.

Two backends, one contract. The tool's own behavior is faked here; what is
asserted is this engine's: the fetched folder is put back at the declared
relative path, the boundary check runs either way, and a tool that cannot answer
costs nothing because the clone follows.
"""
from __future__ import annotations

import subprocess
from pathlib import Path


from nexgen_core import skill_github, skills_tool
from nexgen_core.skill_sources import SkillEntry

PIN = "96608e078b2d5ea607c5" "c3b8b5a54d1c1fba6846"


def _entry(**kw) -> SkillEntry:
    base = {"name": "demo", "origin": "github", "repo": "owner/repo", "commit": PIN,
            "path": "skills/demo"}
    base.update(kw)
    return SkillEntry(**base)


def test_the_tool_puts_the_skill_back_at_its_declared_path(tmp_path, monkeypatch):
    """A clone would have carried `skills/demo` inside the repository; the tool
    writes the skill folder alone, so it is placed at that same relative path."""
    incoming = tmp_path / "incoming"
    incoming.mkdir()

    def fake_fetch(repo, commit, skill, workdir):
        folder = Path(workdir) / ".agents" / "skills" / skill
        folder.mkdir(parents=True)
        (folder / "SKILL.md").write_text("---\nname: demo\n---\n", encoding="utf-8")
        return folder, None

    monkeypatch.setattr(skills_tool, "available", lambda which=None: True)
    monkeypatch.setattr(skills_tool, "fetch", fake_fetch)

    problem = skill_github._tool_checkout(_entry(), incoming, tmp_path / "home")

    assert problem is None
    assert (incoming / "skills" / "demo" / "SKILL.md").is_file()


def test_a_tool_that_cannot_answer_says_so_and_nothing_else(tmp_path, monkeypatch):
    monkeypatch.setattr(skills_tool, "available", lambda which=None: False)

    problem = skill_github._tool_checkout(_entry(), tmp_path / "incoming", tmp_path / "home")

    assert problem == "node and npx are not on this machine"
    assert not (tmp_path / "incoming").exists()


def test_a_repository_that_is_not_the_owner_name_shorthand_keeps_the_clone(tmp_path, monkeypatch):
    """The CLI answers `owner/repo@commit`; a full URL or a local path is not its shape."""
    monkeypatch.setattr(skills_tool, "available", lambda which=None: True)

    problem = skill_github._tool_checkout(_entry(repo=str(tmp_path / "local")), tmp_path / "in", tmp_path / "home")

    assert problem == "the repository is not written as owner/name"


def test_the_clone_follows_when_the_tool_failed(tmp_path, monkeypatch):
    """The fallback is the path every machine without Node takes: same result."""
    repo = tmp_path / "source"
    repo.mkdir()
    run = ["git", "-C", str(repo)]
    for argv in (["init", "--initial-branch=main"], ["add", "."]):
        subprocess.run(run + argv, check=True, capture_output=True, text=True, encoding="utf-8")
    (repo / "skills" / "demo").mkdir(parents=True)
    (repo / "skills" / "demo" / "SKILL.md").write_text("---\nname: demo\n---\n", encoding="utf-8")
    subprocess.run(run + ["add", "."], check=True, capture_output=True, text=True, encoding="utf-8")
    subprocess.run(run + ["-c", "user.name=t", "-c", "user.email=t@localhost", "commit", "-m", "one"],
                   check=True, capture_output=True, text=True, encoding="utf-8")
    pin = subprocess.run(run + ["rev-parse", "HEAD"], check=True, capture_output=True,
                         text=True, encoding="utf-8").stdout.strip()

    monkeypatch.setattr(skills_tool, "available", lambda which=None: True)
    monkeypatch.setattr(skills_tool, "fetch", lambda *a, **k: (None, "the skills CLI failed: boom"))
    cache = tmp_path / "cache"

    ok, note = skill_github.ensure_github_checkout(cache, _entry(repo=str(repo), commit=pin), tmp_path / "home")

    assert (ok, note) == (True, None)
    assert (cache / "skills" / "demo" / "SKILL.md").is_file()
    assert (cache / ".git").is_dir(), "the clone backend leaves a checkout, as it always did"


def test_a_pin_that_is_not_a_full_commit_never_reaches_either_backend(tmp_path, monkeypatch):
    def never(*_args, **_kwargs):
        raise AssertionError("a pin that is not a commit must not be fetched")

    monkeypatch.setattr(skills_tool, "fetch", never)
    ok, note = skill_github.ensure_github_checkout(
        tmp_path / "cache", _entry(commit="b48c832f"), tmp_path / "home")

    assert ok is False
    assert note is not None and note.endswith("a full commit pin is required")
