# AI Council

🇬🇧 [English](../council.md)

`council.py` è un orchestratore locale che convoca come consulenti le altre CLI agentiche che hai già: brainstorming, messa in discussione di un piano, revisione di codice tra vendor diversi.
È Python esplicito, non un LLM, a decidere chi parla e quando.

È un'espansione facoltativa.
Senza un `seats.yaml` configurato è del tutto inerte, e nient'altro nel motore dipende da lui.

## Preparazione

1. Copia lo schema di esempio nella tua radice dati e compila le sedute che hai davvero:

   ```bash
   mkdir -p ~/KnowledgeVault/03-INFRA/agent-universal-layer/council
   cp 03-INFRA/agent-universal-layer/council/seats.yaml.example \
      ~/KnowledgeVault/03-INFRA/agent-universal-layer/council/seats.yaml
   ```

   Correggi il percorso se `AGENT_VAULT_DATA` o `KNOWLEDGE_VAULT_PATH` puntano altrove.
   Questo file sono i tuoi dati e non finisce mai nel motore.
   Un piccolo team può dare a ciascuno il proprio file invece di un `seats.yaml` condiviso: vedi il commento "more than one seats file" in cima a `seats.yaml.example` per le variabili `COUNCIL_SEATS_FILE` e `AGENT_TEAM_MEMBER`.
   Nessuna delle due viene letta se non è impostata, quindi un'installazione a utente singolo non vede differenze.
2. Lascia `schema_version: 1` alla radice, poi scegli le sedute che hai seguendo i commenti del modello.
   Ogni seduta richiede `vendor`, `cli`, `model` e un `zero_retention: true|false` esplicito.
   `cli` deve essere uno tra `opencode`, `agy`, `codex`, `claude` e `ollama`, le CLI che `council.py` sa invocare oggi.
   Metti `zero_retention: true` solo se l'hai verificato su una fonte primaria, non su un riassunto.
   Puoi anche impostare un `timeout_seconds` positivo e facoltativo per una seduta che notoriamente ha bisogno di più o meno tempo.
3. In un setup MULTI, esegui una volta `agent-sync apply` per installare il launcher `council` su Linux e il wrapper `council.cmd` su Windows, poi lancia uno qualsiasi dei comandi qui sotto.
   Senza un `seats.yaml` valido, Council si ferma e dice cosa manca.
   In un setup MINIMAL, o se il launcher non è nel `PATH`, sostituisci `council` con `python3 03-INFRA/agent-universal-layer/council/council.py`.

Il comando `council` (e `council.cmd` su Windows) risolve ed esegue `agent-universal-layer/council/council.py` tramite `nexgen_core/tools/council.py`.
Tutto il flusso di controllo, le modalità e le protezioni descritte qui vivono in quel motore Python.

Council non incapsula le CLI dei provider dietro un server MCP.
Avvia ogni CLI dichiarata come sottoprocesso locale e trasporta il brief via stdin o un file temporaneo protetto.
Un aggiornamento del protocollo MCP quindi non cambia il trasporto dei prompt di Council e non rende stateless la CLI di un vendor.
Se quella CLI sa caricare server MCP, tool o stato persistente, Council deve disattivare o isolare quelle capacità a parte.

## Le quattro modalità

Da ogni seduta ci si aspetta il contratto "senza mani": riceve testo, restituisce testo, e non usa tool di filesystem, shell o MCP.
Council toglie i segreti applicativi dagli ambienti isolati delle sedute e applica i controlli delle CLI dei vendor dove esistono.
L'applicazione non è identica tra vendor, quindi la sezione "Limiti attuali" qui sotto è quella che conta.
Una CLI di cui non si può dimostrare il rispetto del contratto viene rifiutata come seduta.

**Brainstorm.** Una seduta, uno o più giri.
Ogni giro dopo il primo deve attaccare la propria conclusione precedente invece di ripeterla:

```bash
council brainstorm \
  "Should we cache this at the edge or the origin?" \
  --context notes.md --rounds 2 --seat gemini
```

