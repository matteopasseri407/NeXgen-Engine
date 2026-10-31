# Sincronizzazione di OpenClaw

🇬🇧 [English](../openclaw-sync.md)

Cosa mette `nexgen sync` (storicamente `agent-sync apply`) dentro OpenClaw, dove lo mette e cosa lascia stare. Questa è solo la vista lato motore: cosa scrive la sincronizzazione, non come si usa l'harness.

OpenClaw risolve la sua configurazione in questo ordine, e il motore segue lo stesso (`nexgen_core.paths.openclaw_config`):

1. `$OPENCLAW_CONFIG_PATH`, un file.
2. `$OPENCLAW_STATE_DIR/openclaw.json`.
3. `~/.openclaw/openclaw.json`.

La cartella di stato è quella in cui vive il file: `~/.openclaw` per impostazione predefinita, oppure una delle altre quando sono impostate. Tutto quello che segue è relativo a quella.

| Cosa | Dove | Scritto da |
|---|---|---|
| Connettori MCP | `mcp.servers` in `openclaw.json` | il renderer MCP |
| Istruzioni | un blocco in ogni `AGENTS.md` delle cartelle di lavoro | la fase istruzioni della guardia |
| Skill | `~/.agents/skills/` (il catalogo condiviso) | la fase skill |
| Il plugin | `<state dir>/extensions/nexgen/` | la fase permessi della guardia |
| Briefing, checkpoint, postura, engine | `<state dir>/extensions/nexgen/nexgen.config.json` | la fase permessi e lo stato del modulo |

## Connettori MCP

I server del manifest che puntano a OpenClaw finiscono direttamente nella tabella `mcp.servers` di `openclaw.json`, e nient'altro in quel file viene toccato: modelli, canali, impostazioni del gateway e server che il manifest non dichiara sono di OpenClaw. Una macchina dove OpenClaw non è mai stato configurato non ha un file di configurazione, e il renderer non ne inventa uno.

Dentro OpenClaw i connettori del manifest restano montati direttamente in `mcp.servers`, e il motore monta accanto anche il suo gateway `lazy-mcp`. È quella la voce che usa un agente su `claude-cli`: OpenClaw avvia Claude Code con `--strict-mcp-config --mcp-config <il suo file>`, quindi `~/.claude.json` non viene mai letto, e il motore tiene ogni `<server>__*` fuori dal server loopback `alwaysLoad` di OpenClaw (943k token misurati su una richiesta). Il gateway è una voce normale, quindi il Tool Search nativo di OpenClaw (attivo per impostazione predefinita quando `tools.toolSearch` non è impostato) continua a rimandarla, e non viene mai messa in `tools.deny`. Una voce scritta a mano con lo stesso nome viene sostituita al render successivo, perché quel nome appartiene al manifest.

Il motore non scrive mai `tools.toolSearch` e ritira ogni `tools.toolSearch: false` legacy lasciato da setup precedenti. Come documenta OpenClaw in `docs/gateway/local-models.md`, le rotte locali usano un Tool Search strutturato per conto loro, per conservare le capacità senza far esplodere il contesto del prompt. Chi vuole disattivare il Tool Search a livello globale può configurare un oggetto come `tools.toolSearch: { "enabled": false }` (vedi `docs/concepts/experimental-features.md`).

OpenClaw può anche fare da server MCP per gli altri harness (`claude`, `codex`, `antigravity`, `opencode`) tramite `openclaw mcp serve`. La voce canonica nel manifest è `enabled: false` per impostazione predefinita e viene portata a `true` da `nexgen openclaw setup`. Il renderer di OpenClaw esclude questo server quando scrive `openclaw.json`, così OpenClaw non monta mai se stesso in modo ricorsivo.

I plugin ClawHub installati in OpenClaw che incapsulano server MCP esterni o inclusi nel pacchetto possono essere adottati nella libreria canonica con `nexgen openclaw adopt --mcp`. Una volta adottati, i server sottostanti entrano in `manifest.yaml` e il renderer del motore scrive subito le CLI, lo stesso render che esegue `nexgen sync`, quindi Claude Code, Codex, OpenCode e `agy` possono usarli nello stesso minuto invece che al passaggio programmato successivo. Un render che fallisce viene riportato e non annulla l'adozione: la riga è già nel manifest, e il passaggio successivo la scrive di nuovo.

