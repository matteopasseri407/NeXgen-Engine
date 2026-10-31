# OpenClaw, l'harness con l'interfaccia

🇬🇧 [English](../openclaw.md)

OpenClaw (MIT, pacchetto npm `openclaw`) è l'harness ufficiale di NeXgen Engine e il punto d'ingresso con un'interfaccia: un pannello web e un'app per il telefono accoppiata via QR, da cui una persona usa gli abbonamenti flat che già paga e i modelli che girano sulla sua macchina. La sincronizzazione lo scrive anche come quinta CLI, che è la metà minore del suo lavoro.

Il motore non fa un fork di OpenClaw e non possiede il suo file. Scrive solo le sezioni che servono a OpenClaw per raggiungere gli abbonamenti e il Vault, e lascia tutto il resto a OpenClaw e a chi lo usa.

## Cosa rileva il motore

`nexgen openclaw plan` e `nexgen openclaw doctor` leggono questa lista e nient'altro:

| Cosa | Come |
| --- | --- |
| OpenClaw | il `package.json` del pacchetto npm globale, individuato con `npm root -g`; la versione deve corrispondere a quella in `agent-universal-layer/openclaw/pin.json` |
| La configurazione | `$OPENCLAW_CONFIG_PATH`, altrimenti `$OPENCLAW_STATE_DIR/openclaw.json`, altrimenti `~/.openclaw/openclaw.json` |
| Claude | il binario `claude` sul PATH e `~/.claude/.credentials.json` presente. Su macOS il login sta nel Keychain: il motore riporta "non verificabile" e si fida del binario |
| Codex | il binario `codex` sul PATH e `auth.json` sotto `$CODEX_HOME`, oppure `~/.codex` |
| OpenCode Go | una chiave risulta disponibile se `OPENCODE_API_KEY` è impostata, se OpenClaw ha già un profilo di autenticazione `opencode-go`, o se OpenCode stesso è loggato per quel provider. L'`auth.json` di OpenCode viene letto solo per i nomi delle chiavi di primo livello, mai per un valore |
| Ollama | `GET $OLLAMA_HOST/api/tags`, predefinito `http://127.0.0.1:11434`, poi `POST /api/show` per ogni modello per la finestra di contesto e le capacità, con timeout brevi |
| llama.cpp | `GET $NEXGEN_LLAMACPP_URL/v1/models`, predefinito `http://127.0.0.1:8080`, e `/props` per la finestra di contesto |
| n8n | il modulo dichiarato `local` nel motore, con i suoi cancelli d'ambiente impostati |

Il motore non apre mai un file di credenziali per leggerne un valore. L'unica eccezione voluta è l'`auth.json` di OpenCode, analizzato per sapere quali provider hanno una chiave di primo livello, così un login `opencode-go` già esistente può essere riusato; nessun valore viene copiato da nessuna parte. I due backend locali vengono sondati tramite un client iniettabile, quindi una macchina che non li ha paga un timeout breve e nulla di più.

Il motore non scarica mai un modello. Se non ne trova nessuno, il piano stampa cosa avviare e si ferma lì.

## Gli agenti

| Agente | Cosa fa | Modello, nell'ordine in cui viene provato |
| --- | --- | --- |
| `main` (assistente) | parla con la persona, cerca nel Vault, passa il lavoro ad altri | `opencode-go/deepseek-v4.1-flash`, poi un modello locale, poi Codex, poi Claude |
| `capo` (code) | scrive e verifica codice | `anthropic/claude-opus-5-5` con `thinkingDefault: "high"` e il runtime integrato, poi Codex `openai/gpt-6-luna`, poi `opencode-go/deepseek-v4-pro` |
| `automazioni` | costruisce e ripara workflow | solo con n8n attivo: `opencode-go/deepseek-v4-pro`, poi Claude |
| `sviluppo` (developer) | lavora al progetto, uno a uno, dentro OpenCode | `opencode-go/deepseek-v4.1-flash`, e solo quando ci sono sia il comando `opencode` sia la chiave OpenCode Go |

Un agente senza modello disponibile non viene creato, e il piano dice quale e perché. Il motore prova i modelli in quell'ordine quando ne sceglie uno per un agente, così un abbonamento flat viene usato prima di una chiave a pagamento. Un modello locale precede uno cloud solo per l'assistente, dove la latenza conta più della profondità. La scelta si fa una volta, al setup; cosa succede quando il piano scelto si esaurisce è la politica descritta più sotto.

Ogni agente riceve un `AGENTS.md` nella lingua del motore: chi è, cosa fa e cosa non fa, quando passare il lavoro a un altro agente con `sessions_send`, che il Council si chiama con `nexgen council`, e che il Vault si legge e si scrive con gli strumenti `vault-library__*`.

A chi risponde e chi risponde a lui è un secondo blocco delimitato in quel file, intitolato `Organigramma` (o `Org chart`), scritto sia dal setup sia da `nexgen sync` a partire da `reportsTo`. Il manuale spedito è uguale su ogni macchina, quindi l'organico non può stare nel suo corpo.

Ogni manuale dichiara anche l'unica via di scrittura, a chiare lettere: una nota si crea o si modifica con `create_note`, `append_note`, `update_section` o `update_note`, mai con `git commit` o `git push` nella copia del Vault e mai modificandone i file a mano. Su un server quella copia è in sola lettura, quindi una scrittura fatta lì resta invisibile alle altre macchine.

L'agente di codice riceve anche `CLAUDE.md` e `.claude/settings.json`, così Claude Code si comporta allo stesso modo quando lo si raggiunge direttamente.

`nexgen openclaw smoke` è il controllo che un cambio di versione deve: chiede a OpenClaw la versione, valida la configurazione, sonda il gateway, esegue `openclaw mcp doctor` e manda un turno breve a un agente. Cinque righe, un verdetto, lo stesso comando in un terminale e in un controllo di rilascio.

Una macchina gateway preparata a mano registra il suo ruolo con `nexgen role set server`; il setup lo fa da solo con `--server`. Senza quel record il motore non può capire che la macchina è il server, e la copia del Vault che le sta accanto continua a fare commit in un clone da cui nessuno può pushare. `nexgen role` stampa cosa è registrato senza cambiare nulla.

Gli agenti possono scriversi liberamente: il motore non scrive affatto `tools.agentToAgent.allow`, e OpenClaw legge una lista assente come ogni coppia permessa. Una squadra che vuole limitare un agente dichiara `tools.agentToAgent.send` solo per quell'agente. Chi delega a chi, e chi risponde a chi, è la gerarchia che ogni manuale porta da `reportsTo` (vedi [squad.md](squad.md#reportsto)).

## La catena di riserva

OpenClaw sa già spostare un turno sul modello successivo quando un provider risponde "questo piano è finito". Quello che non può sapere è che il piano sta per finire mentre risponde ancora. Il motore fa quindi due cose con la sezione `failover:` di `03-INFRA/agent-universal-layer/quota.yaml`, e nessuna delle due sostituisce il meccanismo di OpenClaw:

- `nexgen openclaw setup` scrive la catena di ogni agente in `agents.entries.<id>.model.fallbacks`, nella forma documentata da OpenClaw (`{primary, fallbacks}`). È il failover di OpenClaw, invariato.
- Scrive anche le stesse catene, la riga di soglia e l'età massima di una lettura nel `nexgen.config.json` di proprietà del motore, accanto al plugin, dove il job del governor le legge a ogni turno.

Quando una lettura ha superato la soglia (`block_at`, 95% per impostazione predefinita), il governor restituisce `providerOverride` e `modelOverride` su `before_model_resolve`, così il turno parte sul modello successivo invece di girare, fallire e ricadere. Scrive una riga nel log del gateway con il provider, la finestra e il modello scelto. Non calcola mai una percentuale: il battito lascia i numeri in `<state dir>/extensions/nexgen/nexgen-quota-state.json`, e uno stato più vecchio di `state_max_age_minutes` (180 per impostazione predefinita) non cambia nulla. Una politica assente, illeggibile o vuota non cambia nulla neanche lei.

Due regole rendono sicuro lavorare in un file che il motore non possiede:

- una catena scritta a mano in OpenClaw non viene mai rimossa. Il merge tiene le vostre voci per prime e aggiunge solo ciò che manca, e una proposta che non nomina nessuna catena lascia la vostra esattamente com'è;
- il motore scrive catene solo per gli agenti che crea. Un agente aggiunto dal pannello di OpenClaw mantiene le sue impostazioni, e `nexgen openclaw doctor` vi dice che non ha una catena invece di modificarlo.

Il plugin ha bisogno di `plugins.entries.nexgen.hooks.allowConversationAccess: true` perché quell'hook venga registrato, dato che non è un plugin incluso nel pacchetto. Il setup lo scrive insieme al flag di attivazione. OpenClaw lo legge all'avvio del gateway, quindi un cambio lì richiede il riavvio che il setup esegue comunque.

