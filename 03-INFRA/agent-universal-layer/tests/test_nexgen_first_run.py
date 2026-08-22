"""L'installazione che arriva fino in fondo, senza un assistente nel mezzo.

Il paradosso d'ingresso: per installare NeXgen serviva già un assistente
funzionante, perché l'ultimo passo diceva di incollare `INIT.md` in una CLI
capace di modificare file. Chi non ne aveva ancora uno si fermava alla prima
mossa, e un'azienda non incolla documenti dentro agenti.

Dei sette passi di `INIT.md` uno solo richiede davvero una conversazione —
offrire di portare dentro i documenti dell'utente, dichiarato facoltativo.
Gli altri sei sono meccanici, e il meccanico sta nel codice.
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
REPO_ROOT = SCRIPTS_DIR.parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.first_run import (
    describe_host,
    seed_skill_manifest,
    write_remotes,
    write_user_profile,
)


def _vault(tmp_path: Path) -> Path:
    """Un vault come quello che l'utente si ritrova appena clonato."""
    vault = tmp_path / "vault"
    (vault / "99-INDEX").mkdir(parents=True, exist_ok=True)
    (vault / "99-INDEX" / "USER-PROFILE.md").write_text(
        (REPO_ROOT / "99-INDEX" / "USER-PROFILE.md").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    skills = vault / "03-INFRA" / "agent-universal-layer" / "skills"
    skills.mkdir(parents=True)
    (skills / "skills.manifest.yaml.example").write_text("skills: {}\n", encoding="utf-8")
    return vault


def test_the_profile_is_filled_from_the_answers(tmp_path):
    vault = _vault(tmp_path)

    written, message = write_user_profile(
        vault, profile="MINIMAL", clis=["claude"], machines="primary (this one)"
    )

    assert written, message
    text = (vault / "99-INDEX" / "USER-PROFILE.md").read_text(encoding="utf-8")
    assert "- **profile**: `MINIMAL`" in text
    assert "- **clis**: `claude`" in text
    assert "- **sync_method**: `manual`" in text, "una macchina sola non usa il provisioner"
    assert "[MINIMAL | MULTI]" not in text, "il segnaposto deve essere sparito"


def test_the_note_addressed_to_the_installing_agent_goes_away(tmp_path):
    """Il muro era scritto dentro il file: «NOTE FOR THE INSTALLER AGENT»."""
    vault = _vault(tmp_path)
    write_user_profile(vault, profile="MINIMAL", clis=["claude"], machines="primary")

    text = (vault / "99-INDEX" / "USER-PROFILE.md").read_text(encoding="utf-8")
    assert "NOTE FOR THE INSTALLER AGENT" not in text


def test_a_profile_someone_already_filled_is_never_overwritten(tmp_path):
    vault = _vault(tmp_path)
    write_user_profile(vault, profile="MULTI", clis=["claude", "codex"], machines="due")

    written, message = write_user_profile(
        vault, profile="MINIMAL", clis=["opencode"], machines="una"
    )

    assert not written, "la seconda passata non deve toccare niente"
    text = (vault / "99-INDEX" / "USER-PROFILE.md").read_text(encoding="utf-8")
    assert "`MULTI`" in text and "`MINIMAL`" not in text, message


def test_the_host_is_described_without_inventing_anything(tmp_path):
    described = describe_host()
    assert described and "[" not in described, "niente segnaposto residui"
    assert "FILL IN" not in described


def test_one_machine_means_there_is_no_remote_to_publish_to(tmp_path):
    """Il rosso falso più grosso per un estraneo.

    Chi installa su una macchina sola resta con `origin` che punta al repo
    pubblico del progetto, e da lì in poi ogni controllo gli chiede di
    pubblicarci sopra le sue note private: due guasti rossi su
    un'installazione corretta.
    """
    vault = _vault(tmp_path)

    written, _message = write_remotes(vault, profile="MINIMAL")

    assert written
    text = (vault / "03-INFRA" / "agent-universal-layer" / "sync" / "remotes.yaml").read_text(
        encoding="utf-8"
    )
    assert "authoritative_remote: local" in text


def test_the_services_answer_does_not_decide_the_remote(tmp_path):
    """Distinzione che avevo sbagliato: dove girano i connettori non dice
    niente su quante macchine ci sono da raggiungere. Il file risultante
    dipende dal profilo e dai remoti che esistono, mai dai servizi.
    """
    first, second = _vault(tmp_path / "a"), _vault(tmp_path / "b")

    write_remotes(first, profile="MINIMAL")
    write_remotes(second, profile="MINIMAL")

    path = Path("03-INFRA") / "agent-universal-layer" / "sync" / "remotes.yaml"
    assert (first / path).read_text(encoding="utf-8") == (second / path).read_text(encoding="utf-8")


def test_an_existing_remotes_file_is_left_alone(tmp_path):
    vault = _vault(tmp_path)
    sync = vault / "03-INFRA" / "agent-universal-layer" / "sync"
    sync.mkdir(parents=True)
    (sync / "remotes.yaml").write_text("authoritative_remote: oracle\nmirrors: []\n", encoding="utf-8")

    written, _message = write_remotes(vault, profile="MINIMAL")

    assert not written
    assert "oracle" in (sync / "remotes.yaml").read_text(encoding="utf-8")


def test_the_skill_manifest_is_seeded_once(tmp_path):
    """Il motore non lo ricrea mai da sé — ma qualcuno deve metterlo la prima
    volta, e finora era un passo di INIT.md, cioè un assistente."""
    vault = _vault(tmp_path)

    written, _message = seed_skill_manifest(vault)
    target = vault / "03-INFRA" / "agent-universal-layer" / "skills" / "skills.manifest.yaml"
    assert written and target.is_file()

    target.write_text("skills: {}\n# scelta deliberata\n", encoding="utf-8")
    written_again, _message = seed_skill_manifest(vault)
    assert not written_again, "una seconda passata cancellerebbe una scelta deliberata"
    assert "scelta deliberata" in target.read_text(encoding="utf-8")


def _git_vault(tmp_path):
    """Un vault che è davvero un repository, come quello di chi ha clonato."""
    import subprocess

    vault = _vault(tmp_path)
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"],
                 ["config", "user.name", "t"], ["add", "-A"],
                 ["commit", "-qm", "vault"]):
        subprocess.run(["git", "-C", str(vault), *args], check=True,
                       capture_output=True, timeout=60)
    return vault