**Challenge.** Una seduta cerca il difetto dominante di un piano.
`APPROVE` costa caro: deve escludere esplicitamente almeno due categorie di rischio, altrimenti non vale.

```bash
council challenge \
  "Single cron job tars the data dir nightly to one S3 bucket." --seat gemini
```

**Code review.** Rifiuta di partire quando `--author-vendor` coincide con il vendor della seduta.

```bash
council code-review \
  changes.diff --author-vendor anthropic --seat gemini
```

**Relay.** Una catena sequenziale di massimo 5 stadi, per esempio architetto, costruttore, revisore e giudice.
Ogni stadio vede il brief originale completo più l'output dello stadio precedente, formattato come blocco citato e preceduto dall'istruzione di valutarlo, non di obbedirgli.
Quella cornice è una convenzione a livello di prompt, non un controllo tecnico: niente impedisce a una seduta compromessa o pilotata in modo avversariale di ignorare l'istruzione e produrre un verdetto fuorviante che lo stadio successivo leggerà.
Tratta il VERDICT finale di un relay come input consultivo per il tuo giudizio, mai come prova che la catena sia corretta.
Ogni CLI supportata può partecipare quando la sua seduta è dichiarata nella sequenza.
Il pre-controllo dei costi OpenCode riordina solo i candidati OpenCode dentro l'ordine originale del documento di decisione, e non promuove mai un altro provider solo perché non ha dati d'uso OpenCode.

Per impostazione predefinita uno stadio che restituisce `VERDICT: REJECT` ferma il relay prima dello stadio successivo: un rifiuto significa che il piano non vale la pena di essere costruito, e girare gli stadi restanti spenderebbe quota su qualcosa che il relay ha già abbandonato.
Passa `--continue-on-reject` per eseguire comunque ogni stadio dichiarato.

L'analisi del verdetto è posizionale, non una ricerca nel testo: solo l'ultima riga non vuota di una risposta può impostare il verdetto di quello stadio, con l'enfasi markdown e la punteggiatura finale tollerate e un prefisso di citazione no.
Uno stadio che cita un `VERDICT: REJECT` precedente mentre arriva a una propria conclusione diversa viene letto come quella conclusione, e un verdetto citato a metà risposta non ferma più il relay.
Lo fa solo la riga di chiusura autonoma di una seduta.

```bash
council relay \
  "Design a rate limiter for the public API." \
  --sequence "architect=glm,builder=qwen,reviewer=deepseek-free"
```

## Relay riprendibile

`council relay --resumable` esegue lo stesso relay sequenziale ma salva l'avanzamento in SQLite dentro la cartella di sessione conservata, così una run interrotta riprende invece di ricominciare:

```bash
council relay "Design a rate limiter." --sequence "architect=glm,builder=qwen" --resumable
# ... interruption (Ctrl+C, dead process, quota pause) ...
council relay "Design a rate limiter." --sequence "architect=glm,builder=qwen" --resume council-design-a-rate-limiter-20260927T120000Z
```

La ripresa controlla il brief, la sequenza e le sedute scelte prima di invocare qualsiasi seduta.
Cambiare modello, CLI, sforzo di ragionamento, pool di quota, politica di conservazione, timeout o identità di routing di una seduta fa rifiutare la ripresa.
I checkpoint creati prima di questi controlli sulle sedute vanno ricominciati come relay nuovo.
Gli stadi completati non vengono mai reinvocati.

Un caso richiede una tua decisione esplicita: se il processo è morto dopo che un provider ha risposto ma prima che l'esito fosse salvato, la ripresa dichiara quale stadio e quale tentativo sono incerti e si ferma.
Reinvocare può fatturare la quota due volte, quindi passa `--allow-uncertain-rerun` solo se lo accetti.
I fallimenti noti come non ritentabili vengono salvati prima che il relay si fermi, e riprenderli riporta il fallimento salvato senza richiamare il provider, anche con `--allow-uncertain-rerun`.

