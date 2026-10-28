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
    "Show the engine version and exit": "Mostra la versione del motore ed esce",
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
    "Nothing to publish": "Niente da pubblicare",
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

    # --- v2 core translation pass (nexgen_core/*.py + cli/*.py) -----------
    'Everything is aligned and working ({count} checks passed).':
        'Tutto allineato e funzionante ({count} controlli superati).',
    'auto-remedied': 'riparato automaticamente',
    'NeXgen Engine — keeps your machines and your assistants aligned.': 'NeXgen Engine — tiene allineate le tue macchine e i tuoi assistenti.',
    'Interrupted.': 'Interrotto.',
    'NeXgen Engine Doctor (v2) - Alignment diagnostics and verification': "NeXgen Engine Doctor (v2) - Diagnostica e verifica dell'allineamento",
    'Show every check run, including the ones that passed': 'Mostra tutti i controlli eseguiti, compresi quelli superati',
    'Strict mode: treat undetermined states as non-compliant too': 'Modalità rigorosa: tratta come guasto anche ciò che non si può verificare',
    'Print the output in JSON format': "Stampa l'output in formato JSON",
    'Print the summary with FAIL=N OK=N counts': 'Stampa il riepilogo con i conteggi FAIL=N OK=N',
    'Print how long each check took, slowest first': 'Stampa quanto ha impiegato ogni controllo, dal più lento',
    'Check timings (slowest first): {count} checks': 'Tempi dei controlli (dal più lento): {count} controlli',
    'Apply automatic remedies for fixable problems': 'Applica i rimedi automatici per i problemi riparabili',
    'Align this machine now': 'Allinea questa macchina adesso',
    'Proceed even without reaching the remote': 'Procedi anche senza raggiungere il remoto',
    "Don't regenerate the connector configurations": 'Non rigenerare le configurazioni dei connettori',
    'The recurring alignment cycle (never publishes)': 'Il ciclo ricorrente di allineamento (non pubblica mai)',
    'Download the data without regenerating derived files': 'Scarica i dati senza rigenerare i file derivati',
    'Check the configuration without writing anything': 'Controlla la configurazione senza scrivere niente',
    "Tell me if something's wrong": 'Dimmi se qualcosa non va',
    'List everything that was checked': 'Elenca tutto ciò che è stato controllato',
    'Also treat undetermined results as failures': 'Tratta come guasto anche ciò che non si può verificare',
    'Output in JSON format': 'Output in formato JSON',
    'A one-line summary': 'Una riga di riepilogo',
    'Apply the available automatic remedies': 'Applica i rimedi automatici disponibili',
    'Update the engine, with confirmation': 'Aggiorna il motore, con conferma',
    "Show me what's moved upstream": 'Mostrami cosa si è mosso a monte',
    "What's installed on this machine, without touching anything": "Cosa c'è installato su questa macchina, senza toccare niente",
    'Show the resolved remote configuration': 'Mostra la configurazione dei remoti risolta',
    'Liveness beat and maintenance tasks (internal use)': 'Battito di liveness e manutenzioni (uso interno)',
    'Alert for a guard unit that failed to start (internal use)': "Allarme per un'unità di guardia non partita (uso interno)",
    'a guard unit': "un'unità di guardia",
    'Diagnose and alert only on failures (internal use)': 'Diagnostica e allerta solo sui guasti (uso interno)',
    'Heartbeat: {status} ({message})': 'Battito: {status} ({message})',
    'active': 'attivo',
    'stalled': 'fermo',
    'notify-failure: {summary} (no alert channel configured)': 'notify-failure: {summary} (nessun canale di allarme configurato)',
    'Diagnostics complete (failures={failures}, ok={ok}).': 'Diagnostica completata (guasti={failures}, a posto={ok}).',
    '>>> MCP connectors per runtime': '>>> Connettori MCP per assistente',
    ' — optional, not mounted: {names}': ' — opzionali, non montati: {names}',
    'none': 'nessuno',
    '>>> Skills: manifest compared against the materialized library': '>>> Skill: manifest a confronto con la libreria materializzata',
    '  {materialized} materialized, {declared} declared': '  {materialized} materializzate, {declared} dichiarate',
    '  outside the manifest (kept, never deleted): {names}': '  fuori manifest (conservate, mai cancellate): {names}',
    '  declared but not materialized yet: {names}': '  dichiarate ma non ancora materializzate: {names}',
    '>>> Instructions per runtime': '>>> Istruzioni per assistente',
    ">>> Runtimes' native memories": '>>> Memorie native degli assistenti',
    'Read-only: nothing was modified.': 'Sola lettura: niente è stato modificato.',
    '{count} durable facts in {path}': '{count} fatti durevoli in {path}',
    'no native memory': 'nessuna memoria nativa',
    '{count} transcript files in {path} (to be distilled, not structured memory)': '{count} file di trascrizione in {path} (da distillare, non memoria strutturata)',
    'no transcripts': 'nessuna trascrizione',
    'Find, show, and materialize skills': 'Trova, mostra e materializza le skill',
    'List the managed skills': 'Elenca le skill gestite',
    'Search for a skill by name or description': 'Cerca una skill per nome o descrizione',
    'One or more terms, ANDed together': 'Uno o più termini, in AND',
    "Show a skill's body": 'Mostra il corpo di una skill',
    "Print a skill's file path": 'Stampa il percorso del file di una skill',
    'Materialize the declared skills': 'Materializza le skill dichiarate',
    'Quarantine the inherited views': 'Metti in quarantena le viste ereditate',
    'Check the manifest without writing anything': 'Controlla il manifest senza scrivere niente',
    'Regenerate the skill catalog': 'Rigenera il catalogo delle skill',
    'Raise guardian-vetted third-party pins with one approval': 'Alza i pin di terze parti già controllati, con un solo sì',
    'Verdicts are stale or missing, recomputing them now (bounded network checks)...': 'I verdetti sono vecchi o mancano, li ricalcolo adesso (controlli di rete brevi)...',
    'No verdicts available right now (offline?). Nothing moved.': 'Nessun verdetto al momento (offline?). Non ho mosso niente.',
    'Nothing vetted to raise. {held} items stay held.': 'Niente da alzare. {held} voci restano ferme.',
    'These {count} updates passed the deterministic checks:': 'Questi {count} aggiornamenti hanno passato i controlli:',
    '{held} items stay held (never touched by this command):': '{held} voci restano ferme (questo comando non le tocca):',
    'Raise these {count} pins? [s/N] ': 'Alzo questi {count} pin? [s/N] ',
    'Nothing moved.': 'Non ho mosso niente.',
    "'{what}': pin already moved, skipped": "'{what}': pin già mosso, saltato",
    "'{what}': pin {old} -> {new}": "'{what}': pin {old} -> {new}",
    "'{what}': {old} -> {new}": "'{what}': {old} -> {new}",
    "'{what}': no precise target, skipped": "'{what}': bersaglio impreciso, saltato",
    "'{what}': upstream-owned, update it with its installer": "'{what}': è del suo installatore, aggiornalo con quello",
    'cannot back up {path}, nothing written': 'non riesco a fare il backup di {path}, non ho scritto niente',
    'manifests invalid after the edit, restore from {backups}': 'manifest non validi dopo la modifica, ripristina da {backups}',
    'Backups kept next to the manifests: {backups}': 'Backup tenuti accanto ai manifest: {backups}',
    "'{what}': held back, '{other}' shares the pin and is not approved": "'{what}': fermo, '{other}' condivide il pin e non è approvato",
    'edits rolled back, manifests restored from {backups}': 'modifiche annullate, manifest ripristinati da {backups}',
    'Another bump is already running, retry in a minute.': 'Un altro bump è già in corso, riprova tra un minuto.',
    '{count} identical-bytes pins already moved on their own:': '{count} pin identici già mossi da soli:',
    'write failed ({error}), manifests restored from backups': 'scrittura fallita ({error}), manifest ripristinati dai backup',
    'write failed ({error}), manifests untouched': 'scrittura fallita ({error}), manifest intoccati',
    'install failed, pins rolled back to retry next round': 'installazione fallita, pin ripristinati: ci riprovo al prossimo giro',
    'could not restore {path} from {backup}': 'non ho potuto ripristinare {path} da {backup}',
    'The connector services, on this machine': 'I servizi dei connettori, su questa macchina',
    'Start the services and configure the connectors': 'Avvia i servizi e configura i connettori',
    'Only some services, instead of all of them': 'Solo alcuni servizi, invece di tutti',
    'Stop the services, leaving the data intact': 'Ferma i servizi, lasciando intatti i dati',
    'Which services are responding': 'Quali servizi stanno rispondendo',
    "The last lines of a service that won't start": 'Le ultime righe di un servizio che non parte',
    'Connector variables written to {target}': 'Variabili dei connettori scritte in {target}',
    'Tools shipped with the engine': 'Utensili distribuiti con il motore',
    'Configure user autostart': 'Configura avvio automatico utente',
    'Install the shell startup notice': "Installa l'avviso all'avvio della shell",
    'With an install flag: remove instead of installing': "Con un flag d'installazione: rimuovi invece di installare",
    'With shell hook: only this shell': 'Con hook shell: solo questa shell',
    'Update notice lanes not ensured: {error}': 'Avvisi di update non garantiti: {error}',
    'The trustworthy local time, with sync status': "L'ora locale attendibile, con lo stato della sincronizzazione",
    "Open a folder in the system's file manager": 'Apri una cartella nel gestore file del sistema',
    'Start or revive Chrome on the debug port': 'Avvia o rianima Chrome sulla porta di debug',
    'Search and scraping via the local instance': "Ricerca e scraping tramite l'istanza locale",
    'Convene a review across models from different vendors': 'Convoca una revisione fra modelli di vendor diversi',
    'Commands on memory (the Vault)': 'Comandi sulla memoria (il Vault)',
    'Publish the durable work': 'Pubblica il lavoro durevole',
    'Commit message': 'Messaggio di commit',
    'Specific files to publish': 'File specifici da pubblicare',
    'Consolidate notes: preview by default, apply with confirmation': 'Consolida le note: anteprima di default, applica con conferma',
    'Map the structure of the notes': 'Mappa la struttura delle note',
    "Analyze notes' freshness and lifecycle": 'Analizza freschezza e ciclo di vita delle note',
    "Publish the durable work (alias for 'vault push')": "Pubblica il lavoro durevole (alias di 'vault push')",
    'MCP and Skill configurations valid': 'Configurazioni MCP e Skill valide',
    'Preflight failed: {error}': 'Preflight fallito: {error}',
    'Updated instruction pointer {path}': 'Aggiornato puntatore istruzioni {path}',
    '{label} instructions restored to canonical': 'Istruzioni di {label} riportate al canonico',
    'opencode instructions restored to canonical': 'Istruzioni di opencode riportate al canonico',
    'local-model: relinked local-model-agent.ps1': 'local-model: relinkato local-model-agent.ps1',
    'local-model: copied local-model-agent.ps1': 'local-model: copiato local-model-agent.ps1',
    'local-model: installed wrapper {name}': 'local-model: installato wrapper {name}',
    'runtime-permissions: could not read {path} ({error})': 'runtime-permissions: impossibile leggere {path} ({error})',
    'runtime-permissions: the root of {path} is not a map': 'runtime-permissions: la radice di {path} non è una mappa',
    'runtime-permissions: {name} escapes permissions/, guardrail rejected': 'runtime-permissions: {name} esce da permissions/, guardrail rifiutato',
    'runtime-permissions: guardrail body missing ({path})': 'runtime-permissions: corpo del guardrail mancante ({path})',
    'Operation blocked by Git: {reason}': 'Operazione bloccata da Git: {reason}',
    'Error during automatic update: {reason}': "Errore durante l'aggiornamento automatico: {reason}",
    'Data state: {status}': 'Stato dati: {status}',
    'Pull completed': 'Pull completato',
    'MCP configurations not regenerated (explicitly requested)': 'Configurazioni MCP non rigenerate (richiesto esplicitamente)',
    'Commands not realigned: {error}': 'Comandi non riallineati: {error}',
    'Startup self-alignment configured': "Auto-allineamento all'avvio configurato",
    'Self-alignment configuration did not succeed: {error}': 'Configurazione auto-allineamento non riuscita: {error}',
    'Error during the alignment operation: {error}': "Errore durante l'operazione di allineamento: {error}",
    'Data updated successfully via fast-forward from {ref}': 'Dati aggiornati con successo tramite fast-forward da {ref}',
    'Fast-forward failed: {error}': 'Fast-forward fallito: {error}',
    "a git rebase is in progress in the vault: run 'git rebase --abort' before continuing": "un rebase git è in corso nel vault: esegui 'git rebase --abort' prima di procedere",
    "a git merge is in progress in the vault: run 'git merge --abort' before continuing": "un merge git è in corso nel vault: esegui 'git merge --abort' prima di procedere",
    'Local-Only mode': 'Modalità Local-Only',
    'detached HEAD': 'HEAD staccato (detached)',
    "Current branch '{found}', expected '{expected}'": "Branch corrente '{found}', atteso '{expected}'",
    'Unsaved changes on {count} tracked files': 'Modifiche non salvate su {count} file tracciati',
    'Offline manually allowed (remote missing)': 'Offline consentito manualmente (remoto assente)',
    "Authoritative remote '{remote}' not configured": "Remoto autoritativo '{remote}' non configurato",
    'Offline manually allowed': 'Offline consentito manualmente',
    'Could not reach remote {ref}: {detail}': 'Impossibile raggiungere il remoto {ref}: {detail}',
    'Could not reach remote {ref}': 'Impossibile raggiungere il remoto {ref}',
    "Local branch '{branch}' not found": "Branch locale '{branch}' non trovato",
    "Local branch '{branch}' is not on {remote} yet": "Il branch locale '{branch}' non è ancora presente su {remote}",
    'Error computing merge-base with {ref}': 'Errore nel calcolo merge-base con {ref}',
    'Data already aligned': 'Dati già allineati',
    'Remote {remote} has new commits (update available)': 'Il remoto {remote} ha nuovi commit (aggiornamento disponibile)',
    'Local branch has commits not yet sent to {remote}': 'Il branch locale ha commit non ancora inviati a {remote}',
    'Local branch has diverged from {remote} (manual resolution required)': 'Il branch locale è divergente rispetto a {remote} (risoluzione manuale richiesta)',
    'git add failed: {error}': 'git add fallito: {error}',
    'git commit failed: {error}': 'git commit fallito: {error}',
    'Could not push: local branch is behind {remote}': 'Impossibile inviare: il branch locale è indietro rispetto a {remote}',
    'Push to {remote} failed: {error}': 'Push su {remote} fallito: {error}',
    'Data has diverged from {remote}, and there are uncommitted changes ({files}). Not realigning automatically: commit them or stash them, then retry': 'Dati divergenti rispetto a {remote}, e ci sono modifiche non committate ({files}). Non riallineo da solo: committale o mettile da parte, poi riprova',
    'Push after rebase failed: {error}': 'Push dopo rebase fallito: {error}',
    'Data has diverged from {remote}, automatic rebase did not succeed': 'Dati divergenti rispetto a {remote}, rebase automatico non riuscito',
    'Failed to create quarantine branch {branch}: {error}': 'Creazione branch di quarantena {branch} fallita: {error}',
    'Created {q_branch}, but reset to {remote}/{branch} failed: {error}': 'Creato {q_branch}, ma il reset su {remote}/{branch} è fallito: {error}',
    'Created {q_branch}, but could not switch to it: {error}': 'Creato {q_branch}, ma non è stato possibile spostarcisi: {error}',
    'Created {q_branch}, but could not preserve uncommitted changes before realignment': 'Creato {q_branch}, ma non è stato possibile salvare le modifiche non committate prima del riallineamento',
    "An update was refused": "Un aggiornamento è stato rifiutato",
    "A new release is not signed by a key this install trusts, so nothing was installed.": "Una nuova versione non è firmata da una chiave di cui questa installazione si fida, quindi non è stato installato nulla.",
    "Run 'nexgen-update --check' and look at the release before doing anything else.": "Lancia 'nexgen-update --check' e guarda la versione prima di fare altro.",
    "An automatic update failed and was undone": "Un aggiornamento automatico è fallito ed è stato annullato",
    "The machine is back on the version that worked. That release is skipped until you update by hand.": "La macchina è tornata alla versione che funzionava. Quella versione viene saltata finché non aggiorni a mano.",
    "Run 'nexgen-update' interactively to see why it failed.": "Lancia 'nexgen-update' di persona per vedere perché è fallito.",
    "An automatic update failed and could not be undone": "Un aggiornamento automatico è fallito e non si è potuto annullare",
    'The engine may be half-updated. It was left as it is.': "Il motore potrebbe essere aggiornato a metà. È stato lasciato com'era.",
    "Run 'nexgen doctor --summary' and follow the recovery printed by 'nexgen-update'.": "Lancia 'nexgen doctor --summary' e segui il ripristino stampato da 'nexgen-update'.",
    'Skill materialization failed (see the [ERROR] lines above).': 'La materializzazione delle skill è fallita (vedi le righe [ERROR] qui sopra).',
    'MCP rendering failed ({error}): fix the CLI config and re-run': 'Il rendering MCP è fallito ({error}): correggi la config della CLI e rilancia',
    "Phase '{phase}' failed: {error}": "La fase '{phase}' è fallita: {error}",
    'Alignment ran every phase, but these failed: {phases} (see actions above)': "L'allineamento ha eseguito tutte le fasi, ma queste sono fallite: {phases} (vedi le azioni qui sopra)",
    '(last cycle: these phases failed: {phases})': '(ultimo ciclo: queste fasi sono fallite: {phases})',
    'The last guard cycle completed every phase.': "L'ultimo ciclo del guard ha completato tutte le fasi.",
    'The last guard cycle could not complete these phases: {phases}.': "L'ultimo ciclo del guard non è riuscito a completare queste fasi: {phases}.",
    "Run 'nexgen sync apply' and read the lines marked [ERROR] to see why.": "Lancia 'nexgen sync apply' e leggi le righe marcate [ERROR] per capire perché.",
    "Automatic remedies were skipped: another sync is running on this machine.": "Le riparazioni automatiche sono state saltate: su questa macchina è in corso un'altra sincronizzazione.",
    "Wait for it to finish, then run 'nexgen doctor --fix' again.": "Aspetta che finisca, poi rilancia 'nexgen doctor --fix'.",
    "Not published: {detail} Reconcile the quarantine branch ('nexgen vault quarantine --diff <branch>'), then publish again.": "Non pubblicato: {detail} Riconcilia il branch di quarantena ('nexgen vault quarantine --diff <branch>'), poi pubblica di nuovo.",
    "Diverged local commits moved to quarantine branch '{q_branch}'. Local branch reset to {remote}/{branch}.": "I commit locali divergenti sono stati spostati nel branch di quarantena '{q_branch}'. Branch locale reimpostato su {remote}/{branch}.",
    'Realigned with {remote}/{branch} via rebase': 'Riallineato con {remote}/{branch} tramite rebase',
    'Error during divergence resolution: {reason}': 'Errore durante la risoluzione della divergenza: {reason}',
    'No quarantine branches in the Vault': 'Nessun branch di quarantena nel Vault',
    'Warnings (nothing is blocked):': 'Avvisi (niente è bloccato):',
    'Found {count} quarantine branch(es) with isolated diverged changes: {branches}': 'Trovati {count} branch di quarantena con modifiche divergenti isolate: {branches}',
    "Review diff with 'git diff main..{branch}', reconcile changes into canonical files, then remove the quarantine branch with 'git branch -D {branch}'.": "Verifica il diff con 'git diff main..{branch}', integra le modifiche nei file canonici, poi elimina il branch di quarantena con 'git branch -D {branch}'.",
    'No guard cycle has been recorded yet': 'Nessun ciclo di guardia precedentemente registrato',
    'The sync cycle has been stalled for {hours:.1f} hours.': 'Il ciclo di sincronizzazione è fermo da {hours:.1f} ore.',
    'Agent sync is not running': 'Sincronizzazione agente non attiva',
    "Run 'agent-sync apply' in the terminal to check the status.": "Esegui 'agent-sync apply' sul terminale per verificare lo stato.",
    'Guard active (last completed {minutes:.0f} minutes ago)': 'Guard attivo (ultimo completamento {minutes:.0f} minuti fa)',
    'Error reading liveness: {error}': 'Errore lettura liveness: {error}',
    "The skills manifest doesn't exist: {path}": 'Il manifest delle skill non esiste: {path}',
    "'{name}': the name can't become a folder": "'{name}': il nome non può diventare una cartella",
    "'{name}': unknown origin '{origin}'": "'{name}': origine '{origin}' sconosciuta",
    "'{name}': unknown exposure '{exposure}'": "'{name}': esposizione '{exposure}' sconosciuta",
    "'{name}': unknown runtimes {runtimes}": "'{name}': runtime sconosciuti {runtimes}",
    "'{name}': github origin without 'repo'": "'{name}': origine github senza 'repo'",
    "'{name}': github origin without 'commit'": "'{name}': origine github senza 'commit'",
    "'{name}': pin '{commit}' is not a full 40-character commit": "'{name}': il pin '{commit}' non è un commit completo a 40 caratteri",
    "'{name}': installer origin without 'version'": "'{name}': origine installer senza 'version'",
    "'{name}': missing SKILL.md file under {path}": "'{name}': manca il file SKILL.md sotto {path}",
    "Linked skill '{name}' into the library": "Collegata skill '{name}' alla libreria",
    "github skill '{name}': pin '{commit}' is not a full 40-character commit, skipping the entry": "Skill github '{name}': il pin '{commit}' non è un commit completo a 40 caratteri, salto la voce",
    "github skill '{name}': cloning {repo} failed: {error}": "Skill github '{name}': clonazione di {repo} fallita: {error}",
    "github skill '{name}': commit {commit} is not reachable in the repository: {error}": "Skill github '{name}': il commit {commit} non è raggiungibile nel repository: {error}",
    "github skill '{name}': {repo} did not respond within {timeout}s, retrying next cycle": "Skill github '{name}': {repo} non ha risposto entro {timeout}s, riprovo al giro successivo",
    "github skill '{name}': {error}": "Skill github '{name}': {error}",
    "github skill '{name}': path '{path}' escapes the cloned repository, skipping the entry": "Skill github '{name}': il percorso '{path}' esce dal repository clonato, salto la voce",
    "Linked github skill '{name}' into the library": "Collegata skill github '{name}' alla libreria",
    "Created active view '{name}' for {target}": "Creata vista attiva '{name}' per {target}",
    '{prefix}: managed view kept': '{prefix}: vista gestita mantenuta',
    '{prefix}: destination already exists, view left untouched': '{prefix}: destinazione già esistente, vista lasciata intatta',
    '{prefix}: would be quarantined outside the discovery roots': '{prefix}: sarebbe messa in quarantena fuori dalle root discovery',
    '{prefix}: quarantined outside the discovery roots': '{prefix}: messa in quarantena fuori dalle root discovery',
    'Usage: agent-skill [list|find|show|path] <arguments>\n       skills-sync [apply|index|validate] [--migrate-legacy]': 'Uso: agent-skill [list|find|show|path] <argomenti>\n     skills-sync [apply|index|validate] [--migrate-legacy]',
    '{failed} of {total} skills were not synced.': '{failed} skill su {total} non sono state sincronizzate.',
    'Skills synced successfully ({count} changes applied).': 'Skill sincronizzate con successo ({count} modifiche applicate).',
    'Skills manifest: {count} problems.': 'Manifest delle skill: {count} problemi.',
    'Skills manifest: no problems.': 'Manifest delle skill: nessun problema.',
    'Index generated at {path}': 'Indice generato in {path}',
    'Usage: agent-skill find <term> [term...]': 'Uso: agent-skill find <termine> [termine...]',
    'No managed skill matches: {terms}': 'Nessuna skill gestita corrisponde a: {terms}',
    'Usage: agent-skill {cmd} <skill-name>': 'Uso: agent-skill {cmd} <nome-skill>',
    "'{name}' is not a valid skill name: letters, digits, dots, hyphens and underscores are allowed.": "'{name}' non è un nome di skill valido: sono ammessi lettere, cifre, punto, trattino e trattino basso.",
    'Unrecognized command: {cmd}\nUsage: agent-skill [list|find|show|path] or skills-sync [apply|index|validate] [--migrate-legacy]': 'Comando non riconosciuto: {cmd}\nUso: agent-skill [list|find|show|path] o skills-sync [apply|index|validate] [--migrate-legacy]',
    "Skill '{name}' not available: the skills manifest doesn't exist yet ({path}). Align this machine first.": "Skill '{name}' non disponibile: il manifest delle skill non esiste ancora ({path}). Esegui prima l'allineamento di questa macchina.",
    "Skill '{name}' is declared in the manifest but hasn't been materialized yet. Run 'skills-sync apply'.": "Skill '{name}' è dichiarata nel manifest ma non è ancora stata materializzata. Esegui 'skills-sync apply'.",
    "Skill '{name}' is not declared in the skills manifest.": "Skill '{name}' non è dichiarata nel manifest delle skill.",
    '{name} not present: nothing to restore for {cli}.': '{name} non presente: niente da ripristinare per {cli}.',
    'no backup {pattern} found: nothing to restore.': 'nessun backup {pattern} trovato: niente da ripristinare.',
    "backup {name} doesn't parse ({error}); refusing to restore a broken file.": 'il backup {name} non si analizza ({error}); rifiuto di ripristinare un file rotto.',
    '{cli}: config already matches the most recent backup; nothing to restore.': "{cli}: la config e' gia' il backup piu' recente; nessun ripristino necessario.",
    'RESTORED {name} from {source}.': 'RIPRISTINATO {name} da {source}.',
    '{cli}: {name} not present -- already clean, nothing to reset.': "{cli}: {name} non presente -- gia' pulito, niente da resettare.",
    "{cli}'s writer can't recreate {name} from scratch if it gets removed; you'd reset with no way back short of render.py --revert {cli}. Nothing is touched.": "il writer di {cli} non puo' ricreare {name} da zero se viene rimosso; resetteresti senza via di ritorno se non render.py --revert {cli}. Niente viene toccato.",
    'RESET {cli}: removed {name} (backup {backup}). Undo with: render.py --revert {cli}. Reprovision clean with: agent-sync apply (or render.py --write {cli}).': 'RESET {cli}: rimosso {name} (backup {backup}). Undo con: render.py --revert {cli}. Riprovisiona pulito con: agent-sync apply (o render.py --write {cli}).',
    '{name} is not valid JSON/TOML ({error}). Restore a .bak-* backup before retrying.': "{name} non e' JSON/TOML valido ({error}). Ripristina un backup .bak-* prima di riprovare.",
    'invalid MCP manifest ({error}).': 'manifest MCP non valido ({error}).',
    '{cli} config not present (not installed, or never launched): nothing to adopt.': '{cli} config non presente (non installata, o mai avviata): niente da adottare.',
    '{cli}: every live server is already in the manifest -- nothing to adopt.': "{cli}: ogni server vivo e' gia' nel manifest -- niente da adottare.",
    'a server carries a literal secret (<AUTH>). Convert it to an env-var reference before adopting.': 'un server porta un segreto letterale (<AUTH>). Convertilo in riferimento env-var prima di adottare.',
    'could not read the manifest ({error}).': 'impossibile leggere il manifest ({error}).',
    "could not find the top-level 'servers:' block; add the entries by hand.": "impossibile trovare il blocco top-level 'servers:'; aggiungi le voci a mano.",
    'the adopted entries broke the manifest ({error}); restored the original.': "le voci adottate hanno rotto il manifest ({error}); ripristinato l'originale.",
    'adopted {count} servers into the manifest: {names}. Backup: {backup}. Review and commit.': 'adottati {count} server nel manifest: {names}. Backup: {backup}. Rivedi e committa.',
    '{cli}: {count} servers in the live config but NOT in the manifest.': '{cli}: {count} server nella config viva ma NON nel manifest.',
    "manifest.yaml STUB below -- review it, adjust it, then place it under 'servers:'. Secrets shown as <AUTH>.": "STUB manifest.yaml di seguito -- rivedi, aggiusta, poi metti sotto 'servers:'. Segreti mostrati come <AUTH>.",
    "Rerun with --apply to add them under 'servers:' (backup + re-validation).": "Rilancia con --apply per aggiungerli sotto 'servers:' (backup + ri-validazione).",
    '(none)': '(nessuno)',
    "Usage: render.py [--write CLI|--revert CLI|--reset CLI|--adopt CLI [--apply]|--inventory]\n  --write CLI      regenerate the CLI's MCP section from the manifest\n  --revert CLI     restore the CLI's config from its most recent backup\n  --reset CLI      backup + remove the config (antigravity/opencode only)\n  --adopt CLI      manifest stub for live servers outside the manifest (--apply to apply)\n  --inventory      list the MCP servers per CLI (read-only)": "Uso: render.py [--write CLI|--revert CLI|--reset CLI|--adopt CLI [--apply]|--inventory]\n  --write CLI      rigenera la sezione MCP della CLI dal manifest\n  --revert CLI     ripristina la config della CLI dal backup piu' recente\n  --reset CLI      backup + rimozione della config (solo antigravity/opencode)\n  --adopt CLI      stub manifest per i server vivi fuori manifest (--apply per applicare)\n  --inventory      elenca i server MCP per CLI (read-only)",
    'render.py: unknown argument: {arg}': 'render.py: argomento sconosciuto: {arg}',
    'Claude configuration updated': 'Configurazione Claude aggiornata',
    "The connectors of {agent} come from Claude Code's own configuration.": "I connettori di {agent} arrivano dalla configurazione nativa di Claude Code.",
    "Claude Code's own MCP configuration was not rendered: {error}": "Non ho potuto scrivere la configurazione MCP nativa di Claude Code: {error}",
    "An agent runs on Claude Code's CLI, but its connectors are not where they belong: {problems}.": "Un agente gira sulla CLI di Claude Code, ma i suoi connettori non sono dove dovrebbero: {problems}.",
    "still on the loopback server: {connectors}": "ancora sul server di loopback: {connectors}",
    "missing from Claude Code's own configuration: {connectors}": "mancanti dalla configurazione nativa di Claude Code: {connectors}",
    "Claude Code's own configuration does not exist ({path})": "la configurazione nativa di Claude Code non esiste ({path})",
    "Run 'nexgen openclaw setup': it denies every connector on the loopback server and renders Claude Code's own configuration, where they load lazily.": "Lancia 'nexgen openclaw setup': nega ogni connettore sul server di loopback e scrive la configurazione nativa di Claude Code, dove vengono caricati in modo lazy.",
    "The {count} agent(s) on Claude Code's CLI keep every connector off the loopback server, and Claude Code's own configuration carries them.": "I {count} agenti sulla CLI di Claude Code tengono ogni connettore fuori dal server di loopback, e la configurazione nativa di Claude Code li porta.",
    'Antigravity configuration updated': 'Configurazione Antigravity aggiornata',
    'OpenCode configuration updated': 'Configurazione OpenCode aggiornata',
    'Codex configuration updated': 'Configurazione Codex aggiornata',
    'OpenClaw configuration updated': 'Configurazione OpenClaw aggiornata',
    'OpenClaw configuration not present: nothing rendered for it': 'Configurazione OpenClaw assente: non ho scritto nulla per lei',
    'OpenClaw instructions at {path} left untouched ({error})': 'Istruzioni OpenClaw in {path} lasciate come sono ({error})',
    "OpenClaw instructions at {path}: the managed block's markers are incomplete, left untouched": 'Istruzioni OpenClaw in {path}: i marcatori del blocco gestito sono incompleti, ho lasciato tutto come sta',
    "OpenClaw instructions at {path}: the agents sharing this workspace declare different AGENTS.md files, so the manual was left as it is": "Istruzioni OpenClaw in {path}: gli agenti che condividono questa cartella dichiarano AGENTS.md diversi, quindi ho lasciato il manuale com'era",
    'OpenClaw instructions at {path} left untouched: safety backup failed ({error})': 'Istruzioni OpenClaw in {path} lasciate come sono: la copia di sicurezza non e\' riuscita ({error})',
    'OpenClaw instructions aligned in {path}': 'Istruzioni OpenClaw allineate in {path}',
    "Could not read OpenClaw's instructions at {path}: {error}": 'Non ho potuto leggere le istruzioni OpenClaw in {path}: {error}',
    "OpenClaw's AGENTS.md is not aligned in {count} workspace(s): {paths}.": "L'AGENTS.md di OpenClaw non e' allineato in {count} cartella di lavoro: {paths}.",
    "Run 'nexgen sync' to rewrite the managed block; the text outside it is yours and is never touched.": "Lancia 'nexgen sync' per riscrivere il blocco gestito; il testo fuori dal blocco e' tuo e non viene mai toccato.",
    "Workspace folders OpenClaw declares but don't exist yet: {paths}": 'Cartelle di lavoro dichiarate da OpenClaw ma non ancora esistenti: {paths}',
    "OpenClaw declares workspace folder(s) that don't exist yet: {paths}.": 'OpenClaw dichiara cartelle di lavoro che non esistono ancora: {paths}.',
    "Run 'nexgen openclaw setup' to create the agent workspace and its instructions.": "Lancia 'nexgen openclaw setup' per creare la cartella di lavoro dell'agente e le sue istruzioni.",
    'OpenClaw reads the canonical AGENTS.md from {count} agent workspace(s)': "OpenClaw legge l'AGENTS.md canonico da {count} cartella di lavoro",
    "OpenClaw's guardrail plugin is not deposited in {path}.": "Il plugin del guardrail di OpenClaw non e' depositato in {path}.",
    "Run 'nexgen sync' to deposit it; enabling it is a separate step, 'nexgen openclaw setup'.": "Lancia 'nexgen sync' per depositarlo; abilitarlo e' un passo a parte, 'nexgen openclaw setup'.",
    "OpenClaw's configuration could not be read, so the plugin's state is unknown: {error}": "Non ho potuto leggere la configurazione di OpenClaw, quindi lo stato del plugin e' ignoto: {error}",
    "Fix the JSON in OpenClaw's own configuration file, then run 'nexgen doctor' again.": "Sistema il JSON nel file di configurazione di OpenClaw, poi rilancia 'nexgen doctor'.",
    "OpenClaw's guardrail plugin is deposited but not enabled ({reason}).": "Il plugin del guardrail di OpenClaw e' depositato ma non abilitato ({reason}).",
    "Run 'nexgen openclaw setup' to enable it with the gateway stopped: a plugins.* change on a running gateway breaks it. Until then OpenClaw's shell commands are not checked.": "Lancia 'nexgen openclaw setup' per abilitarlo con il gateway fermo: cambiare plugins.* a gateway acceso lo rompe. Fino ad allora i comandi shell di OpenClaw non vengono controllati.",
    "OpenClaw's guardrail plugin is deposited and enabled": "Il plugin del guardrail di OpenClaw e' depositato e abilitato",
    'it is in plugins.deny': 'sta in plugins.deny',
    'plugins.entries.{id}.enabled is false': 'plugins.entries.{id}.enabled e\' false',
    'plugins.allow names other plugins only': 'plugins.allow elenca solo altri plugin',
    'install Homebrew (https://brew.sh), then: brew install': 'installa Homebrew (https://brew.sh), poi: brew install',
    "your system's package manager": 'il gestore di pacchetti del tuo sistema',
    'needs Python {wanted} or later → {hint} python3': 'serve Python {wanted} o successivo → {hint} python3',
    'only needed to mount MCP connectors or skills installed via npx': 'serve solo per montare connettori MCP o skill installate da npx',
    'only needed if you keep encrypted secrets in 99-SECRETS/': 'serve solo se tieni segreti cifrati in 99-SECRETS/',
    'only needed for the full install on this machine (nexgen stack up)': "serve solo per l'installazione completa su questa macchina (nexgen stack up)",
    '{name}/ (created)': '{name}/ (creata)',
    'rerun without --check to create it': 'rilancia senza --check per crearla',
    'the clone looks incomplete: double-check you cloned the whole repository': 'il clone sembra incompleto: ricontrolla di aver clonato tutto il repository',
    '{count} commands installed in ~/.local/bin': '{count} comandi installati in ~/.local/bin',
    'commands not installed ({error})': 'comandi non installati ({error})',
    "The agents' sudo policy was not installed: visudo refuses this file ({error}).": "La policy sudo degli agenti non è stata installata: visudo rifiuta questo file ({error}).",
    "This install has no sudo without a password, so the agents' sudo policy was not installed.": "Questa installazione non ha sudo senza password, quindi la policy sudo degli agenti non è stata installata.",
    'The file is written and checked at {path}. As an admin, run this one command:': 'Il file è scritto e controllato in {path}. Come amministratore lancia questo unico comando:',
    'The file says:': 'Il file dice:',
    "The agents' sudo policy is already in force at {path}: nothing to change.": "La policy sudo degli agenti è già in vigore in {path}: non c'è niente da cambiare.",
    "The agents' sudo policy was not installed ({error}).": "La policy sudo degli agenti non è stata installata ({error}).",
    "The agents' sudo policy is in force at {path}: {count} commands, and nothing else.": "La policy sudo degli agenti è in vigore in {path}: {count} comandi, e nient'altro.",
    'exit code {code}': 'codice di uscita {code}',
    "The gateway is configured, but the agents' sudo policy is not in force: see the line above.": "Il gateway è configurato, ma la policy sudo degli agenti non è in vigore: guarda la riga qui sopra.",
    'How many CLIs will you use?': 'Quante CLI userai?',
    'How many machines do you want kept in sync?': 'Quante macchine vuoi tenere allineate?',
    'Where do the services live?': 'Dove vivono i servizi?',
    'N=none / H=here / S=on a server': 'N=nessuno / H=qui / S=su un server',
    'Checks prerequisites, prepares the vault, and says what the next step is.': 'Controlla i prerequisiti, prepara il vault e dice qual è il passo dopo.',
    'Checks only: no questions and no writes': 'Solo controlli: nessuna domanda e nessuna scrittura',
    'Vault root (default: the repository folder)': 'Radice del vault (default: la cartella del repository)',
    'NeXgen Engine · first run': 'NeXgen Engine · primo avvio',
    "A Git vault for your assistants' configuration and memory.": 'Un vault in Git per la configurazione e la memoria dei tuoi assistenti.',
    'Prerequisites': 'Prerequisiti',
    'Vault structure': 'Struttura del vault',
    'no CLI found': 'nessuna CLI trovata',
    "you need an assistant that can write files (Claude Code, Codex, OpenCode, Antigravity): a web chat can't do it": 'serve un assistente che sappia scrivere file (Claude Code, Codex, OpenCode, Antigravity): una chat web non può farlo',
    'Assistants found on this machine': 'Assistenti trovati su questa macchina',
    'Something required is missing.': 'Manca qualcosa di necessario.',
    'Fix it and rerun.': 'Sistemalo e rilancia.',
    'Which install do you want': 'Che installazione vuoi',
    'No file gets written: this is just a recommendation.': 'Nessun file viene scritto: è solo un consiglio.',
    'Profile:': 'Profilo:',
    'Services:': 'Servizi:',
    "the local services run here: 'nexgen stack up' starts them.": "i servizi locali girano qui: 'nexgen stack up' li avvia.",
    'the services live on a server: see 03-INFRA/deploy/.': 'i servizi stanno su un server: vedi 03-INFRA/deploy/.',
    'no services: native search, no remote automation.': 'nessun servizio: ricerca nativa, niente automazioni remote.',
    'Next step': 'Passo successivo',
    'Open {file} and paste its contents into a command-line assistant\n  opened in this folder. It will ask you a few questions and mount the\n  connectors and skills.': 'Apri {file} e incollane il contenuto in un assistente da riga di\n  comando aperto in questa cartella. Ti farà qualche domanda e monterà\n  connettori e skill.',
    'Then, to verify at any time: nexgen doctor': 'Poi, per verificare in qualunque momento: nexgen doctor',

    # --- checks/, runtimes/, stack/, tools/, vault/ (this pass) -------------
    # --- checks/env_checks.py ---
    "State directory '{state_dir}' does not exist.": "La cartella di stato '{state_dir}' non esiste.",
    "Create the directory with: mkdir -p {state_dir}": "Crea la cartella con: mkdir -p {state_dir}",
    "State directory present ({state_dir})": "Cartella di stato presente ({state_dir})",
    "Knowledge Vault folder '{vault_path}' was not found.": "La cartella del Knowledge Vault '{vault_path}' non è stata trovata.",
    "Check the KNOWLEDGE_VAULT_PATH environment variable or clone the Vault.": "Controlla la variabile d'ambiente KNOWLEDGE_VAULT_PATH oppure clona il Vault.",
    "Knowledge Vault found at {vault_path}": "Knowledge Vault trovato in {vault_path}",

    # --- checks/git_checks.py ---
    "Data state aligned ({message})": "Stato dati allineato ({message})",
    "There are unsaved changes in the Vault ({count} files).": "Ci sono modifiche non salvate nel Vault ({count} file).",
    "Stage them first ('git add <file>'), then run 'vault-push' to publish them.": "Prima mettile in stage ('git add <file>'), poi esegui 'vault-push' per pubblicarle.",
    "There is an interrupted merge or rebase in the Vault.": "C'è un'operazione di merge o rebase interrotta nel Vault.",
    "Run 'git rebase --abort' or 'git merge --abort' inside the Vault.": "Esegui 'git rebase --abort' oppure 'git merge --abort' dentro il Vault.",
    "Could not check alignment with the remote server: {message}": "Non riesco a verificare l'allineamento con il server remoto: {message}",
    "Check the internet connection or the remote configuration.": "Controlla la connessione internet o la configurazione del remoto.",
    "There are commits unpublished in the Vault for more than {age_hours}h (the oldest exceeds the {threshold_hours}h threshold).": "Ci sono commit non pubblicati nel Vault da più di {age_hours}h (il più vecchio supera la soglia di {threshold_hours}h).",
    "Run 'vault-push' to publish them.": "Esegui 'vault-push' per pubblicarli.",
    "The local Vault has new commits ready to be pushed.": "Il Vault locale ha nuovi commit pronti per essere inviati.",
    "Remote {remote} has new commits not yet downloaded.": "Il remoto {remote} ha nuovi commit non ancora scaricati.",
    "Run 'agent-sync apply' or 'agent-sync pull' to align the Vault.": "Esegui 'agent-sync apply' oppure 'agent-sync pull' per allineare il Vault.",
    "Git misalignment in the Vault: {message}": "Disallineamento Git nel Vault: {message}",
    "Run 'agent-sync apply' to check and attempt alignment.": "Esegui 'agent-sync apply' per verificare e tentare l'allineamento.",
    "Could not check the alignment of mirror '{mirror}': the authoritative remote '{remote}' is unreachable.": "Non riesco a verificare l'allineamento del mirror '{mirror}': il remoto autoritativo '{remote}' non è raggiungibile.",
    "Mirror '{mirror}' is unreachable, so its alignment could not be checked.": "Il mirror '{mirror}' non è raggiungibile, quindi non è stato possibile verificarne l'allineamento.",
    "Mirror '{mirror}' aligned with authoritative remote '{remote}'": "Mirror '{mirror}' allineato al remoto autoritativo '{remote}'",
    "Mirror '{mirror}' is not aligned with the branch published on '{remote}'.": "Il mirror '{mirror}' non è allineato al branch pubblicato su '{remote}'.",
    "Run 'git push {mirror} {branch}' from the Vault to realign it (the canonical Vault remains {remote}).": "Esegui 'git push {mirror} {branch}' dal Vault per riallinearlo (il Vault canonico resta {remote}).",

    # --- checks/identity_checks.py ---
    "The assistant's personal space does not exist on this machine yet.": "Lo spazio personale dell'assistente non esiste ancora su questa macchina.",
    "No action needed, unless you want to create it.": "Non serve fare niente, a meno che tu non voglia crearlo.",
    "The assistant's personal space exists but cannot be read.": "Lo spazio personale dell'assistente esiste ma non riesco a leggerlo.",
    "Check the permissions on {self_file}": "Controlla i permessi di {self_file}",
    "Personal space present and readable": "Spazio personale presente e leggibile",
    "No frontmatter to check": "Nessun frontmatter da controllare",
    "Could not read the personal space to check its metadata.": "Non riesco a leggere lo spazio personale per controllarne i metadati.",
    "The personal space has no metadata header.": "Lo spazio personale non ha l'intestazione dei metadati.",
    "Add a '---' block with 'status:' at the top of {filename}": "Aggiungi un blocco '---' con 'status:' in cima a {filename}",
    "The personal space is missing {fields} in its metadata.": "Allo spazio personale manca {fields} nei metadati.",
    "Add '{field}:' to the header of {filename}": "Aggiungi '{field}:' nell'intestazione di {filename}",
    "Personal space metadata is in order": "Metadati dello spazio personale a posto",
    "No native memory alongside the Vault": "Nessuna memoria nativa in parallelo al Vault",
    "A runtime keeps a memory of its own alongside the Vault: {found}. Two separate memories end up saying different things.": "Un runtime tiene una memoria propria accanto al Vault: {found}. Due memorie separate finiscono per dire cose diverse.",
    "Some runtimes keep a memory of their own alongside the Vault: {found}. Two separate memories end up saying different things.": "Alcuni runtime tengono una memoria propria accanto al Vault: {found}. Due memorie separate finiscono per dire cose diverse.",
    "nexgen inventory   # to see what it holds before deciding": "nexgen inventory   # per vedere cosa contiene prima di decidere",

    # --- checks/instructions_checks.py ---
    "The canonical instructions file does not exist ({canon}).": "Il file di istruzioni canonico non esiste ({canon}).",
    "Restore 03-INFRA/agent-universal-layer/instructions/AGENTS.md from the Vault.": "Ripristina 03-INFRA/agent-universal-layer/instructions/AGENTS.md dal Vault.",
    "Canonical instructions file AGENTS.md present": "File di istruzioni canonico AGENTS.md presente",
    "~/CLAUDE.md must be a plain-text pointer file, not missing or a symlink ({claude_file}).": "~/CLAUDE.md deve essere un file di testo puntatore, non assente o un symlink ({claude_file}).",
    "Write a short pointer referencing {canon} into {claude_file}.": "Scrivi in {claude_file} un breve puntatore che rimanda a {canon}.",
    "Could not read ~/CLAUDE.md: {error}": "Non riesco a leggere ~/CLAUDE.md: {error}",
    "~/CLAUDE.md correctly points to the canonical AGENTS.md file": "~/CLAUDE.md punta correttamente al file canonico AGENTS.md",
    "~/CLAUDE.md does not reference the canonical AGENTS.md file's path (risk of a duplicate copy silently diverging).": "~/CLAUDE.md non fa riferimento al percorso del file canonico AGENTS.md (rischio di una copia duplicata che diverge in silenzio).",
    "Replace ~/CLAUDE.md with a pointer referencing {canon}.": "Sostituisci ~/CLAUDE.md con un puntatore che rimanda a {canon}.",
    "{cli_name} points to the canonical AGENTS.md file": "{cli_name} punta al file canonico AGENTS.md",
    "{cli_name}'s instructions ({pointer_file}) do not point to the canonical AGENTS.md file.": "Le istruzioni di {cli_name} ({pointer_file}) non puntano al file canonico AGENTS.md.",
    "Run 'agent-sync apply' to recreate the pointer.": "Esegui 'agent-sync apply' per ricreare il puntatore.",
    "{cli_name} is installed but doesn't have the pointer to the canonical instructions yet ({pointer_file}).": "{cli_name} è installato ma non ha ancora il puntatore alle istruzioni canoniche ({pointer_file}).",
    "Run 'agent-sync apply' to generate the pointer.": "Esegui 'agent-sync apply' per generare il puntatore.",
    "OpenCode is installed but its configuration file is missing ({cfg_file}).": "OpenCode è installato ma manca il file di configurazione ({cfg_file}).",
    "Run 'agent-sync apply' to generate it.": "Esegui 'agent-sync apply' per generarlo.",
    "Could not parse the OpenCode configuration ({cfg_file}): {error}": "Non riesco ad analizzare la configurazione di OpenCode ({cfg_file}): {error}",
    "OpenCode's 'instructions' do not include the canonical AGENTS.md file.": "Le 'instructions' di OpenCode non includono il file canonico AGENTS.md.",
    "Run 'agent-sync apply' to register it.": "Esegui 'agent-sync apply' per registrarlo.",
    "OpenCode loads the canonical AGENTS.md file": "OpenCode carica il file canonico AGENTS.md",
    "The AGENTS.md bootstrap is {size} bytes, over the {budget} budget.": "Il bootstrap AGENTS.md pesa {size} byte, oltre il budget di {budget}.",
    "Move task-specific content into a load-on-demand note, pulled in only when needed.": "Sposta il contenuto specifico di un compito in una nota caricata on-demand, richiamata solo quando serve.",
    "AGENTS.md bootstrap within budget ({size}/{budget} bytes)": "Bootstrap AGENTS.md entro il budget ({size}/{budget} byte)",
    "Detail note(s) over the {budget}-byte budget: {notes}.": "Nota/e di dettaglio oltre il budget di {budget} byte: {notes}.",
    "Consider splitting the note into smaller sections loaded on demand.": "Valuta di dividere la nota in sezioni più piccole caricate on-demand.",
    "Detail notes within the {budget}-byte budget": "Note di dettaglio entro il budget di {budget} byte",
    "Could not read the AGENTS.md bootstrap: {error}": "Non riesco a leggere il bootstrap AGENTS.md: {error}",
    "No load-on-demand pointers to check in the bootstrap": "Nessun puntatore load-on-demand da verificare nel bootstrap",
    "Load-on-demand pointer(s) in the bootstrap that don't resolve to an existing file: {pointers}.": "Puntatore/i load-on-demand nel bootstrap che non risolvono a un file esistente: {pointers}.",
    "Fix or remove the references to renamed/removed notes in AGENTS.md.": "Correggi o rimuovi i riferimenti alle note rinominate o rimosse in AGENTS.md.",
    "All {checked} load-on-demand pointers in the bootstrap resolve correctly": "Tutti i {checked} puntatori load-on-demand del bootstrap risolvono correttamente",

    # --- checks/mcp_checks.py ---
    "The MCP manifest '{manifest_path}' does not exist.": "Il manifest MCP '{manifest_path}' non esiste.",
    "Create or restore mcp/manifest.yaml from the templates.": "Crea o ripristina mcp/manifest.yaml dai template.",
    "MCP manifest valid with {count} servers declared": "Manifest MCP valido con {count} server dichiarati",
    "The MCP manifest contains errors: {error}": "Il manifest MCP contiene errori: {error}",
    "Fix the syntax of mcp/manifest.yaml.": "Correggi la sintassi di mcp/manifest.yaml.",
    "Some CLIs' MCP configs are missing servers expected by the manifest: {parts}": "Le config MCP di alcuni assistenti non hanno tutti i server attesi dal manifest: {parts}",
    "Run 'agent-sync apply' to regenerate them.": "Esegui 'agent-sync apply' per rigenerarle.",
    "Some CLIs have not been started yet on this machine, so their MCP config could not be checked: {parts}": "Alcuni assistenti non risultano ancora avviati su questa macchina, quindi non è stato possibile verificarne la config MCP: {parts}",
    "MCP configuration files generated and aligned for all active CLIs": "File di configurazione MCP generati e allineati per tutti gli assistenti attivi",

    # --- checks/reachability_checks.py ---
    "MCP connector '{name}' reachable": "Connettore MCP '{name}' raggiungibile",
    "MCP connector '{name}' is not responding ({detail}).": "Il connettore MCP '{name}' non risponde ({detail}).",
    "Check that the service behind '{name}' is running, then rerun the doctor.": "Controlla che il servizio dietro '{name}' sia avviato, poi riesegui il doctor.",
    "Could not check the reachability of '{name}' ({detail}).": "Non è stato possibile verificare la raggiungibilità di '{name}' ({detail}).",
    "The MCP manifest could not be read, so the {name} key was not checked.": "Non riesco a leggere il manifest MCP, quindi la chiave di {name} non è stata verificata.",
    "The manifest declares no {name} connector: nothing to check": "Il manifest non dichiara alcun connettore {name}: niente da verificare",
    "The {name} connector declares no key to check.": "Il connettore {name} non dichiara alcuna chiave da verificare.",
    "The {name} connector can mount: {gate} is set": "Il connettore {name} può essere montato: {gate} è impostata",
    "The {name} connector is not mounted here: {gate} is not set.": "Il connettore {name} non è montato qui: {gate} non è impostata.",
    "Store it with 'nexgen-secrets set {gate}': the value stays in the secret store, and the next sync mounts the connector.": "Salvala con 'nexgen-secrets set {gate}': il valore resta nel deposito dei segreti e il prossimo sync monta il connettore.",

    # --- checks/security_checks.py ---
    "The canonical AGENTS.md bootstrap is not present.": "Il bootstrap canonico AGENTS.md non è presente.",
    "Check the vault structure (03-INFRA/agent-universal-layer/instructions/).": "Controlla la struttura del vault (03-INFRA/agent-universal-layer/instructions/).",
    "The mandatory rules file required-rules.txt is not present.": "Il file delle regole obbligatorie required-rules.txt non è presente.",
    "Restore required-rules.txt next to AGENTS.md.": "Ripristina required-rules.txt accanto ad AGENTS.md.",
    "{count} required invariant rule(s) missing from the AGENTS.md bootstrap (unintended drift).": "{count} regola/e invariante/i richiesta/e assente/i dal bootstrap AGENTS.md (drift non voluto).",
    "Restore the missing rules in AGENTS.md: {rules}": "Riporta le regole mancanti in AGENTS.md: {rules}",
    "All mandatory invariant rules are present in AGENTS.md": "Tutte le regole invarianti obbligatorie sono presenti in AGENTS.md",
    "Could not read the bootstrap or the rules: {error}": "Non riesco a leggere il bootstrap o le regole: {error}",
    "MCP manifest missing, could not check the tokens.": "Manifest MCP assente, non è stato possibile verificare i token.",
    "Tokens required by active MCP HTTP servers are missing from the environment: {tokens}": "Token richiesti dai server HTTP MCP attivi assenti dall'ambiente: {tokens}",
    "Set the missing env vars (see 99-SECRETS / environment.d) and rerun agent-sync apply.": "Imposta le variabili d'ambiente mancanti (vedi 99-SECRETS / environment.d) e riesegui agent-sync apply.",
    "All tokens for active MCP HTTP servers are present in the environment": "Tutti i token dei server HTTP MCP attivi sono presenti nell'ambiente",

    # --- checks/skill_checks.py ---
    "The skills manifest '{manifest_path}' does not exist.": "Il manifest delle skill '{manifest_path}' non esiste.",
    "Create or restore skills.manifest.yaml from the templates.": "Crea o ripristina skills.manifest.yaml dai template.",
    "Skills manifest valid with {count} skills declared": "Manifest skill valido con {count} skill dichiarate",
    "The skills manifest contains errors: {error}": "Il manifest delle skill contiene errori: {error}",
    "Fix the syntax of skills.manifest.yaml.": "Correggi la sintassi di skills.manifest.yaml.",
    "The skill catalog (~/.agents/skills/INDEX.md) is not present.": "Il catalogo delle skill (~/.agents/skills/INDEX.md) non è presente.",
    "Run 'agent-sync apply' to materialize the skills and regenerate the index.": "Esegui 'agent-sync apply' per materializzare le skill e rigenerare l'indice.",
    "Skill catalog present and aligned": "Catalogo delle skill presente e allineato",
    "Skill library entry/entries with a broken or self-referential symlink: {entries}.": "Voce/i della libreria skill con symlink rotto o auto-referenziale: {entries}.",
    "Run 'agent-sync apply' (skills-sync) to regenerate the library; remove the entries manually if they stay broken.": "Esegui 'agent-sync apply' (skills-sync) per rigenerare la libreria; se restano rotte, rimuovi le voci a mano.",
    "No broken symlinks in the skill library": "Nessun symlink rotto nella libreria skill",
    "Skills materialized but not declared in the manifest: {skills}.": "Skill materializzate ma non dichiarate nel manifest: {skills}.",
    "Adopt the useful skills by adding them to skills.manifest.yaml, or remove them manually from the library (the sync won't delete them on its own).": "Adotta le skill utili aggiungendole a skills.manifest.yaml, oppure rimuovile a mano dalla libreria (il sync non le cancella da solo).",
    "No out-of-manifest skills to reconcile": "Nessuna skill fuori manifest da riconciliare",
    "Skills declared in the manifest but not materialized: {skills}.": "Skill dichiarate nel manifest ma non materializzate: {skills}.",
    "Run 'agent-sync apply' (skills-sync) to materialize them.": "Esegui 'agent-sync apply' (skills-sync) per materializzarle.",
    "All skills declared in the manifest are materialized": "Tutte le skill dichiarate nel manifest sono materializzate",
    "No engine starter commands declared in the manifest": "Nessun comando starter del motore dichiarato nel manifest",
    "Engine starter command(s) not materialized as an active view: {commands}.": "Comando/i starter del motore non materializzati come vista attiva: {commands}.",
    "Run 'agent-sync apply' (skills-sync) to regenerate the views.": "Esegui 'agent-sync apply' (skills-sync) per rigenerare le viste.",
    "All engine starter commands are materialized as an active view": "Tutti i comandi starter del motore sono materializzati come vista attiva",
    "{count} problem(s) in the skills manifest.": "{count} problema/i nel manifest delle skill.",
    "Fix the entries listed: {entries}": "Correggi le voci indicate: {entries}",
    "Skills manifest is semantically valid": "Manifest delle skill semanticamente valido",

    # --- stack/runner.py ---
    "Missing stack description for '{stack}': {cfile}": "Manca la descrizione dello stack '{stack}': {cfile}",
    "Unknown service '{name}'. Available ones are: {known}": "Servizio '{name}' sconosciuto. Quelli disponibili sono: {known}",
    "Generated {count} missing secrets ({names})": "Generati {count} segreti mancanti ({names})",
    "'{name}' did not start: {detail}": "'{name}' non è partito: {detail}",
    "no detail": "nessun dettaglio",
    "{name}: did not stop ({detail})": "{name}: non si è fermato ({detail})",
    "Unknown service '{name}'.": "Servizio '{name}' sconosciuto.",

    # --- tools/chrome.py ---
    "agent-chrome: Chrome or Chromium not found on this system.": "agent-chrome: Chrome o Chromium non trovato su questo sistema.",
    "agent-chrome: error launching Chrome ({error})": "agent-chrome: errore nell'avvio di Chrome ({error})",
    "agent-chrome: Chrome is holding the debug profile (process {pid}) but isn't responding on port 9222. Restarting it.": "agent-chrome: Chrome sta tenendo il profilo di debug (processo {pid}) ma non risponde sulla porta 9222. Lo riavvio.",
    "Usage: agent-chrome [--ensure|--heal] [args...]\n\nManages Chrome with a shared local CDP port.": "Uso: agent-chrome [--ensure|--heal] [args...]\n\nGestisce Chrome con una porta CDP locale condivisa.",
    "agent-chrome: this shell runs on the desktop {desktop}, which the user cannot see. Starting Chrome on the user's desktop through Task Scheduler.": "agent-chrome: questa shell gira sul desktop {desktop}, che l'utente non vede. Avvio Chrome sul desktop dell'utente tramite l'Utilità di pianificazione.",
    "agent-chrome: Task Scheduler could not start Chrome ({error}). Not starting it from here: on {desktop} it would be invisible.": "agent-chrome: l'Utilità di pianificazione non è riuscita ad avviare Chrome ({error}). Non lo avvio da qui: su {desktop} sarebbe invisibile.",
    "agent-chrome: Chrome was started through Task Scheduler but port 9222 did not answer within {seconds} seconds.": "agent-chrome: Chrome è stato avviato tramite l'Utilità di pianificazione ma la porta 9222 non ha risposto entro {seconds} secondi.",
    "agent-chrome: Chrome is responding on port 9222 but has no top-level windows on {desktop}. Restarting it.": "agent-chrome: Chrome risponde sulla porta 9222 ma non ha finestre di primo livello su {desktop}. Lo riavvio.",

    # --- tools/now.py ---
    "Deterministic current date/time source for agents": "Sorgente deterministica di data/ora corrente per gli assistenti",
    "Output in JSON": "Output in JSON",
    "Output human-readable": "Output leggibile da un umano",
    "Output for shell eval": "Output per shell eval",

    # --- tools/vault_map.py ---
    "deterministic structural map of a Markdown vault (read-only)": "mappa strutturale deterministica di un vault Markdown (sola lettura)",
    "vault root directory": "cartella radice del vault",
    "full machine-readable output": "output completo leggibile da una macchina",
    "one summary line + broken list; always exit 0": "una riga di riepilogo + l'elenco dei link rotti; esce sempre con 0",
    "hubs to show in the human report": "hub da mostrare nel referto leggibile da un umano",
    "vault-map: not a directory: {vault}": "vault-map: non è una cartella: {vault}",
    "vault-map: {notes} notes, {links} links": "vault-map: {notes} note, {links} collegamenti",
    "broken links ({count}):": "collegamenti rotti ({count}):",
    "  (probably moved to: {hint})": "  (probabilmente spostato in: {hint})",
    "orphan notes ({count}):": "note orfane ({count}):",
    "top hubs (inbound):": "hub principali (in entrata):",
    "dead links inside archived notes (frozen history, low priority): {count}": "collegamenti morti dentro note archiviate (storia congelata, bassa priorità): {count}",
    "valid-but-excluded targets (e.g. 99-SECRETS, assets): {count}": "destinazioni valide ma escluse (es. 99-SECRETS, allegati): {count}",

    # --- tools/open_folder.py ---
    "Error: an absolute path to an existing folder is required.": "Errore: serve un percorso assoluto di una cartella esistente.",
    "Error: the folder does not exist.": "Errore: la cartella non esiste.",
    "Error: the path does not point to a folder.": "Errore: il percorso non indica una cartella.",
    "Error opening folder on Windows: {error}": "Errore nell'apertura della cartella su Windows: {error}",
    "Error opening folder on macOS: {error}": "Errore nell'apertura della cartella su macOS: {error}",
    "Error: could not find a command to open the file manager (gio or xdg-open).": "Errore: non trovo un comando per aprire il file manager (gio o xdg-open).",
    "Error opening folder: {error}": "Errore nell'apertura della cartella: {error}",
    "Folder opened: {resolved}": "Cartella aperta: {resolved}",
    "Usage: agent-open-folder <absolute-folder-path>\n\nOpens a local folder in the default file manager.": "Uso: agent-open-folder <percorso-assoluto-cartella>\n\nApre una cartella locale nel file manager predefinito.",

    # --- vault/gate.py ---
    "vault-groom: the vault's working tree is not clean ({repo}) -- commit or stash them first. Aborting: the temp-clone gate needs a clean HEAD to clone from, zero writes made.": "vault-groom: l'albero di lavoro del vault non è pulito ({repo}) -- fai prima il commit o lo stash. Interrompo: il cancello del clone temporaneo ha bisogno di un HEAD pulito da cui clonare, zero scritture effettuate.",
    "vault-groom: plan record changed after approval (expected {expected}, got {current}) -- aborting, zero writes. Re-run and re-approve.": "vault-groom: il piano registrato è cambiato dopo l'approvazione (atteso {expected}, trovato {current}) -- interrompo, zero scritture. Riesegui e riapprova.",
    " Proposed tranche (sha256 {short_hash}...) -- read it before confirming": " Tranche proposta (sha256 {short_hash}...) -- leggila prima di confermare",
    "Type exactly 'yes' to execute THIS tranche as-is.": "Digita esattamente 'yes' per eseguire QUESTA tranche così com'è.",
    "Any other answer cancels: no changes to the vault.": "Qualunque altra risposta annulla: nessuna modifica al vault.",
    "Proceed? > ": "Procedo? > ",

    # --- vault/groom.py ---
    (
        "usage: vault-groom [preview|apply]\n"
        "\n"
        "  (default / preview)  read-only: proposes one grooming tranche and stops.\n"
        "                        Never prompts, never writes -- always safe to run.\n"
        "  apply                 the guarded flow: propose (read-only), show you the\n"
        "                        tranche, require a typed 'yes', then execute exactly\n"
        "                        that tranche inside a throwaway clone with no\n"
        "                        remote, and promote it into the real vault only if\n"
        "                        the audit afterward is clean.\n"
        "\n"
        "Environment:\n"
        "  GROOM_RUNNER       claude|codex|agy (default: claude)\n"
        "  GROOM_MODEL        model name passed to the runner (default: claude-sonnet-5)\n"
        "  GROOM_NOPUSH=1     after a clean apply, keep the promoted commits local\n"
        "                     (skip the auto-publish step)\n"
        "  GROOM_LOG          override the preview/propose-pass log path\n"
        "  GROOM_STATE_DIR    where clones and audit records land\n"
        "                     (default: ~/.local/state/vault-groom)\n"
        "  AGENT_VAULT_DATA / KNOWLEDGE_VAULT_PATH   where the vault lives\n"
    ): (
        "uso: vault-groom [preview|apply]\n"
        "\n"
        "  (predefinito / preview)  sola lettura: propone una tranche di pulizia e si ferma.\n"
        "                        Non chiede mai conferma, non scrive mai -- sempre sicuro da eseguire.\n"
        "  apply                 il flusso guardato: propone (sola lettura), ti mostra la\n"
        "                        tranche, richiede di digitare 'yes', poi esegue esattamente\n"
        "                        quella tranche dentro un clone usa-e-getta senza\n"
        "                        remoto, e la promuove nel vault reale solo se\n"
        "                        l'audit successivo è pulito.\n"
        "\n"
        "Ambiente:\n"
        "  GROOM_RUNNER       claude|codex|agy (default: claude)\n"
        "  GROOM_MODEL        nome del modello passato al runner (default: claude-sonnet-5)\n"
        "  GROOM_NOPUSH=1     dopo un apply pulito, tiene i commit promossi in locale\n"
        "                     (salta il passaggio di pubblicazione automatica)\n"
        "  GROOM_LOG          sovrascrive il percorso di log della passata preview/propose\n"
        "  GROOM_STATE_DIR    dove finiscono i cloni e i referti di audit\n"
        "                     (default: ~/.local/state/vault-groom)\n"
        "  AGENT_VAULT_DATA / KNOWLEDGE_VAULT_PATH   dove si trova il vault\n"
    ),
    "log: {log_path}": "log: {log_path}",
    "audit record: {record_path}": "referto di audit: {record_path}",
    "vault-groom: vault = {vault}": "vault-groom: vault = {vault}",
    "vault-groom: empty proposal, nothing to review -- aborting.": "vault-groom: proposta vuota, niente da revisionare -- interrompo.",
    "vault-groom: cancelled, no changes to the vault.": "vault-groom: annullato, nessuna modifica al vault.",
    "vault-groom: unknown mode '{mode}'\n\n{usage}": "vault-groom: modalità sconosciuta '{mode}'\n\n{usage}",
    "vault-groom: the vault does not exist or is not a folder: {vault}": "vault-groom: il vault non esiste o non è una cartella: {vault}",
    "vault-groom: could not prepare the temp clone: {error}": "vault-groom: non riesco a preparare il clone temporaneo: {error}",

    # --- vault/audit_cli.py ---
    "the REAL vault -- never touched until promotion": "il vault REALE -- mai toccato fino alla promozione",
    "the temp-clone gate's working dir": "la cartella di lavoro del cancello del clone temporaneo",
    "the real vault's HEAD before the clone was made": "l'HEAD del vault reale prima di creare il clone",
    "exit code of the write-pass runner CLI -- non-zero blocks promotion unconditionally": "codice di uscita della CLI del write pass -- diverso da zero blocca sempre la promozione",
    "attempt agent_sync.py publish after a successful promotion; omit to never push this run": "tenta 'agent_sync.py publish' dopo una promozione riuscita; ometti per non pubblicare mai in questa esecuzione",
    "directory containing agent_sync.py (required together with --push-if-clean)": "cartella che contiene agent_sync.py (richiesta insieme a --push-if-clean)",

    # --- Passaggio dalla versione precedente -------------------------------
    "Handover complete: nothing here goes through the previous release's launchers any more.":
        "Passaggio completato: qui non passa più niente dai comandi della versione precedente.",
    "{count} command(s) still reach the engine the previous release's way: {names}":
        "{count} comando/i raggiungono ancora il motore come faceva la versione precedente: {names}",
    "Run 'nexgen sync apply' once: it replaces them. Until every machine reports this as complete, the transitional launchers cannot be removed (they are due after {version}).":
        "Esegui 'nexgen sync apply' una volta: li sostituisce. Finché non lo dicono tutte le macchine, i comandi di transizione non si possono togliere (la scadenza è dopo la {version}).",
    "No engine version recorded here yet; this machine has not completed a cycle with a version that records one.":
        "Nessuna versione del motore registrata qui: questa macchina non ha ancora completato un ciclo con una versione che la registri.",
    "It gets recorded on the first completed cycle.":
        "Viene registrata al primo ciclo completato.",
    "Last completed cycle: engine {version}":
        "Ultimo ciclo completato: motore {version}",
    "The last completed cycle ran engine {recorded}, this one is {current}.":
        "L'ultimo ciclo completato ha girato col motore {recorded}, questo è il {current}.",
    "Normal right after an update; it lines up on the next cycle.":
        "Normale subito dopo un aggiornamento: si allinea al ciclo successivo.",

    # --- Primo avvio: l'installazione che arriva fino in fondo ---------------
    "Three questions": "Tre domande",
    "Setting it up": "Preparazione",
    "Where you are": "A che punto sei",
    "Ready.": "Pronto.",
    "Nothing else is required.": "Non serve altro.",
    "Profile filled in: {fields}": "Profilo compilato: {fields}",
    "Profile already filled in: left untouched.":
        "Profilo già compilato: lasciato com'è.",
    "{file} is not in this vault: nothing to fill in.":
        "{file} non c'è in questo vault: niente da compilare.",
    "none detected yet": "nessuna rilevata per ora",
    "Authoritative remote set to '{remote}'.":
        "Remoto autoritativo impostato su '{remote}'.",
    "Only 'authoritative_remote' can be set from the command line; mirrors are edited in remotes.yaml.":
        "Solo 'authoritative_remote' si imposta da riga di comando; i mirror si modificano in remotes.yaml.",
    "Could not write {path}: {error}":
        "Impossibile scrivere {path}: {error}",
    "The vault now has uncommitted changes; publish them with 'nexgen vault push {file}'.":
        "Il vault ora ha modifiche non committate; pubblicale con 'nexgen vault push {file}'.",
    "Remotes already declared: left untouched.":
        "Remoti già dichiarati: lasciati com'erano.",
    "Skill manifest seeded from the example; it is yours to edit.":
        "Manifest delle skill creato dall'esempio: da qui in poi è tuo.",
    "Skill manifest already there: left untouched.":
        "Manifest delle skill già presente: lasciato com'era.",
    "No example skill manifest in this vault.":
        "In questo vault non c'è un manifest delle skill d'esempio.",
    "Machine aligned.": "Macchina allineata.",
    "Alignment did not finish: {detail}":
        "L'allineamento non è arrivato in fondo: {detail}",
    "Alignment could not be run: {error}":
        "Non è stato possibile eseguire l'allineamento: {error}",
    "no reason given": "senza spiegazione",
    "The check itself could not run: {error}":
        "Il controllo stesso non è potuto partire: {error}",
    "Installed. {count} thing(s) still need attention:":
        "Installato. {count} cosa/e richiedono ancora attenzione:",
    "...and {more} more: run 'nexgen doctor'.":
        "...e altre {more}: esegui 'nexgen doctor'.",
    "Most of these are fixed by running 'nexgen sync apply' again.":
        "Quasi tutte si risolvono rieseguendo 'nexgen sync apply'.",
    "From now on: 'nexgen doctor' tells you if anything is wrong, and 'nexgen update' brings in a new version.":
        "Da adesso: 'nexgen doctor' ti dice se qualcosa non va, e 'nexgen update' porta una versione nuova.",
    "Want the guided path too, to bring your own documents in? Open INIT.md.":
        "Vuoi anche il percorso guidato, per portare dentro i tuoi documenti? Apri INIT.md.",
    "Everything required is in place. Run the installer without --check.":
        "C'è tutto quello che serve. Esegui l'installer senza --check.",
    "Not a terminal: the questions were skipped. Run 'nexgen sync apply' when you are ready.":
        "Non è un terminale: le domande sono state saltate. Esegui 'nexgen sync apply' quando vuoi.",
    "no assistant found yet": "nessun assistente ancora presente",
    "install Claude Code, Codex, OpenCode or Antigravity when you want one: the engine will configure it on its own from then on":
        "installa Claude Code, Codex, OpenCode o Antigravity quando ne vorrai uno: da lì in poi il motore lo configura da sé",
    "Nothing to do if you did not mean to run it. To run the connectors on this machine: 'nexgen stack up'.":
        "Niente da fare, se non volevi avviarlo. Per far girare i connettori su questa macchina: 'nexgen stack up'.",
    "→ {action}": "→ {action}",

    "Setup recorded in the vault's history.":
        "Configurazione registrata nella storia del vault.",
    "Nothing to record.": "Niente da registrare.",
    "Could not stage the setup files.":
        "Non è stato possibile preparare i file di configurazione.",
    "Setup could not be recorded: {detail}":
        "Non è stato possibile registrare la configurazione: {detail}",
    'Git does not know who you are yet, so the setup was not recorded. Run: git config --global user.email "you@example.com" and git config --global user.name "Your Name"':
        "Git non sa ancora chi sei, quindi la configurazione non è stata registrata. "
        'Esegui: git config --global user.email "tu@example.com" '
        'e git config --global user.name "Il Tuo Nome"',
    " No remote is set up yet, so it stays local for now; 'nexgen config authoritative_remote <name>' names one later.":
        " Non c'è ancora nessun remoto configurato, quindi per ora resta locale; "
        "'nexgen config authoritative_remote <nome>' lo imposterà più avanti.",
    "The alignment stopped first: {reason}":
        "L'allineamento si è fermato prima: {reason}",
    "Run 'nexgen doctor' for the whole list; most of it clears with 'nexgen sync apply'.":
        "Esegui 'nexgen doctor' per l'elenco completo; quasi tutto si risolve con 'nexgen sync apply'.",

    # --- Errori parlanti (bug-hunt finale pre-2.3.4) -------------------------
    "Unexpected error: {error}":
        "Errore inatteso: {error}",
    "Run 'nexgen doctor' to check the machine, and retry.":
        "Esegui 'nexgen doctor' per controllare la macchina e riprova.",
    (
        "Could not acquire lock '{lock_path}' after {timeout:.1f}s "
        "(another sync is still running). Wait a minute and retry; "
        "if it keeps happening, look for a stuck 'nexgen sync' process."
    ):
        (
            "Non riesco a prendere il lock '{lock_path}' dopo {timeout:.1f}s "
            "(un altro sync è ancora in corso). Aspetta un minuto e riprova; "
            "se continua, cerca un processo 'nexgen sync' bloccato."
        ),
    "Refusing to delete '{branch}': only quarantine/* branches can be deleted with this command.":
        "Mi rifiuto di cancellare '{branch}': con questo comando puoi cancellare solo branch quarantine/*.",
    "Deleted quarantine branch {branch}":
        "Branch di quarantena {branch} cancellato",
    "Error deleting branch {branch}: {error}":
        "Errore cancellando il branch {branch}: {error}",
    "Error getting diff for {branch}: {error}":
        "Errore leggendo il diff di {branch}: {error}",
    "No quarantine branches in the Vault.":
        "Nessun branch di quarantena nel Vault.",
    "Quarantine branches ({count}):":
        "Branch di quarantena ({count}):",
    "AI Council orchestrator not found at {path}. Reinstall the engine.":
        "Orchestratore AI Council non trovato in {path}. Reinstalla il motore.",
    "Council {mode} finished":
        "Council {mode} concluso",
    "Council {mode} stopped":
        "Council {mode} interrotto",
    "Request: {tag}.":
        "Richiesta: {tag}.",
    "It stopped before the end: {reason}":
        "Si è fermato prima della fine: {reason}",
    "Outcome: {summary}.":
        "Esito: {summary}.",
    "It ended without a verdict to quote.":
        "È finito senza un verdetto da citare.",
    "Full outcome: {path}":
        "Esito completo: {path}",

    # --- Secondo giro: sync/guard production grade ---------------------------
    "Refusing to quarantine: HEAD is on '{found}', not '{expected}'":
        "Mi rifiuto di mettere in quarantena: sei su '{found}', non su '{expected}'",
    "Created {q_branch}, but could not stage uncommitted changes before realignment: {error}":
        "Creato {q_branch}, ma non riesco a preparare le modifiche non committate prima del riallineamento: {error}",
    (
        "Work preserved on {q_branch}, but could not switch back to {branch}: {error}. "
        "No reset was performed; switch back by hand and retry."
    ):
        (
            "Lavoro al sicuro su {q_branch}, ma non riesco a tornare su {branch}: {error}. "
            "Nessun reset eseguito; torna indietro a mano e riprova."
        ),
    (
        "{remote} unreachable or without branch {branch}: the commit stays local, publish it later with 'vault-push'"
    ):
        (
            "{remote} non raggiungibile o senza branch {branch}: il commit resta qui, "
            "ripubblicalo più tardi con 'vault-push'"
        ),
    "Could not resolve {remote}/{branch} for publishing":
        "Non riesco a risolvere {remote}/{branch} per pubblicare",
    (
        "Rebase succeeded but restoring the stashed changes failed: resolve the conflict, then push with 'vault-push'. "
        "Nothing was pushed; the work is in 'git stash list'."
    ):
        (
            "Rebase riuscito ma non riesco a ripristinare le modifiche accantonate: risolvi il conflitto, "
            "poi pusha con 'vault-push'. Non ho pushato niente; il lavoro è in 'git stash list'."
        ),
    (
        "Rebase failed and could not be aborted cleanly: resolve the state by hand "
        "('git rebase --abort', 'git stash list'), then retry. Nothing was pushed."
    ):
        (
            "Rebase fallito e non riesco ad abortirlo in modo pulito: sistema lo stato a mano "
            "('git rebase --abort', 'git stash list'), poi riprova. Non ho pushato niente."
        ),
    (
        "Rebase aborted and stashed changes could not be restored: resolve the conflict, "
        "then push with 'vault-push'. Nothing was pushed; the work is in 'git stash list'."
    ):
        (
            "Rebase abortito ma non riesco a ripristinare le modifiche accantonate: risolvi il conflitto, "
            "poi pusha con 'vault-push'. Non ho pushato niente; il lavoro è in 'git stash list'."
        ),
    "a git cherry-pick is in progress in the vault: resolve it before continuing":
        "c'è un cherry-pick a metà nel Vault: sistemalo prima di continuare",
    "a git revert is in progress in the vault: resolve it before continuing":
        "c'è un revert a metà nel Vault: sistemalo prima di continuare",
    "mirror {mirror} not updated ({error}); primary is safe, retry the mirror later":
        "mirror {mirror} non aggiornato ({error}); il primario è a posto, riprova il mirror più tardi",
    (
        "{count} new infra files never committed ({files}): stage and publish them, or they stay local-only."
    ):
        (
            "{count} file infra nuovi mai committati ({files}): mettili in stage e pubblicali, "
            "o restano solo qui."
        ),
    "Stage the new infra files with vault-push.":
        "Metti in stage i nuovi file infra con vault-push.",
    (
        "New infra files never committed ({count}): stage them with vault-push, or they stay local-only."
    ):
        (
            "File infra nuovi mai committati ({count}): mettili in stage con vault-push, "
            "o restano solo qui."
        ),
    "Could not prepare the lock directory '{path}' ({error}).":
        "Non riesco a preparare la cartella del lock '{path}' ({error}).",
    (
        "MCP rendering failed after skills were synced ({error}): fix the CLI config and re-run"
    ):
        (
            "Render MCP fallito dopo aver sincronizzato le skill ({error}): sistema la config del CLI e rilancia"
        ),
    "MCP render failed for: {clis}":
        "Render MCP fallito per: {clis}",
    "Pull completed (derived files not regenerated: run apply next)":
        "Pull completato (file derivati non rigenerati: ora lancia apply)",
    "Alignment completed with warnings (see actions above)":
        "Allineamento completato con avvisi (vedi sopra)",
    "instruction pointer {path} left untouched: safety backup failed ({error})":
        "Puntatore istruzioni {path} lasciato com'è: backup di sicurezza fallito ({error})",
    "opencode dead 'instructions' entries left untouched: safety backup failed ({error})":
        "Voci 'instructions' obsolete di opencode lasciate com'erano: backup di sicurezza fallito ({error})",
    "instruction pointer {path} not aligned ({error})":
        "Puntatore istruzioni {path} non allineato ({error})",
    "Startup self-alignment reported no success and no error; verify with `nexgen doctor`":
        "L'auto-allineamento all'avvio non ha detto né sì né no; verifica con `nexgen doctor`",
    "local-model: adapter not relinked ({error})":
        "local-model: adapter non ricollegato ({error})",
    "Infra auto-commit failed; the Git inspection below decides whether the cycle can proceed":
        "Auto-commit infra fallito; l'ispezione Git qui sotto decide se il ciclo può andare avanti",
    "The remotes file {path} is unreadable ({error}); pushes would silently fall back to 'origin'.":
        "Il file remotes {path} non si legge ({error}); i push ricadrebbero su 'origin' in silenzio.",
    "The remotes file {path} does not contain a map; pushes would silently fall back to 'origin'.":
        "Il file remotes {path} non contiene una mappa; i push ricadrebbero su 'origin' in silenzio.",
    "Fix the YAML syntax of remotes.yaml.":
        "Sistema la sintassi YAML di remotes.yaml.",
    "Fix the structure of remotes.yaml (authoritative_remote, mirrors).":
        "Sistema la struttura di remotes.yaml (authoritative_remote, mirrors).",
    "Remotes declaration readable":
        "Dichiarazione remotes leggibile",
    "(last cycle completed with {count} warnings)":
        "(ultimo ciclo completato con {count} avvisi)",
    "the installer for '{name}' ran but left no new copy to claim: keeping the previous version":
        "l'installer di '{name}' è andato ma non ha lasciato copie nuove da prendere: tengo la versione precedente",
    "Third-party skills materialized at an older pin than the manifest: {skills}.":
        "Skill terze materializzate a un pin più vecchio del manifest: {skills}.",
    "Run 'agent-sync apply' (skills-sync) to fetch the declared pins.":
        "Lancia 'agent-sync apply' (skills-sync) per scaricare i pin dichiarati.",
    "Third-party skills with no recorded pin (freshness unknown until the next sync): {skills}.":
        "Skill terze senza pin registrato (freschezza ignota fino al prossimo sync): {skills}.",
    "Run 'agent-sync apply' (skills-sync) once to record the materialized pins.":
        "Lancia una volta 'agent-sync apply' (skills-sync) per registrare i pin materializzati.",
    "All pinned third-party skills match the manifest":
        "Tutte le skill terze pinnate corrispondono al manifest",
    "Skill '{name}' removed with its library entry for {target}":
        "Skill '{name}' rimossa insieme alla sua entry di libreria per {target}",
    "the 'servers:' block is flow-style with entries; expand it to block style by hand, then rerun.":
        "il blocco 'servers:' è flow-style con entry; espandilo a block style a mano e rilancia.",
    "No drift in the probed domains (see 'not checked here' below for the rest).":
        "Nessun drift nei domini sondati (vedi 'non controllato qui' sotto per il resto).",
    (
        "MCP reachability, upstream versions and secrets drift: doctor-only, "
        "they need the network or live credentials a plan never touches."
    ):
        (
            "Raggiungibilità MCP, versioni upstream e drift segreti: solo doctor, "
            "servono rete o credenziali live che un plan non tocca mai."
        ),
    (
        "Resetting the engine is only half the recovery: generated configs and views "
        "may already be half-new. Re-run provisioning from the restored tree afterwards:\n"
        "  python3 {entry} apply\n"
        "  agent-doctor --summary"
    ):
        (
            "Resettare il motore è metà del recupero: config generate e viste "
            "potrebbero essere già mezze nuove. Rilancia il provisioning dall'albero ripristinato:\n"
            "  python3 {entry} apply\n"
            "  agent-doctor --summary"
        ),

    # --- Agent lanes: un solo workflow per gli agenti -----------------------
    "The engine checkout is on a detached HEAD; lane work belongs on a dev/<agent> branch.":
        "Il checkout del motore è su un HEAD staccato; il lavoro di lane va su un branch dev/<agent>.",
    "Engine checkout on lane branch '{branch}'":
        "Checkout del motore sul branch di lane '{branch}'",
    "Engine checkout on '{branch}', clean and aligned":
        "Checkout del motore su '{branch}', pulito e allineato",
    "{count} uncommitted files":
        "{count} file non committati",
    "{count} commits ahead of developer":
        "{count} commit avanti a developer",
    "Engine work on '{branch}' belongs on a dev/<agent> lane ({what}).":
        "Il lavoro sul motore in '{branch}' va su una lane dev/<agent> ({what}).",
    "Move it: git checkout -b dev/<agent> developer, cherry-pick or merge, then merge lane into developer.":
        "Spostalo: git checkout -b dev/<agent> developer, cherry-pick o merge, poi mergia la lane in developer.",

    # --- Fondamenta: path/file/config blindati --------------------------------
    "Refusing to overwrite unreadable file {path}: {exc}":
        "Mi rifiuto di sovrascrivere il file illeggibile {path}: {exc}",
    'Skill materialization failed; runtime configuration was not regenerated.':
        'Installazione delle skill fallita. Le configurazioni dei runtime non sono state rigenerate.',
    'Could not open lock {path}: {error}':
        'Impossibile aprire il lock {path}: {error}',
    'Cannot verify the existing skill library: {path}':
        'Impossibile verificare la libreria skill esistente: {path}',
    'Could not check the engine lane: missing ref {ref}':
        'Impossibile verificare la corsia del motore: manca il riferimento {ref}.',
    "Fetch the engine repository's remote branches and run doctor again.":
        'Scarica i riferimenti remoti del repository del motore e riesegui doctor.',
    'Move the work to your dev/<agent> lane and verify it before integrating.':
        'Sposta il lavoro nella tua corsia dev/<agent> e verificalo prima di integrarlo.',
    'Lock timeout must be finite.':
        'Il timeout del lock deve essere un numero finito.',
    'Shell hook: cannot update {path}.':
        'Hook della shell: impossibile aggiornare {path}.',
    'Shell hook: unrecognized managed block in {path}; preserved.':
        'Hook della shell: blocco gestito non riconosciuto in {path}, conservato. Rimuovilo manualmente dopo averlo controllato.',

    # --- Falla traballanti: scheduler/moduli/runtime/monitor ------------------
    "template condition on unknown variable '.{name}'":
        "Condizione template su variabile sconosciuta '.{name}'",
    "unmatched template block '{{ {block} }}'":
        "Blocco template spaiato '{{ {block} }}'",
    "unclosed template block: missing '{{ end }}'":
        "Blocco template non chiuso: manca '{{ end }}'",
    "Guard starting up (last cycle {minutes:.0f} minutes ago, machine just started)":
        "Guardia in avvio (ultimo ciclo {minutes:.0f} minuti fa, macchina appena partita)",
    "The liveness file cannot be read ({error}); fix or delete it.":
        "Il file di liveness non si legge ({error}); sistemalo o cancellalo.",
    "Agent sync self-monitoring is blind":
        "L'auto-monitoraggio del sync è cieco",
    "systemd: neither ~/.local/bin/agent-sync nor ~/.local/bin/nexgen exists yet; timer not armed (will be retried on a future run once a shim is created)":
        "systemd: non c'è ancora né ~/.local/bin/agent-sync né ~/.local/bin/nexgen; timer non armato (si riprova al prossimo giro quando c'è uno shim)",
    "systemd: systemctl not found — files written but the timers are NOT enabled":
        "systemd: systemctl non trovato — file scritti ma i timer NON sono attivi",
    "Cannot read skill version record at {path} ({error}).":
        "Impossibile leggere il registro delle versioni skill in {path} ({error}).",
    "Skill '{name}' pin could not be recorded ({error}); update is incomplete.":
        "La versione della skill '{name}' non è stata salvata ({error}); aggiornamento incompleto.",
    "Cannot verify the recorded pin for skill '{name}' ({error}).":
        "Impossibile verificare la versione salvata della skill '{name}' ({error}).",
    "The skill version record cannot be read or is invalid.":
        "Il registro delle versioni skill è illeggibile o non valido.",
    "Inspect and repair installed-skill-versions.json before syncing skills.":
        "Controlla e correggi installed-skill-versions.json prima del sync delle skill.",
    "Engine development belongs on developer (current branch: {branch}).": "Lo sviluppo dell'Engine va su developer (ramo attuale: {branch}).",
    "Move the work to developer and run the engine test gate.": "Porta il lavoro su developer ed esegui il gate dei test dell'Engine.",
    "Engine checkout on developer; development commits and local edits are allowed.": "Checkout dell'Engine su developer; commit di sviluppo e modifiche locali sono consentiti.",
    "Engine branch check failed: {detail}": "Controllo del ramo Engine fallito: {detail}",
    'their require_env is unset in this process':
        'la loro require_env non è impostata in questo processo',
    'settings outside the server list':
        "impostazioni fuori dall'elenco dei server",
    'Some CLI configurations no longer match what the manifest would write: {parts}':
        'Alcune configurazioni delle CLI non coincidono più con quello che il manifest scriverebbe: {parts}',
    "Run 'agent-sync apply' to regenerate them (the guard cycle does it on its own too).":
        "Esegui 'agent-sync apply' per rigenerarle (lo fa da solo anche il ciclo di guardia).",
    'Could not compare these CLI configurations with the manifest: {parts}':
        'Impossibile confrontare queste configurazioni delle CLI con il manifest: {parts}',
    'Every CLI configuration matches what the manifest would write':
        'Ogni configurazione delle CLI coincide con quello che il manifest scriverebbe',
    'Some MCP servers point at a program that is not installed here: {parts}':
        'Alcuni server MCP puntano a un programma non installato su questa macchina: {parts}',
    'Install the missing program, or disable the server in the manifest on this machine.':
        'Installa il programma mancante, oppure disattiva il server nel manifest su questa macchina.',
    'Every stdio MCP server has a program to start':
        'Ogni server MCP locale ha un programma da avviare',
    'Could not record the choice in {path}: {error}':
        'Impossibile salvare la scelta in {path}: {error}',
    "Refusing to publish: {remote} is the engine's public repository ({url}), and the vault is private. The commit stays local; point the remote at your own private repository.":
        "Pubblicazione rifiutata: {remote} è il repository pubblico dell'engine ({url}) e il vault è privato. Il commit resta locale; punta il remote al tuo repository privato.",
    "No remote of the Vault points at the engine's public repository":
        "Nessun remote del Vault punta al repository pubblico dell'engine",
    "The Vault has a remote that is the engine's public repository: {remotes}":
        "Il Vault ha un remote che è il repository pubblico dell'engine: {remotes}",
    "Point it at your own private repository ('git remote set-url <name> <url>') or remove it ('git remote remove <name>'). Publishing is refused until then.":
        "Puntalo al tuo repository privato ('git remote set-url <nome> <url>') oppure toglilo ('git remote remove <nome>'). Fino ad allora la pubblicazione è rifiutata.",
    "Update channel only: {remotes} serve the engine updates and never receive your notes, which go to {auth}.":
        "Solo canale degli aggiornamenti: {remotes} serve per gli update del motore e non riceve mai le tue note, che vanno su {auth}.",
    "Make {name} fetch-only once: 'git remote set-url --push {name} DISABLED'. Your notes already go to {auth}.":
        "Rendi {name} di sola lettura una volta sola: 'git remote set-url --push {name} DISABLED'. Le tue note vanno già su {auth}.",
    "Bare 'git push' would publish to {name}: run 'git remote set-url --push {name} DISABLED' once to keep it fetch-only.":
        "Un 'git push' senza argomenti pubblicherebbe su {name}: dai una volta sola 'git remote set-url --push {name} DISABLED' per tenerlo di sola lettura.",
    'Scheduled-task state is checked on Linux/systemd only; not checked here.':
        'Lo stato delle attività pianificate si controlla solo su Linux/systemd; qui non è stato controllato.',
    'systemd is not available here (or host changes are disabled): the timers were not checked.':
        'systemd non è disponibile qui (o le modifiche alla macchina sono disattivate): i timer non sono stati controllati.',
    "The guard's timers are not running, so nothing tends this machine: {units}":
        'I timer della guardia non girano, quindi nessuno si occupa di questa macchina: {units}',
    "Run 'nexgen guard' once to write and enable them. On a machine with no login session, also: loginctl enable-linger $USER.":
        "Esegui 'nexgen guard' una volta per scriverli e attivarli. Su una macchina senza sessione di login anche: loginctl enable-linger $USER.",
    'The guard and heartbeat timers are enabled and running':
        'I timer della guardia e del battito sono attivi e in funzione',
    'No engine launchers found in {dir}; not checked.':
        "Nessun launcher dell'engine trovato in {dir}; non controllato.",
    'Some engine commands point at an engine that is no longer there: {commands}':
        "Alcuni comandi dell'engine puntano a un engine che non c'è più: {commands}",
    "Run 'nexgen init' from the engine you want to use: it rewrites every launcher.":
        "Esegui 'nexgen init' dall'engine che vuoi usare: riscrive tutti i launcher.",
    'Every engine command points at an engine that exists ({count} checked)':
        "Ogni comando dell'engine punta a un engine che esiste ({count} controllati)",
    'The leak-scan pattern copies could not be compared: {error}':
        'Non è stato possibile confrontare le copie dei pattern anti-leak: {error}',
    "The Vault's leak-scan patterns match the engine's":
        "I pattern anti-leak del Vault coincidono con quelli dell'engine",
    "The Vault's copy of the leak-scan patterns differs from the engine's, so its push gate may be weaker than CI's.":
        "La copia dei pattern anti-leak nel Vault è diversa da quella dell'engine: il suo cancello di push può essere più debole di quello della CI.",
    'Copy {source} over {twin}, then commit it in the Vault.':
        'Copia {source} sopra {twin}, poi fai il commit nel Vault.',
    'The MCP manifest could not be read, so placement was not checked: {error}':
        'Il manifest MCP non si legge, quindi la disposizione non è stata controllata: {error}',
    'The MCP placement is incoherent: {problems}':
        'La disposizione degli MCP è incoerente: {problems}',
    "Run 'nexgen mcp plan' to see it per CLI, then fix the manifest.":
        "Esegui 'nexgen mcp plan' per vederla per CLI, poi correggi il manifest.",
    "Every CLI's gateway is mounted where servers are routed behind it":
        'Il gateway di ogni CLI è montato dove ci sono server instradati dietro di lui',
    'why':
        'perché',
    'needs {env}':
        'serve {env}',
    'Served by the gateway, per CLI:':
        'Serviti dal gateway, per CLI:',
    'Compared with how the gateway served before it knew its CLI:':
        'Rispetto a come serviva il gateway prima di sapere la sua CLI:',
    'no longer served: {names}':
        'non più serviti: {names}',
    'newly served: {names}':
        'ora serviti: {names}',
    'Incoherent:':
        'Incoerente:',
    'No MCP manifest at {path}':
        'Nessun manifest MCP in {path}',
    'no answer to {method} within {seconds:g}s':
        'nessuna risposta a {method} entro {seconds:g}s',
    'the process ended before answering {method}':
        'il processo è terminato prima di rispondere a {method}',
    'the gateway is not mounted in {cli}, but {names} are routed behind it':
        'il gateway non è montato in {cli}, ma {names} sono instradati dietro di lui',
    'the gateway does not offer {tools}':
        'il gateway non offre {tools}',
    'the gateway thinks it serves {other}, not {cli}':
        'il gateway crede di servire {other}, non {cli}',
    'the rendered gateway entry does not carry {var}={cli}':
        'la voce del gateway scritta non porta {var}={cli}',
    '{name} should be behind the gateway in {cli} and is not listed':
        '{name} dovrebbe stare dietro il gateway in {cli} e non è elencato',
    '{name} is served in {cli} but the plan does not route it there':
        '{name} è servito in {cli} ma il piano non lo instrada lì',
    'gateway served {count} server(s) in {seconds}s: {names}':
        'il gateway ha servito {count} server in {seconds}s: {names}',
    '--eager and --lazy contradict each other: pick one (lazy is the default)':
        '--eager e --lazy si contraddicono: scegline uno (lazy è il predefinito)',
    'a trial lasts between a moment and {max:g} hours, not {hours:g}':
        'una prova dura da un attimo a {max:g} ore, non {hours:g}',
    "'{name}' is already in the manifest: a trial would only shadow it":
        "'{name}' è già nel manifest: una prova lo coprirebbe soltanto",
    "{name} is on trial for {hours:g}h on this machine only: it is served by the gateway (no CLI config is touched, nothing syncs) and disappears by itself. Keep it with 'nexgen mcp promote {name}', end it with 'nexgen mcp drop {name}'.":
        "{name} è in prova per {hours:g}h solo su questa macchina: lo serve il gateway (nessuna config delle CLI viene toccata, niente si sincronizza) e sparisce da solo. Tienilo con 'nexgen mcp promote {name}', chiudilo con 'nexgen mcp drop {name}'.",
    'No MCP servers on trial.':
        'Nessun server MCP in prova.',
    '{left} left':
        '{left} rimasti',
    '{name}: trial ended.':
        '{name}: prova terminata.',
    '{name} is not on trial here.':
        '{name} non è in prova qui.',
    '{name} is not on trial here (it may have expired).':
        '{name} non è in prova qui (può essere scaduto).',
    'trial, {left} left, this machine only':
        'prova, {left} rimasti, solo questa macchina',
    'MCP trials that ran out were removed: {names}':
        'Prove MCP scadute e rimosse: {names}',
    'MCP trials could not be tidied: {error}':
        'Non è stato possibile ripulire le prove MCP: {error}',
    'No MCP servers on trial':
        'Nessun server MCP in prova',
    'MCP servers on trial on this machine, not synced: {names}':
        'Server MCP in prova su questa macchina, non sincronizzati: {names}',
    "Keep one with 'nexgen mcp promote <name>' or end it with 'nexgen mcp drop <name>'; they end by themselves anyway.":
        "Tienine uno con 'nexgen mcp promote <nome>' o chiudilo con 'nexgen mcp drop <nome>'; finiscono comunque da soli.",
    'needs {var} in the environment':
        "serve {var} nell'ambiente",
    '{tools} tools, about {tokens} tokens of definitions loaded up front':
        '{tools} strumenti, circa {tokens} token di definizioni caricati subito',
    '{tools} tools, ~{tokens} tokens, {seconds}s':
        '{tools} strumenti, ~{tokens} token, {seconds}s',
    # --- Cosa c'è installato (nexgen info) e l'aggiornamento dei pin -------------
    "core": "core",
    "yours": "tuo",
    "third-party": "di terzi",
    "direct": "diretto",
    "lazy": "lazy",
    "mixed": "misto",
    "off": "spento",
    "trial": "in prova",
    "CONNECTORS (MCP)": "CONNETTORI (MCP)",
    "THIRD-PARTY INSIDE MODULES": "COMPONENTI DI TERZI NEI MODULI",
    "SKILLS": "SKILL",
    "upstream {new} available (pinned {old})": "a monte c'è la {new} (tu hai la {old})",
    "held: {why}": "ferma: {why}",
    "read the changelog first": "prima leggi cosa è cambiato",
    "cleared, ready to raise": "controllata, si può alzare",
    "not pinned: runs whatever the registry serves today, and nothing watches it":
        "non è bloccato a una versione: parte con quella che c'è oggi nel registro, e nessuno la controlla",
    "{count} tools hidden": "{count} strumenti nascosti",
    "only {count} tools exposed": "solo {count} strumenti esposti",
    "not mounted anywhere: {why}": "non è montato da nessuna parte: {why}",
    "your Vault's copy differs from the engine's, and the copy is what runs":
        "la copia nel tuo Vault è diversa da quella del motore, e quella che gira è la tua copia",
    "declared but not materialized on this machine yet": "dichiarata ma non ancora installata su questa macchina",
    "could not read the installed connectors and skills": "non sono riuscito a leggere i connettori e le skill installati",
    "{count} more (yours, or core ones that follow the engine): nexgen info --all":
        "altre {count} (tue, o del motore che lo seguono): nexgen info --all",
    "a frozen copy in your Vault, identical to the engine's today: nexgen skills adopt":
        "una copia ferma nel tuo Vault, oggi uguale a quella del motore: nexgen skills adopt",
    "outside the manifest (kept, never deleted): {names}": "fuori dal manifest (tenute, mai cancellate): {names}",
    "Newer upstream versions exist for third-party items: {count} (checked {age} ago).":
        "Ci sono versioni più nuove per cose di terzi: {count} (controllato {age} fa).",
    "upstream {new} available (the engine's default image is {old}; the server running it may differ)":
        "a monte c'è la {new} (l'immagine predefinita del motore è la {old}; quella che gira sul server può essere diversa)",
    "its publisher withdrew support for this version: {why}":
        "chi lo pubblica ha smesso di supportare questa versione: {why}",
    "Support withdrawn by the publisher: {names}": "Supporto ritirato da chi li pubblica: {names}",
    "no CLI is in its targets": "nessuna CLI è tra quelle a cui è destinato",
    "switched off in the manifest": "spento nel manifest",
    "neither core nor switched on": "non è essenziale e non è acceso",
    "nexgen skills bump  (or: nexgen mcp bump)  raises the ones the guardian cleared, tries each server and puts the old pin back if it fails.":
        "nexgen skills bump  (o: nexgen mcp bump)  alza quelle che il guardiano ha controllato, prova ogni server e, se non parte, rimette la versione di prima.",
    "Upstream versions not checked on this machine yet: the hourly watch will, or run nexgen skills bump.":
        "Su questa macchina le versioni a monte non sono ancora state controllate: lo fa il controllo orario, oppure lancia nexgen skills bump.",
    "Third-party pins are current (checked {age} ago).": "Le versioni di terzi sono aggiornate (controllato {age} fa).",
    "Trying the new versions before keeping them: {names}": "Provo le versioni nuove prima di tenerle: {names}",
    "could not try {names}: {error}": "non sono riuscito a provare {names}: {error}",
    "could not regenerate the configurations ({error})": "non sono riuscito a rigenerare le configurazioni ({error})",
    "the new version did not work: every pin is back where it was and the configurations regenerated":
        "la versione nuova non funziona: ogni versione è tornata com'era e le configurazioni sono state rigenerate",
    "{name}: the gateway does not offer it": "{name}: il gateway non lo offre",
    "{name}: it starts but lists no tools": "{name}: parte ma non mostra nessuno strumento",
    # --- Le skill del motore: seguire il motore invece di tenere copie ferme ---
    "differs in {files}; the engine's text has {added} lines yours lacks and yours has {removed} the engine's lacks":
        "è diversa in {files}; il testo del motore ha {added} righe che la tua non ha, la tua ne ha {removed} che il motore non ha",
    "Nothing to do: no skill in your Vault is a copy of one the engine ships.":
        "Niente da fare: nel tuo Vault nessuna skill è una copia di una che il motore porta con sé.",
    "Skills the engine ships and your Vault keeps its own copy of (the copy runs, so it does not follow engine updates):":
        "Skill che il motore porta con sé e di cui il tuo Vault tiene una copia (gira la copia, quindi non segue gli aggiornamenti del motore):",
    "nexgen skills adopt --all  follows the engine for the identical ones; name a skill (with --force) for one that differs.":
        "nexgen skills adopt --all  fa seguire il motore a quelle identiche; per una diversa nominala e aggiungi --force.",
    "Name skills or use --all, not both.": "Nomina le skill oppure usa --all, non tutte e due.",
    "Commit and publish with 'nexgen vault push' so the other machines follow too.":
        "Salva e pubblica con 'nexgen vault push' così le altre macchine ti seguono.",
    "identical to the engine's": "uguale a quella del motore",
    "not a skill the engine ships, or already followed from the engine":
        "non è una skill del motore, oppure segue già il motore",
    "would follow the engine (your copy kept in a backup folder)":
        "seguirebbe il motore (la tua copia resta in una cartella di backup)",
    "cannot back up the manifest, nothing written": "non riesco a fare il backup del manifest, non ho scritto niente",
    "the manifest no longer validates ({problems}); put back as it was":
        "il manifest non è più valido ({problems}); l'ho rimesso com'era",
    "follows the engine now; your copy is in {where}": "ora segue il motore; la tua copia è in {where}",
    "could not find a single `origin: vault` line in its manifest entry: edit it by hand":
        "non trovo una sola riga `origin: vault` nella sua voce del manifest: modificala a mano",
    "Left alone: look first, then --force if the engine's is the one you want.":
        "Lasciata stare: guardala prima, poi --force se vuoi quella del motore.",
    "follows the engine now; its old copy could not be moved away ({error})":
        "ora segue il motore; non sono riuscito a spostare la vecchia copia ({error})",
    "identical to the engine's: {names}": "uguali a quelle del motore: {names}",
    "different from the engine's: {names}": "diverse da quelle del motore: {names}",
    "Skills the engine ships are kept as copies in the Vault, so engine updates do not reach them ({parts}).":
        "Alcune skill del motore sono tenute come copie nel Vault, quindi gli aggiornamenti del motore non le raggiungono ({parts}).",
    "Run 'nexgen skills adopt --all' (identical ones) and look at the others with 'nexgen skills adopt'.":
        "Lancia 'nexgen skills adopt --all' (per quelle uguali) e guarda le altre con 'nexgen skills adopt'.",
    "No skill the engine ships is kept as a frozen copy in the Vault":
        "Nessuna skill del motore è tenuta come copia ferma nel Vault",
    # --- Primo avvio da pacchetto e gateway nel manifest ---------------------------
    "this folder is not a vault yet: clone the template into it first: git clone {url} {root}":
        "questa cartella non è ancora un Vault: prima clonaci dentro il modello: git clone {url} {root}",
    "The gateway ({gateway}) was not in the manifest, so it was added too: nothing behind it is reachable without it.":
        "Il gateway ({gateway}) non era nel manifest, quindi l'ho aggiunto anch'esso: senza, niente di ciò che ci sta dietro è raggiungibile.",
    "The gateway ({gateway}) is not in your manifest, and a trial is served by it. Adding any server with 'nexgen mcp add' puts it in; or copy the {gateway} entry from the shipped template.":
        "Il gateway ({gateway}) non è nel tuo manifest e una prova passa da lì. Aggiungere un server qualsiasi con 'nexgen mcp add' lo mette; oppure copia la voce {gateway} dal modello fornito.",
    'Could not read the Codex version of {which}, so the desktop app and the CLI were not compared.':
        'Non riesco a leggere la versione di Codex di {which}, quindi app desktop e CLI non sono stati confrontati.',
    'the one bundled in ChatGPT desktop':
        'quella inclusa in ChatGPT desktop',
    'the codex CLI':
        'la CLI codex',
    'ChatGPT desktop and the codex CLI run the same Codex version ({version})':
        'ChatGPT desktop e la CLI codex usano la stessa versione di Codex ({version})',
    'ChatGPT desktop bundles Codex {desktop} but the codex CLI is {cli}; both read the same ~/.codex, so each may run settings the other does not understand.':
        "ChatGPT desktop include Codex {desktop} ma la CLI codex è {cli}; leggono entrambi lo stesso ~/.codex, quindi ciascuno potrebbe eseguire impostazioni che l'altro non capisce.",
    "Chrome is responding on CDP port 9222 but has no top-level windows on {desktop}.":
        "Chrome risponde sulla porta CDP 9222 ma non ha finestre di primo livello su {desktop}.",
    "Run 'agent-chrome --heal' to restart Chrome on the user's desktop.":
        "Esegui 'agent-chrome --heal' per riavviare Chrome sul desktop dell'utente.",
    "Shared Chrome is up on {desktop} with CDP on 127.0.0.1:9222":
        "Il Chrome condiviso è attivo su {desktop} con CDP su 127.0.0.1:9222",
    "Antigravity's MCP configuration has not been rendered on this machine yet, so its copies were not compared.":
        'La configurazione MCP di Antigravity non è ancora stata generata su questa macchina, quindi le sue copie non sono state confrontate.',
    'points elsewhere':
        'punta altrove',
    'differs':
        'diversa',
    'missing':
        'mancante',
    'Antigravity reads its MCP servers from folders that no longer match the canonical file: {parts}':
        'Antigravity legge i server MCP da cartelle che non corrispondono più al file canonico: {parts}',
    "Run 'agent-sync apply' to point every folder back at the canonical file.":
        "Esegui 'agent-sync apply' per far puntare di nuovo ogni cartella al file canonico.",
    "Antigravity's MCP configuration is the same in every folder it reads from":
        'La configurazione MCP di Antigravity è la stessa in ogni cartella da cui legge',
    # --- OpenClaw: l'harness con l'interfaccia -----------------------------------
    # Rilevamento
    "npm is not on PATH, so a global OpenClaw cannot be found or installed":
        "npm non è nel PATH, quindi un OpenClaw globale non si trova e non si installa",
    "npm is not on PATH, so the version of {binary} cannot be read":
        "npm non è nel PATH, quindi non posso leggere la versione di {binary}",
    "npm did not say where its global packages live":
        "npm non ha detto dove tiene i pacchetti globali",
    "no npm package named 'openclaw' under {root}":
        "nessun pacchetto npm chiamato 'openclaw' sotto {root}",
    "the 'claude' command is not on PATH":
        "il comando 'claude' non è nel PATH",
    "on macOS the login lives in the Keychain: not verifiable from here":
        "su macOS il login sta nel Keychain: non è verificabile da qui",
    "no ~/.claude/.credentials.json: Claude Code is not logged in":
        "manca ~/.claude/.credentials.json: Claude Code non ha fatto il login",
    "the 'codex' command is not on PATH":
        "il comando 'codex' non è nel PATH",
    "no auth.json in {home}: Codex is not logged in":
        "manca auth.json in {home}: Codex non ha fatto il login",
    "no local model backend answered":
        "nessun backend di modelli locali ha risposto",
    # Il piano
    "The assistant is not created: no model is available for it.":
        "L'assistente non viene creato: non c'è nessun modello disponibile per lui.",
    "The code agent is not created: no model is available for it.":
        "L'agente del codice non viene creato: non c'è nessun modello disponibile per lui.",
    "The automation agent is not created: no model is available for it.":
        "L'agente delle automazioni non viene creato: non c'è nessun modello disponibile per lui.",
    "The automation agent is not created: the n8n module is not active on this machine.":
        "L'agente delle automazioni non viene creato: il modulo n8n non è attivo su questa macchina.",
    "The gateway token is a placeholder: 'nexgen openclaw setup' generates the real one.":
        "Il token del gateway è un segnaposto: 'nexgen openclaw setup' genera quello vero.",
    "The code agent runs Claude with high reasoning and the embedded runtime.":
        "L'agente del codice usa Claude con ragionamento alto e il runtime incorporato.",
    "The assistant runs on Codex; only the models this account has are configured.":
        "L'assistente gira su Codex; vengono configurati solo i modelli che questo account ha.",
    "No local model answered. The engine never downloads one: start it yourself, for example 'ollama serve' or 'llama-server -m <model>.gguf --port 8080', then ask again.":
        "Nessun modello locale ha risposto. Il motore non ne scarica: avvialo tu, per esempio 'ollama serve' oppure 'llama-server -m <model>.gguf --port 8080', poi richiedi di nuovo.",
    "The context window is unknown for these models and is not written: {models}":
        "La finestra di contesto di questi modelli non è nota e non viene scritta: {models}",
    "OpenClaw {version} ({path})":
        "OpenClaw {version} ({path})",
    "OpenClaw not installed{detail}":
        "OpenClaw non installato{detail}",
    "Claude: {state}":
        "Claude: {state}",
    "Codex: {state}":
        "Codex: {state}",
    "OpenCode Go: {state}":
        "OpenCode Go: {state}",
    "key present":
        "chiave presente",
    "no key":
        "nessuna chiave",
    "Local models: Ollama {ollama}, llama.cpp {llamacpp}":
        "Modelli locali: Ollama {ollama}, llama.cpp {llamacpp}",
    "Local models: none ({detail})":
        "Modelli locali: nessuno ({detail})",
    "no backend answered":
        "nessun backend ha risposto",
    "n8n: {state}":
        "n8n: {state}",
    "not active":
        "non attivo",
    "not found":
        "non trovato",
    "logged in":
        "login presente",
    "installed, not logged in":
        "installato, senza login",
    "installed, login not verifiable":
        "installato, login non verificabile",
    "OpenClaw plan for {path}":
        "Piano OpenClaw per {path}",
    "Agents:":
        "Agenti:",
    "The configuration at {path} cannot be read: {error}":
        "Non riesco a leggere la configurazione in {path}: {error}",
    "No configuration at {path} yet: everything below would be created.":
        "Non c'è ancora una configurazione in {path}: tutto quello che segue verrebbe creato.",
    "Configuration in force read from {path}.":
        "Configurazione in vigore letta da {path}.",
    "Sections this setup would change:":
        "Sezioni che questo setup cambierebbe:",
    "  added: {items}":
        "  da aggiungere: {items}",
    "  changed: {items}":
        "  da cambiare: {items}",
    "  yours, left untouched: {items}":
        "  tue, lasciate intatte: {items}",
    "  no longer proposed, left in place: {items}":
        "  non più proposte, lasciate al loro posto: {items}",
    "Nothing was written. 'nexgen openclaw setup' (B2) applies this plan.":
        "Non è stato scritto niente. 'nexgen openclaw setup' (B2) applica questo piano.",
    # Il doctor di OpenClaw
    "The OpenClaw module is declared on this machine but OpenClaw is not installed.":
        "Il modulo OpenClaw è dichiarato su questa macchina ma OpenClaw non è installato.",
    "Install the pinned release: {command}":
        "Installa la versione fissata: {command}",
    "OpenClaw {version} is installed, but the engine cannot read its pin.":
        "OpenClaw {version} è installato, ma il motore non riesce a leggere la versione fissata.",
    "Check agent-universal-layer/openclaw/pin.json: it holds the release the engine expects.":
        "Controlla agent-universal-layer/openclaw/pin.json: contiene la versione che il motore si aspetta.",
    "OpenClaw {version} is installed, the engine configures {pinned}.":
        "OpenClaw {version} è installato, il motore configura {pinned}.",
    "Align it with: {command}":
        "Allinealo con: {command}",
    "OpenClaw {version} is the pinned release.":
        "OpenClaw {version} è la versione fissata.",
    "OpenClaw is installed but this machine does not declare the module: the engine will not keep it aligned.":
        "OpenClaw è installato ma questa macchina non dichiara il modulo: il motore non lo terrà allineato.",
    "Declare 'openclaw: local' under this host in modules.state.yaml, or remove OpenClaw.":
        "Dichiara 'openclaw: local' sotto questa macchina in modules.state.yaml, oppure rimuovi OpenClaw.",
    "The configuration at {path} is valid JSON.":
        "La configurazione in {path} è JSON valido.",
    "Fix the file or move it aside; 'nexgen openclaw plan' shows what the engine would write.":
        "Sistema il file o spostalo da parte; 'nexgen openclaw plan' mostra cosa scriverebbe il motore.",
    "OpenClaw has no configuration at {path} yet.":
        "OpenClaw non ha ancora una configurazione in {path}.",
    "Run 'nexgen openclaw plan' to see what the engine would write.":
        "Lancia 'nexgen openclaw plan' per vedere cosa scriverebbe il motore.",
    "Ollama model ids carry ':latest', which OpenClaw does not recognise: {ids}":
        "Gli id dei modelli Ollama hanno ':latest', che OpenClaw non riconosce: {ids}",
    "Write the id without ':latest' and run 'nexgen openclaw setup' again.":
        "Scrivi l'id senza ':latest' e rilancia 'nexgen openclaw setup'.",
    "Every Ollama model id is written without ':latest'.":
        "Ogni id dei modelli Ollama è scritto senza ':latest'.",
    "This local model has no cage in tools.byProvider: {models}":
        "Questo modello locale non ha la gabbia in tools.byProvider: {models}",
    "Run 'nexgen openclaw plan', then setup: a local model must not write, execute or browse.":
        "Lancia 'nexgen openclaw plan' e poi setup: un modello locale non deve scrivere, eseguire né navigare.",
    "Every local model is caged ({count}).":
        "Ogni modello locale è nella gabbia ({count}).",
    "The engine plugin is not enabled here, so the evidence check is not installed.":
        "Qui il plugin del motore non è abilitato, quindi il controllo delle evidenze non c'è.",
    "The engine plugin is enabled but its evidence switch is missing: the check cannot run.":
        "Il plugin del motore è abilitato ma manca il suo interruttore delle evidenze: il controllo non può partire.",
    "Run 'nexgen sync': it deposits the switch beside the plugin.":
        "Lancia 'nexgen sync': deposita l'interruttore accanto al plugin.",
    "The evidence check is off: this machine names no local model whose answers it could check.":
        "Il controllo delle evidenze è spento: questa macchina non nomina nessun modello locale di cui controllare le risposte.",
    "The evidence check holds each answer of these local models to its own tool receipts: {providers}.":
        "Il controllo delle evidenze tiene ogni risposta di questi modelli locali alle ricevute dei suoi tool: {providers}.",
    "The evidence check covers {providers}, but OpenClaw does not let the plugin read the conversation, so no answer is checked.":
        "Il controllo delle evidenze copre {providers}, ma OpenClaw non lascia che il plugin legga la conversazione: nessuna risposta viene controllata.",
    "Run 'nexgen openclaw setup' with the gateway stopped: it grants plugins.entries.nexgen.hooks.allowConversationAccess.":
        "Lancia 'nexgen openclaw setup' con il gateway fermo: concede plugins.entries.nexgen.hooks.allowConversationAccess.",
    "The heartbeat is off, as the engine configures it.":
        "Il battito è spento, come lo configura il motore.",
    "The heartbeat runs every '{every}': it starts system jobs that write to the channels.":
        "Il battito gira ogni '{every}': avvia job di sistema che scrivono sui canali.",
    "(not set)":
        "(non impostato)",
    "Set agents.defaults.heartbeat.every to '0m'.":
        "Imposta agents.defaults.heartbeat.every a '0m'.",
    "The gateway binds to {bind}.":
        "Il gateway si lega a {bind}.",
    "The gateway binds to '{bind}', which is neither loopback nor the home network.":
        "Il gateway si lega a '{bind}', che non è né loopback né la rete di casa.",
    "The gateway binds to the home network, but its token is missing or still the placeholder.":
        "Il gateway si lega alla rete di casa, ma il suo token manca o è ancora il segnaposto.",
    "Run 'nexgen openclaw setup' to generate a real token, or set gateway.bind to 'loopback'.":
        "Lancia 'nexgen openclaw setup' per generare un token vero, oppure imposta gateway.bind a 'loopback'.",
    "The gateway uses token authentication, but the token is missing or still the placeholder.":
        "Il gateway usa l'autenticazione a token, ma il token manca o è ancora il segnaposto.",
    "Run 'nexgen openclaw setup': it generates the token once and never prints it.":
        "Lancia 'nexgen openclaw setup': genera il token una volta e non lo stampa mai.",
    "Node {version} satisfies OpenClaw's requirement ({requirement}).":
        "Node {version} soddisfa il requisito di OpenClaw ({requirement}).",
    "Node {version} does not satisfy OpenClaw's requirement ({requirement}).":
        "Node {version} non soddisfa il requisito di OpenClaw ({requirement}).",
    "Install a Node release in that range from {url}, then run 'nexgen doctor' again.":
        "Installa una versione di Node in quell'intervallo da {url}, poi rilancia 'nexgen doctor'.",
    "The Node version cannot be read, so OpenClaw's requirement ({requirement}) is unverified.":
        "Non riesco a leggere la versione di Node, quindi il requisito di OpenClaw ({requirement}) resta non verificato.",
    "Make sure 'node --version' answers in this shell, then run 'nexgen doctor' again.":
        "Assicurati che 'node --version' risponda in questa shell, poi rilancia 'nexgen doctor'.",
    "Lingering is enabled for {user}: the gateway survives logout.":
        "Il lingering è attivo per {user}: il gateway sopravvive al logout.",
    "Lingering is off for {user}: the gateway stops when the last session closes.":
        "Il lingering è spento per {user}: il gateway si ferma quando chiudi l'ultima sessione.",
    "Run 'loginctl enable-linger {user}' once.":
        "Lancia una volta 'loginctl enable-linger {user}'.",
    # --- Due PC: i nodi che il gateway conosce -----------------------------------
    "connected":
        "connesso",
    "not connected":
        "non connesso",
    "No node is paired with this gateway.":
        "Nessun nodo è accoppiato a questo gateway.",
    "Nodes this gateway knows: {items}":
        "Nodi che questo gateway conosce: {items}",
    "The gateway listens on the home network: a node on another PC can reach it.":
        "Il gateway ascolta sulla rete di casa: un nodo su un altro PC lo raggiunge.",
    "The gateway listens on this machine only ({bind}): a node on another PC cannot reach it.":
        "Il gateway ascolta solo su questa macchina ({bind}): un nodo su un altro PC non lo raggiunge.",
    "Run 'nexgen openclaw setup --lan' here and restart the gateway.":
        "Lancia qui 'nexgen openclaw setup --lan' e riavvia il gateway.",
    "Run 'nexgen openclaw setup --lan' here and restart the gateway, when a second machine has to reach it.":
        "Lancia qui 'nexgen openclaw setup --lan' e riavvia il gateway, quando una seconda macchina deve raggiungerlo.",
    "Also list the nodes the running gateway knows (starts OpenClaw's CLI once)":
        "Elenca anche i nodi che il gateway in esecuzione conosce (avvia una volta la CLI di OpenClaw)",
    "Nodes already paired:":
        "Nodi già accoppiati:",
    "Set gateway.bind to 'loopback', or 'lan' if you use the phone app on your home network.":
        "Imposta gateway.bind a 'loopback', oppure 'lan' se usi l'app sul telefono nella rete di casa.",
    # --- Come il gateway è raggiungibile -----------------------------------------
    "The gateway listens on loopback only: nothing outside this machine can reach it.":
        "Il gateway ascolta solo su loopback: niente fuori da questa macchina lo raggiunge.",
    "The gateway listens on loopback only and Tailscale Serve publishes it over HTTPS on the tailnet.":
        "Il gateway ascolta solo su loopback e Tailscale Serve lo pubblica in HTTPS sulla tailnet.",
    "The gateway listens on the home network ({bind}).":
        "Il gateway ascolta sulla rete di casa ({bind}).",
    "The gateway listens on the Tailscale address itself ({bind}), with no HTTPS in front.":
        "Il gateway ascolta direttamente sull'indirizzo Tailscale ({bind}), senza HTTPS davanti.",
    "Set gateway.bind to 'loopback' and gateway.tailscale.mode to 'serve' for an HTTPS address on the tailnet.":
        "Imposta gateway.bind a 'loopback' e gateway.tailscale.mode a 'serve' per un indirizzo HTTPS sulla tailnet.",
    "The gateway is published on the public internet through Tailscale Funnel, on {bind}.":
        "Il gateway è pubblicato su internet con Tailscale Funnel, su {bind}.",
    "Use gateway.tailscale.mode 'serve' unless the public URL is what you want.":
        "Usa gateway.tailscale.mode 'serve' a meno che l'URL pubblico non sia quello che vuoi.",
    "A gateway published through Tailscale Serve must stay on loopback: this one also listens on {bind}, so the port is reachable without the HTTPS Serve provides.":
        "Un gateway pubblicato con Tailscale Serve deve restare su loopback: questo ascolta anche su {bind}, quindi la porta è raggiungibile senza l'HTTPS che Serve dà.",
    "Set gateway.bind to 'loopback' and run 'nexgen openclaw setup --server' again.":
        "Imposta gateway.bind a 'loopback' e rilancia 'nexgen openclaw setup --server'.",
    "The gateway listens on every interface ({bind}): the network it sits on decides who reaches it.":
        "Il gateway ascolta su tutte le interfacce ({bind}): la rete in cui si trova decide chi lo raggiunge.",
    "Set gateway.bind to 'loopback', or to 'lan' when only the devices at home have to reach it.":
        "Imposta gateway.bind a 'loopback', oppure a 'lan' quando solo i dispositivi di casa devono raggiungerlo.",
    "The gateway is published through Tailscale Serve: a node on another PC reaches it by the gateway's MagicDNS name, and no port is opened here.":
        "Il gateway è pubblicato con Tailscale Serve: un nodo su un altro PC lo raggiunge con il nome MagicDNS del gateway, e qui nessuna porta viene aperta.",
    "Run 'nexgen openclaw node --host <host>.<tailnet>.ts.net' on the other machine.":
        "Lancia 'nexgen openclaw node --host <host>.<tailnet>.ts.net' sull'altra macchina.",
    "These configured channels have no agent binding: {names}. With explicit ownership their messages reach no agent.":
        "Questi canali configurati non hanno un binding verso un agente: {names}. Con l'ownership esplicita i loro messaggi non arrivano a nessuno.",
    "Run 'nexgen openclaw setup': it binds them to the main agent and leaves your bindings untouched.":
        "Lancia 'nexgen openclaw setup': li collega all'agente main e non tocca i tuoi binding.",
    "Every configured channel has an agent binding.":
        "Ogni canale configurato ha un binding verso un agente.",
    "Tool Search is active ({mode}): MCP and runtime tools are deferred behind search and call.":
        "Tool Search è attivo ({mode}): i tool MCP e di runtime sono differiti dietro ricerca e chiamata.",
    "Tool Search is disabled by a legacy engine setting; next setup will remove it.":
        "Tool Search è disabilitato da un residuo del motore: il prossimo setup lo toglie.",
    "Run 'nexgen openclaw setup' to retire the legacy setting and restore native Tool Search.":
        "Esegui 'nexgen openclaw setup' per rimuovere il residuo e ripristinare Tool Search.",
    "Tool Search is disabled by user configuration with {count} MCP servers mounted: direct schemas add large overhead to every prompt.":
        "Tool Search è disattivato dalla configurazione utente con {count} server MCP montati: gli schemi diretti appesantiscono ogni prompt.",
    "Remove 'tools.toolSearch' from openclaw.json to restore native Tool Search.":
        "Rimuovi 'tools.toolSearch' da openclaw.json per ripristinare il Tool Search nativo.",
    "Tool Search is disabled by user configuration ({count} MCP servers mounted, direct schemas in use).":
        "Tool Search è disattivato dalla configurazione utente ({count} server MCP montati, in uso gli schemi diretti).",
    "No lazy-mcp gateway is mounted in OpenClaw (connectors are direct).":
        "Nessun gateway lazy-mcp è montato in OpenClaw (i connettori sono diretti).",
    "The lazy-mcp gateway is mounted inside OpenClaw by the engine: OpenClaw connects MCP servers directly.":
        "Il gateway lazy-mcp è montato dentro OpenClaw dal motore: OpenClaw collega i server MCP direttamente.",
    "Run 'nexgen sync' or 'nexgen mcp render' to remove the engine's lazy-mcp entry.":
        "Esegui 'nexgen sync' o 'nexgen mcp render' per rimuovere la voce lazy-mcp del motore.",
    "A custom 'lazy-mcp' connector is configured under mcp.servers; OpenClaw does not use the engine gateway.":
        "Un connettore 'lazy-mcp' personalizzato è configurato in mcp.servers; OpenClaw non usa il gateway del motore.",
    "Verify if this custom connector is needed or rename it to avoid collision with the engine gateway.":
        "Verifica se questo connettore personalizzato serve o rinominalo per evitare collisioni con il gateway del motore.",
    "MCP server '{servers}' has a literal URL matching a secret declared in the manifest; credentials must not be stored in openclaw.json.":
        "Il server MCP '{servers}' ha un URL letterale che coincide con un segreto dichiarato nel manifest; le credenziali non devono essere salvate in openclaw.json.",
    "Run 'nexgen sync' or 'nexgen mcp render' to rewrite the connector through the bridge.":
        "Esegui 'nexgen sync' o 'nexgen mcp render' per riscrivere il connettore attraverso il ponte.",
    "No literal secret URLs in OpenClaw MCP configuration.":
        "Nessun URL segreto letterale nella configurazione MCP di OpenClaw.",
    "The setup adds a binding for these channels: {pairs}.":
        "Il setup aggiunge un binding per questi canali: {pairs}.",
    "The gateway asks for a token.":
        "Il gateway chiede un token.",
    "The gateway does not use token authentication (mode: {mode}).":
        "Il gateway non usa l'autenticazione a token (modalità: {mode}).",
    "Set gateway.auth.mode to 'token' and let 'nexgen openclaw setup' generate the token.":
        "Imposta gateway.auth.mode a 'token' e lascia che 'nexgen openclaw setup' generi il token.",
    "OpenClaw is configured with a Google subscription, which its terms do not allow: {names}":
        "OpenClaw è configurato con un abbonamento Google, che i suoi termini non permettono: {names}",
    "Remove it: use Claude, Codex, the OpenCode Go key or a local model.":
        "Rimuovilo: usa Claude, Codex, la chiave OpenCode Go o un modello locale.",
    "No Google subscription is configured in OpenClaw.":
        "Nessun abbonamento Google è configurato in OpenClaw.",
    "The local '{backend}' CLI backend runs 'agy' with permissionMode '{mode}': 'agy' approves every tool call by itself, with no approval from a person.":
        "Il backend CLI locale '{backend}' lancia 'agy' con permissionMode '{mode}': 'agy' approva da solo ogni chiamata di tool, senza l'approvazione di una persona.",
    "Set plugins.entries.{backend}.config.permissionMode to 'sandbox' or 'settings'.":
        "Imposta plugins.entries.{backend}.config.permissionMode a 'sandbox' o 'settings'.",
    "The local '{backend}' CLI backend is configured: 'agy' keeps its own login and OpenClaw holds no Google credential (permissionMode '{mode}').":
        "Il backend CLI locale '{backend}' è configurato: 'agy' conserva il proprio login e OpenClaw non detiene nessuna credenziale Google (permissionMode '{mode}').",
    "The local '{backend}' CLI backend declares permissionMode '{mode}', which is neither 'skip' nor 'sandbox'/'settings': the doctor cannot say whether 'agy' approves a tool call by itself.":
        "Il backend CLI locale '{backend}' dichiara permissionMode '{mode}', che non è né 'skip' né 'sandbox'/'settings': il doctor non può dire se 'agy' approva da solo una chiamata di tool.",
    "The ACP bridge allows the 'gemini' agent; its terms do not allow it outside Google's clients.":
        "Il bridge ACP permette l'agente 'gemini'; i termini non lo permettono fuori dai client Google.",
    "Remove 'gemini' from acp.allowedAgents.":
        "Togli 'gemini' da acp.allowedAgents.",
    "The ACP bridge does not allow a Google agent.":
        "Il bridge ACP non permette nessun agente Google.",
    "These agents use a subscription this machine does not have: {items}":
        "Questi agenti usano un abbonamento che questa macchina non ha: {items}",
    "Log in to the subscription, or run 'nexgen openclaw plan' to see the model the engine would choose.":
        "Fai il login dell'abbonamento, oppure lancia 'nexgen openclaw plan' per vedere il modello che sceglierebbe il motore.",
    "Every agent uses a model this machine can reach.":
        "Ogni agente usa un modello che questa macchina raggiunge.",
    "The model override policy is explicit, so the model map does not restrict it.":
        "La policy di scelta del modello è esplicita, quindi la mappa dei modelli non la limita.",
    "No model override allowlist is in force: any model this machine reaches can be chosen.":
        "Non c'è nessuna lista bianca attiva: si può scegliere qualsiasi modello che questa macchina raggiunge.",
    "`agents.defaults.models` carries no `agents.defaults.modelPolicy`, so OpenClaw treats its {count} entries as the only models an agent may switch to: {refs}":
        "`agents.defaults.models` non ha nessuna `agents.defaults.modelPolicy`, quindi OpenClaw tratta le sue {count} voci come gli unici modelli a cui un agente può passare: {refs}",
    "Write `agents.defaults.modelPolicy.allow` with the exact refs or the `provider/*` entries a person may choose, or an empty `modelPolicy: {}` to lift the list.":
        "Scrivi `agents.defaults.modelPolicy.allow` con i riferimenti esatti o le voci `provider/*` che una persona può scegliere, oppure una `modelPolicy: {}` vuota per togliere la lista.",
    "No background job runs on a local model.":
        "Nessun lavoro di fondo gira su un modello locale.",
    "These background jobs would run on the local model {models}: {jobs}.":
        "Questi lavori di fondo girerebbero sul modello locale {models}: {jobs}.",
    "Run 'nexgen openclaw setup': it points them at the cloud model an agent already uses.":
        "Lancia 'nexgen openclaw setup': li sposta sul modello cloud che un agente già usa.",
    "dreaming":
        "dreaming",
    "compaction":
        "compattazione",
    "the memory flush":
        "memory flush",
    "active memory":
        "active memory",
    "The gateway watches the whole of {root} with one recursive handle: every file created under it wakes the configuration reload.":
        "Il gateway guarda {root} per intero con un solo handle ricorsivo: ogni file creato lì dentro sveglia il ricaricamento della configurazione.",
    "Run 'nexgen openclaw setup': it adds env.vars.{name} to the configuration. Restart the gateway once afterwards.":
        "Lancia 'nexgen openclaw setup': aggiunge env.vars.{name} alla configurazione. Poi riavvia il gateway una volta.",
    "The configuration tells the gateway to poll instead of watching {root} recursively; that applies from the next gateway start.":
        "La configurazione dice al gateway di interrogare il file invece di guardare {root} in modo ricorsivo; vale dal prossimo avvio del gateway.",
    "The gateway watches {root}, which does not contain your home.":
        "Il gateway guarda {root}, che non contiene la tua home.",
    "The gateway's root is {root}, and on Linux it watches one directory rather than the whole tree.":
        "La radice del gateway è {root}, e su Linux guarda una cartella invece di tutto l'albero.",
    "Polling is forced, but the gateway watches {root} and not your home: nothing needs it any more.":
        "Il polling è forzato, ma il gateway guarda {root} e non la tua home: non serve più a niente.",
    "Remove env.vars.{name} from {path} and restart the gateway.":
        "Togli env.vars.{name} da {path} e riavvia il gateway.",
    "OpenClaw changes how it watches its configuration only when it starts: restart the gateway once for this key to take effect.":
        "OpenClaw cambia il modo in cui guarda la sua configurazione solo quando parte: riavvia il gateway una volta perché questa chiave abbia effetto.",
    "The gateway answers on 127.0.0.1:{port}.":
        "Il gateway risponde su 127.0.0.1:{port}.",
    "The gateway is not listening on 127.0.0.1:{port} (normal when it is stopped).":
        "Il gateway non è in ascolto su 127.0.0.1:{port} (è normale quando è fermo).",
    "A second OpenClaw from the Microsoft Store is installed ({names}): two gateways in conflict.":
        "È installato un secondo OpenClaw dal Microsoft Store ({names}): due gateway in conflitto.",
    "Remove the Microsoft Store app: the engine manages the npm one.":
        "Rimuovi l'app del Microsoft Store: il motore gestisce quello npm.",
    "Agent {agent}: {count} learned skills created or changed in the last 24 hours.":
        "Agente {agent}: {count} skill imparate create o cambiate nelle ultime 24 ore.",
    "Review what {agent} learned: more than ten new procedures in a day is a lot to keep.":
        "Rivedi cosa ha imparato {agent}: più di dieci procedure nuove in un giorno sono tante da tenere.",
    "the app-owned WSL distribution {name}":
        "la distribuzione WSL dell'app {name}",
    "the app's own WSL storage":
        "lo spazio WSL dell'app",
    "its settings name a gateway on this PC":
        "le sue impostazioni nominano un gateway su questo PC",
    "its settings name a gateway elsewhere":
        "le sue impostazioni nominano un gateway altrove",
    "the app's isolated native gateway":
        "il gateway nativo isolato dell'app",
    "The OpenClaw Companion app points to its own isolated native gateway ({signals}) and the engine gateway is here too.":
        "L'app OpenClaw Companion punta al proprio gateway nativo isolato ({signals}) e c'è anche il gateway del motore.",
    "In OpenClaw Companion choose 'a local Gateway on this PC' with the engine gateway address, and turn off autostart for the native gateway (or uninstall the OpenClaw Gateway package; the app remains).":
        "Nell'app scegliere 'gateway su questo PC' con l'indirizzo del gateway del motore, e spegnere l'avvio automatico del gateway nativo (o disinstallare il pacchetto OpenClaw Gateway, l'app resta).",
    "The OpenClaw Companion app points to its own isolated native gateway ({signals}). This version of the engine configures the native Node gateway, not the isolated one: it writes nothing here and starts no second gateway.":
        "L'app OpenClaw Companion punta al proprio gateway nativo isolato ({signals}). Questa versione del motore configura il gateway Node nativo, non quello isolato: non scrive nulla qui e non avvia un secondo gateway.",
    "The OpenClaw Companion app runs the gateway on this PC ({signals}). This version of the engine configures the native gateway, not the app's one: it writes nothing here and starts no second gateway.":
        "L'app OpenClaw Companion esegue il gateway su questo PC ({signals}). Questa versione del motore configura il gateway nativo, non quello dell'app: qui non scrive niente e non avvia un secondo gateway.",
    "Use the app as it is, or install the native gateway with {command} and run 'nexgen openclaw setup'.":
        "Usa l'app così com'è, oppure installa il gateway nativo con {command} e lancia 'nexgen openclaw setup'.",
    "The OpenClaw Companion app runs a gateway of its own on this PC ({signals}) and the native gateway is here too: two gateways in conflict.":
        "L'app OpenClaw Companion esegue un gateway suo su questo PC ({signals}) e c'è anche il gateway nativo: due gateway in conflitto.",
    "Keep the one you want: in OpenClaw Companion open Connections and point it at this PC's gateway, or stay with the app's one and remove the native gateway with 'npm uninstall -g openclaw'.":
        "Tieni quello che vuoi: in OpenClaw Companion apri Connections e puntalo al gateway di questo PC, oppure resta su quello dell'app e rimuovi il gateway nativo con 'npm uninstall -g openclaw'.",
    "The OpenClaw Companion app is connected to a gateway elsewhere. Nothing here is in conflict: this version of the engine configures the native gateway on this PC only.":
        "L'app OpenClaw Companion è collegata a un gateway altrove. Qui non c'è nessun conflitto: questa versione del motore configura solo il gateway nativo di questo PC.",
    "Nothing to do: the app keeps its own gateway.":
        "Niente da fare: l'app tiene il suo gateway.",
    "The OpenClaw Companion app is installed and names no gateway on this PC.":
        "L'app OpenClaw Companion è installata e non nomina nessun gateway su questo PC.",
    "In OpenClaw Companion open Connections and choose 'a local Gateway on this PC', then 'nexgen openclaw pair' approves the request it leaves.":
        "In OpenClaw Companion apri Connections e scegli 'a local Gateway on this PC', poi 'nexgen openclaw pair' approva la richiesta che l'app lascia.",
    "The OpenClaw Companion app is set to this PC's gateway on port {port}.":
        "L'app OpenClaw Companion è puntata al gateway di questo PC sulla porta {port}.",
    "The gateway lists no device for it yet: 'nexgen openclaw pair' shows the request the app leaves and approves it.":
        "Il gateway non elenca ancora un dispositivo per l'app: 'nexgen openclaw pair' mostra la richiesta che l'app lascia e la approva.",
    "The gateway lists its device.":
        "Il gateway elenca il suo dispositivo.",
    "The gateway lists {count} device(s), none of them naming the app: 'nexgen openclaw pair' shows the request and approves it.":
        "Il gateway elenca {count} dispositivi, nessuno dei quali nomina l'app: 'nexgen openclaw pair' mostra la richiesta e la approva.",
    "The OpenClaw Companion app runs the gateway on this PC ({signals}) and there is no native one.":
        "L'app OpenClaw Companion esegue il gateway su questo PC ({signals}) e non ce n'è uno nativo.",
    "The OpenClaw Companion app points to its own isolated native gateway ({signals}) and there is no native one.":
        "L'app OpenClaw Companion punta al proprio gateway nativo isolato ({signals}) e non ce n'è uno nativo.",
    "This version of the engine configures the native Node gateway, which is the road it supports, not the app's one: nothing was installed and nothing was written, because a native gateway beside it would fight over the same port.":
        "Questa versione del motore configura il gateway nativo Node, che è la strada che supporta, non quello dell'app: non è stato installato né scritto niente, perché un gateway nativo accanto a quello dell'app si batterebbe per la stessa porta.",
    "To use the app with the engine, open Connections in OpenClaw Companion and choose 'a local Gateway on this PC'.":
        "Per usare l'app con il motore, apri Connections in OpenClaw Companion e scegli 'a local Gateway on this PC'.",
    "Two ways forward: use the app as it is, or install the native gateway with {command} and run this setup again.":
        "Due strade: usa l'app così com'è, oppure installa il gateway nativo con {command} e rilancia questo setup.",
    "To take the second road while the app's gateway is still here, stop it in OpenClaw Companion and run this setup again with --node-gateway.":
        "Per prendere la seconda strada mentre il gateway dell'app c'è ancora, fermalo in OpenClaw Companion e rilancia questo setup con --node-gateway.",
    "Something answers on 127.0.0.1:{port} already: stop the app's gateway in OpenClaw Companion and run this setup again.":
        "Su 127.0.0.1:{port} qualcosa risponde già: ferma il gateway dell'app in OpenClaw Companion e rilancia questo setup.",
    "Two gateways cannot share the port, and this setup never starts, stops or writes the app's distribution.":
        "Due gateway non possono dividersi la porta, e questo setup non avvia, non ferma e non scrive mai la distribuzione dell'app.",
    "Going ahead with the native gateway: 127.0.0.1:{port} is free.":
        "Vado avanti con il gateway nativo: 127.0.0.1:{port} è libera.",
    "The app can be pointed at it: in OpenClaw Companion open Connections and choose 'a local Gateway on this PC'.":
        "L'app può essere puntata lì: in OpenClaw Companion apri Connections e scegli 'a local Gateway on this PC'.",
    ", and it is connected to this PC's gateway on port {port}":
        ", e l'app è collegata al gateway di questo PC sulla porta {port}",
    ", and it is connected to a gateway of its own on port {port}":
        ", e l'app è collegata a un gateway suo sulla porta {port}",
    "The OpenClaw Companion app runs the gateway on this PC ({signals}): this engine configures the native Node gateway, which is the road it supports, and cannot write the app's one. To use the app with the engine, open Connections in OpenClaw Companion and choose 'a local Gateway on this PC'; the setup stops here until then, and runs beside the app's gateway only with --node-gateway.":
        "L'app OpenClaw Companion esegue il gateway su questo PC ({signals}): questo motore configura il gateway nativo Node, che è la strada che supporta, e non può scrivere quello dell'app. Per usare l'app con il motore, apri Connections in OpenClaw Companion e scegli 'a local Gateway on this PC'; fino ad allora il setup si ferma qui, e gira accanto al gateway dell'app solo con --node-gateway.",
    "The OpenClaw Companion app points to its own isolated native gateway ({signals}): this engine configures the native Node gateway, which is the road it supports, and cannot write the app's isolated one. To use the app with the engine, open Connections in OpenClaw Companion and choose 'a local Gateway on this PC'; the setup stops here until then, and runs beside the app's gateway only with --node-gateway.":
        "L'app OpenClaw Companion punta al proprio gateway nativo isolato ({signals}): questo motore configura il gateway nativo Node, che è la strada che supporta, e non può scrivere quello isolato dell'app. Per usare l'app con il motore, apri Connections in OpenClaw Companion e scegli 'a local Gateway on this PC'; fino ad allora il setup si ferma qui, ed esegue accanto al gateway dell'app solo con --node-gateway.",
    "The OpenClaw Companion app has a gateway of its own on this PC ({signals}){uses}: the setup below writes the native one.":
        "L'app OpenClaw Companion ha un gateway suo su questo PC ({signals}){uses}: il setup qui sotto scrive quello nativo.",
    "The OpenClaw Companion app points to its own isolated native gateway ({signals}){uses}: the setup below writes the native one.":
        "L'app OpenClaw Companion punta al proprio gateway nativo isolato ({signals}){uses}: il setup qui sotto scrive quello nativo.",
    "The OpenClaw Companion app is connected to a gateway elsewhere: nothing here is in conflict, and the setup below writes the native gateway on this PC only.":
        "L'app OpenClaw Companion è collegata a un gateway altrove: qui non c'è nessun conflitto, e il setup qui sotto scrive solo il gateway nativo di questo PC.",
    "The OpenClaw Companion app is installed and names no gateway on this PC: once the setup below has configured this PC's gateway, open Connections in OpenClaw Companion and choose 'a local Gateway on this PC'.":
        "L'app OpenClaw Companion è installata e non nomina nessun gateway su questo PC: quando il setup qui sotto ha configurato il gateway di questo PC, apri Connections in OpenClaw Companion e scegli 'a local Gateway on this PC'.",    "Session continuity checkpoint is enabled.":
        "Il checkpoint di continuità della sessione è abilitato.",
    "Session continuity checkpoint is disabled.":
        "Il checkpoint di continuità della sessione è disabilitato.",
    "Enable 'checkpoint' in {path} or run 'nexgen sync'.":
        "Abilita 'checkpoint' in {path} o lancia 'nexgen sync'.",
    "{passed} passed, {warned} warnings, {failed} failures":
        "{passed} superati, {warned} avvisi, {failed} errori",
    # Il comando
    "OpenClaw, the harness with the interface (plan and doctor)":
        "OpenClaw, l'harness con l'interfaccia (plan e doctor)",
    "Read-only: what the engine would configure, and what stays yours":
        "Sola lettura: cosa configurerebbe il motore e cosa resta tuo",
    "Also reachable from the home network (the phone app)":
        "Raggiungibile anche dalla rete di casa (l'app sul telefono)",
    "Allow the acpx plugin too":
        "Permetti anche il plugin acpx",
    "Check OpenClaw's installation and configuration":
        "Controlla installazione e configurazione di OpenClaw",
    "OpenClaw is not installed here and this machine does not declare the module: nothing to check.":
        "OpenClaw non è installato qui e questa macchina non dichiara il modulo: non c'è niente da controllare.",
    # L'installazione con un comando (unità B2)
    "Installation":
        "Installazione",
    "Initial configuration":
        "Configurazione iniziale",
    "Plan and confirmation":
        "Piano e conferma",
    "Safe write":
        "Scrittura sicura",
    "Agent workspaces":
        "Cartelle degli agenti",
    "Gateway service":
        "Servizio del gateway",
    "Module":
        "Modulo",
    "Phone":
        "Telefono",
    "Setup complete.":
        "Installazione completata.",
    "OpenClaw {version} is already the pinned release.":
        "OpenClaw {version} è già la versione fissata.",
    "Node is not installed and OpenClaw needs it. Install it from {url}, then run the setup again.":
        "Node non è installato e OpenClaw ne ha bisogno. Installalo da {url}, poi rilancia il setup.",
    "Node {version} does not satisfy what OpenClaw {pinned} needs ({requirement}).":
        "Node {version} non soddisfa quello che OpenClaw {pinned} richiede ({requirement}).",
    "Install a Node release in that range from {url}, then run the setup again.":
        "Installa una versione di Node in quell'intervallo da {url}, poi rilancia il setup.",
    "The systemd user bus is not available in this session, so the gateway service cannot be managed.":
        "Il bus utente di systemd non è disponibile in questa sessione, quindi il servizio del gateway non si può gestire.",
    "Log in to a normal user session with systemd, or start one, then run the setup again.":
        "Entra in una normale sessione utente con systemd, o avviane una, poi rilancia il setup.",
    "npm installs global packages under {prefix}, which this user cannot write.":
        "npm installa i pacchetti globali in {prefix}, dove questo utente non può scrivere.",
    "Run 'npm config set prefix ~/.local' once, or use a version manager for Node, then run the setup again.":
        "Lancia una volta 'npm config set prefix ~/.local', oppure usa un version manager per Node, poi rilancia il setup.",
    "Lingering is off for {user}: the gateway stops when your last session closes.":
        "Il lingering è spento per {user}: il gateway si ferma quando chiudi l'ultima sessione.",
    "Run 'loginctl enable-linger {user}' once, then run the setup again.":
        "Lancia una volta 'loginctl enable-linger {user}', poi rilancia il setup.",
    # --- Un gateway su una macchina accesa, senza schermo ------------------------
    "Server mode: the gateway stays on loopback and Tailscale Serve publishes it over HTTPS on the tailnet, so no port is opened.":
        "Modo server: il gateway resta su loopback e Tailscale Serve lo pubblica in HTTPS sulla tailnet, quindi nessuna porta viene aperta.",
    "--server is for a Linux machine with no screen, and this one runs {platform}.":
        "--server è per una macchina Linux senza schermo, e questa gira {platform}.",
    "Run 'nexgen openclaw setup' without --server to configure a desktop.":
        "Lancia 'nexgen openclaw setup' senza --server per configurare un desktop.",
    "Tailscale is not installed, and this setup never installs a system program on its own.":
        "Tailscale non è installato, e questo setup non installa mai un programma di sistema da solo.",
    "Install it from {url}, run 'tailscale up' and log in, then run this setup again.":
        "Installalo da {url}, lancia 'tailscale up' e fai il login, poi rilancia il setup.",
    "Tailscale is installed but not connected ({state}): there is no address to publish the gateway on.":
        "Tailscale è installato ma non collegato ({state}): non c'è un indirizzo su cui pubblicare il gateway.",
    "Run 'tailscale up' and log in, then run this setup again.":
        "Lancia 'tailscale up' e fai il login, poi rilancia il setup.",
    "no state reported":
        "nessuno stato riportato",
    "Tailscale is connected ({state}) and this account holds no root power: this machine can host the gateway.":
        "Tailscale è collegato ({state}) e questo account non ha poteri da root: questa macchina può ospitare il gateway.",
    "This account is root, and a gateway that runs agents must not run with root power.":
        "Questo account è root, e un gateway che esegue agenti non deve girare con poteri da root.",
    "This account can become root without a password ('sudo -n true' succeeds), and a gateway that runs agents must not carry that power.":
        "Questo account può diventare root senza password ('sudo -n true' riesce), e un gateway che esegue agenti non deve portarsi quel potere.",
    "This account is in the 'docker' group, which is root through a container, and a gateway that runs agents must not carry that power.":
        "Questo account è nel gruppo 'docker', che è root attraverso un container, e un gateway che esegue agenti non deve portarsi quel potere.",
    "Create the account the gateway will use without sudo and without the docker group, and run the setup from there.":
        "Crea l'account che userà il gateway senza sudo e senza il gruppo docker, e lancia il setup da lì.",
    "If you accept the risk, run the same command again with --yes-i-know.":
        "Se accetti il rischio, rilancia lo stesso comando con --yes-i-know.",
    "--yes-i-know was given: the run goes on, and this line records what the gateway keeps.":
        "--yes-i-know è stato dato: l'esecuzione va avanti, e questa riga registra cosa si tiene il gateway.",
    "--server and --lan are opposites: --lan opens the gateway to the network it is on, --server keeps it on loopback and publishes it through Tailscale Serve. Choose one.":
        "--server e --lan sono opposti: --lan apre il gateway alla rete in cui si trova, --server lo tiene su loopback e lo pubblica con Tailscale Serve. Scegline uno.",
    "A Linux machine with no screen: loopback plus Tailscale Serve for HTTPS on the tailnet":
        "Una macchina Linux senza schermo: loopback più Tailscale Serve per l'HTTPS sulla tailnet",
    "Go on even when this account holds sudo or the docker group":
        "Va avanti anche se questo account ha sudo o il gruppo docker",
    "npm is not beside Node. Install Node from {url}: it brings npm with it.":
        "npm non è accanto a Node. Installa Node da {url}: porta npm con sé.",
    "The engine cannot read its OpenClaw pin, so it will not install an unknown release. Check agent-universal-layer/openclaw/pin.json and run the setup again.":
        "Il motore non riesce a leggere il pin di OpenClaw, quindi non installa una versione sconosciuta. Controlla agent-universal-layer/openclaw/pin.json e rilancia il setup.",
    "OpenClaw {version} is not the pinned {pinned}: installing the pin.":
        "OpenClaw {version} non è la versione fissata {pinned}: installo quella.",
    "OpenClaw is not installed: installing {pinned}.":
        "OpenClaw non è installato: installo {pinned}.",
    "The install failed (exit {code}).":
        "L'installazione è fallita (uscita {code}).",
    "OpenClaw {pinned} is installed.":
        "OpenClaw {pinned} è installato.",
    "Note: 'openclaw' is still not on PATH; a new terminal may be needed.":
        "Nota: 'openclaw' non è ancora nel PATH; può servire un terminale nuovo.",
    "The configuration at {path} already exists.":
        "La configurazione in {path} esiste già.",
    "No configuration yet: letting OpenClaw create the baseline, with no questions.":
        "Non c'è ancora una configurazione: lascio che OpenClaw crei la base, senza domande.",
    "Onboarding failed (exit {code}); nothing was written.":
        "L'onboarding è fallito (uscita {code}); non è stato scritto niente.",
    "Onboarding finished but {path} was not created.":
        "L'onboarding è finito ma {path} non è stato creato.",
    "OpenClaw created {path}.":
        "OpenClaw ha creato {path}.",
    "Write this configuration and start the gateway?":
        "Scrivo questa configurazione e avvio il gateway?",
    "Nothing written: the plan above was not applied.":
        "Non ho scritto niente: il piano qui sopra non è stato applicato.",
    "Commands this run would execute:":
        "Comandi che questa esecuzione lancerebbe:",
    "the gateway service, only if it is not installed yet":
        "il servizio del gateway, solo se non è ancora installato",
    "Dry run: nothing was written and nothing was executed.":
        "Prova a vuoto: non ho scritto e non ho eseguito niente.",
    "The engine plugin {plugin} is deposited and enabled.":
        "Il plugin del motore {plugin} è depositato e abilitato.",
    "The engine plugin {plugin} is not deposited on this machine: not enabled.":
        "Il plugin del motore {plugin} non è depositato su questa macchina: non lo abilito.",
    "The file in force cannot be read ({error}); it is backed up before it is replaced.":
        "Il file in vigore non è leggibile ({error}); ne faccio una copia prima di sostituirlo.",
    "The configuration is already what the engine would write: nothing to write.":
        "La configurazione è già quella che scriverebbe il motore: non c'è niente da scrivere.",
    "The gateway did not stop ({pids}); its file was left alone.":
        "Il gateway non si è fermato ({pids}); il suo file è rimasto com'era.",
    "The previous configuration is saved as {name}.":
        "La configurazione precedente è salvata come {name}.",
    "Configuration written to {path}.":
        "Configurazione scritta in {path}.",
    "The gateway token was generated for this machine and is not shown.":
        "Il token del gateway è stato generato per questa macchina e non viene mostrato.",
    "OpenClaw accepts the configuration.":
        "OpenClaw accetta la configurazione.",
    "The gateway starts again now, so it reads the new configuration.":
        "Il gateway riparte adesso, così legge la nuova configurazione.",
    "Starting the gateway: a cold start takes about five minutes.":
        "Avvio il gateway: un avvio a freddo dura circa cinque minuti.",
    "Waiting for the gateway the write step restarted: a cold start takes about five minutes.":
        "Aspetto il gateway riavviato dalla scrittura: un avvio a freddo dura circa cinque minuti.",
    "Waiting for the gateway the install started: a cold start takes about five minutes.":
        "Aspetto il gateway avviato dall'installazione: un avvio a freddo dura circa cinque minuti.",
    "OpenClaw rejected the configuration (exit {code}): the previous file was put back.":
        "OpenClaw ha rifiutato la configurazione (uscita {code}): ho rimesso il file precedente.",
    "The gateway is already installed as a service ({mechanism}).":
        "Il gateway è già installato come servizio ({mechanism}).",
    "Gateway service install: {state}":
        "Installazione del servizio del gateway: {state}",
    "done":
        "fatto",
    "failed":
        "fallito",
    "The gateway already answers on 127.0.0.1:{port}.":
        "Il gateway risponde già su 127.0.0.1:{port}.",
    "Waiting for the gateway on 127.0.0.1:{port}: {seconds} seconds so far.":
        "Aspetto il gateway su 127.0.0.1:{port}: {seconds} secondi finora.",
    "The gateway answers on 127.0.0.1:{port} after {seconds} seconds.":
        "Il gateway risponde su 127.0.0.1:{port} dopo {seconds} secondi.",
    "The gateway did not answer on 127.0.0.1:{port} within {seconds} seconds.":
        "Il gateway non ha risposto su 127.0.0.1:{port} entro {seconds} secondi.",
    "This machine declares 'openclaw: local' for host {host}.":
        "Questa macchina dichiara 'openclaw: local' per l'host {host}.",
    "The module declaration did not land: {error}":
        "La dichiarazione del modulo non è andata a buon fine: {error}",
    "Then approve the request from here: 'nexgen openclaw pair' lists it, 'nexgen openclaw pair --approve <id>' approves it.":
        "Poi approva la richiesta da qui: 'nexgen openclaw pair' la elenca, 'nexgen openclaw pair --approve <id>' la approva.",
    "To connect the phone, run this in a terminal of yours: {command}":
        "Per collegare il telefono, lancia questo in un tuo terminale: {command}",
    "Restarting the gateway: a cold start takes about five minutes.":
        "Riavvio il gateway: un avvio a freddo dura circa cinque minuti.",
    "Stop the gateway before editing its file by hand":
        "Ferma il gateway prima di modificare a mano il suo file",
    "The gateway is stopped.":
        "Il gateway è fermo.",
    "The gateway did not stop: these processes are still alive ({pids}).":
        "Il gateway non si è fermato: questi processi sono ancora vivi ({pids}).",
    "The gateway did not answer: {error}":
        "Il gateway non ha risposto: {error}",
    "no answer from the port":
        "nessuna risposta dalla porta",
    "Waited {seconds} seconds.":
        "Ho aspettato {seconds} secondi.",
    "Requests waiting for approval:":
        "Richieste in attesa di approvazione:",
    "No device is waiting to be paired.":
        "Nessun dispositivo è in attesa di accoppiamento.",
    "Approve one with 'nexgen openclaw pair --approve <id>'.":
        "Approva una con 'nexgen openclaw pair --approve <id>'.",
    "Approve device {id}?":
        "Approvo il dispositivo {id}?",
    "Nothing approved.":
        "Non ho approvato niente.",
    "Device {id} approved.":
        "Dispositivo {id} approvato.",
    "No node is waiting for approval.":
        "Nessun nodo è in attesa di approvazione.",
    "Node requests waiting for approval, with the commands they would expose:":
        "Richieste dei nodi in attesa di approvazione, con i comandi che esporrebbero:",
    "{node}, commands: {commands}":
        "{node}, comandi: {commands}",
    "Approve node {node}?":
        "Approvo il nodo {node}?",
    "No node request {id} is waiting: nothing was approved.":
        "Nessuna richiesta di nodo {id} è in attesa: non ho approvato niente.",
    "Node {node} approved.":
        "Nodo {node} approvato.",
    "Approve a node with 'nexgen openclaw pair --approve-node <id>'.":
        "Approva un nodo con 'nexgen openclaw pair --approve-node <id>'.",
    # --- Due PC: un gateway e un nodo -------------------------------------------
    "Connect this machine as a node of a gateway on another PC":
        "Collega questa macchina come nodo di un gateway su un altro PC",
    "The gateway machine's address: its name on the home network, or its Tailscale MagicDNS name":
        "L'indirizzo della macchina del gateway: il suo nome sulla rete di casa, oppure il suo nome MagicDNS di Tailscale",
    "The gateway's port (default: 18789)":
        "La porta del gateway (predefinita: 18789)",
    "The name the gateway shows for this machine":
        "Il nome che il gateway mostra per questa macchina",
    "Cannot propose the node connection: {error}":
        "Non riesco a proporre il collegamento del nodo: {error}",
    "OpenClaw node for the gateway at {address}":
        "Nodo OpenClaw per il gateway su {address}",
    "Nothing has been changed: these are OpenClaw's own commands, in the order they work.":
        "Non ho cambiato niente: questi sono i comandi di OpenClaw, nell'ordine in cui funzionano.",
    "on the gateway machine":
        "sulla macchina del gateway",
    "on this machine":
        "su questa macchina",
    "check: {command}":
        "controllo: {command}",
    "What stays yours:":
        "Cosa resta a te:",
    "One paste, with a join code the gateway mints":
        "Un solo incollaggio, con un codice di ingresso che crea il gateway",
    "OpenClaw approves the device and its first command surface in the same step.":
        "OpenClaw approva il dispositivo e la sua prima superficie di comandi nello stesso passaggio.",
    "Single use, ten minutes, administrator access: treat the printed URL like a password.":
        "Monouso, dieci minuti, accesso da amministratore: tratta l'URL stampato come una password.",
    "the command it prints, to be pasted as it is on this machine":
        "il comando che stampa, da incollare così com'è su questa macchina",
    "This is the one paste that enrols the device and its first commands together; --session-host also lets it run agent sessions, so keep it to a machine you trust as shared infrastructure.":
        "È l'unico incollaggio che registra insieme il dispositivo e i suoi primi comandi; --session-host gli lascia anche eseguire sessioni di agente, quindi usalo solo su una macchina di cui ti fidi come infrastruttura condivisa.",
    "One approval at a time, without a join code":
        "Un'approvazione alla volta, senza codice di ingresso",
    "Two requests with different ids: the device admits the connection, the node request allows commands.":
        "Due richieste con id diversi: il dispositivo ammette la connessione, la richiesta del nodo permette i comandi.",
    "This writes a user service on this machine; nothing else on the machine changes.":
        "Questo scrive un servizio utente su questa macchina; nient'altro sulla macchina cambia.",
    "the request created by this machine, with the id the next step needs":
        "la richiesta creata da questa macchina, con l'id che serve al passo successivo",
    "Read the request before approving it: the id is this machine's, not another device's.":
        "Leggi la richiesta prima di approvarla: l'id è di questa macchina, non di un altro dispositivo.",
    "Only when the node's log says it paused waiting for pairing: otherwise it reconnects on its own.":
        "Solo quando il log del nodo dice che si è fermato in attesa dell'accoppiamento: altrimenti si ricollega da solo.",
    "The approval lists the commands this node would expose, and those are what you are allowing.":
        "L'approvazione elenca i comandi che questo nodo esporrebbe, e sono quelli che stai permettendo.",
    "Without lingering, the systemd user service stops when your last session closes.":
        "Senza il lingering, il servizio utente systemd si ferma quando chiudi l'ultima sessione.",
    "The gateway on the other PC has to answer on the home network: 'nexgen openclaw setup --lan' writes gateway.bind 'lan', and 'nexgen openclaw doctor' there says whether it is reachable.":
        "Il gateway dell'altro PC deve rispondere sulla rete di casa: 'nexgen openclaw setup --lan' scrive gateway.bind 'lan', e 'nexgen openclaw doctor' lì dice se è raggiungibile.",
    "The address {host} is yours to supply: the engine does not guess another machine's address.":
        "L'indirizzo {host} lo fornisci tu: il motore non indovina l'indirizzo di un'altra macchina.",
    "Your own gateway is left as it is: the node host is a separate process, so a machine that already serves OpenClaw in loopback keeps serving it.":
        "Il tuo gateway resta com'è: il nodo è un processo separato, quindi una macchina che già serve OpenClaw in loopback continua a servirlo.",
    "Away from home only a local gateway in loopback is reachable, and a channel such as Telegram lives on one gateway at a time: it belongs to the machine that stays on.":
        "Fuori casa è raggiungibile solo un gateway locale in loopback, e un canale come Telegram vive su un gateway alla volta: appartiene alla macchina che resta accesa.",
    "Every approval is yours: nothing is approved automatically, and each request names the machine and the commands it would run.":
        "Ogni approvazione è tua: niente viene approvato da solo, e ogni richiesta dice quale macchina e quali comandi eseguirebbe.",
    "Port {port} is the one the gateway listens on; a different one there means passing it here too.":
        "La porta {port} è quella su cui ascolta il gateway; se lì è diversa, va passata anche qui.",
    # Un gateway su una macchina accesa, raggiunta per nome MagicDNS
    "A MagicDNS address means the gateway publishes itself with 'nexgen openclaw setup --server': it stays on loopback and Tailscale Serve gives it the HTTPS address {host} on port 443.":
        "Un indirizzo MagicDNS significa che il gateway si pubblica con 'nexgen openclaw setup --server': resta su loopback e Tailscale Serve gli dà l'indirizzo HTTPS {host} sulla porta 443.",
    "Both machines have to be on the same tailnet and logged in to Tailscale: the address is reachable there and nowhere else.":
        "Tutte e due le macchine devono stare sulla stessa tailnet e aver fatto il login a Tailscale: l'indirizzo è raggiungibile lì e da nessun'altra parte.",
    "The join code carries the HTTPS address the gateway mints, so the node needs no port and no token of its own; the explicit route needs '--port 443 --tls' because Serve owns that port.":
        "Il codice di ingresso porta con sé l'indirizzo HTTPS che crea il gateway, quindi il nodo non ha bisogno di porta né di token propri; la strada esplicita richiede '--port 443 --tls' perché quella porta è di Serve.",
    "Port 443 is the one Tailscale Serve owns on the gateway machine; the gateway's own port {port} stays on its loopback.":
        "La porta 443 è quella che Tailscale Serve occupa sulla macchina del gateway; la porta {port} del gateway resta sul suo loopback.",
    "The approval failed: {error}":
        "L'approvazione è fallita: {error}",
    "In OpenClaw Companion open Connections and choose 'a local Gateway on this PC': the gateway this setup configured answers on 127.0.0.1:{port}.":
        "In OpenClaw Companion apri Connections e scegli 'a local Gateway on this PC': il gateway che questo setup ha configurato risponde su 127.0.0.1:{port}.",
    "The gateway cannot list its devices now: 'nexgen openclaw pair' shows the request and approves it when the gateway answers.":
        "Il gateway ora non riesce a elencare i suoi dispositivi: 'nexgen openclaw pair' mostra la richiesta e la approva quando il gateway risponde.",
    "No request from the app is waiting yet: when the app asks, 'nexgen openclaw pair' lists the request and 'nexgen openclaw pair --approve <id>' approves it.":
        "Nessuna richiesta dall'app è ancora in attesa: quando l'app la lascia, 'nexgen openclaw pair' la elenca e 'nexgen openclaw pair --approve <id>' la approva.",
    "More than one request is waiting: 'nexgen openclaw pair' lists them, and 'nexgen openclaw pair --approve <id>' approves the one you choose.":
        "C'è più di una richiesta in attesa: 'nexgen openclaw pair' le elenca, e 'nexgen openclaw pair --approve <id>' approva quella che scegli.",
    "A request is waiting but its id cannot be read: 'nexgen openclaw pair' shows it.":
        "Una richiesta è in attesa ma il suo id non si legge: 'nexgen openclaw pair' la mostra.",
    "Approve this device?":
        "Approvo questo dispositivo?",
    "Nothing approved: 'nexgen openclaw pair --approve {id}' approves it later.":
        "Non ho approvato niente: 'nexgen openclaw pair --approve {id}' lo approva più tardi.",
    "The approval failed ({reason}): 'nexgen openclaw pair --approve {id}' retries it.":
        "L'approvazione è fallita ({reason}): 'nexgen openclaw pair --approve {id}' la riprova.",
    "{agent}: {path} ({written} manuals written, {bootstrap})":
        "{agent}: {path} ({written} manuali scritti, {bootstrap})",
    "first-run file removed":
        "file del primo avvio rimosso",
    "first-run file not present":
        "file del primo avvio non presente",
    "Would prepare the workspace for {agent}: {path}":
        "Preparerei la cartella per {agent}: {path}",
    "Skipping the manual for {agent}: {error}":
        "Salto il manuale di {agent}: {error}",
    "Skipping {path}: it cannot be read ({error}).":
        "Salto {path}: non riesco a leggerlo ({error}).",
    "Skipping {path}: the engine did not write it, so it is yours and stays as it is.":
        "Salto {path}: non l'ha scritto il motore, quindi è tuo e resta com'è.",
    "The squad names an instructions file that cannot be read: {path}":
        "La squadra indica un file di istruzioni che non riesco a leggere: {path}",
    "The squad names {agent} as the default agent but does not declare it: no default was written.":
        "La squadra indica {agent} come agente predefinito ma non lo dichiara: non ho scritto nessun predefinito.",
    "The OpenCode agent is not created: setup was asked to leave it out.":
        "L'agente OpenCode non viene creato: il setup è stato chiesto di lasciarlo fuori.",
    "The OpenCode agent is not created: the 'opencode' command is not on PATH.":
        "L'agente OpenCode non viene creato: il comando 'opencode' non è nel PATH.",
    "The OpenCode agent is not created: no OpenCode Go key was detected.":
        "L'agente OpenCode non viene creato: non ho rilevato la chiave OpenCode Go.",
    "OpenClaw, the harness with the interface":
        "OpenClaw, l'harness con l'interfaccia",
    "Install, configure, write the agent manuals and start the gateway":
        "Installa, configura, scrive i manuali degli agenti e avvia il gateway",
    "Do not ask for confirmation":
        "Non chiedere conferma",
    "Print the plan and the commands, and change nothing":
        "Stampa il piano e i comandi, e non cambia niente",
    "Do not create the agent that runs OpenCode through ACP":
        "Non creare l'agente che fa girare OpenCode via ACP",
    "Go on with the native gateway even when the Windows app runs its own on this PC":
        "Procedi con il gateway nativo anche quando l'app Windows ne esegue uno suo su questo PC",
    "Restart the gateway and wait for its port":
        "Riavvia il gateway e aspetta la porta",
    "How long to wait for the port (default: 600)":
        "Quanto aspettare la porta (predefinito: 600)",
    "List the phones and the nodes waiting to be paired, and approve one":
        "Elenca i telefoni e i nodi in attesa di accoppiamento e ne approva uno",
    "Approve the device request with this id":
        "Approva la richiesta del dispositivo con questo id",
    "Approve the node request with this id, showing the commands it exposes":
        "Approva la richiesta del nodo con questo id, mostrando i comandi che espone",
    # --- Quota: quanto resta di ogni abbonamento --------------------------------
    "No Codex session on this machine: {path} does not exist.":
        "Nessuna sessione Codex su questa macchina: {path} non esiste.",
    "No Codex session on this machine: {path} holds no rollout.":
        "Nessuna sessione Codex su questa macchina: {path} non contiene nessun rollout.",
    "No rate-limit reading in the Codex sessions examined ({count}).":
        "Nessuna lettura del limite nelle sessioni Codex esaminate ({count}).",
    "No Claude Code transcript on this machine: {path} does not exist.":
        "Nessun transcript di Claude Code su questa macchina: {path} non esiste.",
    "No Claude Code message in the last {days} days.":
        "Nessun messaggio di Claude Code negli ultimi {days} giorni.",
    "{provider}: not measured":
        "{provider}: non misurato",
    "{provider}: the {window} reading is {age} old, older than the window itself":
        "{provider}: la lettura della finestra {window} ha {age}, più della finestra stessa",
    "{provider}: {percent} of the {window} window used ({age})":
        "{provider}: {percent} della finestra {window} usato ({age})",
    "read {age} ago":
        "letto {age} fa",
    "resets in {age}":
        "si azzera fra {age}",
    "{provider}: {amount} spent in the last {window}, no declared ceiling ({age})":
        "{provider}: {amount} spesi nelle ultime {window}, nessun tetto dichiarato ({age})",
    "{provider}: {amount} of {ceiling} in the last {window} ({percent}, {age})":
        "{provider}: {amount} di {ceiling} nelle ultime {window} ({percent}, {age})",
    "{provider}: {tokens} tokens in the last {window} ({age}), the limit is not readable on this machine":
        "{provider}: {tokens} token nelle ultime {window} ({age}), il limite non è leggibile in locale",
    "The declared limits in {path} cannot be read: {error}":
        "I limiti dichiarati in {path} non si leggono: {error}",
    "The declared limit for {entry} is not a positive number: ignored.":
        "Il limite dichiarato per {entry} non è un numero positivo: lo ignoro.",
    "OpenCode: spend is unknown, the harness archive could not be read.":
        "OpenCode: la spesa non si sa, l'archivio del harness non si è potuto leggere.",
    "OpenCode: the harness lane is not installed in this engine.":
        "OpenCode: la corsia del harness non è installata in questo motore.",
    "What is left of each subscription, and how old the reading is":
        "Quanto resta di ogni abbonamento, e quanto è vecchia la lettura",
    "Quota at {percent}%: {provider}":
        "Quota al {percent}%: {provider}",
    "{provider} has used {percent}% of its {window} window; it resets in {age}.":
        "{provider} ha usato il {percent}% della finestra {window}; si azzera fra {age}.",
    "{provider} has used {percent}% of its {window} window.":
        "{provider} ha usato il {percent}% della finestra {window}.",
    "Run 'nexgen quota' to see every window.":
        "Lancia 'nexgen quota' per vedere tutte le finestre.",
    "No provider's percentage is readable on this machine.":
        "Su questa macchina non è leggibile la percentuale di nessun fornitore.",
    "Known quotas: {known}":
        "Quote note: {known}",
    # L'adozione: ciò che nasce in OpenClaw diventa canonico
    "Adopt what OpenClaw created into the canonical library":
        "Adotta nella libreria canonica ciò che ha creato OpenClaw",
    "Only the skills the Skill Workshop learned":
        "Solo le skill imparate dallo Skill Workshop",
    "Only the MCP servers added in OpenClaw":
        "Solo i server MCP aggiunti in OpenClaw",
    "Show the plan and the checks, and change nothing":
        "Mostra il piano e i controlli, e non cambia niente",
    "Do not ask: adopt what is safe, and leave what needs a person":
        "Non chiedere: adotta ciò che è sicuro e lascia stare ciò che richiede una persona",
    "the {kind} manifest cannot be read ({path}): nothing of that kind can be listed":
        "il manifest {kind} non si riesce a leggere ({path}): niente di quel tipo può essere elencato",
    "cannot be adopted ({reasons})":
        "non adottabile ({reasons})",
    "Adopt the skill '{name}' too? It {reasons}":
        "Adotto anche la skill '{name}'? {reasons}",
    "safe to adopt":
        "adottabile senza domande",
    "needs a person first ({reasons})":
        "serve una persona prima ({reasons})",
    "needs a person first":
        "serve una persona prima",
    "cannot be adopted":
        "non adottabile",
    "Nothing to adopt: everything OpenClaw created is already in the canonical library.":
        "Niente da adottare: tutto ciò che OpenClaw ha creato è già nella libreria canonica.",
    "{count} resources created in OpenClaw are not in the canonical library yet:":
        "{count} risorse create in OpenClaw non sono ancora nella libreria canonica:",
    "  note: {note}":
        "  nota: {note}",
    "Dry run: nothing was written.":
        "Prova a vuoto: non è stato scritto niente.",
    "Nothing adopted.":
        "Non ho adottato niente.",
    "Left alone: {items}. A person decides; run 'nexgen openclaw adopt' to be asked.":
        "Lasciate stare: {items}. Decide una persona; lancia 'nexgen openclaw adopt' per farti chiedere.",
    "Adopt the resources above into the canonical library now?":
        "Adotto adesso nella libreria canonica le risorse qui sopra?",
    "the manifest would no longer validate ({problems}); put back as it was":
        "il manifest non sarebbe più valido ({problems}); l'ho rimesso com'era",
    "a skill named '{name}' is already in the Vault: nothing was overwritten":
        "una skill chiamata '{name}' è già nel Vault: non ho sovrascritto niente",
    "a skill named '{name}' is already declared in the manifest":
        "una skill chiamata '{name}' è già dichiarata nel manifest",
    "the manifest cannot be backed up: nothing was written":
        "non riesco a fare il backup del manifest: non ho scritto niente",
    "archived in the Workshop, so OpenClaw loads the canonical copy":
        "archiviata nel Workshop, così OpenClaw carica la copia canonica",
    "in the Vault and in the manifest ({changes} views written). OpenClaw: {note}":
        "nel Vault e nel manifest ({changes} viste scritte). OpenClaw: {note}",
    "`openclaw` is not on PATH: archive the learned copy by hand when it is":
        "`openclaw` non è nel PATH: archivia a mano la copia imparata quando c'è",
    "OpenClaw refused the archive: {reason}":
        "OpenClaw ha rifiutato l'archiviazione: {reason}",
    "there is no skills manifest here: the Vault is not this machine's library":
        "qui non c'è il manifest delle skill: questo Vault non è la libreria di questa macchina",
    "the copy failed ({error}); nothing was written":
        "la copia è fallita ({error}); non ho scritto niente",
    "'{name}' carries a credential in its headers: replace it with ${{VARIABLE}} and store the value with `nexgen-secrets set`":
        "'{name}' porta una credenziale negli header: sostituiscila con ${{VARIABLE}} e metti il valore in `nexgen-secrets set`",
    "'{name}' has no usable URL to adopt":
        "'{name}' non ha un URL utilizzabile da adottare",
    "'{name}' passes environment variables to a remote server, and the manifest has no place for them: nothing was adopted and nothing was dropped":
        "'{name}' passa variabili d'ambiente a un server remoto, e il manifest non ha un posto per loro: non ho adottato niente e non ho perso niente",
    "openclaw.json is not readable ({error}): no server can be listed":
        "openclaw.json non si riesce a leggere ({error}): nessun server può essere elencato",
    "{count} files: a skill is a handful, this is a tree":
        "{count} file: una skill è una manciata, questa è un albero",
    "the whole skill weighs {size} bytes (limit {limit})":
        "la skill intera pesa {size} byte (limite {limit})",
    "holds files a person should read first: {files}":
        "contiene file che una persona deve leggere prima: {files}",
    "carries what looks like a credential in {files}: move it into a secret reference first":
        "porta quello che sembra una credenziale in {files}: spostala prima in un riferimento a un segreto",
    "the frontmatter declares no name":
        "il frontmatter non dichiara un nome",
    "the frontmatter declares no description":
        "il frontmatter non dichiara una descrizione",
    "its folder is '{folder}' but its frontmatter says '{name}'":
        "la sua cartella è '{folder}' ma il frontmatter dice '{name}'",
    "{keys}: this entry carries a credential in plain text. Store it with `nexgen-secrets set` and reference it as ${{VARIABLE}} instead":
        "{keys}: questa voce porta una credenziale in chiaro. Mettila in `nexgen-secrets set` e referenziala come ${{VARIABLE}}",
    "{name} weighs {size} bytes (limit {limit})":
        "{name} pesa {size} byte (limite {limit})",
    "{name} is a link: the copy would hold bytes that are not in the skill":
        "{name} è un collegamento: la copia conterrebbe byte che non sono nella skill",
    "SKILL.md has no readable frontmatter: OpenClaw needs a name and a description":
        "SKILL.md non ha un frontmatter leggibile: OpenClaw vuole un nome e una descrizione",
    "the description is longer than {limit} bytes":
        "la descrizione supera {limit} byte",
    "'{name}' cannot become a folder name":
        "'{name}' non può diventare il nome di una cartella",
    "the skill folder is a link or is gone: nothing to copy":
        "la cartella della skill è un collegamento o non c'è più: niente da copiare",
    "no SKILL.md in the folder: OpenClaw would not load it either":
        "nella cartella non c'è SKILL.md: nemmeno OpenClaw la caricherebbe",
    "its URL is not http(s)":
        "il suo URL non è http(s)",
    "it has neither a URL nor a command":
        "non ha né un URL né un comando",
    "'{name}' is not a safe manifest key":
        "'{name}' non è una chiave sicura per il manifest",
    "its command carries a newline":
        "il suo comando contiene un a capo",
    "{count} resources created in OpenClaw are not adopted yet.":
        "{count} risorse create in OpenClaw non sono ancora adottate.",
    "Run 'nexgen openclaw adopt': it shows each one and asks before writing.":
        "Lancia 'nexgen openclaw adopt': te le mostra una per una e chiede prima di scrivere.",
    "Every skill and server OpenClaw created is in the canonical library.":
        "Ogni skill e ogni server creati da OpenClaw sono nella libreria canonica.",

    # --- Ripiego: quando un piano finisce, il lavoro passa al successivo ------------
    # La politica la scrive il proprietario in quota.yaml: il motore non ne ha una
    # propria, e queste frasi servono a dire cosa ha letto e cosa non ha potuto usare.
    "{row}, then {model}":
        "{row}, poi {model}",
    "The fallback policy in {path} cannot be read: {error}":
        "La politica di ripiego in {path} non si legge: {error}",
    "The fallback 'agents' entry is not a mapping of agent to chain: ignored.":
        "La voce 'agents' della politica di ripiego non è una mappa agente → catena: la ignoro.",
    "The fallback model {entry} is not written as 'provider/model': ignored.":
        "Il modello di ripiego {entry} non è scritto come 'fornitore/modello': lo ignoro.",
    "The fallback line {value} is not a percentage between 0 and 100: {default} is used.":
        "La soglia di ripiego {value} non è una percentuale fra 0 e 100: uso {default}.",
    "The fallback reading age {value} is not a positive number of minutes: {default} hours are used.":
        "L'età della lettura di ripiego {value} non è un numero positivo di minuti: uso {default} ore.",
    "No OpenClaw agent is configured yet: there is no fallback chain to judge.":
        "Nessun agente OpenClaw è ancora configurato: non c'è nessuna catena di ripiego da giudicare.",
    "The fallback policy names these agents, and their chain is not in the configuration: {names}":
        "La politica di ripiego nomina questi agenti, e la loro catena non è nella configurazione: {names}",
    "Run 'nexgen openclaw setup', and only for an agent the engine creates: it writes no agent it does not own.":
        "Lancia 'nexgen openclaw setup', e solo per un agente che il motore crea: non scrive agenti che non sono suoi.",
    "The fallback policy is declared: {covered} agents have a chain, {missing} have none ({names}).":
        "La politica di ripiego è dichiarata: {covered} agenti hanno una catena, {missing} nessuna ({names}).",
    "No fallback policy is declared: {covered} agents have a chain of their own, {missing} have none ({names}).":
        "Nessuna politica di ripiego dichiarata: {covered} agenti hanno una catena propria, {missing} nessuna ({names}).",
    "The fallback policy is declared: every one of the {count} agents has a chain.":
        "La politica di ripiego è dichiarata: tutti e {count} gli agenti hanno una catena.",
    "No fallback policy is declared; the {count} agents have a chain of their own.":
        "Nessuna politica di ripiego dichiarata; i {count} agenti hanno una catena propria.",
    "No fallback policy is declared, and no OpenClaw agent has a fallback chain.":
        "Nessuna politica di ripiego dichiarata, e nessun agente OpenClaw ha una catena di ripiego.",
    "No fallback policy names an agent of this setup: no chain was written.":
        "Nessuna politica di ripiego nomina un agente di questo setup: non ho scritto nessuna catena.",
    "The engine plugin is not deposited: its fallback chains were not written.":
        "Il plugin del motore non è depositato: le sue catene di ripiego non sono state scritte.",
    "The fallback chains reach {count} agents; the plugin reads them on every turn.":
        "Le catene di ripiego arrivano a {count} agenti; il plugin le legge a ogni turno.",
    "OpenClaw MCP server enabled for other harnesses.":
        "Server MCP di OpenClaw abilitato per gli altri harness.",
    "Could not enable OpenClaw MCP server: {error}":
        "Impossibile abilitare il server MCP di OpenClaw: {error}",
    "plugin '{id}' is not an MCP wrapper ({kind}): not adoptable":
        "il plugin '{id}' non è un wrapper MCP ({kind}): non adottabile",
    "Build the reproducible plugin tarball for OpenClaw gateways":
        "Costruisce il pacchetto riproducibile del plugin per i gateway OpenClaw",
    "Notice: shell is retired in favor of OpenClaw TUI":
        "Avviso: la shell è ritirata a favore di OpenClaw TUI",
    "The interactive shell has been retired: use 'openclaw tui' or the OpenClaw Dashboard instead.":
        "La shell interattiva è stata ritirata: usa 'openclaw tui' o la Dashboard di OpenClaw.",
    "Adopt safe resources and retire absent ones without asking":
        "Adotta risorse sicure e ritira quelle assenti senza chiedere",
    "No auto-adoption pass recorded yet.":
        "Nessun passaggio di adozione automatica ancora registrato.",
    "Last adoption pass: {entered} entered, {exited} exited, {pending} waiting for review.":
        "Ultimo passaggio di adozione: {entered} entrate, {exited} uscite, {pending} in attesa di revisione.",
    "Run 'nexgen openclaw adopt' to review and decide on pending resources.":
        "Lancia 'nexgen openclaw adopt' per revisionare e decidere sulle risorse in attesa.",
    "openclaw.json is not readable ({error})":
        "openclaw.json non è leggibile ({error})",
    "OpenClaw gateway RPC unreachable ({error})":
        "Gateway RPC di OpenClaw non raggiungibile ({error})",
    "OpenClaw config unavailable ({error})":
        "Configurazione di OpenClaw non disponibile ({error})",
    "OpenClaw configuration could not be read: {error}. Nothing adopted or removed.":
        "Impossibile leggere la configurazione di OpenClaw: {error}. Niente adottato o rimosso.",
    "Canonical library manifest could not be read; skipping auto pass.":
        "Impossibile leggere il manifest della libreria canonica; salto il passaggio automatico.",
    "Everything is aligned: no new safe items to adopt, nothing absent to retire.":
        "Tutto allineato: nessun nuovo elemento sicuro da adottare, nessun elemento assente da ritirare.",
    "MCP manifest does not exist":
        "Il manifest MCP non esiste",
    "cannot read the manifest ({error}); fix it before modifying":
        "non riesco a leggere il manifest ({error}); sistemalo prima di modificare",
    "server '{name}' is not declared in servers":
        "il server '{name}' non è dichiarato nei servers",
    "server '{name}' is not origin: openclaw: will not remove automatically":
        "il server '{name}' non ha origin: openclaw: non verrà rimosso automaticamente",
    "manifest modification failed validation: {error}":
        "la modifica del manifest non ha superato la convalida: {error}",
    "server '{name}' retired from MCP manifest":
        "server '{name}' ritirato dal manifest MCP",
    "the server role owns this machine, and its notes are written with the vault-library":
        "questa macchina ha il ruolo server, e le sue note si scrivono col vault-library",
    "the vault-library here serves {path}, which is another checkout":
        "il vault-library di questa macchina serve {path}, che è un'altra copia",
    "Vault clone updated with a fast-forward from {ref}":
        "Copia del Vault aggiornata con un fast-forward da {ref}",
    "Vault clone not updated ({detail}): the notes it reads may be stale":
        "Copia del Vault non aggiornata ({detail}): le note che legge possono essere vecchie",
    "Nothing local to quarantine":
        "Non c'è niente di locale da mettere in quarantena",
    "Local changes could not be recorded on {branch}; nothing was lost and the working tree is unchanged":
        "Le modifiche locali non sono state registrate su {branch}; non si è perso niente e la cartella di lavoro è com'era",
    "Local changes are safe on '{branch}', but realigning {target} failed: {error}":
        "Le modifiche locali sono al sicuro su '{branch}', ma il riallineamento a {target} non è riuscito: {error}",
    "Local changes parked on quarantine branch '{branch}': notes are written with the vault-library, not with git.":
        "Modifiche locali messe da parte sul ramo di quarantena '{branch}': le note si scrivono col vault-library, non con git.",
    "Vault clone read-only ({reason}): there is no remote to pull from":
        "Copia del Vault in sola lettura ({reason}): non c'è un remoto da cui scaricare",
    "Vault clone read-only, and it could not be realigned: {message}":
        "Copia del Vault in sola lettura, e non è stato possibile riallinearla: {message}",
    "Vault clone read-only ({reason}); no remote is configured":
        "Copia del Vault in sola lettura; non c'è nessun remoto configurato",
    "Could not check the read-only vault clone: {message}":
        "Non ho potuto controllare la copia del Vault in sola lettura: {message}",
    "Check the connection and the remote, then run 'nexgen doctor' again.":
        "Controlla la connessione e il remoto, poi lancia di nuovo 'nexgen doctor'.",
    "Run 'nexgen pull': it parks them on a 'quarantine/diverged-*' branch and realigns the clone.":
        "Lancia 'nexgen pull': mette le modifiche da parte su un ramo 'quarantine/diverged-*' e riallinea la copia.",
    "The gateway's vault clone has {count} unpublished change(s): notes are written with the vault-library, not with git.":
        "Nel clone del gateway ci sono {count} modifiche non pubblicate: le note si scrivono col vault-library, non con git.",
    "{commits} commit(s) not published, {files} file(s) modified":
        "{commits} commit non pubblicati, {files} file modificati",
    "The gateway's vault clone is read-only and aligned; notes are written with the vault-library.":
        "Il clone del gateway è in sola lettura e allineato; le note si scrivono col vault-library.",
    "Refusing to publish from this vault clone ({reason}). Notes are written with the vault-library.":
        "Mi rifiuto di pubblicare da questa copia del Vault ({reason}). Le note si scrivono col vault-library.",
    "read-only vault clone: the pins are not committed here":
        "copia del Vault in sola lettura: i pin non vengono committati qui",
    "Which role this machine runs, and how to record it":
        "Che ruolo ha questa macchina, e come registrarlo",
    "Record the role this machine runs":
        "Registra il ruolo di questa macchina",
    "The role to record":
        "Il ruolo da registrare",
    "This machine is recorded as: {role}":
        "Questa macchina risulta: {role}",
    "The record lives in {path}":
        "Il registro è in {path}",
    "No role is recorded on this machine.":
        "Su questa macchina non è registrato nessun ruolo.",
    "If this is the always-on gateway, record it with 'nexgen role set server'.":
        "Se questa è il gateway sempre acceso, registralo con 'nexgen role set server'.",
    "The role could not be recorded in {path}":
        "Non sono riuscito a registrare il ruolo in {path}",
    "Recorded the role: {role}":
        "Ruolo registrato: {role}",
    'Local gateway retired: {parts}.':
        'Gateway locale ritirato: {parts}.',
    'Gateway drop-in left without its unit, moved to {path}.':
        'Drop-in del gateway rimasto senza la sua unit, spostato in {path}.',
    'Nothing left behind by a local gateway.':
        'Nessun residuo di un gateway locale.',
    'Gateway auth block removed from {path}: the node uses its pairing token.':
        'Blocco auth del gateway tolto da {path}: il nodo usa il token del pairing.',
    'Channels switched off on this PC, they answer from the central gateway: {names}.':
        'Canali spenti su questo PC, rispondono dal gateway centrale: {names}.',
    'Session hosting enabled: this node can run agent sessions, not only commands.':
        'Session hosting acceso: questo nodo può far girare sessioni degli agenti, non solo comandi.',
    'drop-in archived':
        'drop-in archiviato',
    'gateway auth removed':
        'auth del gateway rimossa',
    'channels off: {names}':
        'canali spenti: {names}',
    'session hosting on':
        'session hosting acceso',
    '- gateway auth block: removed, the node uses its pairing token':
        '- blocco auth del gateway: rimosso, il nodo usa il token del pairing',
    '- channels switched off here: {names}':
        '- canali spenti qui: {names}',
    '- session hosting: switched on, so this node can run agent sessions':
        '- session hosting: acceso, così questo nodo può far girare sessioni degli agenti',
    'Configuration not readable, left as it is: {reason}':
        "Configurazione non leggibile, lasciata com'è: {reason}",
    'This machine is already a node of {current}.':
        'Questa macchina è già nodo di {current}.',
    'The gateway asked for is {wanted}.':
        'Il gateway richiesto è {wanted}.',
    'To move it to another gateway, run the same command with --force.':
        'Per spostarla su un altro gateway, rilancia lo stesso comando con --force.',
    'The node service is running: nothing to enroll again.':
        "Il servizio nodo è in esecuzione: non c'è niente da accoppiare di nuovo.",
    'The node service is not running: starting it.':
        'Il servizio nodo non è in esecuzione: lo avvio.',
    '- local gateway: stopped and removed, the node host replaces it':
        '- gateway locale: fermato e rimosso, il node host prende il suo posto',
    '- drop-in left without its unit: archived, not deleted':
        '- drop-in rimasto senza la sua unit: archiviato, non cancellato',
    '- node service: installed and started with host {host} port {port}, session hosting on':
        '- servizio nodo: installato e avviato con host {host} porta {port}, session hosting acceso',
    '- enrolment: the join link taken from the file, approved by the gateway in the same step':
        '- accoppiamento: il link di ingresso preso dal file, approvato dal gateway nello stesso passo',
    '- enrolment: the device waits for approval on the gateway, nothing is approved here':
        "- accoppiamento: il dispositivo aspetta l'approvazione sul gateway, qui non si approva niente",
    '- name shown by the gateway: {name}':
        '- nome mostrato dal gateway: {name}',
    'Check on this machine: openclaw node status':
        'Controllo su questa macchina: openclaw node status',
    'Check on the gateway: openclaw nodes describe --node {name}':
        'Controllo sul gateway: openclaw nodes describe --node {name}',
    "The gateway still has to admit this machine: run 'nexgen openclaw pair' there, approve the device, then approve the node request.":
        "Il gateway deve ancora ammettere questa macchina: lancia 'nexgen openclaw pair' là, approva il dispositivo, poi approva la richiesta del nodo.",
    'Node install failed (exit code {code}).':
        'Installazione del nodo fallita (codice {code}).',
    "Node service did not start (exit code {code}). Try 'openclaw node status'.":
        "Il servizio nodo non è partito (codice {code}). Prova 'openclaw node status'.",
    'A local gateway is not a reason to stop, but it is retired first: its own token and its own channels make the node fight the central gateway. --apply does that; the commands above leave it alone, so a machine with no channels can keep serving it.':
        'Un gateway locale non è un motivo per fermarsi, ma viene ritirato prima: il suo token e i suoi canali fanno litigare il nodo con il gateway centrale. Ci pensa --apply; i comandi qui sopra lo lasciano stare, così una macchina senza canali può continuare a servirlo.',
    'Do it on this machine: retire the local gateway, prepare the configuration and start the node service':
        'Fallo su questa macchina: ritira il gateway locale, prepara la configurazione e avvia il servizio nodo',
    "A private file holding the gateway's join URL; it is read and never printed":
        'Un file privato con il link di ingresso del gateway; viene letto e mai stampato',
    'With --apply: print what would change and change nothing':
        'Con --apply: stampa cosa cambierebbe e non cambia niente',
    'Move a machine that is already a node of another gateway to this one':
        'Sposta su questo gateway una macchina che è già nodo di un altro',
    'Cannot read the join file {path}: {error}':
        'Non riesco a leggere il file di ingresso {path}: {error}',
    'the file is empty':
        'il file è vuoto',
    'The join link is spent: delete the file you kept it in.':
        'Il link di ingresso è consumato: cancella il file in cui lo avevi messo.',
    'Or let the engine retire the local gateway first and attach for you:':
        "Oppure lascia che il motore ritiri prima il gateway locale e faccia l'aggancio:",
    '  nexgen openclaw node --host <gateway address> --apply --join-file <file>':
        '  nexgen openclaw node --host <indirizzo del gateway> --apply --join-file <file>',
    'Compact the agent chats that sat idle or grew too large':
        'Compatta le chat degli agenti rimaste ferme o diventate troppo grandi',
    'Show what would be compacted, change nothing':
        'Mostra cosa verrebbe compattato, senza cambiare niente',
    'No agent chat needs compacting.':
        'Nessuna chat agente da compattare.',
    '  Context guard: skipped in dry-run.':
        '  Guardia del contesto: saltata in prova a vuoto.',
    "  Context guard: 'nexgen' is not on PATH, job not registered.":
        "  Guardia del contesto: 'nexgen' non è nel PATH, job non registrato.",
    '  Context guard: hourly job registered.':
        '  Guardia del contesto: job orario registrato.',
    '  Context guard: the job was not registered, the gateway refused it.':
        '  Guardia del contesto: il job non è stato registrato, il gateway lo ha rifiutato.',
}
