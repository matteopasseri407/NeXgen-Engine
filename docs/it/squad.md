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
- `role`: a cosa serve l'agente.
- `workspace`: percorso personalizzato facoltativo per la cartella di lavoro dell'agente.
- `thinkingDefault`: livello di ragionamento facoltativo, per esempio `high`.
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

### `models`

Dizionario facoltativo di voci di modello e specifiche di provider personalizzati. Ogni chiave è un riferimento `provider/model` e il suo valore è la voce di quel modello in `agents.defaults.models` (vedi [Scelta del modello per agente](#scelta-del-modello-per-agente)). Non finisce mai nell'oggetto `models` alla radice di `openclaw.json`: quello è rigido e accetta solo `mode`, `providers` e `catalogRefresh`.

## Scelta del modello per agente

`model` su un agente è solo il predefinito di quell'agente, il modello su cui parte una sessione nuova. Ogni modello che la macchina può davvero eseguire compare nel selettore di ogni agente, quindi il modello si può cambiare per sessione dal Control UI, dall'app del telefono, da Telegram o con `/model` senza modificare `squad.yaml`.

`nexgen openclaw setup` scrive `agents.defaults.modelPolicy.allow` con un carattere jolly per ogni provider raggiungibile: `anthropic/*` quando Claude Code è loggato, `openai/*` quando lo è Codex, `opencode-go/*` con la chiave flat di OpenCode Go, `ollama/*` e `llama-cpp/*` per i modelli che questa macchina serve da sé, e `google-antigravity-cli/*` quando il plugin del backend locale `agy` è permesso. Un carattere jolly invece di una lista di id, così un modello che un provider pubblica domani è selezionabile senza un altro setup.

Un runtime di CLI è un pin sul modello, mai un provider in quella lista: un login Claude dà `anthropic/*` più `agents.defaults.models["anthropic/*"] = {"agentRuntime": {"id": "claude-cli"}}`, la forma verso cui `doctor --fix` di OpenClaw migra una voce `claude-cli/*` legacy.

Lo stesso setup nomina il modello dell'assistente come primario di sistema in `agents.defaults.model`, a meno che il file ne dichiari già uno: con la chiave assente OpenClaw sceglie da sé un primario, e su una macchina con un modello locale sceglie quello.

La squadra aggiunge quello che nomina: ogni `model` su un agente e ogni chiave della sua tabella `models` diventano una voce in `agents.defaults.models`, e il loro provider un carattere jolly. È anche per questo che un agente si può spostare su un modello che la squadra non nomina mai.

Due regole proteggono una scelta già fatta. Una politica già in vigore viene estesa, mai sostituita: quello che il proprietario ha scritto resta primo e resta lì, e un provider che la configurazione nomina già resta selezionabile. Il `model` di nessun agente viene cambiato da tutto questo.

### `bindings`

Lista di binding di canale che instradano i messaggi in arrivo su un canale di comunicazione a un agente specifico:

```yaml
bindings:
  - match:
      channel: "telegram"
    agentId: "main"
```

Un binding porta anche il plugin incluso in quel canale dentro `plugins.allow` (`telegram` per l'esempio qui sopra), la voce che OpenClaw aggiunge da sé la prima volta che un canale viene configurato: un canale il cui plugin non è permesso non instrada nulla. Un canale che nessun plugin incluso serve resta al plugin che la persona ha installato e permesso.

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
