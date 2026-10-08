"""`nexgen skills adopt`: the engine's own skills follow the engine, not a copy frozen in the Vault.

What it must prove: an identical copy is handed back with nothing else in the manifest touched, a copy that differs
is never touched without being named, nothing is deleted (the old copy is set aside), a manifest that stops
validating is put back, and the doctor says so while a frozen copy exists.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

INFRA = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(INFRA / "scripts"))

from nexgen_core import i18n, skill_adopt  # noqa: E402
from nexgen_core.checks.skill_checks import check_skill_engine_copies  # noqa: E402
from nexgen_core.report import Severity  # noqa: E402
from nexgen_core.skills import SkillMaterializer  # noqa: E402


@pytest.fixture(autouse=True)
def _english():
    i18n.set_language("en")
    yield
    i18n.set_language(None)


MANIFEST = """# comment that must survive
schema_version: 1
skills:
  # engine skills, once copied into the Vault
  same-one:
    origin: vault
    exposure: core
    targets: [claude]
  drifted:
    origin: vault   # my note
    exposure: core
    targets: [claude]
  same-two:
    origin: vault
    exposure: core
    targets: [claude]
  mine:
    origin: vault
    exposure: manual
    targets: [claude]
  borrowed:
    origin: github
    repo: o/r
    commit: %s
    exposure: manual
    targets: [claude]