`nexgen doctor` stampa una riga, `openclaw.fallbacks`: se una politica è dichiarata, quanti agenti hanno una catena e quali non ne hanno.

## Cosa può fare la chat

Il plugin trasforma i verbi del motore in comandi e strumenti, e ogni risposta è il testo che il motore ha già prodotto. Telefono e terminale quindi non possono contraddirsi.

| Dalla chat | Cosa risponde | Chi può usarlo |
| --- | --- | --- |
| `/quota` | quanto resta di ogni piano, e quanto è vecchia la lettura | chiunque |
| `/spend` | quanto ha speso questa macchina, per provider, e quanto resta a ogni piano | chiunque |
| `/council <domanda>` | avvia un council e risponde con un identificatore; l'esito arriva quando la corsa finisce | chiunque |
| `/sup` | ogni attività supervisionata con le sue unità, poi la spesa delle sessioni | chiunque |
| `/sup <id>` | quella singola attività | chiunque |

Gli agenti raggiungono le stesse cose tramite strumenti:

| Strumento | Cosa fa | Chi può chiamarlo |
| --- | --- | --- |
| `nexgen_council`, `nexgen_council_result` | avvia un council, poi legge cosa ha prodotto | qualsiasi agente |
| `nexgen_sup_status`, `nexgen_sup_costs` | legge la supervisione e il suo costo | qualsiasi agente |
| `nexgen_sup_start`, `nexgen_sup_unit`, `nexgen_sup_run`, `nexgen_sup_collect`, `nexgen_sup_review`, `nexgen_sup_publish` | porta un'unità supervisionata attraverso i suoi passi | l'agente supervisore, `capo` |

I sei strumenti che cambiano qualcosa sono registrati `optional`: OpenClaw non li offre a nessun modello finché un operatore non mette in allowlist il nome esatto, e `nexgen openclaw setup` scrive quell'allowlist solo su `capo`. Un agente i cui strumenti sono stati elencati a mano conserva il proprio `tools.allow`, perché OpenClaw rifiuta `allow` e `alsoAllow` nello stesso ambito, e `nexgen openclaw plan` nomina gli strumenti da aggiungere lì.

Niente di tutto questo è una seconda porta nel motore: il plugin avvia la stessa CLI che avvia un terminale, senza shell, e il guardrail vede ogni chiamata a uno strumento su `before_tool_call` come tutte le altre.

Un council è l'unico lavoro che non risponde nello stesso turno. Dura minuti, quindi `/council` lo avvia staccato e lo dice subito; la fine arriva attraverso il megafono del motore, l'unica superficie autorizzata a parlare con la persona, e la cartella di sessione della corsa conserva l'intera trascrizione.

## L'agente OpenCode, uno a uno

`sviluppo` è OpenCode stesso, avviato via ACP, quindi la stessa CLI, gli stessi login, le stesse skill e lo stesso guardrail valgono sia che una persona apra OpenCode in un terminale sia che scriva all'agente dal telefono.

Tre impostazioni lo rendono reale, e servono tutte e tre:

- `runtime.type: "acp"` con `runtime.acp.agent: "opencode"`, `backend: "acpx"` e `mode: "persistent"`;
- `model.primary` impostato a `opencode-go/deepseek-v4.1-flash`. Per un agente ACP quel campo sceglie il modello dell'harness, e senza di esso OpenClaw esegue quello che l'harness ha come predefinito, che è come un agente una volta ha risposto con il modello sbagliato;
- `acp.allowedAgents` con la sola lista degli harness che questa macchina può davvero eseguire, scelti tra `claude`, `codex` e `opencode`. `gemini` non viene mai scritto: i termini di Google non permettono il suo client fuori dalle applicazioni di Google.

Il plugin `acpx` va in `plugins.allow` con `permissionMode: "approve-all"` e `nonInteractivePermissions: "deny"`. Una sessione non interattiva non ha modo di cliccare una richiesta di permesso, quindi o procede o nega e continua, e non si blocca mai. `--no-sviluppo` lascia fuori l'agente anche quando entrambi i prerequisiti ci sono.

## La gabbia intorno a un modello locale

Un modello locale in OpenClaw può raggiungere gli strumenti di qualsiasi altro, e un modello da 12B con una shell è una cattiva idea. Ogni modello locale riceve quindi una voce in `tools.byProvider["<provider>/<id>"]`:

- `profile: "messaging"`;
- `alsoAllow`: `read`, `view_image`, `pdf`, `tts`, `memory_search`, `memory_get`;
- `deny`: le famiglie `group:web`, `group:runtime`, `group:ui`, `group:nodes` e `group:automation`, `write`, `edit`, `apply_patch`, `playwright__*`, `lazy-mcp__lazy_mutate`, e gli strumenti di scrittura di ogni server MCP che ne ha.

In OpenClaw gli strumenti di scrittura si chiamano `server__tool`, quindi la lista dei divieti viene da un'unica tabella in `profile.py` invece di essere sparsa nel codice. Oggi la tabella copre `n8n-mcp` (create, delete, update, publish, execute, test e il resto), `vault-library` (create, append, update, section), `drive` (i due passi di upload) e `firecrawl` (scrape, map, extract, crawl, agent, browser, interact; search e le letture restano permesse).

Gli id di Ollama vengono scritti esattamente come li restituisce `/api/tags`, senza il suffisso `:latest`. Con il suffisso OpenClaw non riconosce il modello e sbaglia la finestra di contesto.

Ogni blocco di modello viene scritto intero: id, nome, reasoning, i tipi di input, la finestra di contesto e il suo conteggio di token, `maxTokens`, costo zero e i flag di compatibilità. Un blocco con il solo id fa perdere a OpenClaw la finestra di contesto, e va in overflow. Quando un backend non ne dichiara una, il piano nomina il modello e lascia fuori il campo invece di inventare un numero. Una voce di modello già nel file mantiene i suoi campi, e il motore aggiunge solo ciò che manca.

La gabbia limita cosa può fare un modello locale. Cosa può affermare è una seconda domanda, a cui risponde la sezione seguente.

## Il controllo delle prove

Prima che l'harness accetti la risposta di un modello locale, il plugin la confronta con le ricevute degli strumenti dello stesso turno: una lettura che non è stata fatta, una ricerca che non è mai partita, un comando o un test che non è stato eseguito, una scrittura che non è avvenuta, una nota citata che non è mai stata aperta. Le regole vengono dalla corsia locale ritirata e ora vivono nel plugin, quindi coprono ogni agente invece di una singola azione alla volta, e sono funzioni pure di testo e ricevute.

Una risposta che non sta in piedi sulle sue ricevute riceve un altro passaggio del modello, con l'indicazione esatta di quali affermazioni non hanno prove. Se torna uguale, o se la corsa non può essere riavvolta, la risposta viene consegnata con una nota visibile sotto. Non viene mai scartata in silenzio, e la nota non finisce mai sul messaggio di qualcun altro: va sulla consegna che porta il testo giudicato. Le tabelle delle affermazioni contengono inglese e italiano insieme, senza che una lingua configurata decida quale metà si legge, quindi una risposta inglese senza prove viene trovata esattamente come una italiana e una risposta mista viene giudicata affermazione per affermazione. La nota è in inglese, come ogni altra frase che il plugin produce da solo, perché un file senza locale non può conoscere la lingua di chi legge.

L'interruttore non sta nel plugin: è la sezione `evidence` di `nexgen.config.json` accanto, scritta dal deposito a partire dalla configurazione in vigore. Elenca esattamente i provider locali che questa macchina serve, quindi un modello cloud non viene mai controllato, e una macchina senza modelli locali porta un `off` esplicito.

Il controllo legge la conversazione, che OpenClaw nega a ogni plugin non incluso nel pacchetto se `plugins.entries.nexgen.hooks.allowConversationAccess` non è vero. `nexgen openclaw setup` scrive quel permesso, fondendolo nel blocco `hooks` già presente, e `nexgen openclaw doctor` avvisa quando manca. Gli hook stessi vengono consegnati al plugin da OpenClaw, quindi non serve installare altro.

## I lavori che girano quando nessuno guarda

Dreaming, compaction, il flush della memoria prima di una compaction e la memoria attiva risolvono tutti il loro modello attraverso il modello predefinito degli agenti quando la loro chiave non è impostata. Su una macchina il cui predefinito è un modello locale, la scansione del dreaming alle 03:00, una compaction automatica e un caricamento di recall occupano quel modello mentre nessuno guarda, e su una macchina senza scheda grafica è tutto il computer per minuti. La persona ha scelto un modello locale per una conversazione, non per una scansione.

Quando il predefinito è locale e un agente gira già su un modello cloud, il setup scrive:

| Lavoro | Chiave | Cancello richiesto da OpenClaw |
| --- | --- | --- |
| dreaming | `plugins.entries.memory-core.config.dreaming.model` | `plugins.entries.memory-core.subagent.allowModelOverride: true` |
| compaction | `agents.defaults.compaction.model` | nessuno |
| memory flush | `agents.defaults.compaction.memoryFlush.model` | nessuno |
| memoria attiva | `plugins.entries.active-memory.config.model` | lo stesso cancello, su `active-memory` |

