"""Tests for the universal on-demand `agent-skill` command."""
from __future__ import annotations

import shutil
import subprocess
import sys


def _populate_library(sandbox):
    sandbox.skill_library.mkdir(parents=True, exist_ok=True)
    for name in ("fake-skill-a", "fake-skill-excluded"):
        shutil.copytree(sandbox.skills_dir / name, sandbox.skill_library / name)


def _run(sandbox, *args):
    return subprocess.run(
        [sys.executable, str(sandbox.scripts_dir / "agent-skill.py"), *args],
        env=sandbox.env(),
        capture_output=True,
        text=True,
        timeout=20,
    )


def test_list_find_and_show_load_only_the_requested_skill(sandbox):
    _populate_library(sandbox)

    listed = _run(sandbox, "list")
    assert listed.returncode == 0
    assert "fake-skill-a" in listed.stdout
    assert "fake-skill-excluded" in listed.stdout

    found = _run(sandbox, "find", "sintetica")
    assert found.returncode == 0
    assert "fake-skill-a" in found.stdout

    shown = _run(sandbox, "show", "fake-skill-a")
    assert shown.returncode == 0
    assert "Skill sintetica per i test B1" in shown.stdout
    assert "fake-skill-excluded" not in shown.stdout


def test_list_and_show_point_at_the_missing_manifest_on_a_fresh_install(sandbox):
    """A fresh install has no skills.manifest.yaml on disk at all -- only
    the .example ships. "Run agent-sync guard first" was a proven no-op
    there: skills-sync.py just warns and proceeds with an empty skill set
    when the manifest is missing, it never creates it, so that advice sent
    the user in a loop. The library is empty (never populated) and the
    fixture's seeded manifest is removed here to reproduce that exact
    state; agent-skill must name the real fix instead."""
    (sandbox.skills_dir / "skills.manifest.yaml").unlink()

    listed = _run(sandbox, "list")
    assert listed.returncode == 1
    assert "skills.manifest.yaml.example" in listed.stderr
    assert "Run agent-sync guard first." not in listed.stderr

    shown = _run(sandbox, "show", "whatever")
    assert shown.returncode == 1
    assert "skills.manifest.yaml.example" in shown.stderr
    assert "use agent-skill find" not in shown.stderr


def test_list_and_show_keep_the_guard_hint_when_manifest_exists_but_unsynced(sandbox):
    """Same empty-library symptom, but the manifest IS present on disk
    (fixture default) -- here "run agent-sync guard" is the correct next
    step (a sync just hasn't happened yet), so the original message must
    survive unchanged."""
    assert (sandbox.skills_dir / "skills.manifest.yaml").is_file()

    listed = _run(sandbox, "list")
    assert listed.returncode == 1
    assert "Run agent-sync guard first." in listed.stderr
    assert "skills.manifest.yaml.example" not in listed.stderr

    shown = _run(sandbox, "show", "whatever")
    assert shown.returncode == 1
    assert "Run agent-sync guard, or use agent-skill find to choose one." in shown.stderr
    assert "skills.manifest.yaml.example" not in shown.stderr


def test_show_rejects_path_traversal_and_unknown_skills(sandbox):
    _populate_library(sandbox)

    traversal = _run(sandbox, "show", "../outside")
    assert traversal.returncode == 2
    assert "Invalid skill name" in traversal.stderr

    missing = _run(sandbox, "show", "does-not-exist")
    assert missing.returncode == 1
    assert "not installed" in missing.stderr


def test_list_and_find_use_multiline_frontmatter_description(sandbox):
    skill = sandbox.skill_library / "multiline-skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\n"
        "name: multiline-skill\n"
        "description: |\n"
        "  Finds a specific capability without loading every playbook.\n"
        "  Use for a focused manual lookup.\n"
        "---\n",
        encoding="utf-8",
    )

    listed = _run(sandbox, "list")
    assert "Finds a specific capability without loading every playbook." in listed.stdout

    found = _run(sandbox, "find", "focused", "lookup")
    assert found.returncode == 0
    assert "multiline-skill" in found.stdout


def test_show_and_path_accept_names_skills_sync_can_actually_install(sandbox):
    """G3-nomiskill: skills-sync.py accepts any manifest name matching
    config_schema.ENTRY_NAME_RE (letters, digits, '.', '_', '-'), so a name
    with an underscore or a dot can legitimately land in the library and the
    generated INDEX.md. Before the fix, agent-skill's own NAME pattern was
    narrower (lowercase letters/digits/hyphens only) and rejected such a
    name as invalid, making an installed skill unreachable through
    `agent-skill show`/`path` even though `list`/`find` already showed it."""
    skill = sandbox.skill_library / "some_skill.v1"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\ndescription: Underscore and dot in the name.\n---\nCorpo della skill.\n",
        encoding="utf-8",
    )

    listed = _run(sandbox, "list")
    assert listed.returncode == 0
    assert "some_skill.v1" in listed.stdout

    shown = _run(sandbox, "show", "some_skill.v1")
    assert shown.returncode == 0
    assert "Corpo della skill." in shown.stdout

    path = _run(sandbox, "path", "some_skill.v1")
    assert path.returncode == 0
    assert "some_skill.v1" in path.stdout


def test_show_forces_utf8_even_when_windows_code_page_is_not_utf8(sandbox):
    skill = sandbox.skill_library / "unicode-skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("Perché: sorgente → destinazione.\n", encoding="utf-8")
    env = sandbox.env()
    env["PYTHONIOENCODING"] = "cp1252"

    shown = subprocess.run(
        [sys.executable, str(sandbox.scripts_dir / "agent-skill.py"), "show", "unicode-skill"],
        env=env,
        capture_output=True,
        timeout=20,
    )

    assert shown.returncode == 0, shown.stderr.decode("utf-8", errors="replace")
    assert shown.stdout.decode("utf-8").splitlines() == ["Perché: sorgente → destinazione."]