In `mcp.servers.*.env` ogni `${VAR}` resta un riferimento dentro `openclaw.json`: OpenClaw la sostituisce da sé, e una credenziale non deve essere copiata fuori dall'ambiente in un file. L'eccezione è un valore i cui riferimenti sono tutti percorsi o porte del motore (`AGENT_VAULT_DATA`, `AGENT_ENGINE_ROOT`, `KNOWLEDGE_VAULT_PATH`, le porte del tunnel), che viene scritto risolto perché l'ambiente del gateway non è quello del motore. Un valore che mescola le due cose resta un riferimento: una porta mancante è un esito migliore di un segreto esposto.

Per i server HTTP remoti il cui URL nel manifest contiene `${VAR}` o dichiara `url_env` (come l'endpoint segreto della libreria del Vault), OpenClaw non accetta `${VAR}` nel campo `url`, perché valida gli URL prima della sostituzione delle variabili. Perché nessun segreto finisca in chiaro in `openclaw.json`, il renderer scrive questi connettori come processi figli stdio attraverso `03-INFRA/agent-universal-layer/mcp/mcp-http-bridge.mjs` (via `mcp-remote`), passando `"${VAR}"` e il nome della variabile del token in `args`, dove OpenClaw fa la sostituzione sicura a runtime. I connettori con URL letterali non segreti continuano a essere scritti come HTTP nativo (`streamable-http`).

### Quando il gateway sta in WSL

La distribuzione WSL di proprietà dell'app non può avviare i comandi Windows che il manifest dichiara per i connettori stdio. `nexgen mcp bridge start` serve quegli stessi connettori sul lato Windows come MCP Streamable HTTP, e il renderer scrive, per un connettore che il motore gli chiede di mettere sul bridge, `url`, `transport: "streamable-http"` e `Authorization: Bearer ${NEXGEN_MCP_BRIDGE_TOKEN}` al posto di `command` e `args`. Nient'altro di quel connettore cambia: i suoi timeout e il suo filtro degli strumenti viaggiano con lui.

`nexgen_core.mcp_bridge.bridged_http_servers()` costruisce quella mappa dal bridge in esecuzione, e `McpRenderer.openclaw_http_servers` è il punto in cui il motore la consegna. Senza bridge attivo la mappa è vuota e ogni connettore viene scritto come prima, perché un connettore puntato a una porta morta fallisce alla prima chiamata. Il token resta un riferimento nel file: viene generato nel deposito del motore su questa macchina, e il gateway deve trovare `NEXGEN_MCP_BRIDGE_TOKEN` nel proprio ambiente dentro la distribuzione perché la sostituzione si risolva.

## Istruzioni

OpenClaw non ha un file puntatore. A ogni sessione carica l'`AGENTS.md` della cartella di lavoro dell'agente, troncato a `agents.defaults.bootstrapMaxChars` (20000 caratteri per impostazione predefinita). Il motore tiene quindi un blocco delimitato in cima a quel file:

```markdown
<!-- nexgen:instructions:begin -->
...
<!-- nexgen:instructions:end -->
```

Il blocco dice dove sta l'`AGENTS.md` canonico di questa macchina e di leggerlo e seguirlo all'inizio della sessione. Non porta una politica propria, quindi i due non possono divergere. Tutto quello che sta fuori dai marcatori è tuo e non viene mai riscritto. Un file che non esiste viene creato con il solo blocco, più il manuale della squadra dove la squadra ne dichiara uno. Una cartella di lavoro che non esiste non viene creata qui: lo fa il comando di setup. Prima di modificare un file esistente ne viene lasciata accanto una copia `.pre-instructions-<timestamp>.bak`, la stessa convenzione degli altri puntatori alle istruzioni. Una cartella di lavoro di OpenClaw è spesso un repository Git, e quella copia vi compare come file non tracciato finché non la cancelli.

Lo stesso ciclo scrive il manuale che un `squad.yaml` dichiara (`agents_md` più il `common_md` condiviso) sotto quel blocco, quindi modificare la squadra ed eseguire `nexgen sync` basta, senza aspettare il prossimo `nexgen openclaw setup`. Viene sostituito solo un file che ha scritto il motore: uno che porta il marchio della squadra, o uno che è esattamente il manuale che il motore spedisce per quell'agente, cioè quello che contiene una cartella preparata prima che la squadra dichiarasse le istruzioni. Un file scritto a mano viene lasciato byte per byte, e due agenti che condividono una cartella e dichiarano manuali diversi vengono segnalati invece che indovinati. Vedi [La squadra come dato](squad.md).

Le cartelle di lavoro vengono dalla configurazione di OpenClaw stesso: `agents.entries.<id>.workspace` per agente, `agents.defaults.workspace` per gli agenti che non ne fissano una, e `<state dir>/workspace` quando non esiste nessuna delle due.

## Skill

OpenClaw scopre `~/.agents/skills` da solo come sorgente delle sue skill personali dell'agente, per gli agenti sulla cartella di stato predefinita. Quella cartella è già il catalogo condiviso del motore, quindi `openclaw` è un target delle skill senza una cartella propria: una voce del manifest che lo nomina in `targets` viene materializzata lì, una che nomina solo un altro runtime no. `~/.agents/skills/INDEX.md` elenca ogni skill con i suoi target, così "questa skill arriva a OpenClaw?" si legge senza aprire il manifest.

### Da dove viene una skill

OpenClaw scrive una skill in più di una cartella, e `nexgen openclaw adopt` le legge tutte e tre:

| cartella | chi la scrive | adottata | dopo l'adozione |
|---|---|---|---|
| `<agentDir>/workshop-skills/<nome>` | lo Skill Workshop | solo su richiesta, con `--skills` | la copia nel Workshop viene archiviata |
| `<workspace>/skills/<nome>` | `openclaw skills install`, quindi ClawHub | sì | la copia resta dov'è |
| `<state dir>/plugin-skills/<nome>` | un link che OpenClaw genera per la skill di un plugin | solo per un plugin installato dalla persona | la copia resta dov'è |

La prima riga è quella che una persona deve chiedere. Una skill che il Workshop ha imparato da solo resta dentro OpenClaw: il passaggio automatico (il tick di 30 minuti e l'hook `skill_changed`, entrambi `nexgen openclaw adopt --auto`) non la adotta e non la mette in revisione, e il doctor non la conta come in attesa. La regola è una costante, `AUTOMATIC_SOURCES` in `adopt_scan.py`, che nomina le sorgenti portate dalle corsie non presidiate: oggi `workspace` e `plugin`, non `workshop`. Una sorgente esclusa viene comunque offerta dal passaggio che esegue una persona, che mostra il piano e chiede prima di scrivere:

```
nexgen openclaw adopt --skills     # anche le skill del Workshop
nexgen openclaw adopt              # tutto il resto, più una nota su quante sono rimaste fuori
```

Il comando semplice è onesto su cosa ha lasciato fuori: stampa una riga con il conteggio e il flag `--skills` per vederle. Adottarne una a mano è quello stesso comando, leggi la riga, rispondi sì; la copia nel Vault viene scritta e l'originale nel Workshop archiviato, esattamente come prima. Una voce di revisione che un file di stato vecchio conserva per una skill del Workshop viene ignorata invece che segnalata di nuovo.

La cartella ClawHub è la cartella di lavoro dell'agente, risolta come la risolve OpenClaw: `agents.entries.<id>.workspace`, poi `agents.defaults.workspace` (con l'id aggiunto per ogni agente tranne `main`), poi `<state dir>/workspace` o `<state dir>/workspace-<id>`. `openclaw skills install <slug>` copia in `<workspace>/skills/<slug>` e registra la provenienza in `<workspace>/.clawhub/lock.json`. Il motore scrive nella stessa cartella anche lui, `<nome>.md` e `INDICE.md`, le skill della squadra di `setup_skills`, ma come file e mai come cartella di skill, e la scansione legge solo cartelle.

`plugin-skills` è una cartella di proprietà di OpenClaw, che la riempie con un link per ogni skill di plugin pubblicata. Un plugin incluso nel pacchetto pubblica dall'interno del pacchetto, quindi un link che risolve lì non è della persona e viene saltato; anche un link che risolve altrove viene saltato. Quello che resta, un plugin installato sotto `<state dir>/extensions`, viene giudicato ancora una volta, perché OpenClaw installa i suoi plugin di catalogo in quella stessa cartella (`acpx`, `codex`, ...) e il motore vi deposita il proprio:

- un plugin il cui nome di cartella, id nel manifest o nome del pacchetto (o `@openclaw/<id>`) sta nell'istantanea del catalogo ufficiale di OpenClaw è di OpenClaw, e le sue skill parlano di strumenti che esistono solo dentro OpenClaw: non è un candidato;
- il plugin del motore (`nexgen`) non è mai un candidato;
- un plugin assente dal catalogo è della persona, e le sue skill sono candidate;
- quando il catalogo non è leggibile, nessuna skill di plugin viene offerta e la scansione lo dice, perché indovinare porterebbe dentro le skill di OpenClaw o nasconderebbe quelle della persona.

Due conseguenze da sapere:

- una skill già nella libreria canonica non viene mai adottata di nuovo: la scansione scarta ogni nome che il manifest delle skill dichiara, in qualunque cartella lo abbia trovato;
- una copia installata da OpenClaw resta al suo posto, perché nessun comando di OpenClaw la archivia. Il loader di OpenClaw fonde le sue sorgenti in un ordine fisso e la cartella di lavoro viene per ultima (`workspace-skill-loader`), quindi la copia lasciata lì è quella che OpenClaw carica: la copia nel Vault arriva alle altre quattro CLI, e le due possono divergere. Rimuovere la copia installata è una decisione della persona, in OpenClaw.

## Il plugin: un plugin, un file per lavoro

Gli hook di OpenClaw sono callback JavaScript dentro il processo del gateway, quindi il motore spedisce un solo plugin nativo e lo deposita, byte per byte, in `<state dir>/extensions/nexgen/`:

```
extensions/nexgen/
  index.mjs                     registra i lavori; il guardrail viene per primo
  guardrail.mjs                 il cancello su before_tool_call
  briefing.mjs                  il briefing del Vault dopo una compaction
  checkpoint.mjs                continuità di sessione: un puntatore a questa sessione, nello stato della macchina
  quota.mjs                     /quota: le righe che il motore ha già prodotto
  spend.mjs                     /spend: quanto è stato speso, per provider, dal motore
  workshop.mjs                  una riga di log quando lo Skill Workshop impara una skill
  adopt.mjs                     /adopt: il passaggio di adozione, e cosa aspetta una persona
  governor.mjs                  il passaggio di consegne del fallback su before_model_resolve
  council.mjs                   /council: l'AI Council, avviato ora e riportato quando finisce
  sup.mjs                       /sup: le attività supervisionate, le loro unità e la loro spesa
  sup-tools.mjs                 la supervisione che un agente può guidare, come strumenti
  evidence.mjs                  la risposta di un modello locale, tenuta alle sue ricevute
  engine-run.mjs                come ognuno di loro avvia il motore: nessuna shell, una scadenza
  engine-config.mjs             legge nexgen.config.json, una volta per ogni lavoro con un interruttore
  session.mjs                   a quale sessione appartiene un evento di hook
  package.json                  openclaw.extensions -> ./index.mjs
  openclaw.plugin.json          id: nexgen, onStartup, i nomi degli strumenti che possiede
  nexgen-guardrail-core.mjs     il nucleo condiviso che ogni adattatore importa
  nexgen-guardrail.config.json  quale corpo del guardrail, con quale timeout, il file di audit
  nexgen.config.json            gli interruttori del motore: briefing, checkpoint, postura, engine,
                                governor, evidence
  nexgen-quota-state.json       cosa ha letto il battito l'ultima volta, per il governor
  vault-briefing.txt            il testo del briefing, condiviso con Claude Code
  hooks/<corpo>.mjs             il corpo del guardrail (la politica privata)
```

Due cose sull'attivazione:

- il plugin viene depositato, non abilitato. Modificare `plugins.*` mentre il gateway gira lo rompe, quindi il motore non lo fa mai nel ciclo di guardia. `nexgen openclaw setup` lo abilita a gateway fermo. Fino ad allora `nexgen doctor` lo riporta come depositato e non abilitato, che è la verità e non un fallimento della sincronizzazione;
- un solo id. La cartella è `nexgen`; la vecchia cartella `nexgen-guardrail` viene ripresa al primo deposito, e solo quando il suo manifest prova che è del motore. Una cartella con quel nome che non è nostra non viene mai cancellata.

### Guardrail

Su `before_tool_call`, una chiamata a uno strumento che porta un comando di shell (`exec`, `process`, `terminal`, `code_execution`) viene passata al corpo del guardrail nello stesso JSON stdin/stdout che usano gli hook `PreToolUse` di Claude Code. Un diniego blocca la chiamata con il motivo del corpo; una domanda diventa una richiesta di approvazione per la persona; un permesso lascia stare la chiamata. Un guasto non diventa mai un permesso: un nucleo mancante, un sidecar illeggibile o un corpo che va in errore bloccano o chiedono, e OpenClaw stesso blocca la chiamata quando il gestore solleva un'eccezione.

Il sidecar si cerca accanto al plugin e, quando OpenClaw esegue una copia che costruisce del pacchetto sotto il proprio `<state dir>/tmp/plugin-captures/`, nel deposito, risalendo alla cartella di stato che contiene `openclaw.json`. Una copia senza nessun deposito è comunque un blocco, mai un permesso.

### Briefing

`after_compaction` e `session_start` di OpenClaw sono osservazioni: non hanno un posto dove restituire contesto, quindi il plugin segna lì la sessione (un `session_start` conta solo se porta `resumedFrom`) e inietta al primo `before_prompt_build` che segue, una volta e mai più per quella sessione. Il testo è `vault-briefing.txt`, lo stesso file che inietta `claude-vault-checkpoint.mjs` di Claude: un testo, due host.

Lo strato segue un fatto di Claude stesso: il briefing si accende solo quando l'hook del checkpoint è registrato su `SessionStart` in `~/.claude/settings.json`. Il motore non distribuisce nessuna copia di quell'hook, quindi la sua registrazione è la condizione osservabile. Un briefing che fallisce viene inghiottito: una sessione che perde il briefing è una sessione peggiore, non una rotta.

### I comandi `/quota` e `/spend`

`quota.mjs` registra un comando della chat e risponde con le righe che il motore ha già prodotto: `nexgen quota --json` stampa `lines`, e il plugin inoltra quel testo senza formattare un solo numero da solo, così telefono e terminale non possono discordare su una percentuale. Cosa dà ogni provider, e cosa non è misurabile da qui, è in [quota.md](quota.md).

`spend.mjs` fa lo stesso con `nexgen usage --json`: le stesse letture, raggruppate per il provider che è stato pagato, perché è la domanda che una chat fa. Si chiama `/spend` e non `/usage` di proposito: `/usage` è un comando di OpenClaw (il piè di pagina per risposta e il suo `/usage cost`), e un comando di plugin viene confrontato prima di quelli integrati, quindi registrare quel nome toglierebbe la superficie a OpenClaw. Il suo riepilogo dei costi somma i log delle sessioni, ed è per questo che un agente su abbonamento riporta $0 lì e la corsia flat che è stata davvero pagata non compare affatto.

Il percorso del motore è depositato, non cercato: `nexgen.config.json` porta `engine.command` e `engine.entry`, l'interprete e il modulo della CLI, perché il launcher `nexgen` su Windows è un `.cmd` e ha bisogno di `cmd.exe`, mentre il plugin lancia i processi con `shell: false` e non apre mai una shell. La chiamata ha un limite di tempo, scarta stderr e trasforma ogni guasto in una frase nella chat: nessun percorso del motore, nessun avvio, troppo lento, o un'uscita in errore. Una chat che perde `/quota` è una chat peggiore, non un gateway rotto. Su un OpenClaw la cui API non ha `registerCommand` il lavoro non registra nulla e non rompe nulla.

### L'avviso del Workshop

OpenClaw è dove il proprietario crea: lo Skill Workshop impara una skill nella cartella di stato di un singolo agente, che nessun'altra CLI legge. `workshop.mjs` ascolta l'hook `skill_changed` e, per una creazione o un aggiornamento la cui `source` è `workshop`, scrive una riga nel log del gateway: c'è una nuova skill nel Workshop, e `nexgen openclaw adopt --skills` la rende canonica. Solo osservazione: non restituisce nulla, non può bloccare il cambiamento, non scrive file e non esegue comandi, perché l'adozione riscrive la libreria ed è una decisione della persona. Una rimozione non lascia nulla da adottare, e un evento da ClawHub o da un'installazione da sorgente è affare di quell'installer, quindi entrambi restano silenziosi.

### Adozione e ritiro automatici

`nexgen openclaw adopt --auto` esegue la sincronizzazione della libreria senza una persona:

- ingresso sicuro. Le risorse verificate come SAFE entrano nel manifest o nella libreria canonica senza chiedere. Tutto il resto, sia quello che richiede una persona sia quello che i controlli rifiutano, resta fuori e viene tenuto nello stato locale della macchina (`openclaw-adopt-state.json`, nella cartella di stato del motore) con il suo motivo, e `/adopt` lo mostra.
- il Workshop resta indietro. Una skill che lo Skill Workshop ha imparato da solo non è candidata per questo passaggio, non viene scritta nello stato di revisione e non viene contata dal doctor. Resta dove OpenClaw impara; `nexgen openclaw adopt --skills` è la porta esplicita.
- provenienza. Una riga MCP entrata da OpenClaw porta `origin: openclaw` più il suo identificatore OpenClaw (`openclaw_id`); una skill adottata porta `openclaw_id` e registra la stessa coppia nel frontmatter del suo `SKILL.md`, mentre il suo `origin` nel manifest resta `vault`, la chiave che nomina da dove viene materializzata una skill. Una voce senza marchio appartiene alla libreria e non viene mai toccata.
- uscita in due passaggi per i server MCP. Un server adottato da OpenClaw e assente da `mcp.servers` per due passaggi consecutivi viene ritirato dal manifest, elencato sotto `retired_servers` e smontato dalla sincronizzazione successiva. Un solo passaggio mancato, una configurazione illeggibile o un gateway offline non rimuovono nulla e vengono registrati; un ritiro che non si riesce a scrivere viene riportato come fallimento, non come uscita. Le skill non vengono mai ritirate così: l'adozione archivia l'originale nel Workshop, quindi l'assenza cancellerebbe ciò che la libreria ha appena adottato; una copia ClawHub o di plugin resta dov'è e non è una prova neanche lei.
- il lavoro del plugin. `adopt.mjs` registra `/adopt`, ascolta `skill_changed` e tiene un passaggio limitato ogni 30 minuti sull'orologio del gateway (`gateway_start` lo arma, `gateway_stop` lo azzera, un passaggio alla volta). OpenClaw non annuncia nessun hook per `mcp.servers`, quindi è quel tick che si accorge di un server rimosso.
- render immediato. Quando un passaggio aggiunge almeno un server MCP, esegue subito il render del motore, così il server è usabile nelle altre CLI senza aspettare il tick successivo da 30 minuti. `--dry-run` non scrive nulla, e un render fallito viene riportato senza annullare l'adozione.

### Prove

Il controllo che aveva la vecchia corsia locale, spostato nell'harness che ora esegue il ciclo, così copre ogni agente su un modello locale invece di una singola azione alla volta. Su `before_agent_finalize` il plugin vede la risposta finita (`lastAssistantMessage`), il provider e i `messages` del turno; su `after_tool_call` raccoglie le ricevute man mano che arrivano. `verifyAnswer` è una funzione pura del testo e di quelle ricevute, quindi il verdetto è testabile senza gateway, modello o rete.

Cosa può dire il verdetto, e cosa non può mai fare:

| Affermazione nella risposta | Ricevuta che serve |
|---|---|
| una lettura (`ho letto`, `I read`) | una ricevuta di lettura riuscita da questo turno |
| una ricerca (`ho cercato`, `I searched`) | una ricevuta di ricerca riuscita da questo turno |
| un comando o un'esecuzione (`ho eseguito`, `I ran`) | una ricevuta di shell/exec riuscita da questo turno |
| test o verifica (`ho verificato`, `tests passed`) | una ricevuta di shell/exec riuscita da questo turno |
| una scrittura (`ho salvato`, `file aggiornato`, `I saved`) | una ricevuta di scrittura riuscita da questo turno |
| una fonte citata (`[01-NOTE/x.md]`) | quel percorso tra i file letti in questo turno |

Le tabelle contengono inglese e italiano insieme, e nessuna lingua configurata decide quale metà si legge: il motore è pubblico e risponde in inglese per impostazione predefinita, quindi una risposta inglese senza prove viene trovata esattamente come una italiana, e una risposta mista viene giudicata affermazione per affermazione. La nota è in inglese, come ogni altra frase che il plugin produce da solo, perché il motore traduce le proprie parole e il plugin non ha modo di sapere la lingua di chi legge.

Una risposta senza prove non viene scartata e non passa in silenzio:

1. il modello riceve un altro passaggio (`revise`, con `retry.maxAttempts: 1` e una chiave del motore) e gli viene detto quali affermazioni non hanno ricevuta;
2. se torna comunque senza prove, o se un hook di stop sta già chiedendo un passaggio, la risposta viene consegnata con una nota visibile, `Unverified claims: ...`, sotto.

L'interruttore e i provider non stanno nel plugin. Sono la sezione `evidence` di `nexgen.config.json`, scritta dal deposito a partire dalla configurazione in vigore: `{"enabled": true, "providers": ["llama-cpp", "ollama"]}`. Una macchina senza modelli locali riceve `{"enabled": false, "providers": []}`, e un modello cloud non è mai nella lista, quindi non viene mai controllato.

Due limiti da sapere. `revise` richiede un'integrazione di finalizzazione: il runner integrato e il relè degli hook di Codex ne hanno una, l'harness Copilot no, e una corsa che ha già avuto un effetto collaterale deterministico non viene riavvolta, ed è per questo che la nota è la strada che non dipende dall'harness. La nota si aggiunge alla consegna che porta il testo giudicato, riconosciuta dalla sua prima riga: un canale che riscrive la risposta fino a renderla irriconoscibile non riceve la nota, e nessun altro messaggio di quella sessione viene annotato al suo posto. Le tabelle delle frasi sono lunghe e bilingui, non esaustive: un'affermazione formulata in un modo che nessuna delle due liste contiene viene comunque controllata dove la regola non dipende da una frase (una fonte citata, un risultato inventato, fatti senza nulla di recuperato).

### Continuità di sessione

La continuità di sessione tra CLI passa per lo stato locale della macchina e MCP:

- da OpenClaw allo stato. Su `after_compaction` e `session_end`, il lavoro `checkpoint.mjs` scrive un puntatore di sessione in `openclaw-checkpoint.json` nella cartella di stato locale della macchina (`~/.local/state/nexgen/`, mai nel KnowledgeVault). Il record cattura l'origine (`openclaw`), l'agente, la `session_key` formattata per `openclaw mcp serve`, l'evento, il motivo e un timestamp ISO.
- da Claude Code al contesto. Su `SessionStart` (ripresa o compact), `claude-vault-checkpoint.mjs` resta rigorosamente in sola lettura e non scrive mai. Se esiste un checkpoint recente (meno di 12 ore), aggiunge una nota di guida che cita l'agente, l'ora e la chiave di sessione, e indirizza chi opera a ispezionare la cronologia della conversazione attraverso il server MCP `openclaw` (`openclaw mcp serve`, reso disponibile tra gli harness dal bridge MCP).
- Se il checkpoint manca, ha più di 12 ore o è illeggibile, l'hook ricade in silenzio sul solo testo del briefing.

Il lavoro del checkpoint è controllato da `checkpoint.enabled` in `nexgen.config.json`, attivo per impostazione predefinita come il briefing. Qualsiasi errore durante la scrittura viene inghiottito in sicurezza e non rompe mai le sessioni di OpenClaw.

### Postura

La postura del manifest privato dei permessi viaggia in `nexgen.config.json` nel vocabolario neutro, e il plugin la rende lì. `openclaw.json` non viene mai riscritto dalla fase permessi, perché si legge all'avvio del gateway e non deve cambiare sotto un gateway in esecuzione.

| Postura | Cosa fa OpenClaw |
|---|---|
| `bypass` | decide solo il guardrail: ciò che il corpo permette gira senza domanda, ciò che nega viene bloccato, un corpo che chiede chiede comunque |
| `accept-edits` | come `bypass` per le scritture su file; un comando di shell che il corpo non nega chiede approvazione |
| `ask` | ogni comando di shell chiede approvazione, e anche uno strumento che modifica file (`write`, `edit`, `apply_patch`) la chiede |

Senza una postura nel manifest non viene scritto nulla, esattamente come nelle altre CLI; il plugin si comporta allora come `bypass`.

## Parità con Claude Code

Il motore mette gli stessi sei strati in entrambi gli host. `nexgen doctor` riporta un esito `openclaw.parity.*` per strato e tace su una macchina che non ha entrambe le CLI.

| Strato | Claude Code | OpenClaw | Runtime `claude-cli` / `codex` |
|---|---|---|---|
| MCP | `mcpServers` in `~/.claude.json` | `mcp.servers` in `openclaw.json` | un agente `claude-cli` li raggiunge tramite la voce `lazy-mcp` in `mcp.servers` di OpenClaw (il file di Claude Code non viene mai letto lì); Codex usa la sua configurazione scritta dal renderer |
| Istruzioni | `~/CLAUDE.md` punta all'`AGENTS.md` canonico | il blocco gestito nell'`AGENTS.md` di ogni cartella di lavoro | il runtime dell'agente carica le sue istruzioni |
| Skill | `~/.claude/skills` | `~/.agents/skills` (catalogo condiviso) | il runtime dell'agente carica le sue skill |
| Guardrail | `PreToolUse` → `claude-guardrail-adapter.mjs` | `before_tool_call` nel plugin | il guardrail di Claude Code o di Codex, installato da questo motore |
| Briefing | `SessionStart` → `claude-vault-checkpoint.mjs` | il briefing del plugin, stesso `vault-briefing.txt` | l'hook del checkpoint di Claude Code |
| Postura | `permissions.defaultMode` | `posture` in `nexgen.config.json` | il file di postura di quella CLI |

Uno strato allineato è un PASS; uno strato che un host ha e l'altro no è un WARN che nomina la lacuna. L'azione è quasi sempre `nexgen sync`, l'unica fase che scrive entrambi i lati. Lo strato MCP confronta solo i nomi, mai un blocco `env`: un token non viene mai letto, stampato o conservato.

## Cosa non copre

Il plugin vede solo le chiamate che OpenClaw esegue lui stesso: il runner integrato, che è quello che usano gli agenti su OpenCode Go e sul modello locale. Un agente il cui runtime è `claude-cli` o `codex` esegue i suoi strumenti dentro Claude Code o Codex, dove sono già al comando il guardrail, il briefing e la postura di quella CLI, installati da questo stesso motore. Due di ciascuno su una chiamata significherebbero due politiche da tenere allineate; uno per chiamata è il punto.

`code_execution` esegue Python, non una riga di shell. È nell'insieme osservato perché un campo `command` su di esso verrebbe giudicato, ma il suo sorgente non viene passato a un corpo che confronta pattern di shell, dove corrisponderebbe per caso invece che per significato.

## Cosa controlla il doctor

| Controllo | Dice |
|---|---|
| `guardrail.consulted.openclaw` | il guardrail è stato chiamato almeno una volta da quando è stato installato |
| `guardrail.openclaw_plugin` | il plugin è depositato, e se OpenClaw lo caricherà (`plugins.allow`, `plugins.deny`, una voce spenta) |
| `instructions.openclaw` | l'`AGENTS.md` di ogni cartella di lavoro porta il blocco gestito, e quali cartelle non esistono ancora |
| `openclaw.checkpoint` | la continuità di sessione è attiva: il plugin scrive il suo puntatore alla compaction e alla fine della sessione |
| `openclaw.evidence` | se il controllo delle prove è attivo, e per quali modelli locali; un WARN quando il plugin è abilitato ma mancano l'interruttore o l'accesso alla conversazione |
| `openclaw.parity.*` | una riga per strato qui sopra: PASS quando entrambi gli host lo hanno, WARN con l'azione quando ne manca uno |
| `openclaw.unadopted` | quante skill e server OpenClaw ha creato che la libreria canonica non dichiara ancora, con `nexgen openclaw adopt` come azione; silenzioso quando uno dei due manifest non è leggibile. Una skill che il Workshop ha imparato da solo non viene contata: non è in attesa di adozione |
| `openclaw.adoption` | ultimo passaggio di adozione automatica: conteggio di entrati, usciti e voci in attesa di revisione. Una skill del Workshop che un file di stato vecchio ha lasciato in revisione viene ignorata |