""" % ("ab" * 20)


@pytest.fixture
def machine(tmp_path, monkeypatch):
    vault, engine, home, state = tmp_path / "vault", tmp_path / "engine", tmp_path / "home", tmp_path / "state"
    layer = vault / "03-INFRA" / "agent-universal-layer" / "skills"
    shipped = engine / "agent-universal-layer" / "skills"
    layer.mkdir(parents=True)
    shipped.mkdir(parents=True)
    home.mkdir()
    monkeypatch.setenv("NEXGEN_HOME", str(home))
    monkeypatch.setenv("AGENT_STATE_DIR", str(state))
    monkeypatch.setenv("AGENT_VAULT_DATA", str(vault))
    monkeypatch.setenv("AGENT_ENGINE_ROOT", str(engine))

    def write(root: Path, name: str, body: str) -> None:
        (root / name).mkdir(parents=True)
        (root / name / "SKILL.md").write_text(f"---\nname: {name}\ndescription: d\n---\n{body}\n", encoding="utf-8")

    for name in ("same-one", "same-two"):
        write(shipped, name, "engine text")
        write(layer, name, "engine text")
    write(shipped, "drifted", "engine's newer text\nwith an extra line")
    write(layer, "drifted", "old text")
    write(layer, "mine", "only mine")
    (layer / "skills.manifest.yaml").write_text(MANIFEST, encoding="utf-8")
    return {"vault": vault, "engine": engine, "home": home, "state": state, "layer": layer, "shipped": shipped}


def _adopt(m, names=None, **kwargs):
    return skill_adopt.adopt(names, home=m["home"], vault_data=m["vault"], engine_root=m["engine"], state_dir=m["state"], **kwargs)


def _manifest(m) -> str:
    return (m["layer"] / "skills.manifest.yaml").read_text(encoding="utf-8")


def test_only_skills_the_engine_ships_are_candidates_and_each_says_whether_it_still_matches(machine):
    found = {c.name: c.status for c in skill_adopt.candidates(machine["vault"], machine["engine"])}
    assert found == {"same-one": "same", "same-two": "same", "drifted": "differs"}


def test_identical_copies_are_handed_back_and_nothing_else_in_the_manifest_changes(machine):
    before = _manifest(machine)
    outcomes = _adopt(machine)
    assert {o.name for o in outcomes if o.adopted} == {"same-one", "same-two"}
    after = _manifest(machine)
    changed = [(a, b) for a, b in zip(before.splitlines(), after.splitlines()) if a != b]
    assert len(changed) == 2 and all(a.strip() == "origin: vault" and b.strip() == "origin: engine" for a, b in changed)
    assert "# comment that must survive" in after and "# my note" in after
    entries = yaml.safe_load(after)["skills"]
    assert entries["same-one"]["origin"] == "engine" and entries["drifted"]["origin"] == "vault"


def test_the_old_copy_is_set_aside_not_deleted_and_the_library_links_the_engine(machine):
    _adopt(machine)
    assert not (machine["layer"] / "same-one").exists()
    stashed = list((machine["state"] / "skill-copies").glob("*/same-one/SKILL.md"))
    assert len(stashed) == 1 and "engine text" in stashed[0].read_text(encoding="utf-8")
    library = SkillMaterializer(vault_data=machine["vault"], engine_root=machine["engine"], home=machine["home"]).library_dir
    assert (library / "same-one").resolve() == (machine["shipped"] / "same-one").resolve()
    assert (library / "same-one" / "SKILL.md").is_file()
    assert list(machine["layer"].glob("skills.manifest.yaml.pre-adopt-*.bak"))


def test_a_copy_that_differs_is_left_alone_unless_it_is_named_and_forced(machine):
    before = _manifest(machine)
    outcomes = {o.name: o for o in _adopt(machine, ["drifted"])}
    assert not outcomes["drifted"].adopted and "differs in" in outcomes["drifted"].note
    assert _manifest(machine) == before and (machine["layer"] / "drifted" / "SKILL.md").is_file()

    forced = {o.name: o for o in _adopt(machine, ["drifted"], include_changed=True)}
    assert forced["drifted"].adopted
    assert yaml.safe_load(_manifest(machine))["skills"]["drifted"]["origin"] == "engine"
    kept = list((machine["state"] / "skill-copies").glob("*/drifted/SKILL.md"))
    assert len(kept) == 1 and "old text" in kept[0].read_text(encoding="utf-8")


def test_the_difference_is_described_in_lines(machine):
    candidate = next(c for c in skill_adopt.candidates(machine["vault"], machine["engine"]) if c.name == "drifted")
    note = skill_adopt.describe_difference(candidate)
    assert "SKILL.md" in note and "yours lacks" in note


def test_a_dry_run_changes_nothing(machine):
    before = _manifest(machine)
    outcomes = _adopt(machine, dry_run=True)
    assert outcomes and not any(o.adopted for o in outcomes)
    assert _manifest(machine) == before and (machine["layer"] / "same-one").is_dir()
    assert not (machine["state"] / "skill-copies").exists()


def test_adopting_twice_finds_nothing_more_to_do(machine):
    _adopt(machine)
    assert [c.name for c in skill_adopt.candidates(machine["vault"], machine["engine"])] == ["drifted"]
    assert _adopt(machine) == []


def test_a_name_that_is_not_a_candidate_is_reported_not_ignored(machine):
    outcomes = _adopt(machine, ["mine", "nonexistent"])
    assert [o.adopted for o in outcomes] == [False, False]
    assert "mine" in {o.name for o in outcomes}


def test_a_manifest_that_stops_validating_is_put_back_and_no_copy_moves(machine, monkeypatch):
    before = _manifest(machine)
    monkeypatch.setattr(SkillMaterializer, "validate_manifest", lambda self: ["something is wrong"])
    outcomes = _adopt(machine)
    assert any(o.name == "*" and "put back" in o.note for o in outcomes)
    assert _manifest(machine) == before
    assert (machine["layer"] / "same-one").is_dir() and not (machine["state"] / "skill-copies").exists()


def test_an_entry_without_an_explicit_origin_line_is_not_guessed_at(machine):
    path = machine["layer"] / "skills.manifest.yaml"
    path.write_text(MANIFEST.replace("  same-one:\n    origin: vault\n", "  same-one:\n"), encoding="utf-8")
    outcomes = {o.name: o for o in _adopt(machine, ["same-one"])}
    assert not outcomes["same-one"].adopted and "by hand" in outcomes["same-one"].note
    assert (machine["layer"] / "same-one").is_dir()


# --- the doctor ---------------------------------------------------------------------------------------------------

def test_the_doctor_warns_while_a_frozen_copy_exists_and_fix_hands_back_the_identical_ones(machine):
    outcome = check_skill_engine_copies(machine["vault"], machine["home"], machine["engine"])
    assert outcome.severity is Severity.WARN
    assert "same-one" in outcome.message and "drifted" in outcome.message
    assert outcome.remedy is not None
    outcome.remedy()
    again = check_skill_engine_copies(machine["vault"], machine["home"], machine["engine"])
    assert again.severity is Severity.WARN and "same-one" not in again.message and "drifted" in again.message
    # With only the changed one left there is nothing the doctor may do by itself.
    assert again.remedy is None


def test_the_doctor_is_quiet_when_nothing_is_frozen(tmp_path):
    outcome = check_skill_engine_copies(tmp_path / "no-vault", tmp_path / "home", tmp_path / "no-engine")
    assert outcome.severity is Severity.OK


# --- the command --------------------------------------------------------------------------------------------------

def test_without_names_the_command_only_lists(machine, capsys):
    assert skill_adopt.main([], all_=False, force=False, dry_run=False) == 0
    out = capsys.readouterr().out
    assert "same-one" in out and "drifted" in out
    assert (machine["layer"] / "same-one").is_dir()


def test_all_adopts_the_identical_ones_and_names_the_rest(machine, capsys):
    assert skill_adopt.main([], all_=True, force=False, dry_run=False) == 0
    out = capsys.readouterr().out
    assert "same-one" in out and "vault push" in out
    assert not (machine["layer"] / "same-two").exists() and (machine["layer"] / "drifted").is_dir()


def test_names_and_all_together_are_refused(machine):
    assert skill_adopt.main(["same-one"], all_=True, force=False, dry_run=False) == 2


def test_the_verb_exists():
    from nexgen_core.cli import build_parser

    args = build_parser().parse_args(["skill", "adopt", "--all", "--dry-run"])
    assert args.all is True and args.dry_run is True and args.force is False
