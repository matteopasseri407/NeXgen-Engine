"""Source-level invariants for the vendored starter command skills.

The engine ships a set of cross-CLI command skills. They must stay portable across every runtime that consumes the
agentskills.io shape (Claude Code, Codex, OpenCode, Antigravity), which the
dialect verification of 2026-07-17 reduced to: lowercase-hyphen name equal to
the folder name, a description in the frontmatter, and an argument-free body
(placeholder syntaxes like $ARGUMENTS diverge per CLI and are silently left
verbatim by skill-based runtimes).
"""
from __future__ import annotations

import re

import yaml

from conftest import REAL_VAULT

STARTERS = (
    "vault-doctor", "vault-close", "vault-save",
    "vault-council", "vault-groom", "nexgen-update", "vault-update",
    "vault-map",
)
SKILLS_ROOT = REAL_VAULT / "03-INFRA" / "agent-universal-layer" / "skills"
EXAMPLE_MANIFEST = SKILLS_ROOT / "skills.manifest.yaml.example"

# agentskills.io: lowercase alphanumerics + single hyphens, 1-64 chars.
PORTABLE_NAME_RE = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*\Z")
# Interpolation tokens that only SOME dialects expand; a portable body may
# not depend on any of them.
NON_PORTABLE_PLACEHOLDER_RE = re.compile(r"\$ARGUMENTS|\$\d|\{\{args\}\}")
# Names that would shadow a CLI built-in (verified 2026-07-17: Claude Code
# bundles /doctor and lets same-named user skills override it; OpenCode has
# /init and /review; Codex/Antigravity reserve their TUI commands).
RESERVED_BUILTIN_NAMES = {
    "doctor", "init", "review", "help", "clear", "compact", "skills",
    "agents", "model", "plan", "resume", "share", "undo", "redo",
}