Il modello cloud è quello su cui gira già un agente, `main` per primo. `allowedModels` viene fuso come insieme, quindi un'allowlist scritta dalla persona mantiene le sue voci e guadagna questa. Non viene scritto nulla quando il predefinito è già cloud, quando nessun agente ha un modello cloud, o quando la persona ha impostato quella chiave: una chiave presente è della persona, e un `allowModelOverride: false` esplicito si legge come una decisione, non come una lacuna. La memoria attiva viene scritta solo quando quel plugin è acceso.

`nexgen openclaw doctor` dice in una riga quale lavoro sveglierebbe ancora un modello locale.

## Il watcher, e tutta la home

OpenClaw osserva `openclaw.json` per rilevare i cambiamenti. Per farlo risale di un livello dalla cartella che contiene il file e osserva quella, quindi con il predefinito `~/.openclaw` la cartella osservata è la home. Su Windows il backend lì è un unico handle ricorsivo `ReadDirectoryChangesW` su tutto l'albero: ogni file che un programma qualsiasi crea sotto la home diventa un evento che il gateway deve assorbire. Le esecuzioni di test del motore ne creano e ne cancellano migliaia, la coda si riempie, una scansione di riconciliazione apre un file che un altro processo ha già cancellato, e `EBADF` non è tra gli errori da cui quella libreria si riprende. Dopo tre tentativi OpenClaw disattiva il reload a caldo.

Il setup scrive quindi `env: { vars: { CHOKIDAR_USEPOLLING: "1" } }` quando la cartella di stato mette la home sotto il watcher e la piattaforma è Windows. È l'interruttore documentato da OpenClaw: in modalità `poll` l'hub nativo non viene mai avviato e si interroga solo il file di configurazione, ogni 100 ms per impostazione predefinita. Arriva al processo del gateway perché OpenClaw applica il blocco `env` della configurazione a `process.env` durante il bootstrap del gateway, prima che il gestore del reload crei il watcher, e non sovrascrive mai un valore già presente nel processo. Non si installa nulla, non si reinstalla nessun servizio, non si aggiunge nessuno script wrapper e non si tocca nessuna variabile d'ambiente di sistema.

Il costo è reale e dichiarato: con il polling attivo, l'indice della memoria e l'aggiornamento delle skill interrogano ogni trenta secondi invece di ricevere eventi. Su Linux il motore non scrive nulla. Lo stesso salto di un livello avviene anche lì, ma su Linux un ambito di voce è un watcher non ricorsivo per cartella, quindi la home non viene percorsa.

Un cambio di modalità ha effetto al successivo avvio. Il setup non fa ciclare il gateway solo per questa chiave: scrive la chiave, e la normale via di scrittura riavvia il gateway una volta, cosa che il piano dice quando la chiave è nuova. `nexgen openclaw doctor` dice se la macchina è nel caso ricorsivo, se sta già facendo polling, o se fa polling quando non serve più.

## Termini di servizio

OpenClaw usa gli abbonamenti della persona attraverso i login che esistono già sulla macchina. Il motore non vi scrive nessuna chiave API propria, e nessun valore di credenziale entra mai nella configurazione che produce.

In OpenClaw non si configura nessun piano Google: Gemini e Antigravity sono ammessi solo dentro i client di Google. Il motore non scrive mai un plugin `google`, e `nexgen openclaw doctor` fallisce una configurazione che ne ha uno, compresa una voce OAuth sotto un altro nome. Un seggio `gemini` in `acp.allowedAgents` è un avviso, perché il protocollo lo permette e i termini no.

Una strada resta aperta, ed è il client di Google a fare il lavoro: un plugin di backend CLI che esegue la CLI ufficiale `agy` come processo locale (OpenClaw documenta i backend CLI in `docs/gateway/cli-backends.md` e `docs/plugins/cli-backend-plugins.md`). `agy` tiene il login e la sessione, OpenClaw conserva solo il marcatore sintetico e mai un token di Google, e la rotta è `google-antigravity-cli/*` oppure un `agentRuntime.id` con lo stesso id. `nexgen openclaw doctor` dedica a quel backend una riga sua e nomina la modalità di permesso data al plugin: `skip` (e ometterla, che è il predefinito del plugin) è un avviso, perché lascia che `agy` approvi da solo ogni chiamata a uno strumento, mentre `sandbox` e `settings` mantengono un confine di approvazione.

### Il backend `agy`: il plugin, e cosa serve a `agy` stesso

Il plugin è di terzi: `google-antigravity-provider`, MIT, da `github.com/jason-allen-oneal/openclaw-google-antigravity-provider`. È pubblicato su ClawHub e si installa a una release esatta, mai a `@latest`. `agent-universal-layer/openclaw/agy-plugin.json` contiene la sorgente, il pacchetto e la versione, come `pin.json` contiene quelli di OpenClaw. La sorgente è scritta perché il pacchetto npm con lo stesso nome non esiste, e un setup che desse per scontato npm fallirebbe su una macchina nuova.

Quando la squadra mette un agente su `google-antigravity-cli/...`, in `squad.yaml` o già approvato nel file in vigore, `nexgen openclaw setup` fa tre cose nel passo di scrittura, in questo ordine:

1. Esegue `openclaw plugins install clawhub:<pacchetto>@<versione> --no-enable`. ClawHub è una sorgente fidata, quindi un'installazione nuova non chiede nulla e non ha bisogno di `--force`. Quando c'è già una copia a un'altra release, il comando guadagna `--force`, il flag che OpenClaw legge come "sovrascrivi la destinazione dell'installazione". `--no-enable` lascia l'attivazione al motore, che possiede `openclaw.json`.
2. Prova la release su disco: la `version` in `extensions/google-antigravity-cli/package.json` deve essere quella fissata, altrimenti il setup si ferma senza aver scritto nulla. ClawHub registra l'hash dell'artefatto della release che ha installato, ma `plugins install` non accetta un hash con cui confrontarlo, quindi la versione è il controllo che questo motore può fare.
3. Scrive `plugins.allow` con il backend, `enabled: true` e `permissionMode: sandbox`, oppure la modalità protetta (`sandbox`, `settings`) che il file dichiarava già. `skip` non viene mai scritto.

Una squadra che non nomina nessun modello `agy` non ottiene niente di tutto questo: nessun comando, nessuna riga, lo stesso file di prima. `--dry-run` stampa il comando di installazione invece di eseguirlo, e `nexgen openclaw plan` mostra le voci `plugins` che il setup aggiungerebbe.

Una cosa il setup si limita a segnalare, perché appartiene alla persona: `agy` sul PATH e il suo login. La credenziale non è un file che il motore legge, dato che `agy` la tiene nel portachiavi di sistema (su Windows, la voce di Credential Manager `gemini:antigravity`), quindi il doctor non dice mai "non loggato". Dice che non riesce a vedere il login e chiede alla persona di eseguire `agy` una volta.

La fiducia che `agy` chiede per ogni cartella è lavoro dello stesso passo, perché è un percorso assoluto scritto in un file che il motore può vedere. `agy` registra la risposta in `~/.gemini/antigravity-cli/settings.json` sotto `trustedWorkspaces`, e un percorso copre solo se stesso: le cartelle di lavoro degli agenti che OpenClaw crea non sono in quella lista su una macchina nuova, e un agente che parte in una cartella non fidata si ferma al prompt. Quando `agy` è sul PATH, il setup aggiunge ogni cartella di agente che manca, nell'ordine che il file ha già, e lascia stare ogni altra chiave. Prima fa un backup del file (`settings.json.pre-agy-trust-<stamp>.bak`, accanto) e dice quante cartelle ha aggiunto e dove sta il backup. Un file mancante viene creato con la sola chiave, `--dry-run` stampa lo stesso piano e non scrive nulla, e un file che il motore non riesce a leggere come oggetto JSON non viene mai sostituito: il setup lo lascia com'è, stampa le due righe che stampava prima, e la persona esegue `agy` una volta in ogni cartella e accetta il prompt. Una macchina senza il comando `agy` non riceve nessuna scrittura.

`nexgen openclaw doctor` ha tre righe per questo backend e non si sovrappongono. `openclaw.google.cli_backend` giudica la modalità di permesso. `openclaw.google.cli_backend.plugin` dice che il plugin manca o non è la release fissata, con il comando di installazione esatto. `openclaw.google.cli_backend.agy` dice che il comando `agy` non è sul PATH o che una cartella di lavoro non è fidata. Un plugin mancante non viene mai più segnalato come problema di `permissionMode`.

## Windows, le trappole che costano un pomeriggio

