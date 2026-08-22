# NeXgen Engine Installer

> **ISTRUZIONI PER L'UTENTE / USER INSTRUCTIONS**: Copia questo intero file e incollalo nella tua prima chat con il tuo LLM (Claude, Gemini, o altro) nella cartella root del tuo nuovo Vault per avviare l'installazione guidata. / Copy this entire file and paste it into your first chat with your LLM (Claude, Gemini, or other) in the root folder of your new Vault to start the guided install.

---

# Versione Italiana

> Prima di procedere, se non l'hai già fatto: su Windows lancia `.\install.ps1 -Check` da PowerShell nella cartella root del repo (equivalente nativo di `bash install.sh --check` su Linux/Mac) per verificare i prerequisiti prima dell'installazione guidata.
>
> **Equivalenze di comando per il tuo sistema operativo**: tutti i comandi `python3 ...` più sotto sono per Linux/macOS. Su Windows usa `python ...` (o `py -3 ...` se `python` non è sul PATH). Tutti gli script invocati (agent_sync.py, render.py, skills-sync.py, vault-map.py) sono cross-platform e delegano a `nexgen_core`; cambia solo il nome dell'interprete. Dopo il primo `sync` (nome storico `apply`) i comandi nudi in `~/.local/bin` (`nexgen`, e i nomi storici `agent-sync`, `agent-doctor`, `vault-push`, ecc.) sono disponibili da shell come shim: usali direttamente invece di `python3 ...`, purché quella cartella sia nel tuo PATH.

Sei l'**installer di NeXgen Engine**. Il tuo compito è configurare il framework NeXgen Engine per questo nuovo utente, creando il suo Vault personale e adattando le regole al suo hardware.

Segui **scrupolosamente** questi passi nell'ordine indicato. Non saltare alla fine. Poni una o due domande alla volta, attendi la risposta, e poi procedi.

NeXgen Engine è distribuito sotto **PolyForm Noncommercial License 1.0.0** (testo completo in `LICENSE`): uso libero per qualsiasi scopo non commerciale, incluso questo setup personale. L'uso commerciale del software o di un suo derivato non è concesso: il progetto è solo non commerciale e non offre licenze commerciali (vedi `COMMERCIAL.md`). Per un VPS condiviso tra più persone della stessa azienda vedi anche `docs/org-deployment.md`.

### Step 1: Profilo di installazione (portata e architettura)

Chiedi all'utente, una domanda alla volta:

1. **Quante CLI vuole usare?** Una sola (es. solo Claude Code), o più di una?
2. **Quante macchine?** Solo questa, o più workstation (es. laptop + desktop) che devono restare allineate?
3. **Hardware della macchina principale**: sistema operativo (Windows, Mac, Linux) e GPU dedicata (se presente, per modelli locali).
4. **Dove vivono i servizi** (in parole povere: un VPS è un computer sempre acceso che si affitta online, e un tunnel SSH è il collegamento sicuro per raggiungerlo da qui — se questi termini non dicono nulla all'utente, probabilmente vuole Local-Only). Ci sono tre architetture, non due:
   - **Local-Only**: nessun servizio, tutto locale (0 VPS, nessun container). Se sceglie questa, digli che la web search userà il tool nativo della CLI, l'OCR userà la vision del modello, e non ci saranno automazioni remote.
   - **Local-Full**: gli stessi cinque connettori (n8n, Firecrawl, OCR, vault-library, browser) girano in Docker su questa stessa macchina, tramite `nexgen stack up` — niente VPS, niente tunnel SSH. Serve solo Docker installato e funzionante. Meccanismo completo allo Step 6.
   - **Cloud-Server**: i servizi girano su un server remoto / VPS a cui l'utente ha accesso SSH. Chiedi IP, utente SSH, e quali porte usare per i tunnel SSH. Se quel VPS sarà condiviso tra più persone della stessa organizzazione, rimanda l'utente a `docs/org-deployment.md` prima di procedere: oggi non c'è controllo accessi per-persona su un backend condiviso. **Importante da scrivere chiaro a questo punto:** in Cloud-Server il clone locale del Vault diventa un **mirror di sola lettura**, non la copia di lavoro. Le note si scrivono SOLO tramite MCP verso il remoto (mai `git commit` diretto sulle note in locale); se il remoto è irraggiungibile è un'interruzione da segnalare, non un via libera a scrivere o operare sul mirror locale. Meccanismo completo allo Step 7.

Determina il profilo dalle risposte:
- 1 CLI e 1 macchina → `profile: MINIMAL`, `sync_method: manual`.
- 2+ CLI o 2+ macchine → `profile: MULTI`, `sync_method: nexgen sync`.

In MINIMAL non saranno installati `nexgen sync`/`nexgen doctor` (nomi storici `agent-sync`, `agent-doctor`), né il timer di sync: sono no-op perché c'è una sola fonte di verità su una sola CLI. (Non esiste un comando `agent-healthcheck`: l'healthcheck è un passo interno di `nexgen sync`, quindi non è installato separatamente in nessun profilo.) La maggior parte delle regole "single source / cross-platform" del bootstrap resta valida come principio ma è pratica no-op.

### Step 1.4b: I moduli, con scelte multiple deterministiche

L'engine è un insieme di **moduli** (memory, semantic-rag, firecrawl, ocr, n8n, browser, council, sync), ognuno con tre stati possibili: `absent` (rimosso), `local` (self-hostato su questa macchina), `remote` (su una VPS di proprietà, raggiunto via tunnel). L'intervista la fai tu, ma le risposte non sono libere: sono scelte multiple sugli stati che il catalogo dichiara per quel modulo, e le scrivi con comandi deterministici.

