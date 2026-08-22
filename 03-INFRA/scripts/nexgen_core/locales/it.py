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
    'The connector services, on this machine': 'I servizi dei connettori, su questa macchina',
    'Start the services and configure the connectors': 'Avvia i servizi e configura i connettori',
    'Only some services, instead of all of them': 'Solo alcuni servizi, invece di tutti',
    'Stop the services, leaving the data intact': 'Ferma i servizi, lasciando intatti i dati',
    'Which services are responding': 'Quali servizi stanno rispondendo',
    "The last lines of a service that won't start": 'Le ultime righe di un servizio che non parte',
    'Connector variables written to {target}': 'Variabili dei connettori scritte in {target}',
    'Tools shipped with the engine': 'Utensili distribuiti con il motore',
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
    'runtime-permissions: phase skipped due to an unexpected error ({error})': 'runtime-permissions: fase saltata per errore imprevisto ({error})',
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
    "Diverged local commits moved to quarantine branch '{q_branch}'. Local branch reset to {remote}/{branch}.": "I commit locali divergenti sono stati spostati nel branch di quarantena '{q_branch}'. Branch locale reimpostato su {remote}/{branch}.",
    'Realigned with {remote}/{branch} via rebase': 'Riallineato con {remote}/{branch} tramite rebase',
    'Error during divergence resolution: {reason}': 'Errore durante la risoluzione della divergenza: {reason}',
    'No quarantine branches in the Vault': 'Nessun branch di quarantena nel Vault',
    'Warnings (nothing is blocked):': 'Avvisi (niente è bloccato):',
    'Found {count} quarantine branch(es) with isolated diverged changes: {branches}': 'Trovati {count} branch di quarantena con modifiche divergenti isolate: {branches}',
    "Review diff with 'git diff main..{branch}', reconcile changes into canonical files, then remove the quarantine branch with 'git branch -D {branch}'.": "Verifica il diff con 'git diff main..{branch}', integra le modifiche nei file canonici, poi elimina il branch di quarantena con 'git branch -D {branch}'.",
    "Could not acquire lock '{lock_path}' after {timeout:.1f}s (another process is active).": "Impossibile acquisire il lock '{lock_path}' dopo {timeout:.1f}s (processo attivo in corso).",
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
    'Antigravity configuration updated': 'Configurazione Antigravity aggiornata',
    'OpenCode configuration updated': 'Configurazione OpenCode aggiornata',
    'Codex configuration updated': 'Configurazione Codex aggiornata',
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
    "the five connectors run here: 'nexgen stack up' starts them.": "i cinque connettori girano qui: 'nexgen stack up' li avvia.",
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

    # --- tools/firecrawl.py ---
    "Firecrawl Local CLI (v2)": "CLI locale di Firecrawl (v2)",
    "Check the service status": "Controlla lo stato del servizio",
    "Scrape a URL": "Esegue lo scrape di un URL",
    "URL to fetch": "URL da scaricare",
    "Comma-separated formats (markdown, links)": "Formati separati da virgola (markdown, links)",
    "Raw JSON output": "Output JSON grezzo",
    "Save the output to a file": "Salva l'output su file",
    "Run a web search": "Esegue una ricerca web",
    "Search terms": "Termini di ricerca",
    "Maximum number of results": "Numero massimo di risultati",
    "Comma-separated sources (web, news, images)": "Sorgenti separate da virgola (web, news, images)",
    "Also fetch the content of the results": "Scarica anche il contenuto dei risultati",
    "Scraping formats": "Formati di scraping",
    "local default": "predefinito locale",
    "status: up": "stato: attivo",
    "status: unreachable ({error})": "stato: non raggiungibile ({error})",
    "unknown error": "errore sconosciuto",

    # --- tools/chrome.py ---
    "agent-chrome: Chrome or Chromium not found on this system.": "agent-chrome: Chrome o Chromium non trovato su questo sistema.",
    "agent-chrome: error launching Chrome ({error})": "agent-chrome: errore nell'avvio di Chrome ({error})",
    "agent-chrome: Chrome is holding the debug profile (process {pid}) but isn't responding on port 9222. Restarting it.": "agent-chrome: Chrome sta tenendo il profilo di debug (processo {pid}) ma non risponde sulla porta 9222. Lo riavvio.",
    "Usage: agent-chrome [--ensure|--heal] [args...]\n\nManages Chrome with a shared local CDP port.": "Uso: agent-chrome [--ensure|--heal] [args...]\n\nGestisce Chrome con una porta CDP locale condivisa.",

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
}
