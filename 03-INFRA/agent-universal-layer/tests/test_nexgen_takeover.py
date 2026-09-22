"""Prendere il posto della versione precedente su una macchina già installata.

Questa suite esiste perché il passaggio si rompe in silenzio e se ne accorge
solo la macchina, a metà. La versione installata oggi esegue l'aggiornamento
da sé: scarica l'albero nuovo, poi lancia i propri comandi. Se quei comandi
non esistono più al percorso da cui i suoi collegamenti li cercano, resta un
albero nuovo con i comandi rotti — su tutte le macchine insieme.

I dati non si toccano: sono gli stessi file, letti dallo stesso posto. Ciò che
cambia è dove il motore tiene il proprio stato, e anche quello va verificato,
perché scriverlo nel posto sbagliato blocca l'aggiornamento successivo per
sempre.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
REPO_ROOT = SCRIPTS_DIR.parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import paths


def test_windows_task_invokes_installed_shim_not_a_checkout_twin():
    """Since v2.3.0 there is no twin left in the checkout to point at: the
    hidden scheduled-task wrapper must drive the installed
    `agent-sync.cmd` shim (which finds Python and the tree entry itself)."""
    from nexgen_core.scheduler import _VBS_TEMPLATE

    rendered = _VBS_TEMPLATE.format(
        engine_root="C:\\eng",
        vault_data="C:\\vault",
        vault="C:\\vault",
        branch="main",
        script="C:\\Users\\t\\.local\\bin\\agent-sync.cmd",
        mode="guard",
    )
    assert "agent-sync.cmd" in rendered
    assert ".ps1" not in rendered
    assert "powershell.exe -NoProfile -ExecutionPolicy Bypass -File" not in rendered


def test_no_transitional_launcher_twins_remain():
    """I gemelli `.sh`/`.ps1` se ne sono andati nella 2.3.0 e non devono
    tornare: un collegamento della v1 che punta nel vuoto lascia la macchina
    senza comandi, e due implementazioni tenute a mano divergono. Il
    preflight di release rifiuta la loro resurrezione; questo test lo fissa
    nella suite, insieme all'assenza del generatore."""
    import importlib.util

    scripts = SCRIPTS_DIR
    twins = sorted(p.name for p in (*scripts.glob("*.sh"), *scripts.glob("*.ps1")))
    assert not twins, f"gemelli transitori tornati in 03-INFRA/scripts: {', '.join(twins)}"
    assert importlib.util.find_spec("nexgen_core.legacy_launchers") is None, (
        "il generatore dei launcher transitori esiste ancora"
    )


def test_post_merge_commands_come_from_the_tree_not_from_twins():
    """La prova d'esecuzione che resta: l'entry del tree risponde, quindi il
    provisioning post-merge dell'updater non ha più bisogno dei gemelli."""
    result = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "nexgen_core" / "cli" / "__init__.py"), "--help"],
        capture_output=True, text=True, check=False, timeout=60,
    )
    assert result.returncode == 0, f"l'entry del tree non risponde:\n{result.stderr}"
    assert "Traceback" not in result.stderr


def test_the_state_directory_is_not_the_engine_checkout(monkeypatch):
    """Lo stato non può vivere dentro il clone git del motore.

    L'aggiornatore rifiuta di lavorare su un albero sporco e conta anche i
    file non tracciati. Un lock e un timbro di liveness scritti nella radice
    del clone lo rendono sporco a ogni giro, e da quel momento il motore non
    riesce più ad aggiornarsi. Per sempre, e senza dire perché.
    """
    for name in ("AGENT_STATE_DIR", "AGENT_ENGINE_ROOT", "XDG_STATE_HOME"):
        monkeypatch.delenv(name, raising=False)
    home = Path("/finta-home")
    state = paths.resolve_state_dir(home=home)
    engine = paths.resolve_engine_root(home=home)

    assert state != engine.parent, (
        "la cartella di stato coincide con il clone del motore: "
        "i file di stato lo renderebbero sporco e bloccherebbero ogni aggiornamento"
    )
    assert engine.parent not in state.parents and state not in engine.parents