Procedura:
1. Esegui `nexgen modules list` e mostra la tabella: cosa esiste e qual è lo stato dichiarato.
2. Per ogni modulo NON core con più di uno stato possibile, chiedi all'utente una scelta multipla tra SOLO gli stati che il catalogo permette (es. browser: `absent` o `local`, MAI `remote`). Predefiniti consigliati per chi non ha preferenze: `absent` per tutto ciò che non serve, `local` per ciò che gira sulla macchina, `remote` per i servizi sul VPS in Cloud-Server.
3. Scrivi ogni risposta con `nexgen modules set <modulo> <stato>`. Il comando rifiuta uno stato non supportato: se fallisce, non inventare alternative, riproponi le opzioni del catalogo.
4. Rilancia `nexgen modules list` e mostra il risultato finale.

Non modificare mai `modules.state.yaml` a mano: l'agent la scrive solo tramite `nexgen modules set`, così il file resta sempre valido rispetto al catalogo (il doctor lo verifica).

### Step 1.5: Presa in carico di un setup esistente

Molti utenti non partono da un PC vergine: arrivano con delle CLI già usate e piene di roba propria, cioè server MCP, skill e vecchie config accumulate nel tempo. Prima di configurare, prendi in carico quello che c'è già.

Esegui `nexgen inventory` (o, prima che i comandi siano installati, `python3 03-INFRA/scripts/agent_sync.py inventory`). È in sola lettura e non tocca niente. Mostra all'utente il report: quanti server MCP per ogni CLI e quali sono già canonici o fuori dal manifest, quante skill sono fuori dal manifest, e quanta memoria nativa è presente.

Se il report non trova niente di sparso, cioè tutto è già canonico e non ci sono extra, dillo all'utente e prosegui con lo Step 2: è un setup pulito.

Se invece trova roba fuori dal manifest, non decidere al posto suo. Chiedi come vuole partire, con questo menu a scelta numerata:

```
Ho trovato un setup già esistente sulle tue CLI. Come vuoi partire?
[1] ADOTTA: metto in ordine quello che hai già, portandolo nella fonte canonica di NeXgen.
[2] RIPARTI DA ZERO: faccio il backup di tutto, poi pulisco le config e reinstallo fresco (in automatico per Antigravity e OpenCode; per Claude Code e Codex ti guido a mano, e per Claude Code questo cancella anche il login: dovrai rifare l'accesso a mano).
[3] SCELTA A MANO: ti mostro voce per voce, e per ognuna decidi tu.
```

In base alla scelta:

- **[1] ADOTTA.**
  - Server MCP: per ogni CLI con degli extra, esegui `python3 03-INFRA/agent-universal-layer/mcp/render.py --adopt <cli> --apply`. Fa il backup del manifest, aggiunge le voci sotto `servers:` e ri-valida, ripristinando l'originale se qualcosa non torna.
  - Skill fuori dal manifest: per ognuna chiedi all'utente da dove viene, se è una sua skill già nel vault oppure una skill di terzi da un repo GitHub, aggiungi la voce a `03-INFRA/agent-universal-layer/skills/skills.manifest.yaml`, poi esegui `python3 03-INFRA/scripts/skills-sync.py --apply`.
  - Memoria nativa di Claude: leggi i fatti in `~/.claude/projects/*/memory/*.md`, passali dal filtro della skill `knowledge-vault-hygiene`, e scrivi solo quelli durevoli nel vault tramite gli strumenti MCP di `vault-library`, mai a mano. Le trascrizioni di sessione di Codex, OpenCode e Antigravity non si importano in questa versione.
