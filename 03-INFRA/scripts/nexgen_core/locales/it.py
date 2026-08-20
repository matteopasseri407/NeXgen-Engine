"""Italiano.

Chi installa questo motore non è chi lo scrive. Il codice resta in inglese —
lo leggono chi lo mantiene e gli assistenti che ci lavorano — e questo file è
il posto in cui quel codice impara a rivolgersi a una persona che l'inglese
non lo legge.

Regole per chi traduce:

- La chiave è la frase inglese esatta. Se cambia in inglese, il messaggio
  ripiega sull'inglese finché qualcuno non aggiorna la riga qui. È voluto:
  meglio una frase inglese giusta che una frase italiana vecchia.
- I campi fra graffe si possono spostare, ma non se ne possono inventare di
  nuovi: un test lo verifica, perché una graffa che non esiste nella sorgente
  fa ripiegare il messaggio sull'inglese per sempre e in silenzio.
- Si dice COSA non va, non quale funzione ha fallito. Una sola azione, pronta
  da incollare. Il dettaglio tecnico resta in inglese: lo legge chi sta
  cercando il guasto, non chi lo subisce.
- Dare del tu. Questo motore parla a una persona, non a un ufficio.
"""
from __future__ import annotations

MESSAGES: dict[str, str] = {
    # --- Il referto ---------------------------------------------------------
    "Problems detected that need your attention:": "Cose che richiedono la tua attenzione:",
    "Checks that could not be verified right now:": "Cose che non è stato possibile verificare adesso:",
    "Checks passed ({count}):": "Controlli superati ({count}):",
    "Suggested action:": "Cosa fare:",
    "Everything is in order.": "È tutto a posto.",
    "({count} checks passed)": "({count} controlli superati)",

    # --- Gli avvisi che arrivano a una persona ------------------------------
    "Action required:": "Cosa fare:",
    "The guard did not start": "La guardia non è partita",
    "There are problems on this machine": "Ci sono problemi su questa macchina",
    (
        "{unit} did not start, and until it does this machine stops staying aligned. "
        "Check it with: systemctl --user status {unit}"
    ): (
        "{unit} non è partita, e finché non parte questa macchina smette di allinearsi. "
        "Controllala con: systemctl --user status {unit}"
    ),

    # --- Pubblicare il proprio lavoro ---------------------------------------
    "Local commit done (Local-Only mode: no remote to update)":
        "Commit locale eseguito (modalità solo-locale: non c'è nessun remoto da aggiornare)",
    "Nothing to commit (Local-Only mode: no remote to update)":
        "Non c'era niente da salvare (modalità solo-locale: non c'è nessun remoto da aggiornare)",
    "Published successfully": "Pubblicato",
    "Could not reach {remote} for publishing": "Non riesco a raggiungere {remote} per pubblicare",
    (
        "{remote} unreachable: the commit stays local, publish it later with 'vault-push'"
    ): (
        "{remote} non raggiungibile: il commit resta qui, ripubblicalo più tardi con 'vault-push'"
    ),

    # --- Allineare la macchina ----------------------------------------------
    "Alignment completed successfully": "Allineamento completato",
    "MCP configurations regenerated for every CLI":
        "Configurazioni dei connettori rigenerate per tutti gli assistenti",
    "Commands realigned ({count})": "Comandi riparati ({count})",
    "Liveness recorded successfully": "Battito registrato",

    # --- Lo stack dei servizi -----------------------------------------------
    (
        "Docker is not installed on this machine. Install it, or use a server with "
        "'bootstrap-vps.sh' if you prefer to host it elsewhere."
    ): (
        "Docker non è installato su questa macchina. Installalo, oppure usa un server "
        "con 'bootstrap-vps.sh' se preferisci ospitarlo altrove."
    ),
    (
        "Docker is installed but does not answer this user. Usually the missing piece is "
        "group membership: 'sudo usermod -aG docker $USER', then open a new session."
    ): (
        "Docker è installato ma non risponde a questo utente. Di solito manca "
        "l'appartenenza al gruppo: 'sudo usermod -aG docker $USER', poi riapri la sessione."
    ),
    "{name} started": "{name} avviato",
    "{name} stopped": "{name} fermato",
    "running": "attivo",
    "not running": "non attivo",
    (
        "{down} of {total} services are not answering. Start them with: nexgen stack up"
    ): (
        "{down} servizi su {total} non rispondono. Avviali con: nexgen stack up"
    ),
    (
        "Reopen your session (or export those variables), then run: nexgen sync"
    ): (
        "Riapri la sessione (oppure esporta quelle variabili), poi esegui: nexgen sync"
    ),
}