def test_the_lock_is_the_same_one_the_previous_version_takes(monkeypatch):
    """Durante il passaggio le due versioni devono escludersi a vicenda.

    Per un po' convivono: la vecchia ha ancora il suo timer, la nuova ha già
    il suo. Se prendono due lock diversi, girano insieme sullo stesso vault.
    """
    monkeypatch.delenv("AGENT_STATE_DIR", raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    home = Path("/finta-home")
    assert paths.resolve_state_dir(home=home) == home / ".local" / "state"


def test_the_data_paths_are_the_ones_the_previous_version_used(monkeypatch):
    """I dati personali e le memorie restano dove sono: stesso vault, stessi file."""
    for name in ("AGENT_VAULT_DATA", "KNOWLEDGE_VAULT_PATH", "AGENT_ENGINE_ROOT"):
        monkeypatch.delenv(name, raising=False)
    home = Path("/finta-home")
    assert paths.resolve_vault_data(home=home) == home / "KnowledgeVault"
    assert paths.resolve_engine_root(home=home) == home / ".nexgen-engine" / "03-INFRA"


def test_the_environment_variables_the_previous_version_set_are_still_honoured(monkeypatch):
    """Le macchine hanno queste variabili nei loro servizi: ignorarle le sposta."""
    monkeypatch.setenv("AGENT_VAULT_DATA", "/dati/altrove")
    monkeypatch.setenv("AGENT_ENGINE_ROOT", "/motore/altrove")
    monkeypatch.setenv("AGENT_STATE_DIR", "/stato/altrove")
    assert paths.resolve_vault_data() == Path("/dati/altrove")
    assert paths.resolve_engine_root() == Path("/motore/altrove")
    assert paths.resolve_state_dir() == Path("/stato/altrove")


def test_knowledge_vault_path_still_works_as_a_fallback(monkeypatch):
    """Il nome più vecchio delle due variabili non è stato ritirato."""
    monkeypatch.delenv("AGENT_VAULT_DATA", raising=False)
    monkeypatch.setenv("KNOWLEDGE_VAULT_PATH", "/dati/storici")
    assert paths.resolve_vault_data() == Path("/dati/storici")


def test_the_paths_the_previous_version_invokes_by_path_still_answer():
    """Alcuni file sono contratto: altri componenti li chiamano per percorso."""
    contract = [
        SCRIPTS_DIR / "agent_sync.py",
        SCRIPTS_DIR / "skills-sync.py",
        SCRIPTS_DIR / "agent-skill.py",
        SCRIPTS_DIR / "firecrawl-search-health.py",
        REPO_ROOT / "03-INFRA" / "agent-universal-layer" / "mcp" / "render.py",
    ]
    missing = [str(p.relative_to(REPO_ROOT)) for p in contract if not p.is_file()]
    assert not missing, f"percorsi che altri componenti invocano e che non esistono più: {missing}"


# --- Sviluppare accanto a un'installazione viva -----------------------------
#
# Un clone di sviluppo e una macchina che si usa davvero non possono
# contendersi gli stessi file. Senza un confine, provare il motore nuovo
# significa sostituire i comandi di quello vecchio — è successo due volte
# mentre questo veniva scritto, e la seconda ha spento le notifiche vere.


def test_a_sandbox_home_moves_everything_the_engine_writes(monkeypatch, tmp_path):
    monkeypatch.setenv("NEXGEN_HOME", str(tmp_path / "sviluppo"))
    for name in ("AGENT_STATE_DIR", "AGENT_ENGINE_ROOT", "AGENT_VAULT_DATA",
                 "KNOWLEDGE_VAULT_PATH", "XDG_STATE_HOME"):
        monkeypatch.delenv(name, raising=False)

    sandbox = tmp_path / "sviluppo"
    for resolved in (paths.resolve_home(), paths.resolve_state_dir(),
                     paths.resolve_engine_root(), paths.resolve_vault_data()):
        assert sandbox in resolved.parents or resolved == sandbox, (
            f"{resolved} è fuori dalla sandbox: toccherebbe l'installazione vera"
        )


def test_the_sandbox_never_wins_over_an_explicit_choice(monkeypatch, tmp_path):
    """Chi nomina un percorso lo ha scelto, e la sandbox non lo sovrascrive."""
    monkeypatch.setenv("NEXGEN_HOME", str(tmp_path / "sviluppo"))
    monkeypatch.setenv("AGENT_VAULT_DATA", "/dati/scelti/a/mano")
    assert paths.resolve_vault_data() == Path("/dati/scelti/a/mano")


def test_without_the_sandbox_nothing_changes(monkeypatch):
    """L'isolamento è opt-in: un motore installato deve continuare come prima."""
    monkeypatch.delenv("NEXGEN_HOME", raising=False)
    assert paths.resolve_home() == Path.home()


def test_every_component_asks_for_its_home_instead_of_taking_it(monkeypatch):
    """Nessun componente può leggere la home per conto proprio.

    Basta un `Path.home()` dimenticato perché il clone di sviluppo scriva
    nella configurazione vera: è esattamente così che è andata.
    """
    import re

    core = SCRIPTS_DIR / "nexgen_core"
    offenders: list[str] = []
    for source in core.rglob("*.py"):
        if source.name == "paths.py" or "__pycache__" in source.parts:
            continue
        for number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"\bPath\.home\(\)", line) and "noqa" not in line:
                offenders.append(f"{source.relative_to(core)}:{number}")
    assert not offenders, (
        "questi punti prendono la home invece di chiederla a resolve_home(), "
        f"e sfuggirebbero all'isolamento: {', '.join(offenders)}"
    )