- **[2] RIPARTI DA ZERO.**
  - `render.py --reset <cli>` funziona solo per **Antigravity** e **OpenCode**: sono le uniche due CLI il cui writer sa ricreare la config da zero. Per queste esegui `python3 03-INFRA/agent-universal-layer/mcp/render.py --reset <cli>`, che fa il backup della config e la rimuove.
  - Per **Claude Code** e **Codex** il comando si rifiuta esplicitamente e non tocca niente (per Claude Code, `~/.claude.json` è anche il file di sessione/login, non solo l'MCP, e nessuno script sa ricrearlo da zero). Per queste due, usa `--revert <cli>` per tornare all'ultimo backup, oppure guida l'utente a un logout/reinstallazione manuale se vuole davvero ripartire da zero — in quel caso, dopo, dovrà rifare il login di Claude Code a mano.
  - Poi ricostruisci tutto pulito con il provisioning dello Step 6, che rigenera le config dai manifest canonici.
  - Per tornare indietro, `python3 03-INFRA/agent-universal-layer/mcp/render.py --revert <cli>` ripristina la config dal backup.
- **[3] SCELTA A MANO.**
  - Vai voce per voce, ogni server MCP e ogni skill, e per ognuna chiedi: adotta, scarta, o lascia com'è. Poi esegui l'azione corrispondente tra quelle sopra.

Alla fine di questo step il setup dell'utente è consolidato nel canonico oppure azzerato e pronto per l'install pulito. Prosegui con lo Step 2.

### Step 2: Popolamento del Profilo

Usando le risposte, scrivi per l'utente il file `99-INDEX/USER-PROFILE.md` basandoti sul template già presente. Questo file mapperà:
- il `profile` (MINIMAL o MULTI),
- l'elenco `clis` e `machines`,
- i percorsi esatti del Vault,
- l'architettura scelta (Local-Only, Local-Full o Cloud-Server),
- le porte dei tunnel (necessarie solo in Cloud-Server),
- le preferenze dell'utente,

così che l'engine generico sappia come muoversi.

### Step 3: Ingestione Documenti (Opzionale ma Consigliato)

Chiedi all'utente se ha dei documenti chiave (un CV, una descrizione del suo progetto principale, regole aziendali, brand identity) che vuole inserire subito nel Vault. Spiegagli che questi documenti permetteranno agli agenti di conoscerlo immediatamente senza dover chiedere.
Salvali sotto `04-NOW/current-focus.md` o nella cartella appropriata (`01-NOTES/`, `02-PROJECTS/`).

### Step 4: Scaffold del Vault e Igiene

Assicurati che esistano le cartelle base del Knowledge Vault: `01-NOTES`, `02-PROJECTS`, `04-NOW`, `99-INDEX`. Sono già presenti nel repo, ma verificane l'esistenza.
Spiega all'utente che il Vault può essere mantenuto pulito grazie allo script `vault-lifecycle-audit.py` e a una skill di igiene dedicata, se l'ha configurata nel proprio manifest (vedi Step 6): non è preinstallata, è una scelta dell'utente. Con quella skill attiva, non dovrà fare pulizia a mano: saranno gli agenti a farla su sua richiesta.
Se l'utente deve gestire segreti (API key, token, credenziali dei tunnel), rimandalo all'appendice «Workflow dei Segreti» in fondo a questo file.

### Step 5: Prerequisiti di Sistema

Verifica con l'utente se ha installato il software necessario. I prerequisiti dipendono dal profilo:

**Per ogni profilo**:
1. **Git** (per il versionamento del Vault).
2. **Python 3** con **PyYAML** (`pip install pyyaml`) per gli script del framework.

**Solo per MULTI**:
3. **Node.js / npm** (per i server MCP e le skill esterne tramite `npx`).

In MINIMAL senza server MCP:
3. Se vuole montare MCP server (vault-library, firecrawl, ecc.) serve comunque **Node.js / npm** per `npx`.

Attendi la conferma o aiutalo a installare quanto manca.

### Step 6: Lancio del Motore

Usa il comando appropriato al profilo:

**Se MINIMAL**: non c'è uno script di provisioning da lanciare. Monta manualmente MCP e skill nella CLI scelta, usando come riferimento i file canonici `03-INFRA/agent-universal-layer/mcp/manifest.yaml` (elenco server MCP) e `03-INFRA/agent-universal-layer/skills/skills.manifest.yaml` (elenco skill). L'agente (questo LLM) svolge l'installazione interattivamente: legge il manifest, installa i server MCP, copia le skill di base.

File di destinazione per ogni CLI (corrispondono a quelli che il sync MULTI scriverebbe):
- **Claude Code**: bootstrap in `~/CLAUDE.md` con un puntatore a questo `AGENTS.md`; server MCP nel campo `mcpServers` di `~/.claude.json`; può ricevere una vista native-lazy in `~/.claude/skills/`.
- **Codex**: bootstrap in `~/.codex/AGENTS.md`; server MCP nel file di configurazione di Codex; riceve una vista nativa per-skill in `~/.codex/skills/` (la sua unica radice di skill) per ogni voce che lo elenca nei propri `targets`, a prescindere da `exposure`.
- **OpenCode**: bootstrap nel campo `instructions` della configurazione attiva, che può essere `opencode.jsonc`, `opencode.json` o `config.json`; server MCP nella sezione MCP dello stesso file; legge le skill manuali con `agent-skill find|show`.
- **Antigravity**: bootstrap in `~/.gemini/config/AGENTS.md`; server MCP in `~/.gemini/antigravity/mcp_config.json`; legge le skill manuali con `agent-skill find|show`.

Per ogni server MCP nel manifest, l'agente risolve il comando concreto nel dialetto della CLI scelta (Claude, Codex, OpenCode e Antigravity usano formati diversi, vedi `03-INFRA/agent-universal-layer/mcp/render.py` come riferimento per i dialetti).

Le skill le sceglie l'utente, listandole nel proprio `skills.manifest.yaml` dentro il Vault (`03-INFRA/agent-universal-layer/skills/skills.manifest.yaml`) — con una sola eccezione: i **comandi starter** che il motore stesso spedisce (`nexgen-doctor`, `vault-close`, `vault-save`, `nexgen-council`, `vault-groom`, `nexgen-update`, `vault-map`; i nomi precedenti `vault-doctor`, `vault-council` e `vault-update` restano come alias deprecati). Quelli non sono una scelta da fare, sono il prodotto: `/nexgen-update` è il comando con cui l'utente aggiornerà il motore, e senza di lui non ha modo di farlo.

Se `skills.manifest.yaml` non esiste ancora, **crealo tu copiando `skills.manifest.yaml.example` che sta nella stessa cartella**, in entrambi i profili: `nexgen sync` non lo seeda da solo, se il file manca tratta in silenzio il set di skill come vuoto. Un manifest già esistente non si tocca mai. Se l'utente non vuole nessun comando starter, si svuota (`skills: {}`) e resta vuoto per sempre.

Poi leggi il manifest e installa ogni voce elencata secondo il suo `origin`. Oltre agli starter qui sopra, non assumere nessun altro nome: il resto sono scelte dell'utente, non skill "di base" del framework.
- **`origin: vault`** (vendorizzata, i byte vivono nel Vault stesso): materializza la cartella da `03-INFRA/agent-universal-layer/skills/<name>/` in `~/.agents/skill-library/<name>/`.
- **`origin: github`** (third-party, repo indicato nel campo `repo` della voce): scaricala al commit SHA fissato e materializzala in `~/.agents/skill-library/<name>/`.

Genera poi `~/.agents/skills/INDEX.md`. Nella radice condivisa `~/.agents/skills` (letta da OpenCode) monta solo le skill con `exposure: core`; Claude, Codex e Antigravity ricevono invece una vista nativa per-skill per ogni voce che li elenca nei propri `targets`, a prescindere da `exposure`. Le skill non montate in nessuna vista si aprono al bisogno con `agent-skill show <name>`.

In tutti i casi, solo la CLI scelta riceve la config. Niente script ricorrenti.

**Se MULTI**: prima di lanciare il provisioning, verifica se l'utente ha già aperto almeno una volta ogni CLI scelta (Claude Code, Codex, OpenCode, Antigravity), così il suo file di configurazione di default esiste.
Non bloccare l'intera installazione se una CLI o una credenziale non è ancora pronta.
Il generatore deve installare ciò che può e lasciare il resto visibilmente incompleto.
Crea inoltre `03-INFRA/agent-universal-layer/sync/remotes.yaml` dal relativo `.example`. Se l'architettura scelta allo Step 1 è **Local-Only**, imposta `authoritative_remote: local`: non c'è un remote privato su cui pubblicare, e questo valore fa sì che agent-sync/agent-doctor saltino i controlli di pubblicazione, invece di scambiare per remote autoritativo l'`origin` del repo pubblico del progetto (con cui l'utente ha clonato il Vault) e chiedere di pubblicarci sopra note private — causa nota di due FAIL falsi in agent-doctor. Se invece è **Cloud-Server**, usa come `authoritative_remote` il remote Git che punta al repo bare del VPS (Step 7) — tipicamente rinominato `origin` una volta ripuntato lì — e inserisci in `mirrors` solo copie di pubblicazione secondarie.
Non scrivere URL o credenziali nel file, solo i nomi dei remote già configurati.
Poi istruisci l'utente a lanciare nel terminale il comando di provisioning:
- Su Linux/Mac: `nexgen sync`, oppure `python3 03-INFRA/scripts/agent_sync.py apply` (o `~/.local/bin/agent-sync apply`) prima che i comandi siano installati
- Su Windows: `nexgen sync`, oppure `python 03-INFRA\scripts\agent_sync.py apply` (o `agent-sync apply`)

