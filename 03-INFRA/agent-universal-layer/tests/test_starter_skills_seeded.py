"""The starter commands README promises must actually exist after an install.

`README.md` says seven starter commands "ship with the engine", every one of
them is vendored under `agent-universal-layer/skills/` and covered by
`test_starter_command_skills.py` -- and until v0.96.1 not one of them ever
materialized on a fresh machine. Nothing created `skills.manifest.yaml` from
the shipped `.example`, `skills-sync.py` printed "manifest not found ...
skipping" to stderr and exited 0, `INIT.md` instructed the installing agent
that there were no base skills and to skip the step, and the doctor's only
line was a WARN worded as normal for a fresh install. Five cold installs went
out without `/nexgen-doctor` or `/nexgen-update` -- the latter being the one
command a non-technical user has for upgrading the engine at all.

The seeding is deliberately the narrowest thing that closes that: create the
file when it is absent, never touch it otherwise.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

import yaml

from conftest import REAL_UL, load_agent_sync_module

import pytest


STARTERS = (
    "nexgen-doctor", "nexgen-council", "nexgen-update",
    "vault-close", "vault-save", "vault-groom", "vault-map",
)
STUBS = ("vault-doctor", "vault-council", "vault-update")
EXAMPLE = REAL_UL / "skills" / "skills.manifest.yaml.example"


@pytest.fixture
def fresh_install(sandbox):
    """A sandbox as a brand-new machine sees it: the engine's shipped example
    and skill bodies are present, the user's own manifest is not."""
    (sandbox.skills_dir / "skills.manifest.yaml").unlink()
    shutil.copy2(EXAMPLE, sandbox.skills_dir / "skills.manifest.yaml.example")
    for name in STARTERS + STUBS:
        body = sandbox.skills_dir / name
        body.mkdir(parents=True, exist_ok=True)
        (body / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: starter\n---\n\nbody\n", encoding="utf-8"
        )
    return sandbox


def _seed(sandbox):
    mod = load_agent_sync_module(sandbox)
    mod.seed_starter_skills(mod.Env())
    return sandbox.skills_dir / "skills.manifest.yaml"


def test_a_fresh_install_gets_the_starter_commands(fresh_install):
    manifest = _seed(fresh_install)

    assert manifest.is_file(), "a fresh install still ends up with no skills manifest at all"
    declared = yaml.safe_load(manifest.read_text(encoding="utf-8"))["skills"]
    for name in STARTERS:
        assert name in declared, f"{name}: promised by README, absent after a fresh install"
        assert declared[name]["exposure"] == "core"


def test_an_existing_manifest_is_never_overwritten(fresh_install):
    """The whole opt-out story rests on this: a user who does not want the
    starters empties the file, and it has to stay empty forever."""
    manifest = fresh_install.skills_dir / "skills.manifest.yaml"
    manifest.write_text("schema_version: 1\nskills: {}\n", encoding="utf-8")
    before = manifest.read_text(encoding="utf-8")

    _seed(fresh_install)

    assert manifest.read_text(encoding="utf-8") == before


def test_seeding_is_idempotent(fresh_install):
    first = _seed(fresh_install).read_text(encoding="utf-8")
    second = _seed(fresh_install).read_text(encoding="utf-8")
    assert first == second


def test_nothing_is_seeded_when_the_bodies_resolve_nowhere(fresh_install):
    """Seeding a manifest whose commands resolve to nothing hands the user
    entries that silently do not exist. Better to leave the file absent and let
    the doctor say so.

    Note this used to be about a split engine/data topology, which `origin:
    engine` now handles by design: bodies living in the engine clone is the
    correct place for them, not a reason to skip. What must still stop the
    seeding is an engine whose bodies are missing outright."""
    for name in STARTERS + STUBS:
        shutil.rmtree(fresh_install.skills_dir / name)

    manifest = _seed(fresh_install)

    assert not manifest.exists()


def test_nothing_is_seeded_without_the_shipped_example(fresh_install):
    (fresh_install.skills_dir / "skills.manifest.yaml.example").unlink()

    assert not _seed(fresh_install).exists()


def test_the_seeded_manifest_passes_the_engines_own_validator(fresh_install):
    """Seeding a file that the very next preflight would reject would turn a
    fresh install from silently command-less into loudly broken. `--validate`
    is exactly what preflight shells out to before any apply continues."""
    _seed(fresh_install)

    # The sandbox IS the engine here, so point the engine root at it: with
    # `origin: engine` the validator resolves the starter bodies there, not
    # under the user's data root.
    env = {**os.environ, "AGENT_ENGINE_ROOT": str(fresh_install.ul.parent)}
    result = subprocess.run(
        [sys.executable, str(fresh_install.scripts_dir / "skills-sync.py"), "--validate"],
        capture_output=True, text=True, env=env,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_the_skills_phase_seeds_before_the_synchronizer_runs(fresh_install):
    """Ordering is the feature: `vault_skills` (phase 3) has to have written
    the manifest before `skills_index` (phase 3.5) shells out to skills-sync,
    or the commands land one full run late."""
    mod = load_agent_sync_module(fresh_install)
    mod.vault_skills(mod.Env())

    assert (fresh_install.skills_dir / "skills.manifest.yaml").is_file()