- `openclaw gateway restart` su Windows non riavvia. Ferma i processi e rilancia il servizio, oppure usa `nexgen openclaw restart`, che fa entrambe le cose e aspetta la porta.
- I plugin non si cambiano a caldo. Dopo una modifica a `plugins`, riavvia, e aspettati che il primo avvio sia lento.
- Un avvio a freddo richiede circa cinque minuti. Il pannello sembra morto mentre aspetta; non lo è.
- Un pacchetto OpenClaw preso dal Microsoft Store installa un secondo gateway. Due gateway si contendono la stessa porta e lo stesso stato, e `nexgen openclaw doctor` nomina il pacchetto dello Store quando lo trova. L'app Windows Hub descritta più sotto è un'altra cosa, e il motore la riconosce da solo.

## OpenClaw Companion, l'app Windows

Su Windows OpenClaw fornisce l'app **Windows Hub / OpenClaw Companion** (`OpenClaw.Tray.WinUI`). Supporta tre modalità di gateway:

1. **Distribuzione WSL (`wsl`)**: l'app prepara una distribuzione WSL2 dedicata (`OpenClawGateway`), ospita il gateway dentro Linux con lo stato sotto `.openclaw` nella distribuzione, e comunica su `ws://127.0.0.1:18789`.
2. **Gateway locale del motore (`engine`)**: l'app si collega al gateway Node nativo dell'host, gestito da NeXgen ("a local Gateway on this PC" nelle impostazioni dell'app), condividendo loopback e stato dell'host.
3. **Gateway nativo isolato (`isolated`)**: l'app prepara un gateway MSIX nativo isolato (`OpenClawFoundation.OpenClawGateway`) che gira dentro una sessione Windows isolata (`isolated-session-v1`) su una porta loopback alta (per esempio 52190). Gira sotto un profilo utente Windows separato e nascosto, con il suo spazio di lavoro e il suo runtime Node in sandbox. Non tocca WSL, ma non può leggere direttamente i file dell'utente principale né lo stato di NeXgen.

### Cosa fa oggi questo motore

Distingue queste situazioni da indizi che non avviano nulla:

| Situazione | L'indizio che legge | Cosa fa il motore |
| --- | --- | --- |
| solo il gateway nativo | il pacchetto npm e `~/.openclaw/openclaw.json` | lo configura, come sempre |
| l'app installata, che non nomina nessun gateway | i file dell'app senza gateway dentro, più il pacchetto nativo o niente | configura quello nativo come sopra, e alla fine dice il passo che collega l'app: Connections, "a local Gateway on this PC" |
| l'app sul gateway di questo PC | un URL di gateway loopback sulla porta del motore | una riga che dice che l'app è collegata e, quando il gateway risponde, se il suo dispositivo è nella lista del gateway |
| solo il gateway dell'app (WSL o isolato) | la distribuzione `OpenClawGateway`, o la sessione del gateway nativo isolato, o un gateway che le sue impostazioni nominano su questo PC | dice cosa ha trovato (WSL o nativo isolato) e si ferma prima di installare o scrivere qualsiasi cosa |
| entrambi | uno di quelli, più il pacchetto nativo | configura quello nativo, e annota il gateway dell'app come secondo gateway in conflitto |
| l'app collegata altrove | un `sshTunnel`, o un URL di gateway che non è loopback | lo dice; non c'è nessun conflitto |

- L'elenco delle distribuzioni viene da `HKCU\Software\Microsoft\Windows\CurrentVersion\Lxss`, che risponde senza avviare WSL. I file dell'app si leggono come JSON: `settings.json` per la configurazione attiva, `gateways.json` per `url`, `isLocal`, `sshTunnel` e `setupManagedDistroName`, e il pacchetto della sessione isolata `setup.json`/`gateway.json` per il contratto del runtime nativo. Ogni campo è opzionale e un file che non si interpreta non dice nulla.
- Un URL di gateway può portare un token nella query string, quindi viene analizzato per host e porta e poi scartato: nessun URL, nessun token e nessuna chiave di dispositivo viene mai stampata, e un file `device-key*` non viene mai aperto.
- Il motore non avvia mai l'app, WSL o la distribuzione, e non scrive mai sotto `%APPDATA%\OpenClawTray`, `%LOCALAPPDATA%\OpenClawTray`, il profilo della sessione isolata o `\\wsl.localhost`.

### Come doctor, plan e setup trattano ogni modalità

`nexgen openclaw doctor`:

- `openclaw.companion_gateway` riporta OK quando è collegato al gateway del motore, o quando il gateway nativo isolato o la distribuzione WSL girano da soli come gateway designato. Avvisa solo quando un gateway del motore e un gateway gestito dall'app girano insieme.
- `openclaw.companion_node` avvisa quando la modalità nodo dell'app è attiva accanto al servizio nodo della CLI, o quando il nodo dell'app non ha credenziali. Prende il posto di una riga OK qui sopra finché una delle due condizioni è vera, e si unisce a una riga di avviso quando c'è, perché quella è la riga su cui una persona deve agire.
- `openclaw.store_gateway` esclude il gateway nativo isolato MSIX dell'app (`OpenClawGatewayMSIX`), così i conflitti con il pacchetto dello Store non vengono segnalati due volte.

`nexgen openclaw plan` annota la modalità di gateway rilevata per l'app (`isolated`, `wsl`, `engine`, `remote`) e distingue un gateway nativo isolato da uno WSL o remoto nei riepiloghi della proposta.

`nexgen openclaw setup`, quando è attivo un gateway dell'app (WSL o isolato), si ferma e spiega come passare al gateway nativo con `--node-gateway`, una volta fermato il gateway dell'app. Non modifica, rimuove o sovrascrive mai i file della sessione isolata o le impostazioni dell'app.

### Il gateway dell'app e quello nativo, quando servono entrambi

Il motore configura il gateway Node nativo, ed è la strada che supporta: l'app è l'interfaccia, il gateway è di questo motore. Quando l'app gestisce l'unico gateway presente, il setup si ferma e lo dice nel piano e nell'esecuzione, con il passo che lo risolve, perché un secondo gateway sulla stessa porta non aiuta nessuno.

`nexgen openclaw setup --node-gateway` imbocca comunque quella strada. Prima di installare o scrivere qualsiasi cosa chiede se la porta del gateway risponde, perché due gateway non possono condividerla:

- non risponde nulla, quindi il gateway nativo viene installato, configurato e avviato, e all'app viene detto dove guardare;
- il gateway dell'app risponde ancora, quindi la corsa si ferma e dice di fermarlo in OpenClaw Companion e riprovare.

La distribuzione dell'app non viene mai avviata, fermata o scritta, con o senza l'opzione. L'app non è nemmeno qualcosa da rimuovere: le righe del doctor dicono quale gateway è quale, mai che l'app deve andarsene.

### Un nodo per PC: l'app, non il servizio CLI

Su Windows l'app Companion ha una sua modalità nodo (`EnableNodeMode` in `%APPDATA%\OpenClawTray\settings.json`), e anche la CLI di OpenClaw ne ha una: `openclaw node install` registra l'attività pianificata `OpenClaw Node` e lascia `~/.openclaw/node.cmd` accanto. Entrambe insieme sono due nodi su un PC. La decisione del proprietario è che il nodo è l'app, quindi il servizio CLI è quello che va via.

L'ordine è la parte che va storta, perché il servizio CLI è il nodo che questo PC ha oggi: rimuoverlo prima che il nodo dell'app funzioni lascia la macchina senza nessun nodo. `nexgen openclaw node --use-app --apply` rifiuta quindi mentre l'app ha la modalità nodo attiva e il suo nodo non è collegato, e stampa i tre passi nell'ordine in cui funzionano:

1. in OpenClaw Companion, apri Connections e accoppia l'app con il gateway, lasciando attiva la modalità nodo;
2. sul gateway, `openclaw devices list`, poi `openclaw devices approve <id>`, e `nexgen openclaw pair --approve-node <id richiesta nodo>` per la richiesta del nodo;
3. quando l'app mostra il suo nodo collegato, esegui di nuovo `--use-app --apply`: è il passo che rimuove il servizio nodo della CLI e registra il ruolo `node`.

Il motore legge entrambi i fatti e non scrive nulla sotto nessuna delle due cartelle dell'app:

- modalità nodo spenta: niente di nuovo, e la riga del doctor sul gateway dell'app è tutto quello che c'è;
- modalità nodo attiva e servizio nodo della CLI installato: un avviso nomina i due nodi e mette l'accoppiamento prima della rimozione, perché la rimozione è il passo che può lasciare questo PC senza nessuno;
- modalità nodo attiva e ultima riga del nodo nel log dell'app uguale a `No node credential available`: un avviso dice che l'app non ha potuto usare il suo nodo, con i passi che lo accoppiano di nuovo (`openclaw devices list`, poi `openclaw devices approve <id>` sul gateway) e la frase che il servizio CLI va via solo dopo.

Il log dell'app si legge dalla fine, l'ultimo megabyte, e solo quando la modalità nodo è attiva: l'ultima riga `Node status:` è come il motore sa che il nodo dell'app è collegato (`Disconnected` contiene `connected`, quindi le due parole si confrontano come parole intere). L'attività pianificata viene interrogata con un `schtasks /Query` di sola lettura che rinuncia dopo cinque secondi. `nexgen openclaw node --use-app` mostra quella transizione e `--apply` la esegue. Il motore non scrive mai `settings.json`, non avvia mai l'app e non spegne mai da solo la modalità nodo: quell'interruttore è della persona.

#### Il codice di setup che l'app accetta, e perché il motore non lo consegna

L'app accetta davvero un codice di setup senza tastiera. Il suo server MCP locale pubblica `app.connection.applySetupCode` (argomento `setupCode`, stringa, obbligatorio; restituisce `{ outcome, error, gatewayUrl, connected }`) su `http://127.0.0.1:8765/mcp`: JSON-RPC 2.0 solo POST, `Authorization: Bearer <token>` con il token in `%APPDATA%\OpenClawTray\mcp-token.txt`, solo loopback e chiuso ai contesti del browser. L'altro lato dello stesso handshake è l'RPC `device.pair.setupCode` del gateway, che richiede `operator.admin` e accetta `bootstrapProfile: "node"` per un nodo. `openclaw devices join-code` è la CLI che la chiama, e trasforma il codice in un URL di onboarding monouso. `openclaw qr` non passa da quell'RPC; genera un codice di setup mobile in locale, dalla configurazione della macchina su cui gira.

Quel server non è attivo quando il motore ne avrebbe bisogno. Lo avvia il servizio nodo dell'app mentre il nodo si collega (`EnableMcpServer` nelle impostazioni, `SetMcpEnabled` nell'app), e il log dell'app lo mostra in ascolto solo nelle esecuzioni in cui il nodo aveva una credenziale: con `No node credential available` il servizio nodo non si attacca e la porta 8765 rifiuta la connessione. Un codice di setup è esattamente la credenziale che manca, quindi la strada è un cerchio la cui unica uscita sono la pagina Permissions dell'app e la sua schermata Connections. Nessun argomento da riga di comando (`--wait-for-pid`, `--post-setup-restart`, `--uninstall`, `--headless`, `--data-dir`, ...) e nessun percorso `openclaw://` porta un codice di setup: lo schema URI accetta un indirizzo di gateway e comandi di interfaccia, e un percorso sconosciuto viene rifiutato. Finché non cambia, il motore non conia nulla che non possa consegnare e non stampa nulla che dovrebbe poi conservare, e la transizione resta i tre passi qui sopra.

