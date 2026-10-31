# Il proxy MCP pigro (`lazy-mcp`)

🇬🇧 [English](../lazy-mcp.md)

I server che il manifest marca `exposure: lazy` non vengono montati nelle CLI. Davanti a loro sta un unico server
piccolo e sempre attivo, `lazy-mcp`: elenca i loro tool, carica la definizione completa di un tool quando il modello la
chiede e inoltra la chiamata. La CLI paga quattro meta-tool invece degli schemi di ogni server.

## Chi riceve cosa

Dove vive un server è una regola sola (`nexgen_core/mcp_placement.py`), interrogata dal renderer, dal gateway e da
`nexgen mcp plan` allo stesso modo. Per ogni CLI un server è montato `direct`, servito dal gateway, oppure assente.
Dichiaralo una volta:

```yaml
servers:
  vault-library: { exposure: eager, ... }   # montato direttamente in ogni CLI elencata in targets
  github:        { exposure: lazy,  ... }   # dietro il gateway in ogni CLI elencata in targets
```

`targets` è sempre la lista consentita di CLI, e `enabled: false` spegne sempre un server. Un manifest senza `exposure`
continua a funzionare con le manopole più vecchie (`tier`, `lazy`, `lazy_targets`). La copia del gateway di ogni CLI
sa quale CLI è (`LAZY_MCP_CLI`, scritta dal renderer) e serve solo ciò che il piano le instrada dietro: un server
montato direttamente in una CLI non sta anche nel gateway di quella CLI, e uno limitato a due CLI non viene offerto
alle altre due. Un gateway a cui la CLI non è mai stata detta, perché la sua configurazione è precedente, serve tutti i
server pigri finché il ciclo di guardia successivo non la riscrive.

```bash
nexgen mcp plan     # la tabella: server x CLI, direct / gateway / -, e perché; cosa è cambiato per il gateway; cosa è incoerente
nexgen mcp check    # avvia il gateway di ogni CLI come farebbe quella CLI, elenca ciò che serve, nomina i backend che non partono
```

`nexgen doctor` segnala staticamente un piano incoerente (`mcp.placement`). `check` è la metà dal vivo e avvia i
backend, quindi è un comando che esegui tu, non parte di `doctor`.

## OpenClaw: il gateway serve gli agenti delle CLI

OpenClaw monta i connettori del manifest direttamente in `mcp.servers` e, accanto, il gateway `lazy-mcp` del motore.
Lì il gateway non sta davanti ai connettori: serve esattamente ciò che il file monta direttamente, quindi un connettore
è raggiungibile in entrambi i modi.

È montato per gli agenti su `claude-cli`. OpenClaw gli passa un server MCP proprio, marcato `alwaysLoad: true`, che
porta tutti i loro tool, connettori compresi: 943k token misurati per una richiesta. Il motore aggiunge `<server>__*`
al `tools.deny` di quegli agenti, e il gateway è la strada che resta. OpenClaw avvia Claude Code con
`--strict-mcp-config --mcp-config <il proprio file>`, quindi `~/.claude.json` non viene mai letto; `lazy-mcp` è una
voce normale in `mcp.servers`, quindi la Tool Search di OpenClaw lo rimanda ancora, e non finisce mai nella lista dei
negati.

Cosa ferma i figli del gateway: una CLI che se ne va chiude l'input del gateway, e il gateway e i suoi backend escono
con lei (testato). Un gateway ucciso di netto, dall'OOM killer o con `kill -9`, non può ripulire dopo di sé e lascia i
backend in esecuzione finché non li fermi a mano.

## Aggiungere un server, e provarne uno prima

`nexgen mcp add` mette un server nuovo dietro il gateway, per ogni CLI. È il comportamento predefinito, e il manifest è
ciò che ogni macchina sincronizza. `--eager` è l'eccezione esplicita, per i pochi server che devono essere sempre
montati; ognuno costa a ogni CLI che carica i tool all'avvio la sua definizione completa, e `nexgen mcp check --direct`
stampa quanto.

Per guardare un server senza impegnarlo:

```bash
nexgen mcp try spike --command npx --args=-y --args=some-mcp@1.2.3 --for 4   # solo questa macchina, 4 ore
nexgen mcp trials                                                            # cosa è in prova e quanto resta
nexgen mcp promote spike                                                     # tienilo: scritto nel manifest, si sincronizza
nexgen mcp drop spike                                                        # oppure chiudila subito
```