def _frontmatter_and_body(name: str) -> tuple[dict, str]:
    text = (SKILLS_ROOT / name / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{name}: SKILL.md must open with YAML frontmatter"
    front, _, body = text[4:].partition("\n---\n")
    return yaml.safe_load(front), body


def test_every_starter_ships_a_valid_portable_skill():
    for name in STARTERS:
        skill_md = SKILLS_ROOT / name / "SKILL.md"
        assert skill_md.is_file(), f"missing vendored starter skill {skill_md}"
        front, body = _frontmatter_and_body(name)
        assert front.get("name") == name, (
            f"{name}: frontmatter name must equal the folder name "
            "(Antigravity and the agentskills spec require the match)"
        )
        assert PORTABLE_NAME_RE.fullmatch(name) and len(name) <= 64
        description = front.get("description")
        assert isinstance(description, str) and 1 <= len(description) <= 1024, (
            f"{name}: description is required (it drives listing and implicit "
            "invocation on every runtime) and capped at 1024 chars by the spec"
        )
        assert body.strip(), f"{name}: SKILL.md body must not be empty"


def test_starter_names_avoid_cli_builtin_collisions():
    for name in STARTERS:
        assert name not in RESERVED_BUILTIN_NAMES
        # The invariant is that a starter is NAMESPACED, so it can never
        # shadow a CLI built-in or bundled skill. It used to say `vault-`
        # specifically, which forced the engine-update command to be called
        # `vault-update` — a name that described the wrong thing.
        assert name.startswith(("vault-", "nexgen-")), (
            f"{name}: starters must carry a product prefix so they can never "
            "shadow a CLI built-in or bundled skill"
        )


def test_starter_bodies_are_free_of_dialect_placeholders():
    for name in STARTERS:
        _, body = _frontmatter_and_body(name)
        hit = NON_PORTABLE_PLACEHOLDER_RE.search(body)
        assert hit is None, (
            f"{name}: body uses non-portable placeholder {hit.group(0)!r}; "
            "write it model-mediated (the text after the command is the request)"
        )


def test_example_manifest_registers_starters_as_core_commands():
    data = yaml.safe_load(EXAMPLE_MANIFEST.read_text(encoding="utf-8"))
    skills = (data or {}).get("skills") or {}
    for name in STARTERS:
        assert name in skills, f"{name}: missing from skills.manifest.yaml.example"
        spec = skills[name]
        assert spec.get("origin") == "vault"
        assert spec.get("exposure") == "core", (
            f"{name}: command skills need exposure core to reach the shared "
            "~/.agents/skills root (Codex + OpenCode discovery)"
        )
        assert {"claude", "antigravity", "opencode"} <= set(spec.get("targets", [])), (
            f"{name}: command skills target every runtime that needs a native "
            "view or a discoverability check"
        )


# --- the engine-update command and its previous name -------------------------


def test_the_engine_update_command_is_not_named_after_the_vault():
    """It upgrades the ENGINE. Named `vault-update`, it read as the opposite,
    and a user looking for it typed `/nexgen ...` and concluded no such command
    existed. The name is the whole feature here."""
    front, _ = _frontmatter_and_body("nexgen-update")
    assert front["name"] == "nexgen-update"
    description = front["description"].lower()
    assert "engine" in description, "the description must say what it updates"


def test_the_previous_name_still_resolves_and_says_where_it_went():
    """Renaming a shipped command silently breaks every manifest that already
    lists it, so the old entry stays as a stub."""
    front, body = _frontmatter_and_body("vault-update")
    assert front["name"] == "vault-update"
    assert "deprecated" in front["description"].lower()
    # Mentioning the new name in prose is not enough: the stub has to send the
    # agent to it, or a user invoking the old command gets an explanation and
    # no upgrade.
    assert re.search(r"(?:load|follow)[^.\n]*`nexgen-update`", body, re.I), (
        "the stub must instruct loading the nexgen-update skill, not just mention it"
    )


def test_the_upgrade_runbook_has_one_executable_source_of_truth():
    """The skills route intent; the executable owns the guarded transaction.

    Keeping merge logic in a skill as well as in the cross-platform command
    would let the chat and terminal entry points drift apart.
    """
    _, canonical = _frontmatter_and_body("nexgen-update")
    _, stub = _frontmatter_and_body("vault-update")
    implementation = (
        REAL_VAULT / "03-INFRA" / "scripts" / "nexgen_update.py"
    ).read_text(encoding="utf-8")
    assert "nexgen-update --check" in canonical
    assert "nexgen-update --yes" in canonical
    assert '"--ff-only" if split_topology else "--no-edit"' in implementation
    # Matched by shape, not by an exact call string: the point is that the
    # executable owns the merge invocation, which stays true when the call
    # grows arguments (it gained check=False so a conflicted merge can be
    # rolled back instead of raising past the abort).
    assert re.search(
        r'_git\(\s*engine_repo,\s*"merge",\s*merge_mode,\s*target\b', implementation
    ), "the executable must own the merge invocation"
    assert "git merge" not in canonical, "the skill duplicated executable release logic"
    assert "git merge" not in stub, "the stub duplicated the runbook instead of deferring"
    assert len(stub) < len(canonical), "the stub should be a pointer, not a copy"


def test_both_names_are_registered_in_the_example_manifest():
    """An unregistered skill never materializes, so a rename that forgets the
    manifest ships a command nobody can invoke."""
    declared = yaml.safe_load(EXAMPLE_MANIFEST.read_text(encoding="utf-8"))["skills"]
    assert "nexgen-update" in declared
    assert "vault-update" in declared


# --- vault-save / vault-close must not hard-require a server ------------------


def test_vault_save_and_vault_close_document_the_local_only_branch():
    """A 2026-07-30 review found these two of the seven starters required the
    `vault-library` MCP with no alternative, and `vault-close` even instructed
    the agent to STOP if the vault was unreachable -- but a Local-Only install
    (no remote, no MCP) has no server to be unreachable: the vault is just a
    writable local folder, and `03-INFRA/vault-write-architecture.md` already
    carries the correct branch (direct-edit + plain `git`). Pin that both
    starters inherited it, so it cannot silently drop out again."""
    for name in ("vault-save", "vault-close"):
        _, body = _frontmatter_and_body(name)
        assert "Local-Only" in body, (
            f"{name}: lost the Local-Only (no remote, no MCP) branch for the "
            "vault write mechanism"
        )
        assert "vault-write-architecture.md" in body, (
            f"{name}: Local-Only branch no longer points at the canonical "
            "write-architecture doc it was copied from"
        )


def test_vault_close_does_not_stop_unconditionally_when_vault_is_unreachable():
    """The old wording told the agent to stop for ANY 'Vault unreachable',
    which is a category error in Local-Only mode (no server exists to be
    unreachable there). The halt must be scoped to Cloud-Server mode."""
    _, body = _frontmatter_and_body("vault-close")
    assert "Cloud-Server mode" in body, (
        "vault-close: the 'stop if unreachable' instruction must be scoped to "
        "Cloud-Server mode, not phrased as an unconditional Vault-unreachable rule"
    )