def test_the_installer_records_its_own_writes_before_aligning(tmp_path):
    """Altrimenti l'installazione sabota se stessa.

    L'allineamento si rifiuta di lavorare su un vault con modifiche non
    salvate, e fa bene. Ma i file che l'installer scrive sono contenuto del
    vault, e un vault è un repository git proprio perché il suo contenuto
    venga registrato. Senza questo passo la prima installazione MULTI si
    bloccava sulle proprie scritture.
    """
    import subprocess

    from nexgen_core.first_run import commit_setup

    vault = _git_vault(tmp_path)
    write_user_profile(vault, profile="MULTI", clis=["claude"], machines="due")

    recorded, message = commit_setup(vault, [vault / "99-INDEX" / "USER-PROFILE.md"])

    assert recorded, message
    dirty = subprocess.run(["git", "-C", str(vault), "status", "--porcelain"],
                           capture_output=True, text=True, check=True, timeout=60)
    assert "USER-PROFILE.md" not in dirty.stdout


def test_it_records_only_what_it_wrote(tmp_path):
    """Il lavoro in corso di qualcun altro non è affare dell'installer."""
    import subprocess

    from nexgen_core.first_run import commit_setup

    vault = _git_vault(tmp_path)
    (vault / "04-NOW").mkdir(parents=True, exist_ok=True)
    mine = vault / "04-NOW" / "appunti-miei.md"
    mine.write_text("# in mezzo a una frase\n", encoding="utf-8")
    write_user_profile(vault, profile="MINIMAL", clis=["claude"], machines="una")

    commit_setup(vault, [vault / "99-INDEX" / "USER-PROFILE.md"])

    staged = subprocess.run(["git", "-C", str(vault), "show", "--name-only",
                             "--format=", "HEAD"],
                            capture_output=True, text=True, check=True, timeout=60)
    assert "USER-PROFILE.md" in staged.stdout
    assert "appunti-miei.md" not in staged.stdout, (
        "il lavoro in corso di qualcun altro è finito nel commit dell'installer"
    )


def test_a_remote_that_does_not_exist_yet_is_not_named(tmp_path):
    """Chi dice «2+ macchine» prima di avere un remoto privato non ha
    sbagliato niente, e non deve trovarsi l'allineamento bloccato."""
    vault = _git_vault(tmp_path)

    write_remotes(vault, profile="MULTI")

    text = (vault / "03-INFRA" / "agent-universal-layer" / "sync" / "remotes.yaml").read_text(
        encoding="utf-8"
    )
    assert "authoritative_remote: local" in text


def test_a_remote_that_exists_is_the_one_that_gets_named(tmp_path):
    import subprocess

    vault = _git_vault(tmp_path)
    subprocess.run(["git", "-C", str(vault), "remote", "add", "origin",
                    "https://example.invalid/vault.git"], check=True,
                   capture_output=True, timeout=60)

    write_remotes(vault, profile="MULTI")

    text = (vault / "03-INFRA" / "agent-universal-layer" / "sync" / "remotes.yaml").read_text(
        encoding="utf-8"
    )
    assert "authoritative_remote: origin" in text


def test_the_installer_writes_its_commands_inside_the_sandbox(tmp_path, monkeypatch):
    """La sandbox vale anche per i comandi installati.

    Onesto su cosa prova e cosa no: `install_shims` risolve la home da sé e
    già rispetta NEXGEN_HOME, quindi questo test passa anche togliendo il
    parametro esplicito da `install_launchers`. Resta perché fissa la
    proprietà — i comandi finiscono nella home data — non perché abbia
    trovato il guasto. Il guasto vero (i comandi di questa macchina riscritti
    verso una cartella in /tmp durante una prova, alle 10:30 del 21 agosto)
    non è stato riprodotto: nessuna delle esecuzioni sospette lo rifà.
    """
    from nexgen_core.bootstrap import install_launchers

    real = tmp_path / "macchina-vera" / ".local" / "bin"
    real.mkdir(parents=True)
    (real / "agent-sync").write_text("#!/bin/sh\n# il comando vero\n", encoding="utf-8")

    monkeypatch.setenv("NEXGEN_HOME", str(tmp_path / "sandbox"))

    install_launchers(REPO_ROOT)

    assert (real / "agent-sync").read_text(encoding="utf-8") == "#!/bin/sh\n# il comando vero\n"
    ext = ".cmd" if sys.platform == "win32" else ""
    assert (tmp_path / "sandbox" / ".local" / "bin" / f"nexgen{ext}").is_file()
