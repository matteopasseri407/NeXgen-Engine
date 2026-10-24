"""Whether a resource OpenClaw created may enter the library, and who must say yes first.

The three answers are the whole contract: `SAFE` is adopted without a question,
`REVIEW` waits for a person, `BLOCKED` is never written. A credential in plain
text always blocks, and no message ever repeats the value it found.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import i18n  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw.adopt_scan import UnmanagedServer, WorkshopSkill  # noqa: E402
from nexgen_core.openclaw.adopt_vetting import (  # noqa: E402
    BLOCKED,
    REVIEW,
    SAFE,
    skill_description,
    vet_server,
    vet_skill,
)

#: Long enough to match the engine's own high-entropy check, and invented.
A_SECRET = "0123456789abcdef" * 3


@pytest.fixture(autouse=True)
def _english():
    i18n.set_language("en")
    yield
    i18n.set_language(None)


def a_skill(tmp_path: Path, name: str = "release-notes", *, description: str = "writes release notes") -> WorkshopSkill:
    folder = tmp_path / name
    folder.mkdir()
    (folder / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n\nHow to write them.\n",
        encoding="utf-8",
    )
    return WorkshopSkill(name=name, agent_id="main", path=folder)


def a_server(name: str = "github", **spec) -> UnmanagedServer:
    return UnmanagedServer(name=name, spec=spec)


def a_link(source: Path, target: Path) -> bool:
    """Whether a link could be made: Windows refuses one without developer mode."""
    try:
        source.symlink_to(target, target_is_directory=target.is_dir())
    except (OSError, NotImplementedError):  # nexgen: allow reason="a machine that cannot make a symlink cannot test one; the other cases still run"
        return False
    return True


# --------------------------------------------------------------------- a skill


def test_a_plain_prose_skill_is_adopted_without_a_question(tmp_path):
    verdict = vet_skill(a_skill(tmp_path))

    assert verdict.status == SAFE
    assert verdict.adoptable
    assert verdict.reasons == ()


def test_a_skill_with_a_script_waits_for_a_person(tmp_path):
    skill = a_skill(tmp_path)
    (skill.path / "scripts").mkdir()
    (skill.path / "scripts" / "run.py").write_text("print('hi')\n", encoding="utf-8")

    verdict = vet_skill(skill)

    assert verdict.status == REVIEW
    assert not verdict.adoptable
    assert "scripts/run.py" in verdict.joined()


def test_a_shell_hook_inside_the_folder_waits_for_a_person(tmp_path):
    skill = a_skill(tmp_path)
    (skill.path / "hooks.json").write_text("{}", encoding="utf-8")

    assert vet_skill(skill).status == REVIEW


def test_a_file_of_unknown_kind_waits_for_a_person(tmp_path):
    skill = a_skill(tmp_path)
    (skill.path / "notes.xyz").write_text("unknown shape\n", encoding="utf-8")

    assert vet_skill(skill).status == REVIEW


def test_a_prose_reference_is_inert_and_harmless(tmp_path):
    skill = a_skill(tmp_path)
    (skill.path / "references").mkdir()
    (skill.path / "references" / "checklist.md").write_text("- one\n", encoding="utf-8")
    (skill.path / "assets").mkdir()
    (skill.path / "assets" / "picture.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)

    assert vet_skill(skill).status == SAFE


def test_a_credential_in_the_text_blocks_and_is_never_repeated(tmp_path):
    skill = a_skill(tmp_path)
    (skill.path / "examples.md").write_text(f"token: {A_SECRET}\n", encoding="utf-8")

    verdict = vet_skill(skill)

    assert verdict.status == BLOCKED
    assert "credential" in verdict.joined()
    assert A_SECRET not in verdict.joined()


def test_a_credential_in_the_body_blocks_the_skill(tmp_path):
    skill = a_skill(tmp_path)
    (skill.path / "SKILL.md").write_text(
        f"---\nname: release-notes\ndescription: x\n---\n\nkey {A_SECRET}\n", encoding="utf-8",
    )

    assert vet_skill(skill).status == BLOCKED


def test_a_link_points_at_bytes_nobody_adopted(tmp_path):
    skill = a_skill(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text("elsewhere\n", encoding="utf-8")
    if not a_link(skill.path / "borrowed.md", outside):
        pytest.skip("this machine cannot create a symlink, so a link cannot be tested")

    verdict = vet_skill(skill)

    assert verdict.status == BLOCKED
    assert "link" in verdict.joined()


def test_a_name_that_cannot_become_a_folder_is_refused(tmp_path):
    skill = a_skill(tmp_path, name="Release Notes")

    assert vet_skill(skill).status == BLOCKED


def test_a_folder_without_a_skill_md_is_not_a_skill(tmp_path):
    folder = tmp_path / "empty"
    folder.mkdir()

    verdict = vet_skill(WorkshopSkill(name="empty", agent_id="main", path=folder))

    assert verdict.status == BLOCKED
    assert "SKILL.md" in verdict.joined()


def test_a_skill_without_frontmatter_is_refused(tmp_path):
    folder = tmp_path / "loose"
    folder.mkdir()
    (folder / "SKILL.md").write_text("# just a title\n", encoding="utf-8")

    assert vet_skill(WorkshopSkill(name="loose", agent_id="main", path=folder)).status == BLOCKED


def test_a_missing_description_is_refused(tmp_path):
    folder = tmp_path / "nodesc"
    folder.mkdir()
    (folder / "SKILL.md").write_text("---\nname: nodesc\n---\n\nbody\n", encoding="utf-8")

    verdict = vet_skill(WorkshopSkill(name="nodesc", agent_id="main", path=folder))

    assert verdict.status == BLOCKED
    assert "description" in verdict.joined()


def test_a_name_that_disagrees_with_its_folder_waits_for_a_person(tmp_path):
    folder = tmp_path / "folder-name"
    folder.mkdir()
    (folder / "SKILL.md").write_text(
        "---\nname: another-name\ndescription: x\n---\n\nbody\n", encoding="utf-8",
    )

    verdict = vet_skill(WorkshopSkill(name="folder-name", agent_id="main", path=folder))

    assert verdict.status == REVIEW
    assert "another-name" in verdict.joined()


def test_a_skill_md_over_the_ceiling_is_refused(tmp_path):
    skill = a_skill(tmp_path)
    body = "---\nname: release-notes\ndescription: x\n---\n\n" + ("paragraph\n" * 5000)
    (skill.path / "SKILL.md").write_text(body, encoding="utf-8")

    assert vet_skill(skill).status == BLOCKED


def test_a_folder_holding_far_too_many_files_is_refused(tmp_path):
    skill = a_skill(tmp_path)
    (skill.path / "references").mkdir()
    for index in range(205):
        (skill.path / "references" / f"note-{index}.md").write_text("x\n", encoding="utf-8")

    assert vet_skill(skill).status == BLOCKED


def test_the_description_is_read_from_the_frontmatter(tmp_path):
    skill = a_skill(tmp_path, description="  writes   release notes  ")

    assert skill_description(skill.path) == "writes release notes"


def test_a_skill_that_vanished_is_a_verdict_not_a_crash(tmp_path):
    missing = WorkshopSkill(name="gone", agent_id="main", path=tmp_path / "gone")

    assert vet_skill(missing).status == BLOCKED


# -------------------------------------------------------------------- a server


def test_a_plain_stdio_server_is_adopted():
    assert vet_server(a_server(command="npx", args=["-y", "some-mcp"])) .status == SAFE


def test_an_http_server_is_adopted():
    assert vet_server(a_server(url="https://example.test/mcp")).status == SAFE


def test_an_environment_reference_is_not_a_credential():
    server = a_server(command="npx", env={"OPENAI_API_KEY": "${OPENAI_API_KEY}"})

    assert vet_server(server).status == SAFE


def test_a_literal_credential_in_env_blocks_and_is_never_repeated():
    server = a_server(command="npx", env={"GITHUB_TOKEN": A_SECRET})

    verdict = vet_server(server)

    assert verdict.status == BLOCKED
    assert "GITHUB_TOKEN" in verdict.joined()
    assert A_SECRET not in verdict.joined()
    assert "nexgen-secrets set" in verdict.joined()


def test_a_secret_named_variable_with_a_literal_value_blocks_even_when_short():
    server = a_server(command="npx", env={"API_KEY": "hunter2"})

    assert vet_server(server).status == BLOCKED


def test_a_credential_in_the_url_blocks():
    verdict = vet_server(a_server(url=f"https://user:{A_SECRET}@example.test/mcp"))

    assert verdict.status == BLOCKED
    assert A_SECRET not in verdict.joined()


def test_a_credential_in_an_argument_or_a_header_blocks():
    assert vet_server(a_server(command="npx", args=["--token", A_SECRET])).status == BLOCKED
    assert vet_server(a_server(url="https://example.test", headers={"Authorization": A_SECRET})).status == BLOCKED


def test_a_name_that_is_not_a_manifest_key_is_refused():
    assert vet_server(a_server(name="../escape", command="npx")).status == BLOCKED


def test_an_entry_with_neither_a_url_nor_a_command_is_refused():
    verdict = vet_server(a_server())

    assert verdict.status == BLOCKED
    assert "neither" in verdict.joined()


def test_a_url_that_is_not_http_is_refused():
    assert vet_server(a_server(url="ftp://example.test/mcp")).status == BLOCKED


def test_a_command_carrying_a_newline_is_refused():
    assert vet_server(a_server(command="npx\nrm -rf /")).status == BLOCKED


def test_a_mapping_spec_is_not_required_to_be_a_dict():
    """The scan hands over whatever the file held; a stranger shape is a verdict, not an exception."""
    server = UnmanagedServer(name="odd", spec={})

    assert vet_server(server).status == BLOCKED
