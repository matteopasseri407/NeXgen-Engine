# La squadra come dato (`squad.yaml`)

🇬🇧 [English](../squad.md)

NeXgen Engine legge la tua squadra di agenti da un file `squad.yaml` nel tuo Knowledge Vault privato.

## Scoperta e precedenza

Durante `nexgen openclaw setup`, o eseguendo l'installer con `--role server` o `--role standalone`, il motore cerca `squad.yaml` in questo ordine:

1. `<vault>/squad.yaml`
2. `<vault>/03-INFRA/agent-universal-layer/squad.yaml`
3. `<vault>/03-INFRA/squad.yaml`
4. `~/.config/nexgen/squad.yaml`
5. `~/.openclaw/squad.yaml`

Senza un file squadra, il motore mantiene l'organico standard (`main`, `capo`, `automazioni`, `sviluppo`). Se hai già cambiato il nome visualizzato di un agente in `openclaw.json`, il setup tiene il tuo nome e non lo sovrascrive mai con un valore predefinito.

Con un file squadra, i dati in `squad.yaml` vincono e vengono applicati a `openclaw.json` in modo sistematico.

Un modello di esempio è in `03-INFRA/agent-universal-layer/squad.example.yaml`.

## Riferimento dello schema

### `agents`

Una mappa da id di agente alla sua configurazione:

- `name`: nome visualizzato in OpenClaw.
- `model`: identificatore del modello, per esempio `anthropic/claude-opus-5-5` o `opencode-go/deepseek-v4.1-flash`.
- `harness`: selettore facoltativo di corsia, uno fra `claude`, `codex`, `opencode`, `agy`, `none`. Omesso o `none` tiene la corsia provider. Vedi [Su quale corsia gira un agente](#su-quale-corsia-gira-un-agente).
- `role`: a cosa serve l'agente.
- `workspace`: percorso personalizzato facoltativo per la cartella di lavoro dell'agente.
- `thinkingDefault`: livello di ragionamento facoltativo, per esempio `high`.
- `tool_surface`: facoltativo `lean`, che pubblica una superficie di strumenti minima sul modello di questo agente invece del catalogo intero del gateway. Spento se omesso. Vedi [`tool_surface: lean`](#tool_surface-lean).
- `context_tokens`: tetto facoltativo di contesto per chiamata, in token, scritto come `contextTokens` di quel modello. Spento se omesso. Vedi [`context_tokens: N`](#context_tokens-n).
- `soul`: prompt di personalità o di guida facoltativo. Una cartella di lavoro riceve un `SOUL.md` solo quando questo campo è presente. `soul_from` fa lo stesso a partire da un file, relativo al file squadra, per un testo più lungo che qualcuno può modificare. Gli agenti senza nessuno dei due non ricevono un `SOUL.md` predefinito.
- `agents_md`: percorso facoltativo, relativo al file squadra, delle istruzioni che questo agente riceve come `AGENTS.md` nella sua cartella di lavoro. Vedi [Istruzioni per ruolo](#agents_md-e-common_md).
- `default`: `true` facoltativo che nomina questo agente come predefinito dell'installazione. Vedi [`default`](#default).
- `exec`: politica di esecuzione facoltativa per questo agente, scritta in `agents.entries.<id>.tools.exec`. Vedi [`exec` e la politica degli strumenti](#exec-e-la-politica-degli-strumenti).
- `reportsTo`: id facoltativo dell'agente a cui questo risponde. Vedi [Chi risponde a chi](#reportsto).
- `tools`:
  - `agentToAgent`:
    - `send`: lista facoltativa di id di agenti destinatari che restringe a chi questo agente può scrivere. Il motore non scrive nessun `tools.agentToAgent.allow` globale: OpenClaw legge una lista assente come ogni coppia permessa, quindi un agente senza `send` può scrivere a tutti e ricevere da tutti. Un agente che questa esecuzione non crea resta fuori da una lista che la squadra dichiara, perché `--no-sviluppo`, un comando `opencode` mancante o una chiave Go mancante significano nessuna voce `sviluppo`, e un id che nessuna voce dichiara è un id che OpenClaw non sa instradare.
  - `exec`:
    - `host`: `"node"` o `"gateway"`.
    - `node`: nome della macchina satellite che esegue i comandi per questo agente.

### `reportsTo`

La gerarchia è dato, non una lista di nomi incollata nei manuali. Ogni agente può dichiarare a chi risponde:

```yaml
agents:
  main:
    name: "assistente"
  capo:
    name: "capo"
    reportsTo: "main"
  sviluppo:
    name: "sviluppo"
    reportsTo: "capo"
```

Senza il campo decide la forma dell'organico: `main` non risponde a nessuno, perché è quello che parla con la persona; `capo`, l'agente che supervisiona, risponde a `main`; ogni altro agente risponde a `capo` quando l'organico ne ha uno, altrimenti a `main`, e a nessuno quando non c'è nessuno dei due. Gli stessi valori predefiniti valgono per l'organico del motore quando non c'è un file squadra.

La risposta finisce nell'`AGENTS.md` di ogni cartella di lavoro come blocco delimitato a sé, intitolato `Organigramma` (o `Org chart`), che nomina il capo dell'agente, gli agenti che rispondono a lui e i suoi pari sotto lo stesso capo, più la regola che chiunque può scrivere a chiunque con `sessions_send`: un compito si delega in basso allo specialista che lo possiede, risultati e blocchi salgono al capo, e a un pari si chiede direttamente. Sia `nexgen openclaw setup` sia `nexgen sync` scrivono quel blocco quando manca o è diverso, quindi lo riceve anche un'installazione già esistente, e il corpo del manuale spedito si limita a rimandare a esso. Nulla fuori dal blocco viene toccato, e un file con i marcatori cancellati a metà viene segnalato e lasciato com'è.

Un `reportsTo` che nomina un agente che l'organico non dichiara, o che chiude un ciclo (`capo` verso `automazioni` mentre `automazioni` risponde a `capo`), viene segnalato nel piano e l'agente tiene il suo capo predefinito. Non viene scritto nulla che OpenClaw non sappia risolvere.

### `default`

L'agente che OpenClaw chiama agente di sistema: quello che una richiesta usa quando nessun binding ne nomina uno. Il setup lo scrive in `agents.defaults.systemAgent.agentId`.

```yaml
default: "main"
```

Oppure per agente, che funziona anche per una squadra copiata da un organico più vecchio:

```yaml
agents:
  main:
    name: "assistente"
    default: true
```

Il primo agente che porta il marchio vince quando `default:` manca. Un `default:` che nomina un agente che la squadra non dichiara non scrive nulla, e il piano nomina l'errore. Un proprietario già configurato e non nominato dalla squadra viene mantenuto.

Questo campo conta su una squadra con più di un agente esplicito. Senza un proprietario, OpenClaw non ha motivo di consegnare un messaggio senza binding al primo agente della lista: la sessione principale, Talk e il job cron del dreaming vengono rifiutati con "no explicit owner", che è quello che produce un organico con più `agents.entries` e nessun `agents.defaults.systemAgent.agentId`.

### `exec` e la politica degli strumenti

`exec` su un agente è la sua politica di esecuzione, scritta in `agents.entries.<id>.tools.exec`:

| Campo | Valori | Significato |
| --- | --- | --- |
| `security` | `deny`, `allowlist`, `full` | Se un comando deve corrispondere alla allowlist. |
| `ask` | `off`, `on-miss`, `always` | Quando viene chiesto a una persona di approvare un comando. |
| `host` | `gateway`, `node` | Dove gira il comando. |
| `node` | un nome di nodo | La macchina satellite che lo esegue. |

Un blocco `tools.exec` più vecchio funziona ancora: i due vengono fusi, e `exec` vince chiave per chiave.

Perché il campo esiste. Con il backend `claude-cli`, `security: "allowlist"` più `ask: "on-miss"` tratta ogni strumento nativo di Claude, `ToolSearch` compreso, e ogni strumento MCP esterno come un miss: OpenClaw chiede a una persona di approvare ogni chiamata e la nega quando nessuno risponde. OpenClaw risolve quelle approvazioni native in `resolveCliNativeToolApprovalPlan`, che restituisce `allow` solo per `security: "full"` con `ask: "off"`. Un agente eseguito da Claude Code ha bisogno di quella coppia; un agente che esegue il gateway stesso va bene con la allowlist.

Il plugin guardrail del motore controlla ogni chiamata a uno strumento su `before_tool_call` prima che venga eseguita, quindi `full` non lascia la macchina senza protezione: i comandi catastrofici si fermano comunque lì.

```yaml
agents:
  sviluppo:
    name: "sviluppo"
    model: "anthropic/claude-opus-5-5"
    exec:
      security: "full"
      ask: "off"
```

### `agents_md` e `common_md`

Ogni agente può portare le sue istruzioni dalla squadra, e una sezione può essere condivisa da tutti:

```yaml
common_md: "squad/common.md"
agents:
  capo:
    agents_md: "squad/capo.md"
```

Entrambi i percorsi sono relativi al file squadra, quindi i due viaggiano insieme. Il setup scrive il testo del ruolo e poi la sezione comune nell'`AGENTS.md` della cartella di lavoro dell'agente, il file che OpenClaw legge all'inizio di ogni sessione, e `nexgen sync` lo riscrive: modificare la squadra ed eseguire la sincronizzazione basta, perché è il comando che una persona esegue senza pensarci. Un agente con il solo `common_md` riceve quella sezione da sola; un agente senza nessuno dei due tiene il manuale che il motore spedisce.

Quel file comincia con il marchio del motore:

```html
<!-- nexgen:squad:begin: written from squad.yaml by 'nexgen openclaw setup', and rewritten on the next run -->
```

Un file che porta il marchio è stato scritto dal motore, quindi l'esecuzione successiva lo sostituisce e conserva quello che sta sopra, dove `nexgen sync` mette il blocco che punta alle istruzioni canoniche. Un file è del motore anche quando è esattamente il manuale che questo motore spedisce per quell'agente, con o senza quel blocco sopra: è una cartella preparata prima che la squadra dichiarasse le istruzioni, e trattarla come della persona congelerebbe il vecchio testo lì per sempre. Ogni altro file appartiene alla persona: viene lasciato byte per byte, e l'esecuzione dice di averlo saltato. Una cartella contiene un solo `AGENTS.md`, quindi due agenti che condividono una cartella di lavoro e dichiarano manuali diversi vengono segnalati invece che indovinati. `SOUL.md` resta separato e viene scritto solo per gli agenti che dichiarano `soul`.

### Delega di esecuzione ai nodi

Le macchine che si collegano con il ruolo `node` fanno da host di esecuzione senza interfaccia. Gli agenti sui nodi non vengono registrati dalla macchina satellite via RPC; sono tutti dichiarati nel `squad.yaml` centrale, valutato sul server gateway:

```yaml
agents:
  builder:
    name: "Build Node"
    model: "anthropic/claude-opus-5-5"
    tools:
      exec:
        host: "node"
        node: "desktop-linux"
```

`node: "desktop-linux"` è la stessa cosa scritta in una riga: il motore scrive `tools.exec` con `host: "node"` e il nome, e ogni comando di quell'agente gira su quella macchina. Un blocco `tools.exec` che l'agente dichiara per conto suo vince sulla scorciatoia.

Quell'agente riceve anche un manuale diverso dagli altri: è il telecomando e il quadro di controllo della macchina, guida le harness locali con `nexgen-local sup` (Claude, Codex, agy, OpenCode e le altre, un adattatore per harness), entra in una sessione già aperta, e dice subito se la macchina è spenta invece di fingere di aver agito. L'organigramma scritto in ogni workspace nomina la macchina che quell'agente controlla, così l'assistente sa a chi passare il lavoro di quel PC.

### `models`

Dizionario facoltativo di voci di modello e specifiche di provider personalizzati. Ogni chiave è un riferimento `provider/model` e il suo valore è la voce di quel modello in `agents.defaults.models` (vedi [Scelta del modello per agente](#scelta-del-modello-per-agente)). Non finisce mai nell'oggetto `models` alla radice di `openclaw.json`: quello è rigido e accetta solo `mode`, `providers` e `catalogRefresh`.

## Scelta del modello per agente

`model` su un agente è solo il predefinito di quell'agente, il modello su cui parte una sessione nuova. Ogni modello che la macchina può davvero eseguire compare nel selettore di ogni agente, quindi il modello si può cambiare per sessione dal Control UI, dall'app del telefono, da Telegram o con `/model` senza modificare `squad.yaml`.

`nexgen openclaw setup` scrive `agents.defaults.modelPolicy.allow` con un carattere jolly per ogni provider raggiungibile: `anthropic/*` quando Claude Code è loggato, `openai/*` quando lo è Codex, `opencode-go/*` con la chiave flat di OpenCode Go, `ollama/*` e `llama-cpp/*` per i modelli che questa macchina serve da sé, e `google-antigravity-cli/*` quando il plugin del backend locale `agy` è permesso. Un carattere jolly invece di una lista di id, così un modello che un provider pubblica domani è selezionabile senza un altro setup.

Un runtime di CLI è un pin sul modello, mai un provider in quella lista: un login Claude dà `anthropic/*` più `agents.defaults.models["anthropic/*"] = {"agentRuntime": {"id": "claude-cli"}}`, la forma verso cui `doctor --fix` di OpenClaw migra una voce `claude-cli/*` legacy.

Lo stesso setup nomina il modello dell'assistente come primario di sistema in `agents.defaults.model`, a meno che il file ne dichiari già uno: con la chiave assente OpenClaw sceglie da sé un primario, e su una macchina con un modello locale sceglie quello.

La squadra aggiunge quello che nomina: ogni `model` su un agente e ogni chiave della sua tabella `models` diventano una voce in `agents.defaults.models`, e il loro provider un carattere jolly. È anche per questo che un agente si può spostare su un modello che la squadra non nomina mai.

Due regole proteggono una scelta già fatta. Una politica già in vigore viene estesa, mai sostituita: quello che il proprietario ha scritto resta primo e resta lì, e un provider che la configurazione nomina già resta selezionabile. Il `model` di nessun agente viene cambiato da tutto questo.

## Su quale corsia gira un agente

`harness` su un agente decide che cosa esegue davvero il suo turno. Un agente senza la chiave tiene la corsia che ha oggi, quindi nessun setup sposta un agente esistente da nessuna parte.

| `harness` | La corsia | Che cosa esegue il turno | Quanto costa |
| --- | --- | --- | --- |
| assente, oppure `none` | provider | OpenClaw stesso, che chiama l'API del modello di cui ha la chiave (`opencode-go/*`, un modello locale, ...) | un turno con il prompt di OpenClaw, sul piano flat o sulla chiave |
| `opencode`, `claude`, `codex` | sessione ACP | la CLI vera, come agente a sé, nella cartella di quell'agente, con il suo login | il contesto dell'harness, sul piano dell'harness. OpenClaw tiene solo canali, consegna e politica |
| `agy` | backend CLI | la CLI ufficiale `agy`, attraverso il plugin `google-antigravity-cli` | il prompt di OpenClaw più la superficie degli strumenti loopback |

La corsia ACP è quella che cambia il conto: un turno porta il prompt e le skill dell'harness invece di quelli di OpenClaw, ed è la differenza fra qualche migliaio di token di contesto e i 78.717 token misurati per un turno `claude-cli` su questo motore (`docs/proposals/daru-burn.md`).

Due cose sono fisse, non configurabili:

- `agy` non diventa mai una sessione ACP. È un backend CLI sul client di Google; ACP per Antigravity richiederebbe il server proprietario separato di Google, che porta un piano Google fuori dai client di Google. Il plugin si richiede con un modello che nomina `google-antigravity-cli/...`, che è una decisione diversa da questa chiave.
- Una corsia ACP si scrive come **due metà, o nessuna**: `agents.entries.<id>.runtime` e una voce in `bindings` di `type: "acp"`. Un blocco runtime senza una conversazione legata risponde a una chat diretta con il modello predefinito invece che con l'harness.

Un agente ACP ha bisogno di un binding di canale che gli instradi una conversazione. Il setup scrive allora un blocco runtime e un binding per quella conversazione:

```yaml
agents:
  sviluppo:
    harness: "opencode"
bindings:
  - match:
      channel: "telegram"
    agentId: "sviluppo"
```

```json5
{
  agents: { entries: { sviluppo: { runtime: { type: "acp", acp: {
    agent: "opencode", backend: "acpx", mode: "persistent", cwd: "<cartella di quell'agente>",
  } } } } },
  bindings: [{ type: "acp", agentId: "sviluppo", match: { channel: "telegram" },
    acp: { mode: "persistent", cwd: "<cartella di quell'agente>", label: "sviluppo-opencode" } }],
}
```

`nexgen openclaw plan` nomina i casi che non può scrivere, e lascia quell'agente sulla corsia che ha già: un valore che non è uno dei cinque qui sopra, un harness non installato su questa macchina, un agente a cui nessun binding instrada una conversazione, un agente senza cartella di lavoro.

Su una corsia ACP il `model` dell'agente viene passato all'harness come sua scelta di modello, quindi deve nominare un modello che quell'harness può eseguire, non uno di cui OpenClaw tiene la chiave. Il controllo del modello su ACP dipende dall'adattatore: se l'adattatore non accetta una scelta, l'harness tiene il suo predefinito e il `model` della voce resta indicativo.

Il setup non rimuove mai quei blocchi. Il merge conserva ogni chiave che può essere tua, quindi togliere `harness:` dalla squadra non riporta indietro l'agente: per riportarlo sulla corsia provider, togli il blocco `runtime` della voce e il suo binding `type: "acp"` in `openclaw.json`.

### `bindings`

Lista di binding di canale che instradano i messaggi in arrivo su un canale di comunicazione a un agente specifico:

```yaml
bindings:
  - match:
      channel: "telegram"
    agentId: "main"
```

Un binding porta anche il plugin incluso in quel canale dentro `plugins.allow` (`telegram` per l'esempio qui sopra), la voce che OpenClaw aggiunge da sé la prima volta che un canale viene configurato: un canale il cui plugin non è permesso non instrada nulla. Un canale che nessun plugin incluso serve resta al plugin che la persona ha installato e permesso.

## Leve di consumo, tutte a scelta

Tre impostazioni limitano quanto può spendere un turno. Tutte e tre sono spente finché non le scrivi: un agente che non ne dichiara nessuna tiene la superficie, il contesto e il comportamento che ha oggi. I numeri qui sotto vengono da `docs/proposals/daru-burn.md`, dove ognuno dice se è misurato o stimato, e la base è il turno `capo` misurato su questo motore: 74 chiamate al modello, circa 78.000 token di prompt in ogni chiamata, circa 5,77 M di token per turno.

| Chiave | Dove | Che cosa scrive | Token per turno, prima | Token per turno, dopo | Consigliato |
| --- | --- | --- | --- | --- | --- |
| `tool_surface: lean` | su un agente | `tools.byProvider["<modello di quell'agente>"] = { profile: "minimal", alsoAllow: [...] }` | 78.000 per chiamata, 5,77 M per turno | ~63.000 per chiamata, ~4,66 M per turno (-19%) | sì, per un agente su backend CLI |
| `context_tokens: N` | su un agente | `models.providers.<provider>.models[].contextTokens` | nessun tetto per chiamata: il caso peggiore è la finestra del modello, quindi 74 M per turno a 1 M | stessa mediana per turno, ma il caso peggiore è 74 x N: 14,8 M per turno a 200.000 | `200000`, poi misura |
| `acp_admission: true` | alla radice | `governor.acp.agents` nel file di fianco al plugin | un turno iniziato su un piano esaurito gira finché non fallisce a metà, pagando le chiamate fatte | 0 chiamate al modello, 0 token: una riga che nomina piano e finestra, prima che l'harness parta | sì, quando esiste una corsia `harness:` |

Chiamate per turno: nessuna delle tre le cambia. `tool_surface` e `context_tokens` rendono più economica ogni chiamata e limitano il totale; `acp_admission` toglie le chiamate di un turno che sarebbe comunque morto. Il numero di chiamate è l'unica leva che l'OpenClaw fissato non espone, e `docs/proposals/daru-burn.md` la descrive invece di far finta del contrario.

### `tool_surface: lean`

```yaml
agents:
  capo:
    model: "anthropic/claude-opus-5-5"
    tool_surface: "lean"
```

Su una corsia backend CLI il catalogo di strumenti del gateway arriva all'harness in modo anticipato, quindi 51 schemi viaggiano in ogni chiamata al modello: erano 112.239 dei circa 264.000 caratteri di prompt del turno misurato, circa 33.000 dei suoi 78.000 token. `profile: minimal` pubblica la lista corta di OpenClaw, e `alsoAllow` rimette quello che l'agente usa davvero: shell, file, web, gli strumenti di sessione, la memoria, gli strumenti di supervisione per l'agente che supervisiona, e i quattro meta-strumenti `lazy-mcp`.

I connettori restano raggiungibili: il deny sul loopback toglie già i loro schemi, e il gateway `lazy-mcp` è l'unica strada lasciata aperta, quindi Vault, GitHub, Vercel e il resto si chiamano con `lazy-mcp__lazy_call` come prima. Nulla della lista di capacità dell'agente viene tolto, e il motore non scrive nessuna lista `allow` (OpenClaw rifiuta `allow` accanto a `alsoAllow` nello stesso ambito; questa voce usa solo `alsoAllow`).

La chiave è il modello, non l'agente, perché è questo che significa `tools.byProvider`: ogni agente che gira su quel modello riceve la superficie ridotta. Se non è quello che vuoi, dai all'agente un modello suo.

### `context_tokens: N`

```yaml
agents:
  capo:
    model: "anthropic/claude-opus-5-5"
    context_tokens: 200000
```

Il tetto è l'input attivo di una chiamata al modello. Sulla corsia `claude-cli` diventa la finestra di auto-compattazione di Claude Code, quindi una conversazione viene riassunta al tetto invece di crescere verso un caso peggiore da 1.000.000 di token: 74 chiamate contro un tetto di 200.000 sono 14,8 M di token, contro 74 M alla finestra nativa. Lo scambio è reale ed è tuo: i turni vecchi diventano un riassunto prima.

Viene scritto solo `contextTokens`, mai `contextWindow`: il tetto è una decisione, la finestra nativa è del modello. Quando due agenti nominano lo stesso modello con numeri diversi, vince il più piccolo.

**Sperimentale, e perché.** È l'unica leva che scrive un array `models` per un provider che appartiene a un plugin incluso (qui `anthropic`). La release fissata documenta la fusione: una voce che scrivi in `models.providers.<id>.models` tiene le sue righe, e il catalogo del plugin smette di contribuire solo quando un `baseURL` a livello di provider punta fuori dagli endpoint dichiarati dal plugin, cosa che questo motore non scrive mai (documentazione di `openclaw models list` nella release fissata). Quindi la via di autenticazione dovrebbe sopravvivere. Dovrebbe non è lo stesso di dimostrato sulla tua macchina: il motore non ha osservato un turno vero sulla tua corsia, quindi verifica una volta. Esegui `openclaw config validate`, poi un turno vero, poi `openclaw models list --provider anthropic` e controlla che la voce porti ancora la sua autenticazione. Se la lista dei modelli cambia forma, togli la chiave: l'agente continua a funzionare e smette di compattare prima.

### `acp_admission: true`

```yaml
acp_admission: true
agents:
  sviluppo:
    harness: "opencode"
    model: "opencode-go/deepseek-v4.1-flash"
```

Una sessione ACP è l'harness che gira come se stesso, quindi le catene di fallback di OpenClaw non vengono mai consultate per lei: senza questo ramo la corsia con più contesto per turno è quella senza nessun cancello. Con questo ramo il plugin legge `nexgen-quota-state.json` (le letture che il battito orario scrive già) prima di un turno, e quando il piano che il modello dell'agente nomina è alla riga `block_at` della tua politica o sopra, risponde con una riga che nomina il piano e la finestra invece di far partire un turno dell'harness.

Non costa nessuna chiamata al modello: la risposta è testo del plugin. Non succede nulla quando lo stato manca, è più vecchio di quanto la politica permette, non ha una lettura per quel piano, o legge sotto la riga; e il ramo viene scritto solo quando `acp_admission: true` è nella squadra, quindi una macchina che non l'ha mai chiesto non rifiuta mai un turno.

## Autenticazione sicura dei modelli

Mentre elabora gli agenti della squadra, il setup ispeziona il provider configurato per ognuno. Se la chiave API corrispondente è nel tuo ambiente (`${VAR}` o una variabile d'ambiente), il motore la registra nel deposito delle credenziali di OpenClaw:

```bash
printf "%s\n" "$KEY" | openclaw models auth paste-api-key --agent <id> --provider <provider>
```

La chiave viaggia sullo standard input e non viene mai registrata nei log, stampata in console o scritta in chiaro in un file. Un agente il cui modello gira su un backend CLI locale (`claude-cli`, `codex`, `agy`) non riceve nessuna chiave registrata: quel login resta nella CLI che ce l'ha già, e il setup lo dice una volta per agente.

## Ambiente del gateway

Il setup rigenera `$OPENCLAW_STATE_DIR/.env` (permessi 0600) dai valori in vigore e non li stampa mai. Lo alimentano due sorgenti:

- la configurazione che il setup sta scrivendo, compresa la chiave del provider che serve al modello di ogni agente;
- i server MCP già montati in `openclaw.json`, letti dal file in vigore: un server stdio cita variabili nei suoi argomenti e nel suo blocco `env`, script del bridge compresi. La tabella dei server montati la scrive `nexgen mcp render`, non il setup.

Il file viene rigenerato a ogni esecuzione, così un token ruotato nel deposito dei segreti del motore non resta vivo qui al suo vecchio valore. Le chiavi che il motore non referenzia sono tue e restano intatte, con un'eccezione: una chiave che nessun agente `claude-cli` legge viene tolta, perché rompe il catalogo dei modelli.
