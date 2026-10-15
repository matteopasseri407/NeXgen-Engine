"""Pinning dello split di skill_adopt prima del refactor.

Fissa il comportamento attuale (non quello desiderato) con skill e
manifest finti: describe_difference su skill finta ritorna differenze
attese, adopt su libreria finta adotta, firme pubbliche invariate.
Solo tmp_path, solo skill finte, nessuna rete, nessuna libreria vera.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

INFRA = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(INFRA / "scripts"))

import pytest  # noqa: E402
import yaml  # noqa: E402

from nexgen_core import i18n, skill_adopt  # noqa: E402


@pytest.fixture(autouse=True)
def _english():
    i18n.set_language("en")
    yield
    i18n.set_language(None)


MANIFEST = """# comment that must survive
schema_version: 1
skills:
  same-one:
    origin: vault
    exposure: core
    targets: [claude]
  drifted:
    origin: vault
    exposure: core
    targets: [claude]
"""


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

    write(shipped, "same-one", "engine text")
    write(layer, "same-one", "engine text")
    write(shipped, "drifted", "engine's newer text\nwith an extra line")
    write(layer, "drifted", "old text")
    (layer / "skills.manifest.yaml").write_text(MANIFEST, encoding="utf-8")
    return {"vault": vault, "engine": engine, "home": home, "state": state, "layer": layer, "shipped": shipped}


def _adopt(m, names=None, **kwargs):
    return skill_adopt.adopt(names, home=m["home"], vault_data=m["vault"], engine_root=m["engine"], state_dir=m["state"], **kwargs)


def test_describe_difference_su_skill_finta_ritorna_differenze_attese(machine):
    candidate = next(c for c in skill_adopt.candidates(machine["vault"], machine["engine"]) if c.name == "drifted")
    assert candidate.status == "differs"
    note = skill_adopt.describe_difference(candidate)
    assert "SKILL.md" in note
    assert "yours lacks" in note
    assert "engine's lacks" in note


def test_adopt_su_libreria_finta_adotta(machine):
    outcomes = _adopt(machine)
    adopted = {o.name for o in outcomes if o.adopted}
    assert adopted == {"same-one"}
    after = (machine["layer"] / "skills.manifest.yaml").read_text(encoding="utf-8")
    entries = yaml.safe_load(after)["skills"]
    assert entries["same-one"]["origin"] == "engine"
    assert entries["drifted"]["origin"] == "vault"
    assert not (machine["layer"] / "same-one").exists()
    stashed = list((machine["state"] / "skill-copies").glob("*/same-one/SKILL.md"))
    assert len(stashed) == 1
    assert "engine text" in stashed[0].read_text(encoding="utf-8")


def test_firme_pubbliche_invariate():
    assert list(inspect.signature(skill_adopt.shipped_skills).parameters) == ["engine_root"]
    assert list(inspect.signature(skill_adopt.candidates).parameters) == ["vault_data", "engine_root"]
    assert list(inspect.signature(skill_adopt.describe_difference).parameters) == ["candidate"]
    assert list(inspect.signature(skill_adopt.adopt).parameters) == [
        "names",
        "include_changed",
        "dry_run",
        "home",
        "vault_data",
        "engine_root",
        "state_dir",
    ]
    assert list(inspect.signature(skill_adopt.main).parameters) == ["names", "all_", "force", "dry_run"]