Solo un'invocazione alla volta può far avanzare una sessione, e una ripresa simultanea si ferma con un errore di sessione occupata prima di chiamare una seduta.
`calls_made` conta i tentativi di invocazione prenotati, comprese le chiamate incerte e le riprese accettate.
La prenotazione viene salvata prima dell'invocazione, quindi questo conteggio può superare le chiamate reali al provider quando un crash o un rifiuto avviene prima che il provider parta.
È un conteggio prudente della quota, non una ricevuta di fatturazione.

`council clean` e la scadenza TTL rimuovono i checkpoint insieme alla sessione.
LangGraph e il suo saver SQLite sono inclusi nell'installazione del motore.
Brainstorm e challenge restano effimeri.

## Consulto indipendente

`council consult` pone lo stesso brief a più sedute in modo indipendente e in parallelo, poi esegue al massimo un giro di replica mirato sui disaccordi:

```bash
council consult "Is this migration plan safe?" --seat luna --seat kimi --seat glm
```

Ogni seduta giudica solo il brief originale, senza passaggi di mano e senza contesto condiviso tra i pareri.
Le sedute che falliscono si astengono con un motivo registrato invece di interrompere il giro; se falliscono tutte, il consulto rifiuta invece di sintetizzare dal nulla.
I budget sono rigidi: al massimo 5 sedute, al massimo 1 giro di replica, timeout per seduta.
Il transcript (`consult.md`) registra pareri, repliche, astensioni, il conteggio dei verdetti e le coppie in disaccordo.
Misuralo su casi comparabili a parità di budget: si guadagna il suo posto solo migliorando la copertura dei problemi o riducendo il tempo, e un accordo più frequente non è una misura di qualità.

Ogni parere o replica completati vengono stampati subito su stdout, anche mentre un'altra seduta sta ancora girando.
Il transcript viene aggiornato dopo ogni risultato e segnato come completato solo quando il consulto finisce.
Un'eccezione inattesa annulla le altre sedute in corso prima di propagare il guasto.
Con `--keep-session` i risultati completati restano su disco dopo un'interruzione.
Senza, restano nell'output del terminale e i file effimeri vengono rimossi.

Ogni modalità accetta `--context FILE` per un contesto aggiuntivo.
Una seduta senza una garanzia di zero-retention confermata resta utilizzabile, ma Council stampa un avviso su stderr prima di inviare il brief.
Il vecchio flag `--allow-training-risk` è stato rimosso del tutto invece di essere tenuto come no-op: passarlo oggi è un errore della CLI.

## Da OpenClaw

`/council <question>` interroga il Council da una chat.
Una run del Council richiede minuti e apre CLI di vendor, quindi il comando non la aspetta: risponde subito con un identificatore e avvia

```bash
council brainstorm "<question>" --auto --keep-session --notify --tag <id>
```

staccata, così la run sopravvive al turno di chat e al gateway stesso.
`--auto` perché non c'è nessuno a un terminale a scegliere una seduta, `--keep-session` perché l'esito completo resti su disco dopo, `--notify` perché la fine arrivi dove sta la persona.

`--notify` manda una riga attraverso il megafono, l'unica superficie di avviso del motore, prima che il processo esca: su un verdetto, su un rifiuto, su qualunque errore.
`--tag` viene citato in quella riga, così due run nella stessa ora restano distinte.
La riga porta il verdetto, l'identificatore e la cartella di sessione quando la sessione è stata conservata; una run che si è fermata dice invece perché.
Il guasto viene comunque sollevato e imposta comunque il codice di uscita: la notifica lo riporta e non lo sostituisce mai.
Lo stesso flag funziona su ogni modalità, `consult` e `relay` comprese.

`nexgen_council` è lo stesso avvio come tool per gli agenti: restituisce l'identificatore.
`nexgen_council_result` rilegge quello che la run ha prodotto finora, ancora in corso oppure la fine del log, e la cartella di sessione della run tiene il resto.
Nessuno dei due tool è un'adesione dell'operatore: chiedere al Council non è un cambio di stato.

## Proposte di routing approvate da una persona

Council funziona senza Governor.
Con il solo `seats.yaml`, `council propose` mostra le sedute dichiarate dall'utente e aspetta una scelta esplicita.