Una prova vive nella directory di stato di questa macchina, mai nel Vault, quindi non si sincronizza. Il gateway la
serve come qualsiasi server pigro, quindi non arriva mai nella configurazione di una CLI, e smette di essere offerta
quando scade il suo tempo (24 ore se non dici altro, una settimana al massimo) senza niente da riavviare. Il ciclo di
guardia riordina il record e `doctor` te lo ricorda finché una prova è in corso. Viene validata come una voce del
manifest, credenziali comprese.

## Cosa non può stare dietro il gateway

Il gateway sa autenticarsi a un server HTTP con un bearer token letto da una variabile d'ambiente (`auth.env`), e
nient'altro. Un server che vuole OAuth, come i server ospitati di Gmail e Calendar di Google, elenca i tool senza
credenziali ma risponde `401` a ogni chiamata, quindi la sua lista di tool sembra sana e lui non funziona.
`nexgen mcp plan` e `doctor` lo segnalano. Un server così va montato dove la CLI gestisce OAuth da sola, oppure
sostituito con uno che usa un token che il motore può tenere; `workspace-mcp` legge l'archivio dei token Google del
motore.

## Quanto costa un server

`nexgen mcp check --direct` si collega a ogni server montato direttamente, stampa i suoi tool e il peso delle loro
definizioni (circa quattro caratteri per token), poi il totale che ogni CLI carica all'avvio. Su una CLI che rimanda
le definizioni dei tool per natura quello non è ciò che paga una sessione; su una che non lo fa, si paga ogni volta.

L'indice che un modello legge ha un budget suo: `LAZY_MCP_INDEX_MAX_TOKENS`, 4000 per impostazione predefinita.
Finché l'intero indice ci sta, `lazy_list` porta una riga di descrizione per tool più le annotazioni del server. Oltre
il budget degrada ai soli nomi e lo dice con `"degraded": true`, che costa solo i suggerimenti: ogni tool resta
elencato, `lazy_load` consegna comunque la definizione completa che legge dal vivo, e `lazy_call` e `lazy_mutate`
inoltranno comunque. Un server grande montato accanto agli altri è il motivo abituale per alzare la variabile.

## Nascondere i tool che non usi mai

Una voce del manifest può nominare i tool da nascondere (`tools_deny`) o gli unici da tenere (`tools_allow`):

```yaml
servers:
  n8n-mcp:
    exposure: eager
    tools_deny: [add_data_table_column, delete_data_table_column, rename_data_table]
```

La regola è la stessa su ogni CLI. Dietro il gateway filtra il gateway. Un server montato direttamente in una CLI
parte invece attraverso `mcp-trim.py`, un filtro stdio tra la CLI e il server: inoltra tutto, toglie i tool nascosti da
`tools/list` e risponde con un errore (`-32602`) a una chiamata verso uno di loro. Il modello non vede mai la
definizione di un tool nascosto, quindi non la paga, e non può chiamarlo indovinandone il nome.
`nexgen mcp check --direct` misura il risultato.

## Quando un server non è disponibile

Il gateway ti dice perché. Un server la cui voce dichiara `${VAR}` e la trova vuota viene segnalato come "needs VAR in
this session's environment"; un server HTTP dichiarato con una variabile bearer vuota viene segnalato come credenziale
mancante. Entrambi compaiono in `lazy_list` (`unavailable`) e nell'errore di una chiamata, mai come "tool not found".

Una CLI avviata da un launcher grafico o da un IDE non eredita gli export del tuo terminale, ed è il motivo abituale
per cui un server che funziona in terminale manca in un'app. Per le variabili che la voce di un server dichiara, e solo
per quelle, il gateway ripiega su `~/.config/environment.d/*.conf`, così un token messo lì lo raggiunge comunque sia
stata avviata la CLI.

Il deposito dei segreti ha lo stesso vuoto: `nexgen-secrets materialize` scrive `~/.config/nexgen/secrets.env`, che
nessuna shell e nessun servizio carica. Dopo l'ambiente del processo e `environment.d`, il gateway legge quel file per
le credenziali che una voce nomina, e solo per quelle: un `${NAME}` semplice in `env`, `auth.env` (il bearer token di
un server HTTP) e `require_env`. Un valore composto come `prefix-${NAME}` non lo vede mai. Il file viene ignorato
quando non è tuo, quando altri possono scriverlo o quando una virgoletta al suo interno è rimasta aperta. Un server
montato direttamente in una CLI non riceve aiuto: la CLI stessa va avviata con la variabile, che è un motivo in più per
lasciare un server dietro il gateway a meno che non debba esserci sempre.