def test_a_live_config_carrying_a_real_token_is_refused_not_swallowed():
    """Adottare una configurazione con un segreto dentro deve fermarsi.

    Il codice cercava già `<AUTH>` per rifiutare, ma niente lo emetteva mai:
    un token scritto in chiaro veniva scartato in silenzio, il server smetteva
    di autenticarsi e nessuno diceva perché.
    """
    from nexgen_core.renderer_cli import LITERAL_SECRET, _adopt_entry

    literal = _adopt_entry(
        "claude",
        {"type": "http", "url": "https://x", "headers": {"Authorization": "Bearer un-token-vero"}},
    )
    assert literal["auth"]["env"] == LITERAL_SECRET

    referenced = _adopt_entry(
        "claude",
        {"type": "http", "url": "https://x", "headers": {"Authorization": "Bearer ${TOKEN}"}},
    )
    assert referenced["auth"]["env"] == "TOKEN"

    absent = _adopt_entry("claude", {"type": "http", "url": "https://x"})
    assert "auth" not in absent, "nessuna autenticazione non è la stessa cosa di un segreto"


# --- Il debito è stato saldato nella 2.3.0 ------------------------------------
#
# I venti involucri esistevano solo per le macchine che arrivavano dalla
# versione precedente. La 2.3.0 li ha cancellati dopo il takeover completo e
# il preflight rifiuta la loro resurrezione; la scadenza scritta
# (REMOVE_AFTER) ha smesso di servire nel momento in cui è stata onorata.


def test_the_machine_records_which_engine_completed_the_cycle(tmp_path, monkeypatch):
    """Senza questo, 'sono tutte migrate?' resta un'impressione."""
    import nexgen_core
    from nexgen_core.beat import Heartbeat

    monkeypatch.setenv("AGENT_STATE_DIR", str(tmp_path / "state"))
    beat = Heartbeat()
    beat.record_liveness()

    assert beat.recorded_version() == nexgen_core.__version__


def test_the_previous_format_is_still_readable(tmp_path, monkeypatch):
    """Un timestamp nudo è ciò che scriveva la versione prima: deve valere ancora."""
    import time

    from nexgen_core.beat import Heartbeat

    monkeypatch.setenv("AGENT_STATE_DIR", str(tmp_path / "state"))
    beat = Heartbeat()
    beat.state_dir.mkdir(parents=True, exist_ok=True)
    beat.liveness_file.write_text(str(time.time()), encoding="utf-8")

    alive, _message = beat.check_liveness()
    assert alive, "il formato precedente non si legge più"
    assert beat.recorded_version() is None, "e non deve inventarsi una versione"


def test_a_real_cycle_under_a_sandbox_home_touches_nothing_outside_it(tmp_path, monkeypatch):
    """L'isolamento va provato eseguendo, non risolvendo percorsi.

    Il test che c'era controllava che `resolve_*` restituisse percorsi dentro
    la sandbox, e passava. Intanto un ciclo vero scriveva la liveness della
    macchina vera e ne prendeva il lock, perché `GuardRunner` passava al
    battito il vault e il motore e lasciava che la cartella di stato se la
    risolvesse da sola dall'ambiente. Qui si guarda cosa è stato toccato.
    """
    import subprocess
    import time

    from nexgen_core.guard import GuardMode, GuardRunner

    outside = tmp_path / "macchina-vera" / ".local" / "state"
    outside.mkdir(parents=True)
    watched = [outside / "agent-guard-liveness", outside / "agent-sync.lock"]
    for path in watched:
        path.write_text("non toccare\n", encoding="utf-8")
    before = {p: p.stat().st_mtime_ns for p in watched}
    time.sleep(0.01)

    monkeypatch.setenv("NEXGEN_HOME", str(tmp_path / "macchina-vera"))
    for name in ("AGENT_STATE_DIR", "XDG_STATE_HOME", "AGENT_ENGINE_ROOT"):
        monkeypatch.delenv(name, raising=False)

    vault = tmp_path / "vault"
    layer = vault / "03-INFRA" / "agent-universal-layer"
    for sub, name, body in (
        ("mcp", "manifest.yaml", "schema_version: 1\nservers: {}\n"),
        ("skills", "skills.manifest.yaml", "schema_version: 1\nskills: {}\n"),
        ("instructions", "AGENTS.md", "# Regole\n"),
    ):
        (layer / sub).mkdir(parents=True, exist_ok=True)
        (layer / sub / name).write_text(body, encoding="utf-8")
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"],
                 ["config", "user.name", "t"], ["add", "-A"], ["commit", "-qm", "init"]):
        subprocess.run(["git", "-C", str(vault), *args], check=True,
                       capture_output=True, timeout=60)

    sandbox_home = tmp_path / "sandbox"
    GuardRunner(vault_data=vault, home=sandbox_home).run(
        mode=GuardMode.APPLY, allow_offline=True
    )

    for path in watched:
        assert path.stat().st_mtime_ns == before[path], (
            f"un ciclo in sandbox ha scritto {path}: è la macchina di qualcun altro"
        )
    assert path.read_text(encoding="utf-8") == "non toccare\n"