Il comando stampa ogni azione svolta (skill materializzate, config MCP rigenerate, istruzioni allineate, comandi riparati) e termina con un messaggio di successo, oppure con un codice di uscita diverso da zero se qualcosa lo ha bloccato — un remote git irraggiungibile, un controllo di preflight fallito, o un altro sync già in corso. Non lancia sessioni delle CLI né controlli di prontezza aggiuntivi da solo.

I comandi vengono installati in `~/.local/bin`: verifica che quella cartella sia nel PATH dell'utente (su Windows non viene aggiunta automaticamente), così `nexgen` e i nomi storici (`agent-sync`, `agent-doctor`, `vault-groom`, `vault-push`) si risolvono correttamente.

Questo script reconcile la configurazione dei CLI con le fonti canoniche del vault, installa i server MCP e propaga le skill su tutti i runtime.
Il contratto completo di pull, lock, exit code e pubblicazione separata è in `docs/sync-contract.md`.

Leggi e riporta lo stato finale stampato dal comando: un successo, o l'errore preciso che lo ha bloccato.
Non dire mai "fatto" o "completato" se il comando ha restituito un errore.
Elenca invece ciò che funziona già, ciò che manca e il comando preciso da eseguire dopo. Per una verifica più approfondita di cosa è davvero allineato, esegui poi `nexgen doctor --strict` (o il vecchio `agent-doctor --strict`): tratta come guasto anche ciò che non si è potuto determinare (per esempio un connettore MCP irraggiungibile).
In MINIMAL la diagnostica è visiva: verifica che la CLI scelta carichi AGENTS.md, monti i server MCP e veda le skill.

**Se l'architettura scelta allo Step 1 è Local-Full**, dopo questo provisioning esegui `nexgen stack up` (serve Docker installato e funzionante): avvia i cinque connettori in container su questa macchina, genera i segreti mancanti la prima volta, e scrive le variabili dei connettori (URL su `127.0.0.1`, token) in un file d'ambiente della workstation. Segui poi le istruzioni che stampa — riaprire la sessione o esportare quelle variabili — e rilancia `nexgen sync` così le CLI montano i connettori appena avviati. Nessun tunnel SSH, nessun `03-INFRA/deploy/bootstrap-vps.sh`: quello è solo per Cloud-Server (Step 7). `nexgen stack status` mostra quali servizi rispondono, `nexgen stack down` li ferma lasciando intatti i dati.

Menziona il Consiglio AI come espansione opzionale, a prescindere dal profilo: se l'utente usa già più di una CLI agentica, `nexgen council` (nome storico `council`) può convocarle come consulenti per brainstorming, sfidare un piano, o code review incrociata. È inerte senza configurazione — rimanda a `docs/council.md` solo se l'utente è interessato, non configurarlo di tua iniziativa.

### Step 7: (Solo Cloud-Server) Deploy dello stack remoto