### Cosa non fa ancora

- Non configura il gateway dell'app: nessuna configurazione scritta attraverso `\\wsl.localhost`, nessun servizio, nessun plugin depositato dentro la distribuzione, nessun accoppiamento al suo interno.
- Non vi installa il suo plugin guardrail come pacchetto.
- `nexgen openclaw setup` su una macchina dove l'app possiede l'unico gateway si ferma e nomina le due strade: usare l'app così com'è, oppure installare il gateway nativo ed eseguire di nuovo il setup. `--node-gateway` prosegue con quello nativo una volta libera la porta, il che significa che il gateway dell'app è stato fermato nell'app.

Il supporto completo, che significa parlare con il gateway attraverso il suo protocollo e installare il plugin come pacchetto, arriva in una release successiva. Fino ad allora i due gateway restano due cose separate, e il motore scrive sempre e solo quello nativo.

## Parlare con il gateway

Il client RPC (`nexgen_core.openclaw.gateway_rpc`) implementa il trasporto WebSocket JSON-RPC (protocollo v4), così il motore parla direttamente con il gateway OpenClaw (`ws://127.0.0.1:18789`).

Si autentica come client operatore con il token del gateway stesso, senza mai stamparlo o registrarlo nei log, ed espone una primitiva per operazione:

- `health()`: lo stato operativo del gateway e i plugin che ha caricato;
- `config_get()`: l'istantanea della configurazione e l'hash di base che serve al controllo di concorrenza;
- `config_patch(patch, base_hash)`: un aggiornamento parziale della configurazione, protetto da locking ottimistico;
- `plugins_install(source)` e `plugins_reload(id)`: installare un plugin e ricaricarlo sul posto.

Con questo client il motore configura OpenClaw e gestisce i suoi plugin qualunque sia l'ambiente di hosting (un gateway nativo Windows, un gateway WSL gestito da OpenClaw Companion, uno Linux o uno remoto): non deve mai entrare in un filesystem isolato, e non deve mai riavviare un processo.

Un comando di sola lettura ispeziona la connessione RPC a mano: `nexgen openclaw rpc health [--json] [--url URL]`.

## Il plugin come pacchetto, per i gateway che il motore non raggiunge

Il motore deposita il plugin `nexgen` copiandone i file in `<state dir>/extensions/nexgen/`, il che richiede un gateway il cui filesystem possa scrivere. Il gateway che l'app Companion esegue dentro la sua distribuzione WSL non lo è: `C:` non è montato lì e la distribuzione appartiene all'app, quindi il motore non deve entrarci. OpenClaw installa lì un plugin da un tarball npm, e `nexgen openclaw package` costruisce quel tarball dalla lista che il deposito stesso legge.

Contiene i file del plugin, il `nexgen-guardrail-core.mjs` condiviso e `vault-briefing.txt`, più un `package.json` che porta la versione del motore, dichiara `type: module` e punta `openclaw.extensions` a `./index.mjs`. Il corpo del guardrail, `nexgen-guardrail.config.json` e `nexgen.config.json` restano fuori: la politica appartiene al Vault, e questo artefatto è la parte che viaggia. Gli stessi file producono gli stessi byte e lo stesso sha256, quindi una build cambiata senza che nessuno se ne accorgesse non può passare per quella revisionata.

Il tarball finisce in `<state dir>/openclaw/plugin/nexgen-<versione>.tgz`. Il motore non installa nulla da solo: spostarlo sull'host del gateway ed eseguire `openclaw plugins install npm-pack:<path.tgz> --force` lì è un passo separato che qualcuno può vedere. Senza un sidecar accanto il plugin non ha una politica da eseguire, quindi rifiuta i comandi che dovrebbe giudicare invece di permetterli: il pacchetto porta il meccanismo, e la politica arriva dalla via che quel gateway permette.

## Il bridge MCP, quando il gateway gira in WSL

L'app companion di Windows tiene OpenClaw in una distribuzione WSL di proprietà dell'app dove l'interop è spento e `C:` non è montato, quindi un connettore che il motore dichiara come comando Windows (`python ...\lazy-mcp.py`, `node ...\playwright-human-safe.mjs`) non parte mai lì.

`nexgen mcp bridge start` esegue quei connettori sul lato Windows e li serve come MCP Streamable HTTP: un endpoint per connettore su `http://127.0.0.1:8765/mcp/<nome>`, più `/mcp` per il gateway stesso (`lazy-mcp`). La lista dei connettori non è scritta due volte: il bridge chiede al renderer cosa questa macchina monta direttamente in OpenClaw e avvia esattamente quello, quindi un connettore aggiunto al manifest compare al bridge da solo. `--server NOME` la restringe, e `--wsl` lega l'interfaccia virtuale WSL, l'indirizzo che una distribuzione sulla rete NAT predefinita raggiunge (con il networking mirrored basta il loopback).

Ogni richiesta porta `Authorization: Bearer $NEXGEN_MCP_BRIDGE_TOKEN`. Il token viene generato nel deposito dei segreti del motore (`~/.config/nexgen/secrets.env`, 0600) e non viene mai stampato; OpenClaw sostituisce la variabile da sé, quindi nessun valore entra in `openclaw.json`. Nulla avvia il bridge automaticamente: `nexgen mcp bridge start`, `stop` e `status` sono tutta la superficie, e `status` esce con codice diverso da zero quando non c'è nulla in ascolto.

`0.0.0.0` è accettato solo come `--host` esplicito e solo con il token. `stop` chiede al listener di finire e aspetta la porta; non uccide mai un processo per pid, perché il pid in un file di stato vecchio può appartenere a qualcun altro nel momento in cui lo si usa.

## Installazione con un comando

`nexgen openclaw setup [--yes] [--lan | --server] [--yes-i-know] [--dry-run] [--no-sviluppo] [--node-gateway]` esegue nove passi in questo ordine, stampando cosa ha fatto ciascuno. Si ferma al primo passo che non può riuscire, e la macchina mantiene lo stato che aveva prima di quel passo.

