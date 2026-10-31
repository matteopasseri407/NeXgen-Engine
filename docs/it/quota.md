# La quota: cosa resta di ogni piano

🇬🇧 [English](../quota.md)

Nessun provider offre una risposta locale a "quanto resta di questo piano", e il motore non se la inventa.
Legge quello che gli harness di questa macchina hanno già scritto sul proprio consumo, ripete i numeri che danno e dice chiaramente quali finestre non sono misurabili da qui.

`nexgen quota` stampa una riga per provider e finestra.
`/quota` stampa le stesse righe dentro OpenClaw.
`nexgen usage` stampa le stesse letture raggruppate per provider, e `/spend` le mostra dentro OpenClaw.

## Le quattro superfici

| Superficie | Cosa dà |
| --- | --- |
| `nexgen quota` | una riga per voce: provider, finestra, quanto è stato usato, la percentuale quando è un numero vero, il reset quando il provider lo dà, e da quanto tempo la lettura è stata presa |
| `nexgen quota --json` | le stesse righe come dati, più `lines`: il testo già composto, nella lingua della macchina |
| `/quota` in OpenClaw | le `lines` del JSON, inoltrate in chat |
| `nexgen usage` / `nexgen usage --json` | le stesse letture, un blocco per provider: la spesa delle corsie flat, i token usati da Claude Code, la percentuale che Codex e Antigravity riportano, e una frase dove una corsia non è leggibile |
| `/spend` in OpenClaw | le `lines` di `nexgen usage`, inoltrate in chat |

Le stringhe vengono composte una volta sola, dal motore.
Il plugin non trasforma mai un numero in una frase e non arrotonda mai una percentuale, così chat e terminale non possono dire cose diverse sulla stessa lettura.
Il comando esce sempre con 0: una fonte che non si riesce a leggere è una risposta, non un guasto.

## Perché il comando in chat è `/spend` e non `/usage`

OpenClaw ha già il suo `/usage`: il piè di pagina per ogni risposta, e `/usage cost`, un riepilogo locale sommato dai log delle sessioni di OpenClaw.
Quella somma riporta `$0` per un agente su abbonamento, e non sa nulla della spesa di OpenCode Go né della finestra di un piano Claude, perché nessuna delle due sta in un log di sessione.
Le letture del motore rispondono a quella domanda, quindi viaggiano accanto al comando di OpenClaw invece di prendersene il nome.
Un comando di plugin viene intercettato prima di quelli integrati, e registrare qui `/usage` toglierebbe dalla chat il piè di pagina e il riepilogo dei costi.

## Cosa dà ogni provider

| Provider | Da dove viene la lettura | Finestre | Percentuale | Reset |
| --- | --- | --- | --- | --- |
| Codex | l'ultimo evento `rate_limits` nel file più recente `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` | `5h`, `7d`, `30d`, come li dichiara l'evento | sì, `used_percent`, ripetuta come data | sì, `resets_at` |
| OpenCode Go, OpenCode Zen | l'archivio dell'harness `~/.local/share/opencode/opencode.db`, spesa per sessione divisa per il provider che è stato pagato | `5h`, `7d`, `30d` | solo quando `quota.yaml` dichiara un tetto per quella finestra | no |
| Claude Code | `message.usage` in `~/.claude/projects/*/*.jsonl` | `5h`, `7d` | mai, il limite del piano non sta in nessun file locale | no |
| Antigravity | i log dell'estensione quota locale `*-Antigravity Quota.log` sotto `%APPDATA%\Antigravity IDE\logs` (crediti prompt) | `30d` | sì, quando esiste un log locale; altrimenti non misurata | no |

### Codex

Codex scrive un evento di conteggio token nel proprio file di sessione, e quell'evento porta il blocco `rate_limits` del piano: la percentuale usata, la durata della finestra in minuti e quando si azzera.
Il motore legge la coda della sessione più recente, al massimo 8 MiB, e ripete i numeri senza ricalcolarli.

Una lettura più vecchia della finestra che descrive viene stampata come scaduta e la sua percentuale viene scartata.
Dopo che una finestra è girata, la vecchia percentuale descrive un periodo già azzerato, e ripeterla sarebbe peggio che tacere.

### OpenCode

La spesa è quella che l'harness ha archiviato, divisa per il provider registrato su ogni sessione: `opencode-go` (l'abbonamento flat) e `opencode` (Zen, a consumo).
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

L'ambiente desktop di Antigravity registra il consumo dei crediti prompt tramite l'estensione `ag-quota` nei log di sessione locali (`*-Antigravity Quota.log` sotto `%APPDATA%\Antigravity IDE\logs` o `%APPDATA%\Antigravity\logs`).
Il motore legge la coda del log più recente ed estrae la percentuale di crediti prompt mensili usati per la finestra `30d`, scartandola come scaduta quando è più vecchia di 30 giorni.
Quando sul disco non esiste un log locale, per esempio su una macchina che usa solo la CLI `agy`, dove la quota viene presa in memoria dall'API di Google Cloud Code senza essere scritta, la riga riporta `not measured`.

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

## La riga del doctor

`nexgen doctor` stampa una riga, `quota.known`, che elenca ogni percentuale che è un numero vero.
Normalmente è `OK`, ed è `WARN` quando una di quelle ha raggiunto l'80%, con l'azione `Lancia 'nexgen quota' per vedere tutte le finestre.`.
Non diventa mai rossa e non cambia mai il codice di uscita: un piano vicino al tetto è un fatto su cui agire, non una macchina rotta.
Una macchina in cui non è leggibile nulla lo dice in una frase invece di una riga vuota.

## Cosa deliberatamente non fa

- Non chiama mai l'API di un provider. Nessun endpoint pubblica quanto resta, e interrogarlo per scoprirlo sarebbe un modo nuovo di sbagliare.
- Non tiene storia e non fa previsioni. Ogni esecuzione legge il presente.
- Non tratta una lettura scaduta come attuale, e non porta avanti l'ultima percentuale conosciuta.
- Non legge nessun file di credenziali per ottenere un numero.
- Non chiama endpoint remoti delle quote, come l'endpoint dell'uso del piano di Claude Code o `fetchAvailableModels` di Google Cloud Code, con le credenziali dell'utente per recuperare le quote in modo dinamico.
