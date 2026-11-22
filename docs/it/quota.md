# La quota: cosa resta di ogni piano

🇬🇧 [English](../quota.md)

Nessun provider offre una risposta locale a "quanto resta di questo piano", e il motore non se la inventa.
Legge quello che gli harness di questa macchina hanno già scritto sul proprio consumo, ripete i numeri che danno e dice chiaramente quali finestre non sono misurabili da qui.

Esiste una sola eccezione, ed è opzionale, e la installi tu: [tokscale](https://github.com/junhoyeo/tokscale) è un binario separato, MIT, che legge gli accessi già salvati su questa macchina e chiede a ogni provider il proprio endpoint delle quote.
Quando è nel `PATH`, `nexgen quota` mostra le sue percentuali live accanto a quelle locali, e un piano a cui risponde compare una volta sola, con quel numero.
Il motore in sé continua a non leggere nessun file di credenziali e a non chiamare nessun endpoint: vedi [il backend opzionale](#il-backend-opzionale-tokscale).

`nexgen quota` stampa una riga per provider e finestra.
`/quota` stampa le stesse righe dentro OpenClaw.
`nexgen usage` stampa le stesse letture raggruppate per provider, e `/spend` le mostra dentro OpenClaw.

## Le superfici

| Superficie | Cosa dà |
| --- | --- |
| `nexgen quota` | una riga per voce: provider, finestra, quanto è stato usato, la percentuale quando è un numero vero, il reset quando il provider lo dà, e da quanto tempo la lettura è stata presa |
| `nexgen quota --json` | le stesse righe come dati, più `lines`: il testo già composto, nella lingua della macchina |
| `nexgen quota --line` | una riga sola e compatta: la finestra peggiore di ogni piano, il suo reset e, oltre i dieci minuti, la sua età. È quella che inietta l'avvio di una sessione |
| `/quota` in OpenClaw | le `lines` del JSON, inoltrate in chat |
| `nexgen usage` / `nexgen usage --json` | le stesse letture, un blocco per provider: la spesa delle corsie flat, i token usati da Claude Code, la percentuale che Codex e Antigravity riportano, e una frase dove una corsia non è leggibile |
| `/spend` in OpenClaw | le `lines` di `nexgen usage`, inoltrate in chat |

Le stringhe vengono composte una volta sola, dal motore.
Il plugin non trasforma mai un numero in una frase e non arrotonda mai una percentuale, così chat e terminale non possono dire cose diverse sulla stessa lettura.
Il comando esce sempre con 0: una fonte che non si riesce a leggere è una risposta, non un guasto.

## La riga con cui parte una sessione

L'hook `SessionStart` di Claude Code inietta una riga compatta, così il primo turno di una sessione vede già cosa resta invece di doverlo chiedere:

```
Quota: Claude 5h 40% (reset 18:00) · Codex 30d 72% · OpenCode Go weekly 0% · Antigravity stale
```

È `nexgen quota --line`: la finestra peggiore di ogni piano, il reset che il provider ha dato, e `stale` per un piano la cui lettura non è stata rinfrescata.
Un piano senza nessuna percentuale, cioè spesa senza un tetto dichiarato o qualcosa che questa macchina non misura, resta fuori invece di essere ipotizzato, e il report completo è il posto dove compare.
L'hook non compone niente: stampa la frase del motore, quindi il contesto della sessione e il terminale non possono contraddirsi.
Il comando scrive quella frase in UTF-8 qualunque code page avesse il terminale all'avvio, perché l'hook lo lancia con una pipe e la decodifica come UTF-8: il `·` fra due piani è un carattere, non un byte singolo di una code page di Windows.

L'hook chiede quella riga con un tetto rigido di tre secondi e non stampa nulla se la chiamata fallisce o scade: l'avvio di una sessione non deve mai dipendere da una lettura.
Per lo stesso motivo la riga non compra mai una corsa del [backend opzionale](#il-backend-opzionale-tokscale): mostra l'ultima risposta di quel backend finché `failover.state_max_age_minutes` di `quota.yaml` la considera affidabile, e oltre i dieci minuti che tutti i chiamanti condividono dice da quanto tempo il numero è stato letto (`read 40m ago`).
Un piano di cui la macchina non sa niente resta fuori invece di essere ipotizzato.
Il resto del lavoro dell'hook non cambia: il briefing del vault arriva ancora su una sessione ripresa o compattata, e una macchina senza motore nel `PATH` semplicemente non riceve la riga.

## Perché il comando in chat è `/spend` e non `/usage`

OpenClaw ha già il suo `/usage`: il piè di pagina per ogni risposta, e `/usage cost`, un riepilogo locale sommato dai log delle sessioni di OpenClaw.
Quella somma riporta `$0` per un agente su abbonamento, e non sa nulla della spesa di OpenCode Go né della finestra di un piano Claude, perché nessuna delle due sta in un log di sessione.
Le letture del motore rispondono a quella domanda, quindi viaggiano accanto al comando di OpenClaw invece di prendersene il nome.
Un comando di plugin viene intercettato prima di quelli integrati, e registrare qui `/usage` toglierebbe dalla chat il piè di pagina e il riepilogo dei costi.

## Cosa dà ogni provider

Ogni piano viene letto dalla migliore fonte che sa rispondere, e solo da quella: prima la lettura live del backend opzionale, poi il lettore del motore, poi `non misurato`.
L'ordine è lo stesso per ogni provider e non si mescola, così nessun piano compare due volte e nessuna riga è un'ipotesi.

| Provider | Fonte usata | Cosa viene mostrato | Lacune note |
| --- | --- | --- | --- |
| Claude Code / Claude Max | le finestre session e weekly live di tokscale; i contatori `message.usage` di `~/.claude/projects/*/*.jsonl` quando non ha niente | le percentuali `Session` e `Weekly` con il reset e l'età della lettura; altrimenti i token delle ultime `5h`/`7d` dai transcript | i transcript non contengono il tetto del piano, e Anthropic limita l'endpoint delle quote: finché lo fa, si mostra l'ultima lettura buona senza percentuale |
| ChatGPT / Codex | le finestre session, weekly e monthly live di tokscale; altrimenti l'ultimo evento `rate_limits` del file più recente `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` | `5h`, `7d`, `30d` come li dichiara il provider, con `resets_at` | il nome del piano è quello che riporta l'account collegato, quindi un secondo account sulla stessa macchina è un altro piano |
| OpenCode Go, OpenCode Zen | le finestre rolling, weekly e monthly di tokscale; altrimenti l'archivio dell'harness `~/.local/share/opencode/opencode.db` | le tre percentuali del provider; la spesa archiviata per finestra, con percentuale solo quando `quota.yaml` dichiara un tetto | l'archivio riporta dollari, mai un tetto del piano |
| Google Antigravity | l'endpoint loopback dell'IDE, tramite tokscale; il log locale `*-Antigravity Quota.log` scritto dalla sua estensione quando quell'endpoint non si raggiunge | i crediti prompt `30d`, con l'età; la lettura dal log viene scartata come scaduta dopo un giorno | serve un language server in esecuzione e, dalla versione 1.5.3, il suo token CSRF: senza nessuno dei due resta solo il log locale, e una macchina che usa solo la CLI `agy` non scrive niente su disco |
| GitHub Copilot | solo la lettura live di tokscale | `Chat`, `Completions`, `Premium`, con il reset che dà il provider | nessun file locale che questo motore sappia leggere |
| qualunque altro piano che tokscale impari | solo la lettura live di tokscale | quello che nomina il provider, alla percentuale del provider | nessun lettore locale, e non se ne inventa uno |

### Codex

Codex scrive un evento di conteggio token nel proprio file di sessione, e quell'evento porta il blocco `rate_limits` del piano: la percentuale usata, la durata della finestra in minuti e quando si azzera.
Il motore legge la coda della sessione più recente, al massimo 8 MiB, e ripete i numeri senza ricalcolarli.

Una lettura più vecchia della finestra che descrive viene stampata come scaduta e la sua percentuale viene scartata.
Dopo che una finestra è girata, la vecchia percentuale descrive un periodo già azzerato, e ripeterla sarebbe peggio che tacere.

### OpenCode

La spesa è quella che l'harness ha archiviato, divisa per il provider registrato su ogni sessione: `opencode-go` (l'abbonamento flat) e `opencode` (Zen, a consumo).
I dollari per sessione li dà il binario opzionale [ccusage](https://github.com/ccusage/ccusage) (MIT) quando è installato, che è quello che prezza l'uso di un abbonamento flat invece di leggere lo zero che l'harness registra, e la colonna `cost` dell'archivio quando non lo è.
Un id di modello che finisce in `-free` costa zero, quindi una corsia gratuita compare come `0.00 $` invece di sparire: un piano a costo zero è un fatto che vale una riga.

L'aritmetica è quella della corsia dell'harness, in `nexgen_local/supervision_spend.py`.
Il report delle quote decide cosa legge una persona, mai come si calcola un costo.
Un motore installato senza quella corsia lo dice in una riga e mostra le altre fonti.

### Claude Code

`nexgen quota` somma i contatori `message.usage` dei transcript scritti negli ultimi sette giorni, divisi in una finestra 5h e una 7d, deduplicati per id di messaggio.

Stampa conteggi di token e nessuna percentuale, perché l'uso del piano di Claude Code non sta in nessun file locale (impostazioni in `~/.claude`, cache o transcript): `/usage` interroga dinamicamente l'endpoint remoto dell'uso del piano di Anthropic.
La riga lo dice: `il limite non è leggibile in locale`.
È la risposta onesta, e il motore non la trasforma in un'ipotesi.

Questo comando è l'unico lettore di quei transcript.
Sulla macchina su cui è stato scritto erano 613 MB in otto giorni, una cifra sostenibile per un comando digitato a mano e non per un timer che gira ogni ora, quindi il beat e il doctor usano le fonti economiche.

### Antigravity

La prima fonte è la CLI di Antigravity stessa. `agy -p /usage` stampa le finestre del piano come fa il suo `/usage` interattivo (modelli Gemini e modelli Claude/GPT, ognuno con un limite settimanale e uno di cinque ore, la quota rimasta e l'azzeramento), e il motore trasforma ogni riga in una riga di quota: l'usato è quello che il client dice consumato, e le righe Gemini portano l'id provider `google-antigravity-cli`, così il blocco sulla delega le legge come ogni altro piano. Il comando risponde dal servizio quote di Google con il login che il client ha già; il motore non legge mai quel login e non chiama Google da sé, e non costa token del modello. Una risposta resta valida dieci minuti nella cartella di stato, così il battito orario, il doctor e la prima riga di una sessione la condividono; `NEXGEN_QUOTA_AGY=off` impedisce di interrogare il client. Solo dove `agy` non è installato, o non stampa niente di leggibile, il motore ripiega sul log dell'estensione dell'IDE qui sotto.

L'ambiente desktop di Antigravity registra il consumo dei crediti prompt tramite l'estensione `ag-quota` nei log di sessione locali (`*-Antigravity Quota.log` sotto `%APPDATA%\Antigravity IDE\logs` o `%APPDATA%\Antigravity\logs`).
Il motore legge la coda del log più recente, estrae la percentuale di crediti prompt mensili usati per la finestra `30d` e porta con sé il momento scritto nella riga di log, così la riga stampa da quanto è vecchia la lettura come tutte le altre.
La lettura viene scartata come scaduta dopo un giorno, non dopo i 30 giorni della sua finestra: l'estensione scrive solo mentre l'IDE gira, quindi una percentuale ancora dentro la finestra mensile può avere settimane e descrivere un periodo già azzerato.
Una lettura scaduta conserva la sua età, perde la percentuale e non arriva mai allo stato che il plugin legge prima di un turno, quindi un 99% vecchio non può far bloccare un piano che è a posto.
Quando sul disco non esiste un log locale, per esempio su una macchina che usa solo la CLI `agy`, dove la quota viene presa in memoria dall'API di Google Cloud Code senza essere scritta, la riga riporta `not measured`.

La fonte live è un'altra strada verso lo stesso numero, e appartiene al backend, non a questo motore: tokscale interroga il language server che l'IDE tiene in esecuzione sul loopback (`RetrieveUserQuotaSummary`), e trova quel server tramite il `--csrf_token` con cui il processo è partito, la porta che la CLI scrive in `cli.log`, o un `ANTIGRAVITY_CSRF_TOKEN` nell'ambiente.
Dalla versione 1.5.3 del language server il token è obbligatorio, quindi un server avviato senza non viene interrogato affatto, e una macchina il cui `cli.log` non nomina nessuna porta non ha niente da sondare.
La riserva è tutta qui: prima l'endpoint live, poi il log locale, poi `non misurato`, mai entrambi e mai una seconda riga per lo stesso piano.

## Il backend opzionale tokscale

[tokscale](https://github.com/junhoyeo/tokscale) è un binario con licenza MIT (npm `tokscale`) che fa esattamente quello che questo motore deliberatamente non fa: legge gli accessi OAuth già salvati sulla macchina e chiede a ogni provider, sul suo endpoint delle quote, quanto resta del piano.
Lo installi con `npm install -g tokscale` e viene usato; se non lo installi il report è identico a prima.

Si guadagna il posto con le righe che nessun file locale sa rispondere:

- un piano Claude Code, che nei transcript riporta token e da nessuna parte su disco un tetto;
- Antigravity, il cui IDE risponde sul proprio endpoint loopback ma su una macchina che usa solo la CLI `agy` non scrive niente su disco;
- OpenCode Go, rolling, weekly e monthly, come li calcola il provider invece che come un tetto in dollari dichiarato;
- Copilot, e ogni altro piano che tokscale imparerà a leggere, senza che questo motore cresca un lettore per ciascuno.

### Cosa non cambia

I lettori locali restano, e sono la riserva.
Quando tokscale risponde per un piano che il motore legge anche in locale, quello che vedi è il numero live e la riga locale viene nascosta, quindi niente compare due volte.
Vale anche per i conteggi di token di Claude Code, che non si stampano accanto a una lettura live dello stesso piano: un piano, una riga, un significato.
Quando tokscale per quel piano non ha niente, la riga locale viene stampata come è sempre stata, compresa la percentuale di Codex dal suo rollout, i dollari dell'archivio dell'harness e il log di Antigravity.
`nexgen usage` continua a mostrare la spesa per provider dell'archivio, quindi i dollari di OpenCode Go non si perdono quando una percentuale di quota li sostituisce in `nexgen quota`.

### I suoi dieci minuti di memoria

Un'esecuzione costa qualche secondo e interroga la rete, e `nexgen quota` non è l'unico chiamante: il beat che ogni ora alza i due avvisi, `nexgen doctor` e il controllo prima di un invio del supervisore leggono le stesse percentuali, mentre la riga che una sessione stampa quando parte legge la loro ultima risposta senza comprarne una.
La risposta viene quindi messa in cache per dieci minuti nella cartella di stato locale della macchina (`~/.local/state/nexgen/tokscale-usage.json`, su Windows `%USERPROFILE%\.local\state\...`), un solo intervallo in una sola costante, così il motore non spende mai più di un'esecuzione del binario ogni dieci minuti, quanti che siano i chiamanti.
L'esecuzione parte da quella stessa cartella: tokscale tiene una cartella `.tokscale/` nella directory di lavoro che riceve, e lasciata ereditare quella del chiamante la scriverebbe nel repository, nel Vault o nella shell da cui il comando è partito.
Ogni riga dice quanto è vecchio il suo numero.
La cache tiene solo quello che questo report legge: provider, id del provider, finestra, percentuale, reset e il momento in cui il numero è stato letto. L'accesso, l'email e l'id dell'account che il binario stampa pure non vengono scritti da nessuna parte.

### Quando un provider limita

L'endpoint delle quote di Anthropic ha un limite di frequenza, ed è chiedendolo troppo spesso che una macchina si sente dire di aspettare mezz'ora.
tokscale quel tempo lo registra da sé, per ogni accesso che tiene, e il motore legge quel registro invece di inventarsi un secondo calendario: finché dura, non si spende un'altra esecuzione, e le righe arrivano dalla cache con l'età che ognuna porta.

Ne seguono queste cose, e si vedono tutte nel report:

- Una lettura che tokscale non ha potuto aggiornare è datata con il momento in cui è stata presa davvero, non con quello di questa esecuzione, e perde la percentuale: `Claude: 34% della finestra Session nell'ultima lettura (letto 40m fa), e non è stata aggiornata`. Il numero è vero, la sua età è vera, e nessun passaggio di testimone ci si appoggia.
- Le righe degli altri piani tengono la loro percentuale finché l'attesa dura, e ognuna stampa la propria età. Una risposta in cache viene riusata non oltre l'età di cui la politica stessa si fida (`failover.state_max_age_minutes`, tre ore se il proprietario non dichiara altro), quindi niente arriva a un turno con un'età che quella politica avrebbe rifiutato; oltre quell'età la risposta si ricompra e torna com'è davvero, cioè come lettura conservata senza percentuale.
- Una riga in fondo dice perché, e c'è una frase per ogni età: più piani i cui ultimi campioni sono stati presi lontano nel tempo vengono descritti ognuno con la propria. Quando l'ultima lettura è stata conservata: `tokscale non ha potuto aggiornare Claude, ed è limitato fino alle 22:41 (fra 10m): mostra la lettura di 40m fa per quel piano.` Quando non si è potuto aggiornare niente: `tokscale è limitato fino alle 22:41 (fra 10m): queste letture sono di 15m fa.` Quando tokscale non ha più nessun campione e Claude manca del tutto dalla risposta: `Claude: tokscale è limitato fino alle 22:41 (fra 25m), quindi le finestre del piano non vengono mostrate.`

Il tempo di attesa non sta in `tokscale usage --json`: il binario stampa quella frase su stderr e solo nella sua vista testuale, quindi una risposta JSON di un provider limitato porta l'ultimo campione con `freshness.stale` impostato e nessuna scadenza.
Il registro che tokscale lascia accanto è la copia leggibile da una macchina dello stesso termine, e coincide con quella frase al secondo.

Un provider che smette del tutto di rispondere conserva l'ultima lettura che ha dato per un giorno, marcata allo stesso modo; dopo esce dal report invece di restare come un numero che nessuno sa datare.

### Quando il token di Claude scade

tokscale chiede ad Anthropic con il token di accesso che Claude Code ha salvato, e appena quel token viene rifiutato omette il piano da una risposta per il resto buona, quindi le finestre di Claude spariscono dal report senza che il report dica perché.
Quando Claude manca e `claude` è nel `PATH`, il motore esegue una volta `claude auth status` e richiede a tokscale una volta sola: quel comando fa riscrivere a Claude Code il proprio accesso, non spende nessuna chiamata al modello e nessuna finestra di nessun piano, e il suo output viene scartato senza leggerlo perché contiene l'indirizzo e-mail dell'account, quindi niente di quell'output finisce in cache, nei log o sullo schermo.
L'aggiornamento avviene al massimo una volta per periodo di cache, quindi una macchina il cui accesso non si rinnova costa un tentativo ogni dieci minuti e non uno per ogni chiamante, e non viene mai tentato mentre tokscale ha registrato un'attesa, perché un piano limitato manca per un'altra ragione.
Quando fallisce il report è quello di prima, Claude sconosciuto, più una frase che dice che il token è stato rifiutato e che il suo accesso non è stato rinnovato.

### Quando qualcosa non va

Un binario che manca, un'uscita diversa da zero, un'esecuzione che supera i venti secondi o una risposta di cui questo motore non riconosce la forma costano una riga in fondo al report e nient'altro.
Quando un'esecuzione precedente aveva già risposto, i suoi numeri restano nel report accanto a quella riga, datati con il momento in cui sono stati letti davvero e marcati come conservati: un timeout non svuota ogni piano per i dieci minuti in cui il guasto viene ricordato.
Il comando esce comunque con 0, le letture locali si stampano lo stesso, e il doctor e il beat proseguono.
L'output non porta nessuna versione di schema, quindi una forma non riconosciuta viene rifiutata intera invece di essere stampata a metà: una release di tokscale che rinomina un campo è una riga che dice `il formato di output di tokscale non è riconosciuto`, mai due numeri e un'ipotesi.

### Come spegnerlo

```yaml
# 03-INFRA/agent-universal-layer/quota.yaml
backends:
  tokscale: off
```

L'interruttore è per macchina e sta accanto ai tetti di cui fa parte. `NEXGEN_QUOTA_TOKSCALE=off` dice la stessa cosa per una sola esecuzione, senza toccare il file: un job schedulato, uno script, o una sandbox che non deve leggere gli account di chi è collegato.

## Una percentuale ha bisogno di un tetto

OpenCode riporta quanto è stato speso, mai quanto era concesso.
Una percentuale compare solo quando la macchina dichiara il tetto, in `03-INFRA/agent-universal-layer/quota.yaml` dentro il Vault:

```yaml
limits:
  opencode-go:
    5h: 12
    7d: 40
  opencode:
    30d: 20
```

`templates/quota.yaml.example` è lo stesso file con i commenti che lo spiegano.
I tetti sono per provider e per finestra, in dollari, e solo per le finestre che il report mostra.
Una voce che non è un numero positivo viene ignorata e nominata in una riga in fondo: un limite dichiarato e inutilizzabile non deve diventare uno zero silenzioso.

Senza il file ogni corsia a pagamento mostra la sua spesa e nessuna percentuale.
Nessun tetto viene mai dedotto dal mese prima, dal nome di un piano o da un'ipotesi.

## I due avvisi

Le percentuali alzano un avviso all'80% e di nuovo al 95%.
Le soglie vivono in un posto solo, `quota.ALERT_AT`, e l'avviso e il doctor leggono la stessa tupla, quindi non possono discordare su cosa significhi "alto".

L'avviso lo alza il beat che gira già ogni ora, su entrambe le piattaforme, subito dopo il controllo delle dipendenze.
Passa dal megafono, come ogni altro avviso, e porta il provider, la finestra e il reset quando il provider lo dà.

La chiave di debounce contiene la fascia: `quota:<provider>:<window>:80` e `quota:<provider>:<window>:95`.
Con la sola chiave del provider, l'avviso all'80% silenzierebbe quello al 95%, che è quello che conta di più.
Sopra si applica il debounce di quattro ore del megafono: una ripetizione persa costa meno di un avviso ogni ora su un piano semplicemente pieno.

Vengono consultate solo le letture economiche.
Nessun timer apre i transcript di Claude Code.
Il backend opzionale tokscale viene interrogato dalla sua cache di dieci minuti, quindi un beat ogni ora paga al massimo un'esecuzione ogni dieci minuti.

## A cosa passa il testimone quando un piano finisce

La sezione `failover:` dello stesso `quota.yaml` dichiara cosa deve usare un turno quando un piano finisce, e in che ordine.
È vuota per impostazione predefinita: quali provider pagare, e in che ordine, è una tua regola e non del motore.

```yaml
failover:
  block_at: 95
  state_max_age_minutes: 180
  chain:
    - anthropic/claude-opus-5-5
    - opencode-go/deepseek-v4-pro
  agents:
    capo:
      - openai/gpt-6-luna
```

`chain` vale per ogni agente OpenClaw che il motore crea, `agents` la sostituisce per uno di loro, e ogni voce è un modello scritto come lo nomina OpenClaw, `provider/model`.
Una voce malformata viene ignorata e nominata in una riga in fondo, come un limite dichiarato che non si può usare.

Con quella sezione al suo posto, ogni riga di `nexgen quota` finisce con il modello a cui quella finestra passa il testimone:

```
Codex (free): 96% of the 30d window used (read 2m ago), resets in 3h 12m, then anthropic/claude-opus-5-5
```

Il modello mostrato è la prima voce della catena dell'assistente che gira su un provider diverso, perché un piano esaurito si porta dietro anche i modelli fratelli.
Un provider senza catena dichiarata stampa la sua riga come ha sempre fatto, e nessun passaggio di testimone viene inventato.

`nexgen openclaw setup` è ciò che porta le catene dentro OpenClaw: vedi [la pagina OpenClaw](openclaw.md#la-catena-di-riserva).
`nexgen doctor` stampa una riga, `openclaw.fallbacks`, con quanti agenti portano una catena e quali non ne portano nessuna.

## Solo abbonamenti (facoltativo)

Niente di questa pagina serve a una macchina che paga a consumo, e nessuna parte del motore ti chiede di cambiare. Questa sezione serve a un caso solo: ogni piano che usi è flat, un abbonamento o un modello su questa macchina, e vuoi che il motore smetta di offrirti il resto.

La chiave è una riga nello stesso `quota.yaml` che contiene i tetti:

```yaml
# 03-INFRA/agent-universal-layer/quota.yaml
flat_providers: [opencode-go, anthropic, openai, ollama]
```

Ogni voce è un id di provider, scritto come lo nomina un riferimento di modello prima della barra. Con la chiave dichiarata:

- `nexgen openclaw setup` scrive `agents.defaults.modelPolicy.allow` con un carattere jolly per ogni provider dichiarato e nient'altro, quindi il selettore di ogni agente offre `opencode-go/*`, `anthropic/*`, `openai/*` e `ollama/*` e nessun piano a consumo. Senza la chiave lo stesso setup scrive un carattere jolly per ogni provider raggiungibile, come ha sempre fatto.
- `nexgen openclaw plan` mostra la lista che il setup scriverebbe prima che un file venga toccato.
- `nexgen doctor` e `nexgen openclaw doctor` stampano una riga, `openclaw.flat_models`. Un riferimento di modello fuori dalla lista è un `FAIL` che nomina dove è stato letto e quale modello è: il `model` di un agente, un anello della sua catena di riserva, o un `subagents.model`. Entrambi i comandi escono con un codice diverso da zero, perché un turno su un provider a consumo spende soldi che nessuno ha approvato.

La lista è una politica, non una riscrittura: il setup cambia quali modelli offrono i selettori e non tocca il `model` di nessun agente, quindi un file che nomina già un provider fuori dalla lista lo conserva, viene segnalato, e resta una cosa che cambi tu.

Un provider va nella lista solo se è così che questa macchina lo raggiunge. Lo stesso provider è spesso insieme un piano e un contatore: `anthropic` è un abbonamento Claude tramite Claude Code e anche una chiave API a pagamento, `openai` è un piano ChatGPT tramite Codex e anche consumo per token, e l'`opencode` di OpenCode (Zen) è a consumo mentre `opencode-go` è quello flat. La lista nomina il provider, non l'account, quindi una voce lì dentro dice "su questa macchina quel provider è già pagato". Una voce per un provider su cui questa macchina non ha nessun accesso viene scritta come dichiarata e semplicemente non offre nulla: l'esempio qui sopra è una forma da copiare, non una lista da incollare intera.

Tornare indietro è cancellare la chiave: il `nexgen openclaw setup` successivo riscrive i caratteri jolly raggiungibili e alla riga del doctor non resta niente da rifiutare. Una voce che non è un id di provider viene ignorata e nominata in una riga, come un tetto che non si può usare; un valore che non è una lista costa l'intero controllo, e la riga lo dice invece di leggersi come "niente da limitare".

## La riga del doctor

`nexgen doctor` stampa una riga, `quota.known`, che elenca ogni percentuale che è un numero vero.
Normalmente è `OK`, ed è `WARN` quando una di quelle ha raggiunto l'80%, con l'azione `Lancia 'nexgen quota' per vedere tutte le finestre.`.
Non diventa mai rossa e non cambia mai il codice di uscita: un piano vicino al tetto è un fatto su cui agire, non una macchina rotta.
Una macchina in cui non è leggibile nulla lo dice in una frase invece di una riga vuota.
Quelle percentuali possono venire dal backend opzionale tokscale, e il doctor lo interroga dalla stessa cache di dieci minuti: un doctor, un beat e un comando digitato negli stessi dieci minuti pagano fra loro un'esecuzione sola, non tre.

## Cosa deliberatamente non fa

- Il motore in sé non chiama mai l'API di un provider. Nessun endpoint pubblica quanto resta, e interrogarlo per scoprirlo sarebbe un modo nuovo di sbagliare.
- Non tiene storia e non fa previsioni. Ogni esecuzione legge il presente.
- Non tratta una lettura scaduta come attuale. Una lettura che un provider ha rifiutato di aggiornare conserva il numero e la sua età e perde la percentuale, quindi l'ultimo valore noto non viene mai scambiato per quello attuale e nessun passaggio di testimone ci si appoggia.
- Il motore in sé non legge nessun file di credenziali per ottenere un numero. Il backend opzionale tokscale fa esattamente questo, ed è per questo che è un binario separato che installi tu, e che `backends: {tokscale: off}` esiste.
- Il motore in sé non chiama endpoint remoti delle quote, come l'endpoint dell'uso del piano di Claude Code o `fetchAvailableModels` di Google Cloud Code, con le credenziali dell'utente. Il backend opzionale tokscale li chiama al posto tuo con l'accesso già salvato su questa macchina, e i suoi numeri sono le risposte dei provider, non l'aritmetica di questo motore.
- Nessun numero del backend viene creduto oltre la sua forma: i campi vengono validati, l'età viene stampata, e un formato non riconosciuto viene rifiutato invece che interpretato.