1. **Installazione.** Se OpenClaw manca, o non è sulla release che `openclaw/pin.json` nomina: `npm install -g openclaw@<pin> --allow-scripts=@google/genai,esbuild,koffi,protobufjs,openclaw`, con npm risolto al suo percorso completo (`npm.cmd` su Windows). Una macchina senza Node riceve un errore chiaro e il link per il download; il motore non installa mai un runtime di sistema da solo.
2. **Configurazione iniziale.** Senza `openclaw.json`, `openclaw onboard --non-interactive --accept-risk --skip-health --no-install-daemon` crea la base, e la corsa continua come se il file fosse sempre stato lì. `--skip-health` perché il gateway non è ancora avviato; `--no-install-daemon` perché il servizio lo installa il motore, e due supervisori per un gateway si contendono la stessa porta.
3. **Piano e conferma.** Stampa esattamente quello che stampa `nexgen openclaw plan`, poi chiede. `--yes` salta la domanda; `--dry-run` si ferma qui dopo aver stampato il piano e i comandi che eseguirebbe.
4. **Scrittura sicura**, a gateway fermo, perché OpenClaw non ricarica a caldo un blocco `plugins` cambiato:
   - un backup accanto al file, `openclaw.json.pre-nexgen-<YYYYMMDD-HHMMSS>`;
   - le sezioni di proprietà del motore fuse nel file in vigore: dizionari fusi ricorsivamente, una chiave che la persona ha scritto fuori dalla proposta mantenuta, `plugins.allow` fuso come unione ordinata, le voci di modello fuse per `id` così un modello scritto a mano non viene perso, e le foglie con lista come `deny` e `allowedModels` fuse come insiemi;
   - i modelli dei lavori in background quando il modello predefinito è locale, e `env.vars.CHOKIDAR_USEPOLLING: "1"` su Windows quando il gateway altrimenti osserverebbe la home, entrambi descritti sopra;
   - il token del gateway generato con `secrets.token_urlsafe(32)` solo quando non ce n'è uno, e mai stampato;
   - il plugin guardrail del motore abilitato solo quando la sua cartella è davvero depositata, e autorizzato con `hooks.allowConversationAccess`, che serve sia al suo hook governor sia al controllo delle prove;
   - una scrittura atomica, poi `openclaw config validate` sul file appena scritto. Un fallimento rimette il backup, stampa l'errore di OpenClaw e esce con codice diverso da zero;
   - dopo che OpenClaw ha accettato il file, le catene di riserva arrivano nel `nexgen.config.json` del plugin, che viene letto a ogni turno e non richiede riavvio.
5. **Cartelle di lavoro degli agenti.** Ogni cartella viene creata quando manca e i manuali vengono scritti solo quando assenti, così un manuale modificato dalla persona non viene mai sovrascritto. L'unica eccezione è una cartella creata dall'onboarding un attimo prima: dentro non può esserci ancora nulla della persona, quindi il manuale del motore sostituisce il segnaposto di OpenClaw e il suo `BOOTSTRAP.md` viene rimosso, perché quel rito di primo avvio ridefinirebbe l'agente.
6. **Servizio.** Il gateway viene installato come servizio quando non lo è già, avviato quando l'installazione lo ha solo registrato, e la porta viene attesa fino a dieci minuti, con i secondi trascorsi stampati: un avvio a freddo richiede circa cinque minuti.
7. **Modulo e bridge.** L'host dichiara `openclaw: local` in `modules.state.yaml`, sotto il proprio nome, e abilita il server MCP canonico `openclaw` (`openclaw mcp serve`) in `manifest.yaml`, così altri harness possono raggiungere gli strumenti di OpenClaw.
8. **Telefono.** Stampa `openclaw qr`, da eseguire in un terminale proprio, e `nexgen openclaw pair` per l'approvazione.
9. **Doctor.** Gira `doctor.collect()` e viene stampato il suo riepilogo.

Dopo il doctor, su Windows, la corsa dice all'app Windows dove sta il gateway e offre la richiesta che l'app lascia. Un'app installata prima del motore non nomina nessun gateway: la corsa dice di aprire Connections in OpenClaw Companion e scegliere "a local Gateway on this PC". Una richiesta già in attesa viene stampata con il suo id e con quello che dichiara di essere, poi approvata solo quando la persona risponde sì a quel prompt. `--yes` risponde al piano, mai a un'approvazione di dispositivo, e una corsa senza terminale non approva nulla e stampa il comando. Più richieste vengono elencate e nessuna viene scelta al posto della persona.

Tutto è idempotente: un secondo `setup` senza nulla da cambiare non scrive file e non riavvia il gateway.

### Una macchina sempre accesa, senza schermo: `--server`

`nexgen openclaw setup --server` sono gli stessi nove passi per una macchina Linux senza desktop: un VPS, un mini PC in un armadio. Il gateway resta su `gateway.bind: "loopback"` e OpenClaw lo pubblica con `gateway.tailscale.mode: "serve"`, la coppia che raccomanda il suo stesso manuale (`gateway/tailscale.md`, `gateway/stable-https-url.md`): HTTPS con un certificato reale su `https://<host>.<tailnet>.ts.net`, e nessuna porta aperta sulla macchina. Una sezione `tailscale` già in `openclaw.json` viene mantenuta; cambia solo il suo `mode`.

Prima che si installi qualsiasi cosa, quattro domande decidono se questo host può portare un gateway:

1. È Linux? Un desktop si configura senza `--server`; `--server` su un altro sistema si ferma e lo dice.
2. Tailscale è installato? Se no, la corsa nomina la pagina di download e si ferma: il motore non installa mai un programma di sistema da solo.
3. Tailscale è loggato? `tailscale status --json` deve riportare `BackendState: "Running"`; qualsiasi altro stato ferma la corsa con `tailscale up` come azione.
4. L'account ha poteri di root? Root stesso, `sudo -n true` che riesce, o l'appartenenza al gruppo `docker` fermano la corsa, perché un gateway che esegue agenti non deve poter diventare root attraverso il processo che supervisiona. `--yes-i-know` è la via esplicita per superarlo, e la corsa stampa cosa ha accettato.

`--server` e `--lan` sono opposti e vengono rifiutati insieme: `--lan` apre il gateway alla rete su cui sta, `--server` lo tiene su loopback e lascia che sia Tailscale Serve a pubblicarlo.

Il servizio è il servizio utente systemd che installa OpenClaw (`openclaw gateway install`), e il controllo del lingering che un host Linux riceve già vale anche qui: senza `loginctl enable-linger <utente>` il gateway si ferma quando si chiude l'ultima sessione.

## OpenClaw come server MCP per gli altri harness

OpenClaw può servire i suoi strumenti agli altri harness (`claude`, `codex`, `antigravity`, `opencode`) tramite `openclaw mcp serve`. La voce canonica è dichiarata in `03-INFRA/agent-universal-layer/mcp/manifest.yaml` con trasporto stdio:

```yaml
openclaw:
  exposure: eager
  transport: stdio
  command: openclaw
  args: ["mcp", "serve"]
  targets: [claude, codex, antigravity, opencode]
  enabled: false
```

È disattivata per impostazione predefinita e viene attivata automaticamente durante `nexgen openclaw setup` quando OpenClaw è installato sulla macchina. Il renderer di OpenClaw la esclude esplicitamente, così OpenClaw non configura mai se stesso come client MCP.

Per tornare indietro, ferma prima il gateway, copia il backup sopra il file, poi lascialo ripartire così i plugin vengono ricaricati:

```bash
nexgen openclaw stop
cp ~/.openclaw/openclaw.json.pre-nexgen-20261023-120000 ~/.openclaw/openclaw.json
nexgen openclaw restart
```

`nexgen openclaw stop` ferma il gateway e aspetta che processo e porta siano spariti. `nexgen openclaw restart [--timeout SECONDI]` ferma il gateway, lo avvia e aspetta la porta, stampando i secondi impiegati; una porta che non risponde mai è un'uscita con codice diverso da zero, mai un successo silenzioso. Su Windows è l'unica cosa che lo riavvia: il `gateway restart` di OpenClaw non funziona.

`nexgen openclaw pair` elenca i telefoni in attesa di approvazione. `nexgen openclaw pair --approve <id>` ne approva uno, chiedendo prima a meno che non ci sia `--yes`. Nessun token viene mai stampato: `pending()` elimina ogni campo il cui nome somiglia a un segreto prima che arrivi sullo schermo.

Un nodo, cioè un'altra macchina invece di un telefono, entra attraverso una seconda richiesta con un id suo. `nexgen openclaw pair` elenca anche quelli, e `--approve-node <id>` mostra il nome del nodo, la sua piattaforma e i comandi che gli sarebbe permesso eseguire prima di chiedere. Un id che non è in attesa viene rifiutato, perché un'approvazione che non si riesce a descrivere è un'approvazione che nessuno ha dato.

## Due PC

Un gateway, un nodo. Il gateway è la macchina che tiene i canali, i job cron, la memoria e il routing dei modelli; il nodo è una macchina che esegue i comandi di OpenClaw per lui. Quello che una persona legge arriva comunque attraverso il gateway, dovunque sia girato il comando.

Un canale vive su un gateway alla volta. Lo stesso PC può avere un gateway suo ed essere nodo di un altro gateway, ma un bot Telegram non può rispondere da entrambi.

### A casa: il portatile come nodo del PC fisso

Sul PC fisso, una volta:

1. `nexgen openclaw setup --lan` scrive `gateway.bind: "lan"` e un token vero. `nexgen openclaw plan --lan` mostra lo stesso cambiamento senza scriverlo. Controllo: `nexgen openclaw doctor` dice che il gateway ascolta sulla rete di casa.
2. `nexgen openclaw restart` se il gateway era già in esecuzione, così si lega di nuovo. Controllo: il comando stampa la porta che risponde, e il doctor è d'accordo.

Sul portatile, una volta:

1. `nexgen openclaw setup` installa la release fissata, scrive la configurazione del portatile e il suo gateway loopback. Quel gateway locale non è quello con cui parla il nodo, e questo setup non lo tocca più. Controllo: `nexgen openclaw doctor` passa sul portatile.
2. `nexgen openclaw node --host <indirizzo del PC fisso>` stampa le due rotte documentate, in ordine, con il controllo dopo ogni comando. Non ne esegue nessuna. Controllo: i comandi stampati sono quelli qui sotto. Su un PC che aveva già un gateway suo, `nexgen openclaw node --host <indirizzo> --apply --join-file <file>` esegue i passi 3 e 4 e ritira prima il vecchio gateway; le rotte stampate restano il riferimento per farlo a mano.
3. O la rotta in un solo incolla: `openclaw devices join-code` sul PC fisso, poi il comando che stampa, `openclaw connect <url> --service --session-host`, sul portatile. OpenClaw approva il dispositivo e la sua prima superficie di comandi nello stesso passo; `--session-host` lascia anche eseguire sessioni di agente al nodo, quindi tienilo per una macchina di cui ti fidi come infrastruttura condivisa. L'URL è monouso, dura circa dieci minuti e porta accesso amministrativo: trattalo come una password e non incollarlo da nessun'altra parte. Oppure la rotta esplicita: `openclaw node install --host <indirizzo> --port 18789` e `openclaw node start` sul portatile, poi `nexgen openclaw pair` sul PC fisso per leggere la richiesta del dispositivo, `nexgen openclaw pair --approve <id dispositivo>` per ammetterlo, una riconnessione che chiede i comandi, e `nexgen openclaw pair --approve-node <id richiesta nodo>` per permetterli. Controllo: `openclaw node status --json` sul portatile.
4. Su Linux, `loginctl enable-linger <utente>` una volta: senza, il servizio utente systemd si ferma quando si chiude l'ultima sessione. `nexgen doctor` dice già quando il lingering è spento.

Sul PC fisso, per vedere il risultato: `nexgen openclaw doctor --nodes` aggiunge i nodi accoppiati al report e controlla che il nodo accetti i comandi che un agente gli chiede di eseguire, e `nexgen openclaw pair` li elenca con il loro stato. Nessuno dei due approva qualcosa da solo.

### Cosa dice il doctor su un nodo

Su una macchina che è solo un nodo, `nexgen doctor` e `nexgen openclaw doctor` smettono di giudicare il gateway: bind, token, esposizione, la porta su cui ascolta, il plugin, gli agenti e i loro modelli non vengono letti lì, e una riga dice perché. L'`openclaw.json` che il PC ha conservato da quando era un gateway resta dov'è, perché il nodo ne legge ancora `nodeHost`, e non è un guasto.

Tre fatti rendono la macchina un nodo, in questo ordine: il ruolo che ha registrato (`nexgen role set node`, scritto anche da `install.ps1 -Role node` e da `nexgen openclaw node --apply`); il servizio nodo installato da OpenClaw, che punta a un gateway altrove, per un PC migrato prima che quel ruolo esistesse; l'app Companion con il suo interruttore nodo attivo e un gateway altrove in `gateways.json`. I file dell'app vengono letti e mai scritti. Una macchina che esegue ancora un servizio gateway locale è un gateway, e mantiene il report completo.

### Un gateway dietro Tailscale Serve, e un portatile sulla stessa tailnet

Sulla macchina sempre accesa: `nexgen openclaw setup --server`, poi `nexgen openclaw restart` se il gateway era già in esecuzione. Controllo: `nexgen openclaw doctor` dice che il gateway ascolta su loopback e che Tailscale Serve lo pubblica in HTTPS sulla tailnet.

Sul portatile: `nexgen openclaw node --host <host>.<tailnet>.ts.net` stampa le stesse due rotte e riconosce il nome MagicDNS. Il codice di join porta l'indirizzo HTTPS che il gateway conia, quindi `openclaw connect <url> --service --session-host` non ha bisogno né di porta né di token proprio; la rotta esplicita viene stampata come `--port 443 --tls`, perché quella porta appartiene a Serve. Entrambe le macchine devono stare sulla stessa tailnet ed essere loggate a Tailscale. Controllo: `openclaw node status --json` sul portatile, e `nexgen openclaw pair` sul gateway per l'approvazione.

### Fuori casa

Il portatile tiene il suo gateway in loopback, senza canali. È quello da cui una persona legge quando il PC fisso non è raggiungibile, ed è il motivo per cui la riga del doctor su un gateway loopback lì è un'informazione e non un guasto: un nodo su un altro PC non può raggiungerlo, e su quella macchina non deve.

La connessione del nodo al PC fisso esiste solo finché le due macchine si raggiungono. Le approvazioni non si perdono quando cade, e nient'altro si perde: memoria, canali e job cron non hanno mai vissuto sul portatile.

### Cosa resta della persona

- L'indirizzo dell'altra macchina. Il motore non lo indovina e non lo legge da nessuna parte.
- Entrambe le approvazioni, quella del dispositivo e quella dei comandi. Nulla viene approvato automaticamente, e ogni richiesta dice quale macchina chiede e cosa le sarebbe permesso eseguire.
- Quale gateway possiede un canale. Mettere un bot su entrambi i gateway è l'errore che questa pagina esiste per evitare.

## Comandi

`nexgen openclaw plan [--json] [--lan | --server] [--acpx]` legge la configurazione in vigore, calcola cosa scriverebbe il motore e stampa cosa aggiungerebbe, cosa cambierebbe, cosa non propone più e quali chiavi sono della persona e restano. Non scrive nulla; `--server` è l'anteprima in sola lettura del cambiamento che `setup --server` applica.

`nexgen openclaw doctor [--json] [--nodes]` controlla l'installazione, la versione rispetto al pin, la validità della configurazione, gli id con `:latest`, i modelli locali in gabbia, se il controllo delle prove gira e per quali modelli locali, il battito, bind e autenticazione del gateway, come il gateway è esposto (loopback, rete di casa, ogni interfaccia, un listener diretto sulla tailnet, Tailscale Serve o Funnel, con un errore quando un gateway pubblicato via Serve ascolta anche su un indirizzo di rete), le voci Google, il backend `agy` locale e la modalità di permesso data al suo plugin, i seggi ACP, gli agenti su abbonamenti che questa macchina non ha, quale lavoro in background sveglierebbe un modello locale, se il gateway osserva tutta la home in modo ricorsivo, se il gateway è in ascolto, se ogni agente ha una catena di riserva, un secondo gateway dal Microsoft Store, il gateway dell'app OpenClaw Companion (riferito, mai configurato), e quante skill e server OpenClaw ha creato che la libreria canonica non dichiara ancora.

Quando l'app è installata, una riga risponde alle due domande che una persona ha: se le impostazioni dell'app puntano al gateway di questo PC, e se la lista dei dispositivi del gateway conosce l'app. `nexgen openclaw doctor` legge quella lista con `openclaw devices list --json`, solo su quella riga e solo quando l'app dice di essere su questo gateway; un gateway fermo è uno stato normale, quindi in quel caso la riga non dice nulla sui dispositivi invece di segnalare un guasto.

`--nodes` aggiunge i nodi che il gateway in esecuzione conosce, il che costa un'esecuzione della CLI di OpenClaw e quindi non si fa mai se non richiesto; la riga sulla rete non ha bisogno di interrogazioni ed è sempre presente. Lo stesso flag legge le approvazioni di esecuzione di ogni nodo per ogni agente che esegue lì i suoi comandi, e avvisa quando quel PC è più severo di quanto l'agente chiede: un nodo il cui fallback è `deny` risponde all'agente con `exec denied: request-failed`, che non dice nulla sulla causa. Senza il flag non si chiede nulla di un nodo. `nexgen doctor` esegue gli stessi controlli come parte del suo report, senza `--nodes` e senza lanciare quella lista, e non dice nulla su una macchina che non ha mai installato OpenClaw, non ha mai dichiarato il modulo e non ha un gateway Companion.

`nexgen openclaw node --host <indirizzo> [--port 18789] [--name <etichetta>] [--apply] [--join-file <percorso>] [--dry-run] [--force]` stampa i comandi che rendono questa macchina un nodo di un gateway altrove: la rotta in un solo incolla di OpenClaw con un codice di join, e la rotta esplicita con le sue due approvazioni. L'indirizzo può essere un nome sulla rete di casa o un nome Tailscale MagicDNS; il secondo non richiede porta nella rotta in un solo incolla e richiede `--port 443 --tls` in quella esplicita. Senza `--apply` non scrive nulla e non esegue nulla, perché `openclaw node install` scrive già il servizio su ogni piattaforma.