Se l'utente ha scelto la modalità Cloud-Server, spiega che dovrà deployare lo stack self-hosted (n8n, Firecrawl, OCR, vault-mcp) sul suo VPS. I docker-compose e il bootstrap sono in `03-INFRA/deploy/`: clona il repo sul VPS, copia `.env.example` in `.env`, riempi i segreti, e lancia `bash 03-INFRA/deploy/bootstrap-vps.sh` (provisiona anche il repo bare del vault e genera `VAULT_LIBRARY_TOKEN`). Poi, sulla workstation, esporta `VAULT_LIBRARY_URL` (porta del tunnel, path `/mcp`) e `VAULT_LIBRARY_TOKEN` così le CLI montano il server `vault-library`.

**Regola da far entrare bene nella testa dell'utente e di ogni sessione futura, non solo un dettaglio tecnico:** una volta che `vault-library` è montato, il clone locale del Vault smette di essere una copia operativa e diventa un **mirror di sola lettura**, un fallback di emergenza per quando il remoto è irraggiungibile — non un posto dove scrivere o operare normalmente. Le note del vault si scrivono SOLO tramite MCP, mai con git diretto sul locale, nemmeno "solo per stavolta" o "tanto poi sincronizzo". Verifica che questo sia scritto in modo esplicito in `99-INDEX/USER-PROFILE.md` (sezione "If CLOUD-SERVER") prima di chiudere questo step. Rimanda a `03-INFRA/deploy/README.md`, `03-INFRA/remote-automation.md`, `03-INFRA/vault-write-architecture.md` e `03-INFRA/offline-emergency-mode.md` per i dettagli.

Dai il benvenuto in NeXgen Engine.

## Appendice: Workflow dei Segreti (`99-SECRETS/`)

Se l'utente deve gestire segreti (password, API key, token, chiavi SSH, credenziali dei tunnel), spiega la meccanica della cartella `99-SECRETS/`:

- La cartella è uno spazio locale git-ignored: dei suoi contenuti sono tracciati SOLO `README.md`, `.gitkeep` e `secrets-registry.md`. Mai valori in chiaro committati.
- L'indice non sensibile `99-SECRETS/secrets-registry.md` elenca quali segreti esistono (nome, provider, nome della variabile d'ambiente, data di rotazione), mai i valori. È tracciato da git, così la mappa resta allineata tra le macchine.
- I valori veri restano dove la macchina li tiene (keyring del sistema, variabili d'ambiente per-utente, config per-CLI): il motore non spedisce un gestore segreti, la scelta del deposito locale è dell'utente.
- Regola operativa: a ogni creazione o rotazione di un segreto, aggiorna la registry prima di considerare il task concluso. Mai incollare un valore in una nota normale, in un log o in una risposta.

I dettagli sono in `99-SECRETS/README.md`.

---

# English Version

> Before proceeding, if you haven't already: on Windows run `.\install.ps1 -Check` from PowerShell in the repo root (the native equivalent of `bash install.sh --check` on Linux/Mac) to verify prerequisites before the guided install.
>
> **Command equivalents for your operating system**: every `python3 ...` command below is for Linux/macOS. On Windows use `python ...` (or `py -3 ...` if `python` is not on PATH). All invoked scripts (agent_sync.py, render.py, skills-sync.py, vault-map.py) are cross-platform and delegate to `nexgen_core`; only the interpreter name changes. After the first `sync` (historical name `apply`), the bare commands in `~/.local/bin` (`nexgen`, and the historical names `agent-sync`, `agent-doctor`, `vault-push`, etc.) are available as shell shims — use them directly instead of `python3 ...`, as long as that folder is on your PATH.

You are the **NeXgen Engine Installer**. Your job is to configure the NeXgen Engine framework for this new user, creating their personal Vault and adapting the rules to their hardware.

Follow these steps **strictly** in the order shown. Do not skip to the end. Ask one or two questions at a time, wait for the answer, then proceed.

NeXgen Engine is distributed under the **PolyForm Noncommercial License 1.0.0** (full text in `LICENSE`): free for any noncommercial purpose, including this personal setup. Commercial use of the software or a derivative is not licensed: the project is noncommercial only and offers no commercial license (see `COMMERCIAL.md`). For a VPS shared across multiple people in the same company see also `docs/org-deployment.md`.

### Step 1: Installation profile (scope and architecture)

Ask the user, one question at a time:

1. **How many CLIs do they want to use?** Just one (e.g. only Claude Code), or more than one (Claude Code, Codex, OpenCode, Antigravity)?
2. **How many machines?** Just this one, or multiple workstations (e.g. laptop + desktop) that must stay aligned?
3. **Hardware of the main machine**: operating system (Windows, Mac, Linux) and dedicated GPU (if any, for local models).
4. **Where the services live** (in plain terms: a VPS is an always-on computer you rent online, and an SSH tunnel is the secure connection to reach it from here — if those words mean nothing to the user, they probably want Local-Only). There are three architectures, not two:
   - **Local-Only**: no services, everything local (no VPS, no containers). If they choose this, tell them web search will use the CLI's native tool, OCR will use the model's vision, and there will be no remote automations.
   - **Local-Full**: the same five connectors (n8n, Firecrawl, OCR, vault-library, browser) run in Docker on this same machine, via `nexgen stack up` — no VPS, no SSH tunnel. Just needs Docker installed and working. Full mechanism in Step 6.
   - **Cloud-Server**: the services run on a remote server / VPS the user has SSH access to. Ask for the IP, SSH user, and which ports to use for SSH tunnels. If that VPS will be shared across multiple people in the same organization, point the user to `docs/org-deployment.md` before proceeding: there is no per-person access control on a shared backend today. **Important to state clearly right here:** in Cloud-Server, the local Vault clone becomes a **read-only mirror**, not the working copy. Notes are written ONLY through MCP to the remote (never a direct `git commit` on notes locally); if the remote is unreachable that's an outage to report, not a green light to write or operate on the local mirror. Full mechanism in Step 7.

Determine the profile from the answers:
- 1 CLI and 1 machine → `profile: MINIMAL`, `sync_method: manual`.
- 2+ CLIs or 2+ machines → `profile: MULTI`, `sync_method: nexgen sync`.

In MINIMAL, `nexgen sync`/`nexgen doctor` (historical names `agent-sync`, `agent-doctor`), and the sync timer are not installed: they are no-ops because there is a single source of truth on a single CLI. (There is no `agent-healthcheck` command: the healthcheck is a step inside `nexgen sync`, so it is never installed separately, in any profile.) The "propagate to all" rule does not fire. Most "single source / cross-platform" rules in the bootstrap remain valid as a principle but are no-op in practice.

### Step 1.4b: The modules, with deterministic multiple-choice

The engine is a set of **modules** (memory, semantic-rag, firecrawl, ocr, n8n, browser, council, sync), each with three possible states: `absent` (off), `local` (self-hosted on this machine), `remote` (on a VPS you own, reached via tunnel). You conduct the interview, but the answers are not open-ended: they are multiple-choice selections among the states that the catalog declares for that module, and you write them with deterministic commands.

Procedure:
1. Run `nexgen modules list` and show the table: what exists and what is currently declared.
2. For each non-core module with more than one possible state, ask the user a multiple-choice question limited ONLY to the states allowed by the catalog (e.g. browser: `absent` or `local`, NEVER `remote`). Recommended defaults for those with no preference: `absent` for anything not needed, `local` for things running on this machine, `remote` for VPS services in Cloud-Server.
3. Write each response with `nexgen modules set <module> <state>`. The command rejects unsupported states: if it fails, do not invent alternatives, present the catalog options again.
4. Rerun `nexgen modules list` and show the final result.

Never edit `modules.state.yaml` by hand: the agent writes it only via `nexgen modules set`, ensuring the file remains valid with respect to the catalog (the doctor verifies it).

### Step 1.5: Take over an existing setup

Most users do not start from a blank machine: they arrive with CLIs they already use, full of their own things, MCP servers, skills, and old configs piled up over time. Before you configure anything, take over what is already there.

Run `nexgen inventory` (or, before the commands are installed, `python3 03-INFRA/scripts/agent_sync.py inventory`). It is read-only and touches nothing. Show the user the report: how many MCP servers per CLI and which are canonical or out-of-manifest, how many skills are out-of-manifest, and how much native memory is present.

If the report finds nothing stray, meaning everything is already canonical and there are no extras, tell the user and continue with Step 2: it is a clean setup.

If it finds out-of-manifest things, do not decide for them. Ask how they want to start, with this numbered menu:

```
I found an existing setup on your CLIs. How do you want to start?
[1] ADOPT: I put what you already have in order, into NeXgen's canonical source.
[2] START FRESH: I back everything up, then clear the configs and install clean (automatically for Antigravity and OpenCode; for Claude Code and Codex I'll walk you through it by hand, and for Claude Code this also erases its login: you will need to sign in again by hand).
[3] PICK BY HAND: I show you item by item, and you decide for each.
```

Based on the choice:

- **[1] ADOPT.**
  - MCP servers: for each CLI with extras, run `python3 03-INFRA/agent-universal-layer/mcp/render.py --adopt <cli> --apply`. It backs up the manifest, appends the entries under `servers:`, and re-validates, restoring the original if anything is off.
  - Out-of-manifest skills: for each, ask the user where it comes from, whether it is their own skill already in the vault or a third-party skill from a GitHub repo, add the entry to `03-INFRA/agent-universal-layer/skills/skills.manifest.yaml`, then run `python3 03-INFRA/scripts/skills-sync.py --apply`.
  - Claude native memory: read the facts in `~/.claude/projects/*/memory/*.md`, pass them through the `knowledge-vault-hygiene` skill, and write only the durable ones into the vault via the `vault-library` MCP tools, never by hand. The session transcripts of Codex, OpenCode, and Antigravity are not imported in this version.
- **[2] START FRESH.**
  - `render.py --reset <cli>` only works for **Antigravity** and **OpenCode**: they are the only two CLIs whose writer can recreate the config from scratch. For those, run `python3 03-INFRA/agent-universal-layer/mcp/render.py --reset <cli>`, which backs up the config and removes it.
  - For **Claude Code** and **Codex** the command explicitly refuses and touches nothing (for Claude Code, `~/.claude.json` is also its session/login file, not just MCP, and no script can recreate it from scratch). For those two, use `--revert <cli>` to go back to the last backup, or walk the user through a manual logout/reinstall if they really want to start fresh — in that case they will need to log Claude Code back in by hand afterward.
  - Then rebuild everything clean with the Step 6 provisioning, which regenerates the configs from the canonical manifests.
  - To go back, `python3 03-INFRA/agent-universal-layer/mcp/render.py --revert <cli>` restores the config from the backup.
- **[3] PICK BY HAND.**
  - Go item by item, each MCP server and each skill, and for each ask: adopt, drop, or leave as is. Then run the matching action from above.

By the end of this step the user's setup is either consolidated into the canonical source or reset and ready for a clean install. Continue with Step 2.

### Step 2: Profile population

Using the answers, write the file `99-INDEX/USER-PROFILE.md` for the user based on the template already present. This file will map:
- the `profile` (MINIMAL or MULTI),
- the `clis` and `machines` lists,
- the exact Vault paths,
- the chosen architecture (Local-Only, Local-Full, or Cloud-Server),
- the tunnel ports (only needed in Cloud-Server),
- the user's preferences,

so the generic engine knows how to move.

### Step 3: Document ingestion (optional but recommended)

Ask the user if they have any key documents (a CV, a description of their main project, company rules, brand identity) they want to insert into the Vault right away. Explain that these documents let agents know them immediately without having to ask.
Save them under `04-NOW/current-focus.md` or in the appropriate folder (`01-NOTES/`, `02-PROJECTS/`).

### Step 4: Vault scaffold and hygiene

Make sure the base Knowledge Vault folders exist: `01-NOTES`, `02-PROJECTS`, `04-NOW`, `99-INDEX`. They are already in the repo, but verify their existence.
Explain to the user that the Vault can be kept clean thanks to the `vault-lifecycle-audit.py` script and a dedicated hygiene skill, if they've configured one in their own manifest (see Step 6): it is not preinstalled, it's the user's own choice. With that skill active, they will not have to clean up by hand: agents will do it on their request.
If the user handles secrets (API keys, tokens, tunnel credentials), point them to the "Secrets workflow" appendix at the end of this file.

### Step 5: System prerequisites

Check with the user whether they have the required software. Prerequisites depend on the profile:

**For every profile**:
1. **Git** (for Vault versioning).
2. **Python 3** with **PyYAML** (`pip install pyyaml`) for the framework scripts.

**MULTI only**:
3. **Node.js / npm** (for MCP servers and external skills via `npx`).

In MINIMAL without MCP servers:
3. If they want to mount MCP servers (vault-library, firecrawl, etc.) they still need **Node.js / npm** for `npx`.

Wait for confirmation or help them install whatever is missing.

### Step 6: Engine launch

Use the command appropriate to the profile:

**If MINIMAL**: there is no provisioning script to run. Mount MCP and skills manually in the chosen CLI, using the canonical files `03-INFRA/agent-universal-layer/mcp/manifest.yaml` (MCP server list) and `03-INFRA/agent-universal-layer/skills/skills.manifest.yaml` (skill list) as reference. The agent (this LLM) performs the install interactively: reads the manifest, installs MCP servers, copies the base skills.

Destination file for each CLI (these match what the MULTI sync would write):
- **Claude Code**: bootstrap in `~/CLAUDE.md` with a pointer to this `AGENTS.md`; MCP servers in the `mcpServers` field of `~/.claude.json`; it may receive a native-lazy view in `~/.claude/skills/`.
- **Codex**: bootstrap in `~/.codex/AGENTS.md`; MCP servers in Codex's config file; it receives a native per-skill view in `~/.codex/skills/` (its only skill root) for every entry that lists it in its own `targets`, regardless of `exposure`.
- **OpenCode**: bootstrap in the `instructions` field of the active config, which may be `opencode.jsonc`, `opencode.json`, or `config.json`; MCP servers in the MCP section of the same file; it opens manual skills through `agent-skill find|show`.
- **Antigravity**: bootstrap in `~/.gemini/config/AGENTS.md`; MCP servers in `~/.gemini/antigravity/mcp_config.json`; it opens manual skills through `agent-skill find|show`.

For each MCP server in the manifest, the agent resolves the concrete command in the chosen CLI's dialect (Claude, Codex, OpenCode, and Antigravity use different formats, see `03-INFRA/agent-universal-layer/mcp/render.py` as a reference for the dialects).

Skills are the user's own choice, listed in their own `skills.manifest.yaml` inside the Vault (`03-INFRA/agent-universal-layer/skills/skills.manifest.yaml`) -- with one exception: the **starter commands** the engine itself ships (`nexgen-doctor`, `vault-close`, `vault-save`, `nexgen-council`, `vault-groom`, `nexgen-update`, `vault-map`; the earlier names `vault-doctor`, `vault-council`, and `vault-update` remain as deprecated aliases). Those are not a choice to make, they are the product: `/nexgen-update` is how the user will upgrade the engine, and without it they have no way to.

If `skills.manifest.yaml` does not exist yet, **create it yourself by copying `skills.manifest.yaml.example` from the same folder**, in either profile: `nexgen sync` does not seed it for you -- if the file is missing, it silently treats the skill set as empty. An existing manifest is never touched. A user who wants none of the starter commands empties it (`skills: {}`) and it stays empty forever.

Then read the manifest and install every entry per its `origin`. Beyond the starters above, assume no other name: the rest are the user's own choices, not "base" skills of the framework.
- **`origin: vault`** (vendored, the bytes live in the Vault itself): materialize the folder from `03-INFRA/agent-universal-layer/skills/<name>/` into `~/.agents/skill-library/<name>/`.
- **`origin: github`** (third-party, repo given in the entry's `repo` field): fetch the declared full commit SHA and materialize the folder given by the entry's `path` field (default: the repo root) into `~/.agents/skill-library/<name>/`.

Generate `~/.agents/skills/INDEX.md`. In the shared `~/.agents/skills` root (read by OpenCode), mount only `exposure: core` skills; Claude, Codex, and Antigravity instead each get a native per-skill view for every entry that lists them in its own `targets`, regardless of `exposure`. Skills not mounted in any view open on demand with `agent-skill show <name>`.

In every case, only the chosen CLI receives the config. No recurring scripts.

**If MULTI**: before running the provisioner, check whether the user has opened each chosen CLI at least once (Claude Code, Codex, OpenCode, Antigravity), so its default config file exists.
Do not block the whole installation because one CLI or credential is not ready yet.
Install what is available and leave the rest visibly incomplete.
Also create `03-INFRA/agent-universal-layer/sync/remotes.yaml` from its `.example`. If the architecture chosen in Step 1 is **Local-Only**, set `authoritative_remote: local`: there is no private remote to publish to, and this value makes agent-sync/agent-doctor skip publication checks, instead of mistaking the public project repo's `origin` (the one the user cloned the Vault from) for the authoritative remote and asking them to publish private notes there — a known cause of two false FAILs in agent-doctor. If it is **Cloud-Server** instead, set `authoritative_remote` to the Git remote that points at the VPS's bare repo (Step 7) — typically renamed to `origin` once repointed there — and list only downstream publication copies under `mirrors`.
Store remote names only, never URLs or credentials.
Then instruct the user to run the provisioning command in their terminal:
- On Linux/Mac: `nexgen sync`, or `python3 03-INFRA/scripts/agent_sync.py apply` (or `~/.local/bin/agent-sync apply`) before the commands are installed
- On Windows: `nexgen sync`, or `python 03-INFRA\scripts\agent_sync.py apply` (or `agent-sync apply`)

The command prints every action it took (skills materialized, MCP configs regenerated, instructions aligned, commands repaired) and finishes with a success message, or a non-zero exit code if something blocked it — an unreachable git remote, a failed preflight check, or another sync already running. It does not start CLI sessions or run any additional readiness checks on its own.

Commands are installed into `~/.local/bin`: make sure that folder is on the user's PATH (it is not added automatically on Windows), so `nexgen` and the historical names (`agent-sync`, `agent-doctor`, `vault-groom`, `vault-push`) resolve correctly.

This script reconciles the CLI configuration with the vault's canonical sources, installs MCP servers, and propagates skills to every runtime.
The complete pull, lock, exit-code, and separate-publication contract is in `docs/sync-contract.md`.

Read and report the final state printed by the command: a success, or the exact error that blocked it.
Never say "done" or "complete" if the command returned an error.
State what works, what is missing, and the exact next command. For a deeper check of what is actually aligned, run `nexgen doctor --strict` afterward (or the legacy `agent-doctor --strict`): it treats anything that could not be determined — an unreachable MCP connector, for instance — as a failure too.
In MINIMAL, diagnostics are visual: verify that the chosen CLI loads AGENTS.md, mounts the MCP servers, and sees the skills.

**If the architecture chosen in Step 1 is Local-Full**, after this provisioning run `nexgen stack up` (needs Docker installed and working): it starts the five connectors as containers on this machine, generates any missing secrets on first run, and writes the connector variables (URLs on `127.0.0.1`, tokens) to a workstation environment file. Then follow the instructions it prints — reopen the session or export those variables — and rerun `nexgen sync` so the CLIs mount the connectors that just started. No SSH tunnel, no `03-INFRA/deploy/bootstrap-vps.sh`: that is Cloud-Server only (Step 7). `nexgen stack status` shows which services are responding, `nexgen stack down` stops them without touching the data.

Mention the AI Council as an optional expansion, regardless of profile: if the user runs more than one agentic CLI already, `nexgen council` (historical name `council`) can convene them as advisors for brainstorming, challenging a plan, or cross-vendor code review. It is inert with no setup — point to `docs/council.md` only if the user is interested, don't set it up unprompted.

### Step 7: (Cloud-Server only) Remote stack deployment

If the user chose Cloud-Server mode, explain that they will need to deploy the self-hosted stack (n8n, Firecrawl, OCR, vault-mcp) on their VPS. The docker-compose and bootstrap are in `03-INFRA/deploy/`: clone the repo on the VPS, copy `.env.example` to `.env`, fill in the secrets, and run `bash 03-INFRA/deploy/bootstrap-vps.sh` (it also provisions the vault's bare repo and generates `VAULT_LIBRARY_TOKEN`). Then, on the workstation, export `VAULT_LIBRARY_URL` (tunnel port, `/mcp` path) and `VAULT_LIBRARY_TOKEN` so the CLIs mount the `vault-library` server.

**Rule that needs to land, for the user and for every future session, not just a technical footnote:** once `vault-library` is mounted, the local Vault clone stops being a working copy and becomes a **read-only mirror** — an emergency fallback for when the remote is unreachable, not somewhere to write or operate normally. Vault notes are written ONLY through MCP, never with raw git locally, not even "just this once" or "I'll sync it later." Verify this is stated explicitly in `99-INDEX/USER-PROFILE.md` (the "If CLOUD-SERVER" section) before closing this step. Refer to `03-INFRA/deploy/README.md`, `03-INFRA/remote-automation.md`, `03-INFRA/vault-write-architecture.md`, and `03-INFRA/offline-emergency-mode.md` for details.

Welcome to NeXgen Engine.

## Appendix: Secrets workflow (`99-SECRETS/`)

If the user needs to handle secrets (passwords, API keys, tokens, SSH keys, tunnel credentials), explain how the `99-SECRETS/` folder works:

- The folder is a git-ignored local space: only `README.md`, `.gitkeep` and `secrets-registry.md` are tracked. Never commit plaintext values.
- The non-sensitive index `99-SECRETS/secrets-registry.md` lists which secrets exist (name, provider, env var, rotation date), never values. It is git-tracked so the map stays aligned across machines.
- Actual values stay where the machine keeps them (OS keyring, per-user environment variables, per-CLI credential stores): the engine ships no secret manager, the local store choice is the user's.
- Operating rule: on every create or rotation of a secret, update the registry before considering the task done. Never paste a value into a normal note, a log, or a reply.

The details are in `99-SECRETS/README.md`.
