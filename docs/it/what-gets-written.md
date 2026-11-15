# Cosa viene scritto

🇬🇧 [English](../what-gets-written.md)

Ogni file che `install.sh`, `install.ps1`, `INIT.md` e il motore in profilo MULTI (`nexgen`, un tempo `agent-sync` e
`agent-doctor`, i cui nomi vecchi funzionano ancora come alias dei launcher) possono creare o modificare fuori da questo
repo. Niente qui tocca file fuori dalla tua home, e niente gira con privilegi elevati.

## `install.sh` / `install.ps1`

Sono entrambi gusci sottili di piattaforma che trovano Python 3.11+ e passano a
`03-INFRA/scripts/nexgen_core/bootstrap.py`, che fa il lavoro allo stesso modo su ogni sistema.

`install.sh --check` (`install.ps1 -Check` su Windows) è rigorosamente in sola lettura: una cartella di scaffold
mancante viene segnalata come controllo fallito e mai creata.

`install.sh` semplice, la modalità guidata predefinita, scrive due cose:

- Le cartelle di scaffold mancanti dentro il repo (`01-NOTES/`, `02-PROJECTS/`, `04-NOW/`, `99-INDEX/`, `99-SECRETS/`,
  ciascuna con un `.gitkeep`), e solo se al tuo clone mancano, cosa che su un clone completo normalmente non accade.
- I launcher dei comandi, `nexgen` più ogni alias storico (`agent-sync`, `agent-doctor`, `vault-push` e gli altri), in
  `~/.local/bin`. È l'unica cosa che lo script scrive fuori dal repo: piccoli wrapper che trovano Python e passano al
  `nexgen_core` di questo clone. Il ciclo di guardia tiene allineato lo stesso insieme di launcher nelle esecuzioni
  successive.

L'intervista guidata di quattro domande alla fine (quante CLI, quante macchine, dove vivono i servizi) dà solo
consigli; lo script non salva nessuna delle tue risposte. Niente qui richiede admin o root.

## `INIT.md` (l'installer guidato dall'AI, qualsiasi profilo)

- `99-INDEX/USER-PROFILE.md`: il tuo profilo, l'hardware, l'elenco di CLI e macchine e la scelta di architettura.
- Facoltativamente `04-NOW/current-focus.md` o una nota sotto `01-NOTES/` o `02-PROJECTS/`, se gli fai ingerire un
  documento (CV, brief di progetto, regole di brand) durante il setup.
- `03-INFRA/agent-universal-layer/skills/skills.manifest.yaml` nella tua radice dati, se non esiste affatto. INIT.md fa
  creare all'assistente copiando lo `skills.manifest.yaml.example` distribuito dalla stessa cartella, così dopo il
  setup esistono i comandi iniziali invece di stare solo nel README. È un passo che l'installer fa a mano, in entrambi i
  profili: il ciclo di guardia e apply non semina questo file, quindi un manifest mancante resta mancante finché
  qualcosa non lo copia dentro. Niente riscrive mai un manifest esistente, quindi svuotarlo a `skills: {}` è un'uscita
  definitiva.

## Bootstrap per CLI e configurazione MCP (MINIMAL: li scrive l'agente a mano; MULTI: `nexgen sync` e `nexgen guard`)