Una sezione `routing:` facoltativa trasforma un documento di routing in una proposta verificata in locale.
Imposta `decision_file` a un percorso relativo dentro la radice dati privata.
Council capisce le tabelle Markdown per ruolo emesse dal pubblico [LLM Model Routing Governor](https://github.com/matteopasseri407/NeXgen-addon-llm-model-routing-governor), e la vecchia tabella piatta per compatibilità.

Council conserva ogni ripiego della tabella del Governor e risolve l'etichetta del modello insieme al suo canale rispetto alle sedute locali.
Go, Zen gratuito e Zen a pagamento restano separati anche se tutti e tre usano OpenCode.
Privacy accetta uno o più candidati locali e ignora le righe escluse esplicitamente.
Canali sconosciuti e righe candidate malformate fermano la proposta.
Una vecchia tabella piatta funziona solo quando ogni modello mappa su un solo pool di esecuzione, e le corrispondenze ambigue vengono escluse.
`propose` e `routing-status` identificano l'host corrente e conservano lo slot originale `prescelto` o `rimpiazzo N`.
Un prescelto mancante non promuove un ripiego a prescelto.
Entrambi i comandi mostrano le esclusioni anche quando altri candidati sono disponibili, ed elencano come scelte manuali, con il risultato della sonda locale, le sedute dichiarate che nessun ruolo di routing nomina.

Questo è un adattatore in memoria.
Né Council né un workflow esterno riscrivono `seats.yaml`: quel file resta la lista di autorizzazione all'esecuzione locale, mentre il documento di routing fornisce l'ordine corrente.
Una CLI mancante, un modello diverso o uno sforzo Codex diverso tolgono un candidato dalla proposta con una spiegazione.
Una zero-retention mancante non lo toglie; la proposta lo segnala con un avviso.

Per una seduta `codex`, Council confronta modello e sforzo dichiarati con il valore predefinito della CLI in `$CODEX_HOME/config.toml` (predefinito `~/.codex/config.toml`).
Un modello esplicito può differire da quel valore quando `models_cache.json` elenca il modello esatto e supporta lo sforzo richiesto.
Questi controlli leggono metadati locali e non spendono quota.

Per una seduta `claude`, Council controlla prima che la CLI installata supporti la scelta esplicita di `--model`.
Richiede anche `--effort` quando la seduta dichiara un `reasoning_effort`.
Basta per includere la seduta in una proposta senza spendere quota in una sonda.
Quando l'utente invoca quella seduta, Council chiede output JSON e confronta `modelUsage` con il modello esatto dichiarato in `seats.yaml`: un valore mancante o un ripiego silenzioso su un altro modello ferma la run invece di accettare una risposta non verificata.

Una riga `no local seat associated` significa che nessuna seduta dichiarata corrisponde a quel modello e a quel canale di esecuzione esatti.
Allinea `seats.yaml` al modello previsto, oppure correggi il documento di routing tramite chi lo produce.
Versioni diverse di un modello non vengono mai trattate come alias.
Privacy resta bloccata quando nessuna seduta locale corrispondente è dichiarata o disponibile su questo host.
Council non crea da solo sedute di ripiego locali o a pagamento.

La proposta non esegue mai un modello.
`brainstorm`, `challenge` e `code-review` richiedono un `--seat` esplicito di una persona, e `relay` richiede una `--sequence` esplicita.
La persona decide il ruolo, il modello e quante sedute chiamare.

```bash
council routing-status
council propose --mode challenge
council challenge "Find the dominant risk in this plan." --seat code-seat
council relay "Design a safe migration strategy." --sequence "planner=planner-seat,judge=judge-seat"
```

Usa `--routing-role L-Sys` per chiedere una proposta diversa; richiede comunque `--seat` prima di un'invocazione a seduta singola.
`council propose --mode relay` elenca i candidati disponibili per i ruoli di relay configurati senza invocarli.

Un `--seat` esplicito può scegliere fuori dal ruolo suggerito da una modalità, con un avviso visibile.
Un `--routing-role` esplicito o i `routing.mode_defaults` configurati restringono comunque le sedute ammesse, e Privacy esclude sempre le sedute cloud.
La conferma di pagamento si applica indipendentemente dall'appartenenza a un ruolo, comprese le sedute senza un prezzo di routing dichiarato.
Go stampa un avviso di quota prepagata prima di ogni chiamata e non chiede mai una conferma di pagamento in denaro.
Per Codex, un modello esplicito può differire dal predefinito quando la cache locale dei modelli verifica modello e sforzo; se i metadati locali non li verificano, Council avvisa prima di inoltrare il modello scelto esplicitamente.

## Timeout

Ogni chiamata a una seduta è limitata da un valore predefinito prudente di 300 secondi.
Imposta un `timeout_seconds` positivo su una seduta per farne il suo budget normale, oppure usa `--timeout-seconds` per sostituire il budget di una singola invocazione.
Il valore da riga di comando vince su quello della seduta, che vince sul predefinito.
Zero, valori negativi, infinito e booleani vengono rifiutati.

```bash
council challenge "Review this plan" --seat gemini --timeout-seconds 90
```

## Sforzo di ragionamento

Un `reasoning_effort: low|medium|high|xhigh|max|none` facoltativo su una seduta in `seats.yaml` viene inoltrato alla CLI di quella seduta.
Una sola mappatura per CLI serve sia il comando reale sia ogni punto che stampa l'etichetta dello sforzo, così i due non possono divergere:

- `claude`: `--effort <value>`, alla lettera.
- `codex`: `-c model_reasoning_effort="<value>"`, alla lettera.
- `opencode`: nessun flag. OpenCode 2 ha eliminato `--variant`, quindi la variante viene aggiunta al modello come `provider/model#<value>`, alla lettera, e un modello che nomina già una variante la mantiene. Il valore dipende dal provider e non esiste un enum fisso da validare in locale.
- `ollama`: `--think` documenta solo `low`/`medium`/`high`. `xhigh` e `max` vengono abbassati a `--think high`, e l'etichetta stampata lo dice. Ogni altro valore viene scartato senza flag, e l'etichetta lo dice.
- `agy`: `--effort` accetta `low`/`medium`/`high`/`xhigh`/`max`, inoltrati alla lettera. Ogni altro valore viene scartato senza flag, e l'etichetta lo dice.

## Gestione delle sessioni

Council crea una cartella di lavoro privata per il brief, l'output di ogni stadio e il verdetto finale.
Rimuove quella cartella dopo aver restituito un risultato, e dopo un errore, a meno che tu non passi `--keep-session`.
Su POSIX le cartelle di sessione conservate usano il modo `700` e i loro file il `600`.
Council rimuove le sessioni conservate e scadute quando parte.
Nel vault non viene scritto nulla automaticamente.
Se un risultato conta in seguito, salva una decisione o una diagnosi breve con il tuo flusso normale sul vault, non il transcript grezzo del Council.

```bash
council brainstorm "Review this plan" --keep-session
```

```bash
council clean                 # removes kept sessions older than 7 days
council clean --ttl-days 1    # custom retention
council clean --all           # removes every kept session now
```

## Protezioni

- **Uscita dei dati**: il brief originale, contesto e diff compresi, viene scansionato alla ricerca di segreti probabili prima che qualsiasi seduta lo riceva. Una corrispondenza ferma la chiamata.
- **Output generato**: quando una seduta emette un valore che sembra un segreto, Council oscura la riga interessata prima che raggiunga lo stadio successivo del relay o una sessione conservata, e il relay può proseguire con il resto dell'analisi.
- Entrambi i controlli riusano il modulo condiviso `leak_scan.py` del motore, e sono una protezione separata e sempre attiva per l'utente finale, non la scansione delle fughe della CI che protegge la pubblicazione su GitHub. Vedi [SECURITY.md](../../SECURITY.it.md).
- **Zero-retention**: ogni seduta deve dichiarare `zero_retention: true|false`.
  Un valore falso stampa un avviso chiaro nei menu e subito prima che il modello parta.
  È un metadato, non un cancello di esecuzione, e la scansione dei segreti resta bloccante come controllo separato.
- **Canali a consumo**: le sedute Zen a pagamento richiedono una conferma interattiva prima di ogni chiamata, anche quando il routing è disattivato o il prezzo manca o è zero.
  Go consuma quota prepagata e non attiva il cancello del pagamento in denaro, e gli altri canali usano il costo dichiarato nel documento di routing.
  Senza una `y` sul terminale dell'operatore, o quando stdin non è disponibile, una chiamata a pagamento viene rifiutata.
  Consult raccoglie tutte le conferme sul thread principale prima di avviare qualsiasi worker di parere o replica.
- **Riepilogo dei costi**: dopo ogni giro o stadio di relay completato, Council stampa un riepilogo d'uso su una riga quando la CLI lo riporta (`tokens` e `cost`, sommati sugli step di opencode; codex riporta `total_cost_usd`).
  Le CLI che non riportano l'uso non stampano nulla.
- **Controlli di conservazione storici**: il vecchio flag `--allow-training-risk` e il sottocomando `council allow-training` sono stati rimossi invece di essere deprecati in un no-op.
  Ognuno dei due oggi è un semplice errore della CLI, senza percorso di compatibilità per gli script che li passano ancora.
- **Quota**: `--max-rounds` (brainstorm) e `--max-seats` (relay) limitano quanto può spendere una sessione anche se chiedi di più.

## Limiti attuali

- Le sedute `codex` e `opencode` sono verificate dal vivo su tutte e quattro le modalità.
  `challenge` è stato inviato dal vivo a una seduta codex reale (2026-07-13, poi di nuovo il 2026-07-15 via `codex-sol`), ed è rivedendo quelle run dal vivo che sono stati trovati diversi bug corretti da allora, compreso il blocco `agy` qui sotto.
  `brainstorm` (opencode, 2026-07-15) ha prodotto un secondo giro vero, che attaccava la conclusione del primo giro.
  `code-review` (opencode, 2026-07-15) è stato eseguito su un diff con un bug di concorrenza reale e piantato, un check-then-decrement non atomico su un token bucket, e lo ha trovato senza suggerimenti come difetto dominante.
  Un `relay` dal vivo a 3 stadi che attraversava opencode, codex e agy (2026-07-15) ha inoltre verificato che il meccanismo di relay multi-vendor funziona tra wrapper di CLI diversi che si passano il turno dentro una sola catena.
  Claude Opus 5 è stato verificato dal vivo il 2026-08-01: la CLI ha accettato la selezione esatta di `--model` e ha restituito lo stesso modello canonico tramite `modelUsage`, con i tool disattivati e la persistenza di sessione spenta.
  Le sedute `ollama` non sono ancora state verificate dal vivo.
- **`agy` (Antigravity) ha un adattatore Council**, invocato come `agy --print --model <model> --disable-slash-commands --new-project --sandbox [--effort <level>]` con il prompt su stdin.
  L'adattatore chiede un progetto nuovo e rimuove i token applicativi.
  I flag di sandbox non provano che i tool MCP siano disattivati, e i limiti di isolamento della CLI restano specifici del vendor.
  I test di regressione verificano il trasporto e l'inoltro dello sforzo; non certificano la disponibilità dal vivo di ogni modello né l'isolamento su ogni host.
- I test di regressione automatici coprono il flusso di controllo di tutte e quattro le modalità, la pulizia delle sessioni, il ripiego del relay, tutte le CLI di vendor supportate e il launcher Linux.
  Usano sedute finte, quindi non sostituiscono le verifiche dal vivo di ogni CLI.
- Il launcher Windows ha test di regressione portabili e gira nativamente sotto il core Python unificato.
- I prompt grandi usano stdin per Codex, Claude, Ollama e Antigravity, e un allegato temporaneo protetto per OpenCode.
  L'allegato viene creato dentro l'albero privato della sessione e rimosso quando la seduta esce.
- Le sedute via CLI sono lente, minuti e non secondi: servono per brainstorming, messa in discussione e revisione, non per una domanda veloce a metà lavoro.
