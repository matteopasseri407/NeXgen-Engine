"""Pigro davvero: su tutti i runtime, e reversibile.

"Tutto è pigro per difetto" è facile da rispettare finché nessuno cambia idea.
Le due condizioni difficili sono altre: che una skill installata da uno
strumento di terze parti non finisca lo stesso dove ogni runtime la carica da
sola, e che una skill resa immediata possa tornare pigra. Senza la seconda, il
manifest smette di descrivere la realtà dopo la prima modifica.
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.skills import SkillMaterializer


def _vault_with(tmp_path: Path, manifest: str) -> Path:
    vault = tmp_path / "vault"
    skills_dir = vault / "03-INFRA" / "agent-universal-layer" / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    (skills_dir / "skills.manifest.yaml").write_text(manifest, encoding="utf-8")
    return vault


def _own_skill(vault: Path, name: str) -> Path:
    body = vault / "03-INFRA" / "agent-universal-layer" / "skills" / name
    body.mkdir(parents=True, exist_ok=True)
    (body / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")
    return body


def _materializer(tmp_path: Path, vault: Path, monkeypatch) -> SkillMaterializer:
    monkeypatch.setenv("AGENT_STATE_DIR", str(tmp_path / "state"))
    return SkillMaterializer(vault_data=vault, home=tmp_path / "home")


def test_a_skill_without_an_exposure_reaches_no_runtime(tmp_path, monkeypatch):
    vault = _vault_with(tmp_path, "skills:\n  quieta:\n    origin: vault\n")
    _own_skill(vault, "quieta")
    mat = _materializer(tmp_path, vault, monkeypatch)

    mat.materialize(apply=True)

    assert (mat.library_dir / "quieta").exists(), "deve stare nella libreria"
    for directory in mat.discovery_dirs:
        assert not (directory / "quieta").exists(), (
            f"pigra per difetto, e invece è comparsa in {directory}"
        )


def test_eager_reaches_only_the_runtimes_it_names(tmp_path, monkeypatch):
    vault = _vault_with(
        tmp_path,
        "skills:\n  subito:\n    origin: vault\n    exposure: eager\n    targets: [claude]\n",
    )
    _own_skill(vault, "subito")
    mat = _materializer(tmp_path, vault, monkeypatch)

    mat.materialize(apply=True)

    assert (mat.claude_dir / "subito").exists()
    assert not (mat.codex_dir / "subito").exists()
    assert not (mat.opencode_dir / "subito").exists()


def test_a_skill_can_be_made_lazy_again(tmp_path, monkeypatch):
    """Il caso che prima era senza ritorno."""
    vault = _vault_with(
        tmp_path,
        "skills:\n  ripensata:\n    origin: vault\n    exposure: eager\n    targets: [claude]\n",
    )
    _own_skill(vault, "ripensata")
    mat = _materializer(tmp_path, vault, monkeypatch)
    mat.materialize(apply=True)
    assert (mat.claude_dir / "ripensata").exists()

    (vault / "03-INFRA" / "agent-universal-layer" / "skills" / "skills.manifest.yaml").write_text(
        "skills:\n  ripensata:\n    origin: vault\n    exposure: manual\n    targets: [claude]\n",
        encoding="utf-8",
    )
    mat.materialize(apply=True)

    assert not (mat.claude_dir / "ripensata").exists(), (
        "tolta dal manifest come immediata, la vista deve sparire"
    )
    assert (mat.library_dir / "ripensata").exists(), "ma la skill resta disponibile su richiesta"


def test_a_skill_dropped_from_the_manifest_loses_its_view(tmp_path, monkeypatch):
    vault = _vault_with(
        tmp_path,
        "skills:\n  passeggera:\n    origin: vault\n    exposure: eager\n    targets: [claude]\n",
    )
    _own_skill(vault, "passeggera")
    mat = _materializer(tmp_path, vault, monkeypatch)
    mat.materialize(apply=True)

    (vault / "03-INFRA" / "agent-universal-layer" / "skills" / "skills.manifest.yaml").write_text(
        "skills: {}\n", encoding="utf-8"
    )
    mat.materialize(apply=True)

    assert not (mat.claude_dir / "passeggera").exists()


def test_something_that_is_not_ours_is_never_removed(tmp_path, monkeypatch):
    """Il sync è additivo: ciò che non ha messo lui, non lo toglie lui."""
    vault = _vault_with(tmp_path, "skills: {}\n")
    mat = _materializer(tmp_path, vault, monkeypatch)
    stranger = mat.claude_dir / "roba-di-qualcun-altro"
    stranger.mkdir(parents=True)
    (stranger / "SKILL.md").write_text("non toccarmi\n", encoding="utf-8")

    mat.materialize(apply=True)

    assert stranger.is_dir() and (stranger / "SKILL.md").is_file()


def test_a_view_edited_by_hand_is_left_alone(tmp_path, monkeypatch):
    """Una copia che ha smesso di combaciare può contenere lavoro di qualcuno."""
    vault = _vault_with(
        tmp_path,
        "skills:\n  toccata:\n    origin: vault\n    exposure: eager\n    targets: [claude]\n",
    )
    _own_skill(vault, "toccata")
    mat = _materializer(tmp_path, vault, monkeypatch)
    mat.materialize(apply=True)

    view = mat.claude_dir / "toccata"
    if view.is_symlink():
        view.unlink()
    view.mkdir(parents=True, exist_ok=True)
    (view / "SKILL.md").write_text("# modificata a mano\n", encoding="utf-8")

    (vault / "03-INFRA" / "agent-universal-layer" / "skills" / "skills.manifest.yaml").write_text(
        "skills: {}\n", encoding="utf-8"
    )
    mat.materialize(apply=True)

    assert view.is_dir(), "una copia divergente non si cancella"
    assert "modificata a mano" in (view / "SKILL.md").read_text(encoding="utf-8")


def test_a_third_party_installer_is_run_and_its_copy_is_taken_out_of_sight(tmp_path, monkeypatch):
    """Il caso che prima non succedeva affatto.

    Un installer con scope globale lascia la sua copia dove ogni runtime la
    trova. Il motore la sposta nella libreria, e da lì crea solo le viste
    dichiarate — che per una skill pigra sono nessuna.
    """
    vault = _vault_with(
        tmp_path,
        "skills:\n"
        "  impeccable:\n"
        "    origin: installer\n"
        '    version: "4.1.1"\n'
        "    exposure: manual\n"
        "    targets: [claude, codex]\n",
    )
    mat = _materializer(tmp_path, vault, monkeypatch)

    # L'installer finto fa quello che fa quello vero: scrive nella cartella
    # da cui Claude scopre le skill da sola.
    dropped = mat.claude_dir / "impeccable"
    installer = tmp_path / "finto-installer.sh"
    installer.write_text(
        f'#!/bin/sh\nmkdir -p "{dropped}"\nprintf "# impeccable\\n" > "{dropped}/SKILL.md"\n',
        encoding="utf-8",
    )
    installer.chmod(0o755)
    entry = mat.load_manifest()["impeccable"]
    entry.install = ["sh", str(installer)]
    monkeypatch.setattr(mat, "load_manifest", lambda: {"impeccable": entry})

    _changes, actions = mat.materialize(apply=True)

    assert (mat.library_dir / "impeccable" / "SKILL.md").is_file(), (
        f"l'installer ha girato ma la skill non è nella libreria: {actions}"
    )
    assert not dropped.exists(), (
        "la copia dell'installer è rimasta dove ogni runtime la carica da sola"
    )
    for directory in mat.discovery_dirs:
        assert not (directory / "impeccable").exists()


def test_the_installer_is_not_run_again_for_a_version_already_installed(tmp_path, monkeypatch):
    vault = _vault_with(
        tmp_path,
        "skills:\n  fissa:\n    origin: installer\n    version: \"1.0.0\"\n    exposure: manual\n",
    )
    mat = _materializer(tmp_path, vault, monkeypatch)

    counter = tmp_path / "quante-volte"
    installer = tmp_path / "conta.sh"
    installer.write_text(
        f'#!/bin/sh\nprintf x >> "{counter}"\n'
        f'mkdir -p "{mat.claude_dir / "fissa"}"\n'
        f'printf "# fissa\\n" > "{mat.claude_dir / "fissa" / "SKILL.md"}"\n',
        encoding="utf-8",
    )
    installer.chmod(0o755)
    entry = mat.load_manifest()["fissa"]
    entry.install = ["sh", str(installer)]
    monkeypatch.setattr(mat, "load_manifest", lambda: {"fissa": entry})

    mat.materialize(apply=True)
    mat.materialize(apply=True)

    assert counter.read_text() == "x", "il pin non è cambiato: l'installer non va rieseguito"


def test_an_installer_skill_without_an_install_command_is_reported_not_ignored(tmp_path, monkeypatch):
    vault = _vault_with(
        tmp_path,
        "skills:\n  monca:\n    origin: installer\n    version: \"1.0.0\"\n",
    )
    mat = _materializer(tmp_path, vault, monkeypatch)

    _changes, actions = mat.materialize(apply=True)

    assert any("monca" in a for a in actions), (
        "una voce che non si può soddisfare deve comparire nel referto, non sparire"
    )


def test_a_github_skill_is_cloned_from_where_it_lives():
    """Il manifest nomina una skill come la scrivono le persone.

    `repo: owner/nome` finiva dritto in `git clone`, che lo leggeva come
    percorso locale e rispondeva che il repository non esiste. Tre skill su
    questa macchina fallivano a ogni allineamento per questo.
    """
    from nexgen_core.skills import clone_url

    assert clone_url("blader/humanizer") == "https://github.com/blader/humanizer.git"
    assert clone_url("  owner/name  ") == "https://github.com/owner/name.git"


def test_a_full_url_is_left_as_written():
    """Chi ha scritto un URL intero intendeva quello."""
    from nexgen_core.skills import clone_url

    for written in (
        "https://github.com/x/y.git",
        "git@github.com:x/y.git",
        "https://gitlab.com/x/y.git",
        "/un/percorso/locale",
    ):
        assert clone_url(written) == written