| CLI | File di bootstrap | Configurazione MCP | Cartella delle skill |
|---|---|---|---|
| Claude Code | `~/.claude/CLAUDE.md` (puntatore user-scope agli `AGENTS.md` di questo repo) | campo `mcpServers` in `~/.claude.json` | `~/.claude/skills/`, per ogni skill con `claude` nei `targets` |
| App Claude Desktop | nessuno: tiene le proprie preferenze e lo stato della finestra nello stesso file, e il motore non tocca quelle chiavi | `mcpServers` in `%APPDATA%\Claude\claude_desktop_config.json` (Windows), `~/Library/Application Support/Claude/` (macOS), `~/.config/Claude/` (Linux), solo i server autorizzati: il manifest deve dichiarare `desktop_allow: true`, il valore predefinito è il rifiuto. I nomi che il motore ha scritto sono elencati accanto in `claude_desktop_config.managed.json`, così revocare il flag toglie solo quelli | nessuna: l'app non ha una cartella di skill che il motore scrive |
| Codex | `~/.codex/AGENTS.md` | `~/.codex/config.toml` | `~/.codex/skills/` (la radice che `codex debug prompt-input` elenca, verificata su codex-cli 0.155.1) e il catalogo condiviso `~/.agents/skills/` che l'app Codex/ChatGPT legge, per ogni skill con `codex` nei `targets`. Si scrivono entrambe le viste: una versione della CLI che legge solo la prima resterebbe senza nessuna skill dell'utente |
| OpenCode v2 | `~/.config/opencode/AGENTS.md`, symlink agli `AGENTS.md` di questo repo (V2 accetta l'array `instructions` ma non lo risolve mai, quindi non viene più scritto; le voci aggiunte dal motore vengono rimosse una volta, quelle tue restano) | `mcp.servers` in `opencode.jsonc` o `opencode.json`, con il layout piatto V1 migrato una volta, più le chiavi native `plugins` e `permissions` (`plugin` e `permission` migrano una volta, le regole tue non vengono mai sovrascritte) | viste native in `~/.config/opencode/skills/` e catalogo condiviso `~/.agents/skills/`, per ogni skill con `opencode` nei `targets` |
| Antigravity | `~/.gemini/config/AGENTS.md` | `~/.gemini/config/mcp_config.json`\* | tre cartelle insieme, vedi sotto |
| OpenClaw | il blocco gestito negli `AGENTS.md` del workspace di ogni agente (`agents.entries.<id>.workspace`, altrimenti `agents.defaults.workspace`, altrimenti `<state dir>/workspace`); OpenClaw non ha un file puntatore proprio | `mcp.servers` in `~/.openclaw/openclaw.json`, oppure il file nominato da `OPENCLAW_CONFIG_PATH`, oppure `OPENCLAW_STATE_DIR/openclaw.json` | il catalogo condiviso `~/.agents/skills/`, che OpenClaw scopre da solo, per ogni skill con `openclaw` nei `targets` |

`exposure` non decide se una skill ottiene una vista. Una skill dichiarata viene collegata in ogni runtime nominato nei
suoi `targets`, qualunque sia la sua exposure; `lazy`, il valore predefinito, `eager`, `manual` e `core` cambiano solo
come la skill viene presentata e caricata, tramite `INDEX.md` e la lettura su richiesta.

Antigravity è l'unica CLI le cui viste delle skill si moltiplicano: una skill con target `antigravity` viene scritta in
tutte e tre `~/.gemini/antigravity-cli/skills/`, `~/.gemini/config/skills/` e `~/.gemini/skills/`, la radice storica,
perché modi diversi di avviare Antigravity sono stati visti leggere cartelle diverse.

Le sezioni MCP sono additive per impostazione predefinita: un server già nella configurazione di una CLI ma non nel
manifest viene conservato, perché può essere tuo. Un server che il motore gestisce conserva invece le chiavi che il
motore non scrive, cioè lo stato per server dell'app: l'interruttore `disabled`, le liste dei tool ammessi e negati, i
flag eager. Spegnere un server nella schermata di Antigravity, di Claude o di Codex sopravvive quindi al successivo
`nexgen sync`: vengono riscritti solo i campi di avvio (`command`, `args`, `env`, l'URL con i suoi header, i timeout).
Due tipi di nome escono comunque da ogni configurazione generata: un
nome esatto che il manifest elenca sotto `retired_servers`, una lapide esplicita, e un nome che questo motore ha
dichiarato in un sync precedente e che il manifest non dichiara più. Il motore ricorda i nomi dichiarati all'ultimo
render reale in `mcp-state.json`, nella directory di stato della macchina, così una rimozione arriva a ogni CLI anche
quando nessuno scrive una lapide. Un nome che il motore non ha mai dichiarato non viene mai toccato, e una prova in
corso o una voce in `orphans_allowlist` tiene vivo il suo nome. `nexgen doctor` segnala una voce rimasta del secondo
tipo come detrito del motore che il prossimo `nexgen sync` rimuove, mai come qualcosa da adottare. Le voci HTTP
autenticate di Antigravity usano `mcp-http-bridge.mjs`, di proprietà del motore; il JSON generato contiene solo il nome
della variabile d'ambiente del bearer, mai il suo valore. Un server HTTP autorizzato sull'app Claude Desktop passa dallo
stesso bridge, quindi anche `claude_desktop_config.json` contiene solo il nome di quella variabile.

\* `~/.gemini/config/mcp_config.json` è il file canonico che il renderer genera: è il percorso che la documentazione
MCP di Google indica sia per l'app Antigravity sia per la CLI `agy`. Lo stesso passo lo moltiplica, con symlink o con una
copia reale su Windows quando il privilegio di symlink manca, verso altri tre percorsi:
`~/.gemini/antigravity-cli/mcp_config.json`, `~/.gemini/antigravity-ide/mcp_config.json` e
`~/.gemini/antigravity/mcp_config.json`, la cartella in cui scrivevano le versioni precedenti. `nexgen doctor` li
controlla tutti, e una macchina ancora nel layout vecchio (il file reale in `~/.gemini/antigravity/`, `~/.gemini/config/`
un link a esso) viene migrata al render successivo: il contenuto viene salvato, ripubblicato nel percorso canonico, e la
vecchia cartella diventa una delle copie. Due file reali che non concordano non vengono mai sovrascritti: il render si
ferma e nomina entrambi i percorsi.

Sono modifiche a file che devono già esistere, perché ogni CLI crea la propria configurazione predefinita la prima
volta che la apri. Niente qui crea da zero la configurazione di una CLI; se non hai mai aperto una CLI scelta, quel
passo viene saltato.

### OpenClaw: tre regole che il suo schema non ti fa indovinare

OpenClaw è l'harness con l'interfaccia (pannello web, app per telefono) attraverso cui si usano gli altri harness,
quindi il suo file è suo: il motore scrive solo la tabella `mcp.servers` e lascia stare modelli, canali e impostazioni
del gateway. Tre regole del suo schema ti costano un connettore in silenzio, e il renderer le impone:

- L'URL viene validato prima che `${VAR}` sia sostituita. Un `${VAR}` rimasto nel campo `url` viene rifiutato, quindi
  l'URL viene scritto già risolto dalla variabile che il manifest nomina. Se quella variabile non è impostata, il
  server viene saltato come farebbe `require_env`.
- Un server http ha bisogno di `"transport": "streamable-http"`. Senza, l'handshake va in timeout e niente spiega
  perché.
- Un server MCP Python muore con `UnicodeEncodeError` su Windows appena un valore porta un accento o un'emoji. Ogni
  server il cui comando è un interprete Python (`python`, `python3`, `py`, o l'`.exe` di un virtualenv) riceve
  `PYTHONUTF8=1` e `PYTHONIOENCODING=utf-8` nel suo ambiente, su ogni piattaforma.

I timeout vengono convertiti dai secondi del manifest a `connectionTimeoutMs` e `requestTimeoutMs`, e una lista
`tools_deny` diventa `"toolFilter": {"exclude": [...]}`. I segreti restano riferimenti
(`"Authorization": "Bearer ${NAME}`), mai valori.

### Quanto del bootstrap legge ogni CLI

Ogni CLI che carica `AGENTS.md` all'avvio legge solo la prima parte del file e scarta il resto senza dirlo, quindi una
regola scritta oltre quel punto smette di esistere per quel runtime soltanto.

| CLI | File che legge | Limite, verificato il 14/11/2026 | Fonte |
|---|---|---|---|
| Antigravity | `~/.gemini/config/AGENTS.md` | 24.000 byte, oltre i quali il file viene troncato | https://antigravity.google/docs/rules/ |
| Codex | `~/.codex/AGENTS.md` | 32 KiB di default (`project_doc_max_bytes`) | https://learn.chatgpt.com/docs/agent-configuration/agents-md |
| DeepSeek harness | `~/.dsh/AGENTS.md` | 65.536 byte per catena di istruzioni | https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/context/agent-instructions/README.md |
| Claude Code | `~/.claude/CLAUDE.md` | 4 MiB, oltre i quali il file viene saltato del tutto | https://code.claude.com/docs/en/memory |

OpenCode non documenta nessun limite in byte per il suo `AGENTS.md` (https://opencode.ai/docs/instructions/), quindi per
lui non ne viene applicato nessuno. `nexgen doctor` misura il file che ogni CLI installata legge davvero, avvisa al 90%
del limite di quella CLI e segnala un allineamento rotto quando lo supera; una CLI che non è installata, o che non ha
ancora un file di istruzioni, non viene riportata. I numeri applicati stanno in `INSTRUCTION_READERS`, in
`03-INFRA/scripts/nexgen_core/checks/instruction_limits_checks.py`.

## Claude Code come plugin nativo (facoltativo, spento di default)

`plugins.claude.enabled: true` nella configurazione privata dell'installazione
(`03-INFRA/agent-universal-layer/sync/remotes.yaml`) sposta i tre artefatti di Claude Code in un'unica cartella di
plugin che il motore genera nella sua cartella di stato (`plugins/claude/`) e installa con i comandi di Claude Code
stesso: `claude plugin validate`, `claude plugin marketplace add <cartella> --scope user`, `claude plugin install
nexgen@nexgen-engine --scope user`. L'albero generato è un marketplace (`.claude-plugin/marketplace.json`) con dentro un
plugin: `.claude-plugin/plugin.json`, l'hook del guardrail in `hooks/hooks.json` che lancia lo stesso adattatore
fail-closed tramite `${CLAUDE_PLUGIN_ROOT}`, i server in `.mcp.json` e le skill con `claude` nei `targets`. I segreti non
finiscono lì: un `${VAR}` che una voce server leggerebbe dall'ambiente diventa un'opzione `userConfig` con
`"sensitive": true`, quindi Claude Code tiene il valore nel portachiavi di sistema. Le tre righe qui sopra per Claude
Code non vengono più scritte solo dopo che Claude Code ha accettato l'albero generato, mai solo perché l'interruttore è
acceso: il motore toglie il proprio hook da `~/.claude/settings.json`, i server che il plugin monta da `~/.claude.json`
e i propri collegamenti in `~/.claude/skills`, così niente è registrato due volte e un'installazione che continua a
fallire non lascia mai Claude Code senza guardrail. `claude plugin uninstall nexgen@nexgen-engine` lo rimuove, e
riportare l'interruttore a `false` fa tornare i writer classici.

## Solo profilo MULTI: scritture aggiuntive del ciclo di guardia (`nexgen sync`, `nexgen guard`, alias `agent-sync apply`, `agent-sync guard`)

- `~/.config/systemd/user/agent-sync.service` e `agent-sync.timer`, più `agent-heartbeat.service`,
  `agent-heartbeat.timer` e `agent-alert@.service`: timer ricorrenti a livello utente, solo su Linux con systemd.
  `agent-sync.timer` esegue `agent-sync guard` (pull, rigenerazione dei file di runtime delle CLI, healthcheck, nessun
  push) circa ogni 30 minuti. `agent-heartbeat.timer` esegue ogni ora un passaggio di liveness, controllo dipendenze e
  self-upgrade. `agent-alert@.service` parte solo su `OnFailure` dell'unit della guardia, per lanciare un allarme quando
  la guardia ricorrente stessa smette di girare. I nomi delle unit mantengono la grafia storica `agent-sync`,
  `agent-heartbeat` e `agent-alert` anche se il comando sotto è `nexgen`: rinominare una unit systemd su una macchina
  già installata è una migrazione a sé.
- Prima di sovrascrivere il contenuto di un file in cui si fonde (il puntatore alle istruzioni di una CLI, il suo file
  dei permessi o la registrazione di un hook guardrail), il ciclo copia accanto la versione precedente, con suffisso
  `.pre-instructions-<timestamp>.bak` per le fusioni del puntatore alle istruzioni e `.pre-permissions-<timestamp>.bak`
  per quelle di permessi e guardrail. I backup dei puntatori alle istruzioni non ruotano e si accumulano; quelli dei
  permessi tengono i tre più recenti, come le copie `<file>.bak-<timestamp>` che il renderer MCP scrive per
  `~/.claude.json`, la configurazione MCP di Antigravity, quella di OpenCode e `~/.codex/config.toml`. Una terza
  convenzione vale solo per le cartelle di libreria e di viste delle skill: se una cartella reale, non un symlink,
  occupa già il posto di una skill con contenuto diverso, una cartella tua fatta a mano o un residuo di un layout
  vecchio, viene rinominata di lato in `<name>.bak-<timestamp>` prima che il symlink gestito prenda il suo posto, su
  qualsiasi sistema, mai sovrascritta in silenzio.
- `~/.local/bin/nexgen-update` su Linux e macOS, oppure `~/.local/bin/nexgen-update.cmd` su Windows: il comando vero di
  aggiornamento, un launcher sottile generato come ogni altro nome di comando. Trova i dati tramite `AGENT_VAULT_DATA`
  o `KNOWLEDGE_VAULT_PATH`, oppure il solito checkout `~/KnowledgeVault`, poi scarica i tag pubblici delle release e non
  sposta il ramo installato finché non ha mostrato il piano e ricevuto conferma. In una topologia separata con un
  `99-INDEX/ENGINE-PIN.txt` esistente, la transazione confermata aggiorna e committa solo quel pin tramite `vault-push`
  prima del provisioning.
- `~/.nexgen-engine/`: la directory di stato locale della macchina, ridefinibile con `AGENT_STATE_DIR`, che contiene
  file che non si sincronizzano mai:
  - `agent-sync.lock`: il lock di transazione valido su tutto l'host, che porta PID, nome del comando e timestamp di chi
    lo tiene, in testo semplice, per diagnosticare un lock bloccato.
  - `agent-guard-liveness`: un timestamp che la guardia aggiorna a ogni esecuzione riuscita; il battito orario avvisa se
    invecchia.
  - `agent-healthcheck.state`: il registro JSON di debounce degli allarmi dietro ogni notifica, così lo stesso guasto
    non ti avvisa a ogni ciclo.
  - `third-party-upgrades.md`: un report delle dipendenze di terze parti pinnate (skill pinnate a GitHub, server MCP
    pinnati a npm) che si sono mosse a monte o hanno perso il supporto del publisher, più i componenti di terze parti
    che un modulo acceso dichiara di portare (`upstream:` nel catalogo dei moduli). Lo scrive il battito orario e non
    applica niente.
- `~/.claude/settings.json`, il file di impostazioni di Claude, modificato in modo additivo, e solo se il tuo Vault
  privato dichiara `agent-universal-layer/permissions/manifest.yaml`; senza quel file l'intero passo è un no-op, che è
  il caso predefinito. Una voce `hooks` registra `claude-guardrail-adapter.mjs` del motore
  (copiato in `~/.claude/` con il `nexgen-guardrail-core.mjs` condiviso) come hook `PreToolUse` su `Bash`, e copia il
  corpo del guardrail dichiarato in `~/.claude/nexgen-guardrail-hooks/<il nome che dichiara>`, qualunque nome gli dia
  il manifest privato, con un sidecar `~/.claude/nexgen-guardrail.config.json` che lo nomina. L'adapter esiste perché un
  corpo registrato direttamente fallisce aperto: Claude tratta ogni codice di uscita diverso da 2, e ogni timeout, come
  un errore non bloccante ed esegue il comando. L'adapter esegue il corpo in un sottoprocesso, trasforma ogni esito
  anomalo (crash, blocco, file mancante, risposta illeggibile) in una decisione esplicita (`ask`, oppure `deny` sotto
  `bypassPermissions`), ed esce con 2 se fallisce lui. Una registrazione scritta da una versione precedente, dove il
  corpo stesso era l'hook, viene migrata sul posto. La modalità di permesso **non** viene scritta:
  `permissions.defaultMode` e `skipDangerousModePermissionPrompt` appartengono a Claude e a chi sceglie la modalità
  nella sua schermata di impostazioni, e un motore precedente che li riasseriva a ogni ciclo poteva annullare quella
  scelta. Un blocco `posture` rimasto nel manifest privato viene ignorato, e `nexgen doctor` lo dice una volta.
- Hook guardrail di OpenCode, Antigravity e OpenClaw, dallo stesso manifest e nello stesso passo. Un hook guardrail può
  avere come target `opencode`, `antigravity` o `openclaw` oltre a, o al posto di, `claude`. Il contratto di hook nativo
  di ogni CLI è diverso da quello di Claude, quindi un adapter sottile del motore lo traduce da e verso la forma JSON su
  stdin e stdout che il corpo del guardrail già parla; il corpo resta lo stesso file privato del Vault per ogni CLI a cui
  punta. Tutti e quattro gli adapter condividono un core (`nexgen-guardrail-core.mjs`, distribuito accanto a ciascuno) e
  registrano ogni consultazione in `~/.local/state/guardrail/<cli>.json`, un conteggio e un orario, mai il comando. È
  quello che `nexgen doctor` legge per distinguere un guardrail che la CLI chiama davvero da uno solo registrato.
  - OpenCode: il guardrail è una cartella di plugin, `<opencode config dir>/plugins/nexgen-guardrail/`, che OpenCode
    carica da sola; nulla di essa viene scritto in `opencode.json` o `opencode.jsonc`. La cartella contiene l'adapter
    come `index.mjs` (la forma V2 `{ id, setup(ctx) }`), `nexgen-guardrail-core.mjs`, il corpo dichiarato sotto `hooks/`
    e un sidecar `nexgen-guardrail.config.json`. Il plugin veta un comando negato in
    `ctx.shell.hook("create.before")`, qualunque cosa dicano le regole dei permessi, e risponde alla richiesta di
    permesso in `ctx.permission.hook("evaluate")`. I motori precedenti registravano il file dell'adapter nell'array
    `plugins`; OpenCode 2.0.24 lo rifiuta ("configured plugin path must be a directory") e non chiama più l'hook V1
    `permission.ask`, quindi quel guardrail non veniva mai consultato. La prima esecuzione della versione nuova ritira
    quella registrazione e i file che aveva distribuito, per nome, e lascia stare gli altri plugin. L'unica regola di
    permesso che il motore scrive è `shell: ask`, ed è per il plugin: OpenCode consulta l'hook dei permessi solo per
    un'azione che sta per chiedere. Con quella regola il plugin risponde a ogni comando, `allow` per ciò che il corpo
    permette (quindi non c'è comunque richiesta) e `deny` per il resto, e il sidecar lo registra come `autoAllow` (con
    `strict`, che fa bloccare un guardrail rotto invece di chiedere). Se OpenCode non chiamasse il plugin, ogni comando
    di shell chiederebbe conferma in modo visibile invece di passare non controllato in silenzio. Un `shell: allow`
    lasciato da un motore precedente viene portato a `ask`; un `deny` esplicito sulla stessa coppia non viene mai
    toccato. Del resto dei permessi non viene scritto niente: ciò che fanno `edit` e le altre azioni è impostazione di
    OpenCode. L'oggetto `permission` storico migra una volta in regole equivalenti, così la stessa coppia ha una sola
    fonte di verità. Chi ha scelto `ask` continua a ricevere la richiesta: la regola resta `ask` e `autoAllow` resta
    spento, così il plugin si limita a bloccare un comando negato e la conferma arriva comunque. L'event sink di
    OpenCode non viene più installato, e nemmeno il codice che ripuliva una registrazione storica esiste più: una
    macchina che ne porta ancora una se la tiene.
  - Antigravity: `antigravity-guardrail-adapter.mjs` viene copiato in `~/.gemini/config/antigravity-guardrail-adapter.mjs`
    e registrato come voce `PreToolUse` sotto la chiave `nexgen-guardrail` in `~/.gemini/config/hooks.json`. Ogni altro
    nome di hook di primo livello in quel file, tuo o di un altro strumento, resta intatto. Il corpo dichiarato va in
    `~/.gemini/config/nexgen-guardrail-hooks/`, con lo stesso tipo di sidecar accanto all'adapter. `toolPermission` e
    `artifactReviewPolicy` sono impostazioni dell'app e non vengono scritte. Questo passo gira quando la CLI `agy` ha
    girato qui **oppure** quando ci ha girato l'app Antigravity o l'IDE: leggono tutti lo stesso `hooks.json` globale,
    quindi anche una macchina con l'app e senza CLI viene sorvegliata.
  - OpenClaw: una cartella di plugin nativa, `<openclaw state dir>/extensions/nexgen-guardrail/`, con l'adapter
    (`index.mjs`), `package.json`, `openclaw.plugin.json`, il core condiviso, il corpo dichiarato sotto `hooks/` e il
    sidecar. Il plugin risponde a `before_tool_call` per i tool che portano un comando di shell (`exec`, `process`,
    `terminal`, `code_execution`): una negazione blocca la chiamata, una domanda diventa una richiesta di approvazione.
    `openclaw.json` non viene mai toccato da questo passo, perché modificare `plugins.*` sotto un gateway in esecuzione
    lo rompe, quindi abilitare il plugin è `nexgen openclaw setup` con il gateway fermo, e `nexgen doctor` segnala
    "deposited but not enabled" fino ad allora. L'hook copre solo le chiamate che OpenClaw esegue lui; un agente sul
    runtime `claude-cli` o `codex` esegue i suoi tool dentro quella CLI, dove vale già il suo guardrail.
  - Codex: `codex-guardrail-adapter.mjs` viene copiato in `~/.codex/codex-guardrail-adapter.mjs` e registrato come voce
    `PreToolUse` con matcher `Bash` in `~/.codex/hooks.json`, accanto agli hook che hai scritto tu: il motore trova la
    propria voce dal comando e non tocca nessun altro gruppo, evento o file. Il corpo dichiarato va in
    `~/.codex/nexgen-guardrail-hooks/`, con lo stesso tipo di sidecar accanto all'adapter. Codex non esegue un hook
    finché una persona non accetta quella esatta definizione da `/hooks`: il motore scrive la registrazione e mai
    l'accettazione, quindi il primo ciclo stampa cosa devi confermare, e `nexgen doctor` segnala "registrato ma non
    accettato" (e "cambiato dopo l'accettazione") finché non lo fai. `approval_policy` e `sandbox_mode` sono
    impostazioni di Codex e non vengono scritte.
- Solo Windows, e solo se il tuo Vault privato contiene `03-INFRA/scripts/local-model-agent.ps1` (un adapter
  bring-your-own, mai parte del motore pubblico): quello script viene ricollegato, o copiato quando i privilegi di
  symlink mancano, in `~/.local/bin/local-model-agent.ps1`, accanto a due wrapper stabili,
  `~/.local/bin/local-worker.ps1` e `~/.local/bin/local-agent.ps1`.
- Avviso di aggiornamento nella shell e all'accesso, con un interruttore per spegnerlo. La guardia tiene un blocco
  gestito in `~/.bashrc`, più `~/.zshrc`, fish e i profili PowerShell dove quelle shell esistono (un profilo PowerShell
  viene scritto solo dove `pwsh` è installato o il profilo esiste già), e un controllo all'avvio: un timer utente
  systemd e l'autostart XDG su Linux, un'attività all'accesso su Windows.
  `nexgen tool update-notifier --install-shell-hook --remove` e `--install-autostart --remove` li rimuovono e registrano
  la scelta in `update-notifier-optout.txt` nella directory di stato della macchina, così il ciclo di guardia successivo
  non li rimette; il comando di installazione corrispondente toglie il divieto.
- I quattro percorsi di configurazione di Antigravity. `~/.gemini/config/mcp_config.json` è il file canonico;
  `~/.gemini/{antigravity,antigravity-cli,antigravity-ide}/mcp_config.json` sono link a esso, copie dove i link non si
  possono fare. Un file reale già presente in uno di quei tre percorsi viene prima salvato accanto a sé, e i server che
  contiene e che il file canonico non ha vengono portati dentro. Il renderer migra una volta il layout che le versioni
  precedenti scrivevano (il file reale in `~/.gemini/antigravity/`), con lo stesso salvataggio.
- Il wrapper di Playwright modifica la cache di npm. `playwright-human-safe.mjs` esegue `@playwright/mcp` alla versione
  esatta che pinna e applica le sue patch di sicurezza umana alla copia nella cache `_npx` di npm, tenendo l'originale
  accanto come `*.agent-human-file-chooser-patch-v1.original`. Quella copia è condivisa con chiunque altro esegua
  `npx @playwright/mcp@<stessa versione>` su questa macchina; `node playwright-human-safe.mjs --restore` rimette
  l'originale. Le patch sono cinque, ognuna legata a un difetto che si vede su un Chrome che una persona condivide con
  l'agente: a monte prende ogni selettore di file nativo, azzera il comportamento di download del browser mentre è
  agganciato, non porta mai in primo piano una scheda nuova (quindi le azioni del puntatore su di essa si bloccano su un
  browser con finestra), chiude il contesto che gli è stato passato quando l'ultimo client se ne va, e adotta una
  finestra di web app installata come scheda di riserva. Il wrapper passa anche `--no-webmcp`, così una pagina non può
  registrare tool per l'agente. `node playwright-human-safe.mjs --self-test` controlla che ogni patch si applichi ancora
  alla versione pinnata; una versione che non riesce a patchare viene rifiutata invece che patchata a metà.

### Chrome: volutamente non toccato

Un progetto precedente prevedeva di scrivere un launcher ombra `google-chrome.desktop` su Linux, e di riscrivere i file
`.desktop` delle web app di Chrome, così che il Chrome condiviso con il debug CDP vincesse sempre la corsa su chi apre
Chrome per primo. Era troppo invasivo, perché reindirizzava in silenzio i launcher di browser esistenti di una persona,
e non è mai stato distribuito. `nexgen tool chrome` (alias `agent-chrome`) avvia o riconnette solo un processo Chrome o
Chromium sulla porta di debug locale; non scrive niente sotto `~/.local/share/applications` e non tocca mai un file
`.desktop`.

### Windows: attività pianificata e wrapper nascosto

Su Windows ogni esecuzione di `nexgen sync` e `nexgen guard` (`install_scheduler` in `nexgen_core/scheduler.py`)
ripara da sola gli stessi trigger ricorrenti che systemd fornisce su Linux, usando solo il tuo account utente, senza
elevazione e senza servizio:

- Due wrapper VBS nascosti, `start-agent-sync-hidden.vbs` e `start-agent-heartbeat-hidden.vbs`, vengono scritti sotto
  `~/.local/state/`. Quel percorso è fisso per lo scheduler di Windows ed è separato dalla directory di stato
  `~/.nexgen-engine/`. Ognuno conserva i valori risolti di motore, dati del Vault, Vault e ramo, poi richiama lo shim
  installato `agent-sync.cmd` (modalità `guard` o `heartbeat`) tramite `cmd.exe /c`, con finestra nascosta, così nessuna
  console lampeggia a ogni ciclo.
- Le voci di Task Scheduler chiamate `KnowledgeVault Agent Sync` (ogni 30 minuti), `KnowledgeVault Agent Sync Logon`
  (all'accesso) e `KnowledgeVault Agent Heartbeat` (ogni ora) vengono create o aggiornate con `schtasks.exe`. Tutte e tre
  richiamano il loro wrapper VBS tramite `wscript.exe`.
- Se il trigger di accesso non si riesce a registrare (errore di `schtasks.exe`), una copia del wrapper di sync va nella
  cartella Esecuzione automatica (`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\KnowledgeVault Agent
  Sync.vbs`) così la guardia ricorrente gira comunque dopo l'accesso.

La politica dei remoti è dato privato, non derivato di runtime del motore. In un Vault MULTI può essere dichiarata in
`03-INFRA/agent-universal-layer/sync/remotes.yaml`, partendo dal pubblico `remotes.yaml.example`. Vedi
[sync-contract.md](sync-contract.md).

## `nexgen doctor`: cosa controlla, e l'unica vera chiamata di rete

`nexgen doctor` (alias `agent-doctor`) esegue per impostazione predefinita controlli in sola lettura sui file locali:
allineamento Git, validità del manifest MCP, se la configurazione generata di ogni CLI porta davvero ogni server atteso
dal manifest, materializzazione delle skill, allineamento dei puntatori alle istruzioni, igiene dei file di bootstrap
e se valori con forma di segreto sono finiti in file tracciati. `--fix` (`--remedy`) applica i rimedi automatici che
alcuni controlli portano. `--strict` cambia solo come un risultato *indeterminato* influisce sul codice di uscita, dove
indeterminato significa un controllo che non ha potuto concludere, per esempio una CLI mai avviata su questa macchina;
non attiva nessun controllo aggiuntivo.

L'unico controllo che raggiunge la rete è la sonda di raggiungibilità MCP. Per ogni connettore che il manifest marca
`tier: core` (per esempio Playwright, `vault-library` e le corsie OCR del Vault) la cui precondizione `require_env` è
soddisfatta, apre una connessione TCP grezza verso host e porta di quel connettore, con un timeout breve per connettore,
e riporta se qualcosa è in ascolto. È un semplice connect su socket, non una sessione CLI: non avvia nessun processo
`agy`, `opencode`, `claude` o `codex`, non consuma quota di modelli e gira a ogni invocazione, qualunque sia `--strict`.
Nessuna variabile d'ambiente lo salta.

Un connettore che su questa macchina non ha un indirizzo riceve l'altra metà della stessa domanda. Firecrawl è l'API
cloud del fornitore, quindi a decidere se esiste qui è la sua chiave: `nexgen doctor` riporta se `FIRECRAWL_API_KEY` è
presente, e legge solo la presenza, senza mai confrontare, stampare o scrivere il valore in un report, in un allarme o
in un log. Una macchina senza chiave riceve un avviso che nomina il comando per salvarla, non un guasto: rinunciare al
connettore è una scelta.

## `99-SECRETS/`

Solo locale. Gli agenti, seguendo il flusso in `99-SECRETS/README.md`, possono scrivere
`99-SECRETS/secrets-registry.md` (solo nomi e variabili d'ambiente, mai valori, tracciato in git). Il motore non scrive
mai valori di credenziali: restano nei keyring e negli archivi di ambiente di ogni macchina, fuori dal Vault. Niente nel
motore scrive nemmeno il registro; lo popola un agente o lo popoli tu a mano quando aggiungi un segreto.

## Cosa questo non fa mai

Niente sudo, niente modifiche fuori dalla tua home, niente telemetria e nessun push a un remoto git se non quando tu o
un agente eseguite esplicitamente un passo di pubblicazione. Ogni chiamata di rete raggiunge un servizio che hai messo
in piedi tu: la modalità Cloud-Server raggiunge solo la VPS a cui la punti, attraverso il tunnel SSH che hai
configurato, e l'altro caso è la sonda TCP del doctor descritta sopra.