## I quattro tool

| Tool | Cosa fa |
| --- | --- |
| `lazy_list` | L'indice: ogni server, i nomi dei suoi tool, un suggerimento di una riga. Un server che non è partito porta `error` con il motivo. |
| `lazy_load` | La definizione completa di un tool. Sempre prima di chiamarlo. |
| `lazy_call` | Inoltra una chiamata a un tool che il manifest dichiara in sola lettura (`readonly: true` sul server, oppure `readonly_tools: [...]`). Rifiuta tutto il resto. |
| `lazy_mutate` | Inoltra una chiamata a qualsiasi altro tool. Richiede `"confirm": true`. |

Letture e scritture sono tool separati perché i permessi della tua CLI possano distinguerli: consentire `lazy_call` e
continuare a essere interpellato per `lazy_mutate`. La sola lettura non si deduce mai dal nome di un tool né dalle
annotazioni del server, che arrivano da un server che non controlli: deve dirlo il manifest. `"confirm": true` è il
modello che riconosce che la chiamata cambia stato, non è la tua approvazione. La tua approvazione è la richiesta della
tua CLI per `lazy_mutate`, che un assetto bypass elimina per scelta, quindi sotto bypass il registro di audit è
l'unica traccia.

Ogni caricamento, chiamata e rifiuto viene aggiunto a `~/.local/state/lazy-mcp-audit.jsonl`, sotto `NEXGEN_HOME` o
`XDG_STATE_HOME` quando sono impostate.

## Cosa riceve un server

Un server stdio parte con un ambiente filtrato: ciò che serve a un runtime per partire (`PATH`, `HOME`, locale,
cartelle temporanee, impostazioni di proxy e certificati, `XDG_*`, `NODE_*`, `NPM_CONFIG_*`, `PYTHON*`, le `NEXGEN_*` e
`AGENT_*` del motore), meno tutto ciò il cui nome somiglia a un segreto (`*TOKEN*`, `*SECRET*`, `*PASSWORD*`,
`*API_KEY*`, `*_KEY*` e così via). L'ambiente del proxy contiene ogni token che il tuo terminale esporta, e prima ogni
server li riceveva tutti.

Un server che ne ha bisogno lo dice nella sua voce del manifest, e solo allora lo riceve:

```yaml
servers:
  my-server:
    exposure: lazy
    command: npx
    args: ["-y", "my-mcp@1.2.3"]
    env:
      MY_SERVICE_TOKEN: "${MY_SERVICE_TOKEN}"
```

## Quando un server non parte

Un server che muore all'avvio, per un import mancante o un percorso sbagliato, veniva segnalato come "tool not found"
buttando via quello che aveva stampato. Le ultime righe del suo stderr ora vengono conservate e mostrate nell'errore,
con i valori dei segreti che il proxy gli ha dato e le forme di token abituali sostituite da `[redacted]`.

### `command: npx` su Windows

Windows avvia un file batch solo passandolo a `cmd.exe`, e lì `npx` è `C:\Program Files\nodejs\npx.cmd`: un percorso
con uno spazio, che l'interprete spezza (`"C:\Program" is not recognized`). Lo shim di npm avvolge una voce JavaScript
che sta accanto, quindi il motore avvia `node node_modules/npm/bin/npx-cli.js` invece, con gli stessi argomenti e
nessun interprete in mezzo. Una regola sola, `nexgen_core.processes`, e ogni launcher la usa: il gateway, `mcp-trim`,
`nexgen mcp check` e il percorso degli allarmi tramite OpenClaw. Quando la voce o node mancano, l'avvio viene rifiutato
con una frase che nomina lo shim e la voce attesa, e il gateway ripete quella frase invece del nome della classe
dell'eccezione. Su Linux e macOS il comando passa esattamente come è scritto.

## Concorrenza

Le chiamate ai tool vengono servite in parallelo, quindi una chiamata lenta a un server non blocca più un `ping` o una
chiamata a un altro. Le chiamate di un singolo server restano in serie, perché il suo canale è un unico flusso. Al
massimo 32 richieste sono in volo (`LAZY_MCP_MAX_CONCURRENCY`).