`--apply` fa la parte della macchina in quella transizione, nell'unico ordine che funziona su un PC che aveva già un gateway suo. Ferma e rimuove il servizio gateway locale, archivia un drop-in systemd rimasto senza la sua unit, elimina il blocco `auth` del gateway, spegne i canali locali e attiva `nodeHost.workerRuns`. Tutti e tre sono residui che rompono un nodo in modi che nessuna riga del doctor nomina: il token nel file vince sul token di accoppiamento e il gateway centrale risponde `AUTH_TOKEN_MISMATCH`, due gateway che interrogano lo stesso bot si uccidono il poller a vicenda ogni trenta secondi, e un nodo senza worker runs non ospita sessioni. Ogni altra chiave della configurazione resta, e il file viene copiato da parte prima dell'unica scrittura. Poi si registra: con `--join-file` riscatta il link come servizio e aspetta la connessione; senza, esegue `openclaw node install` e `openclaw node start` e lascia le due approvazioni a `nexgen openclaw pair` sul gateway. `--dry-run` stampa lo stesso piano e non cambia nulla, `--force` sposta una macchina che è già nodo di un altro gateway. `install.sh --role node` e `install.ps1 -Role node` eseguono questo stesso ritiro prima di attaccarsi.

`nexgen openclaw node --use-app [--apply]` è la direzione opposta, per il PC Windows il cui nodo è l'app Companion: legge se il servizio nodo della CLI `OpenClaw Node` è installato e se il nodo dell'app è collegato, e con `--apply` rimuove il servizio con `openclaw node uninstall` e registra il ruolo `node`, ma solo quando il nodo dell'app è collegato. Mentre l'app ha la modalità nodo attiva e il suo nodo non è su, `--apply` rifiuta e stampa i tre passi nell'unico ordine che funziona. Non scrive mai sotto le cartelle dell'app e non avvia mai l'app; la sezione sull'app Companion dice cosa serve ancora all'app stessa.

`nexgen openclaw setup` applica quello stesso piano: installa la release fissata, fa l'onboarding quando non c'è il file, fonde le sezioni di proprietà con un backup accanto, scrive i manuali degli agenti, installa e avvia il servizio, dichiara il modulo ed esegue il doctor. Quello che il piano promette è quello che il setup scrive, perché entrambi vengono dalla stessa funzione pura. Su una macchina Windows finisce anche puntando l'app a questo gateway e offrendo la richiesta che l'app lascia, nel paragrafo che segue il riepilogo stampato dal doctor.

`nexgen openclaw adopt [--skills] [--mcp] [--dry-run] [--yes] [--auto] [--json]` prende quello che è stato creato dentro OpenClaw e lo rende canonico: le skill imparate dallo Skill Workshop, i server in `mcp.servers` che nessun manifest dichiara, e i plugin ClawHub che incapsulano server MCP, rilevati dai manifest dei plugin `openclaw.plugin.json`, `package.json`, dai `mcp.json`/`.mcp.json` inclusi, o da descrizioni di catalogo che corrispondono a servizi MCP ufficiali o ospitati. I server MCP sottostanti vengono estratti e proposti come server non gestiti, con le credenziali in `${VAR}` conservate come riferimenti. I plugin installati che non incapsulano MCP (canali, memoria, provider di modelli) restano fuori con una nota di una riga.

Stampa il piano con ogni controllo prima di scrivere qualsiasi cosa. Un nome già preso è un rifiuto e mai una sovrascrittura, una credenziale in chiaro blocca, e una skill che contiene uno script, un eseguibile o un file sconosciuto è una domanda: `--yes` adotta solo quello che i controlli hanno promosso e nomina quello che ha lasciato. Una skill adottata viene copiata in `03-INFRA/agent-universal-layer/skills/<nome>/`, dichiarata `origin: vault` con `openclaw_id` nel manifest delle skill, costruita in ogni vista e archiviata nel Workshop tramite `openclaw skills workshop archive`, così OpenClaw carica la copia canonica come ogni altra CLI. Un server adottato entra nel manifest MCP in modalità lazy, attraverso lo stesso scrittore che usa `nexgen mcp add`, marcato con `origin: openclaw` e il suo identificatore OpenClaw.

Le risorse canoniche senza un marchio di provenienza non vengono mai rimosse dall'adozione. Il marchio è diverso per manifest perché `origin` nel manifest delle skill significa già altro: lì nomina da dove vengono materializzati i byte (`engine`, `vault`, `github`, `installer`, `upstream`), e una skill adottata è una skill del Vault, dato che la copia vive nel Vault come tutte le altre. La sua provenienza OpenClaw è l'`openclaw_id` che porta, e il frontmatter del `SKILL.md` della skill registra la stessa coppia. Il manifest MCP non aveva quella chiave, quindi lì il marchio è `origin: openclaw`. In entrambi i casi, una voce senza marchio appartiene alla libreria e questo meccanismo non la tocca mai.

Con `--auto`, l'adozione gira in modo non interattivo: le risorse verificate come SAFE entrano subito nella libreria canonica, mentre tutto il resto, sia ciò che richiede una persona sia ciò che i controlli rifiutano, resta fuori e viene registrato nello stato locale della macchina (`openclaw-adopt-state.json`, nella cartella di stato del motore, mai nel Vault e mai nel repository) con il motivo, perché il plugin lo mostri. `--auto` tiene anche traccia delle assenze per i server MCP: un server adottato da OpenClaw e assente per due passaggi consecutivi viene ritirato dal manifest, elencato in `retired_servers` e smontato dalla sincronizzazione successiva. Un solo passaggio mancato, una configurazione rotta o un gateway irraggiungibile non rimuovono nulla e lo dicono, e un ritiro la cui scrittura fallisce viene riportato come fallimento, mai come uscita.

Le skill entrano con lo stesso passaggio e non vengono mai ritirate per assenza, perché lì l'assenza non significa nulla: una skill adottata è stata copiata nel Vault e il suo originale nel Workshop archiviato, che è esattamente ciò che fa l'adozione, quindi un'interrogazione del Workshop la troverebbe sparita e cancellerebbe la copia che la libreria aveva appena preso. Una skill lascia la libreria quando la sua voce nel manifest viene rimossa, che è una modifica della persona e non un effetto collaterale di un timer. Un server è diverso: `openclaw.json` è il file della persona, la sua tabella `mcp.servers` è il registro onesto di ciò che OpenClaw ha, e il motore non scrive mai quella tabella. Un nome già elencato sotto `retired_servers:` non viene riadottato; rimuovilo prima da quella lista, la stessa regola che enuncia `nexgen mcp add`, così riportare indietro un server resta un atto deciso.

Il plugin `nexgen` di OpenClaw registra `/adopt` per eseguire un passaggio dalla chat e mostrare cosa è in attesa, e il `doctor` stampa una riga sull'ultimo passaggio: cosa è entrato, cosa è uscito e cosa aspetta una persona.

Il passaggio parte da tre punti, tutti nel plugin che OpenClaw carica già: `/adopt` in chat, l'hook `skill_changed` (l'unico evento del ciclo di vita che OpenClaw annuncia per ciò che creano il Workshop e ClawHub), e l'orologio del gateway, un passaggio limitato ogni 30 minuti mentre il gateway è acceso, la stessa cadenza del ciclo di guardia del motore, armato su `gateway_start` e azzerato su `gateway_stop`, un passaggio alla volta. È quello che copre un server MCP rimosso: il catalogo degli hook di OpenClaw (`docs/plugins/hooks/reference.md` nella release installata) non ha un equivalente per `mcp.servers`, quindi il tick è il passaggio che se ne accorge. Un tick che non è riuscito ad avviare il motore scrive la frase nel log del gateway; un cambio di skill resta silenzioso, così l'avviso del Workshop si legge da solo.

`nexgen openclaw package [--json]` costruisce il tarball npm del plugin nella cartella di stato del motore e stampa il file e il suo sha256, la coppia con cui si verifica un'installazione su un altro gateway. Non installa nulla e non scrive altro; `--json` riporta lo stesso percorso e lo stesso hash con i file dentro.

`nexgen openclaw restart` e `nexgen openclaw pair` riportano su il gateway e lasciano entrare un telefono, o un'altra macchina; sono descritti sopra, con il resto dell'installazione. `nexgen openclaw stop` lo spegne e aspetta che la porta sia libera, che è ciò che serve a una modifica a mano di `openclaw.json`.

`nexgen openclaw rpc health [--json] [--url URL]` ispeziona lo stato operativo del gateway attraverso la chiamata WebSocket `health`, riportando uptime e plugin caricati senza cambiare nulla.

`nexgen openclaw groom [--dry-run]` compatta le chat degli agenti rimaste inattive o cresciute troppo.
