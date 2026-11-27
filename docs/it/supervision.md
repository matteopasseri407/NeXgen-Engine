# Compiti supervisionati (fasi 1, 2, 3a, 3b e 3c)

🇬🇧 [English](../supervision.md)

Un lavoro supervisionato è un obiettivo portato a termine da agenti harness che il proprietario può guardare: un modello frontier scrive il brief, un esecutore lavora senza finestra in una sessione del proprio harness, e il motore tiene i file, i soldi e il verdetto.
Questa pagina copre le fasi 1, 2, 3a, 3b e 3c: la cartella del compito, le finestre visibili quando le chiede `--tab`, il registro dei costi, la copia di lavoro dell'unità, i permessi con cui gira l'esecutore, i cancelli che il motore esegue prima che qualcuno riveda un diff, il giro di revisione, la pubblicazione, il grafo di ripresa, il ricognitore, il rebase, gli adattatori degli harness, l'esecutore locale, OpenClaw come harness con il suo specchio Workboard, l'adattatore DeepSeek Harness, e il ritiro di un'unità finita.

**Stato: anteprima.** I verbi funzionano e sono coperti dai test, ma il supervisore guida ancora ogni passo.
Leggi "Cosa garantiscono i cancelli, e cosa no" prima di fidarti di un PASS.

`sup` è un supervisore frontier che guida worker economici su abbonamenti flat, scelti per corsia con margine di quota (`nexgen quota`).
Il brainstorming tra modelli frontier non è `sup`: è il Council (`docs/it/council.md`).

## Dove vive un compito

`nexgen-local sup start "<objective>"` crea una cartella sotto lo stato della corsia, `~/.local/state/nexgen/local-lane/supervision/<id>/`, dove l'id è un timestamp più uno slug dell'obiettivo. La cartella contiene quattro file:

| file | proprietario | cosa contiene |
|---|---|---|
| `00-obiettivo.md` | il motore | l'obiettivo, l'id e quando è iniziato |
| `01-diario.jsonl` | il motore | una riga JSON per evento, solo in aggiunta |
| `20-brief-MODELLO.md` | il supervisore | i campi del brief, vuoti, uno per unità |
| `30-resoconto-MODELLO.md` | l'esecutore | i campi del resoconto, vuoti |

Un'unità aggiunge `20-brief-<unit>.md` (il brief come scritto), `22-ambiente-<unit>.md` (dove sta la copia, su quale ramo, dentro quale confine), `40-pacchetto-<unit>.md` e `50-gate-<unit>.txt` (il pacchetto di revisione e l'output grezzo dei cancelli), più la propria copia di lavoro in `23-unita-<unit>/`.
Una revisione aggiunge `45-revisione-<unit>-<round>.md`, e ogni richiesta che il motore rifiuta di eseguire finisce in `07-richieste-proprietario.md`.

Il diario è la verità della macchina. I suoi eventi sono `started`, `unit_created`, `unit_started`, `unit_queued`, `attached`, `harness_session_lost`, `harness_session_foreign`, `unit_reworked`, `unit_model_refused`, `unit_stalled`, `unit_resumed`, `unit_rebased`, `unit_collected`, `report_relocated`, `unit_reviewed`, `unit_published`, `unit_publish_failed`, `unit_retired`, `unit_retire_failed`, `session_closed`, `unit_unjobbed`, `turn_reaped`, `turn_unguarded`, `workboard_card`, `workboard_moved`, `local_server_started`, `local_server_stopped`, e per il ricognitore `scout_opened` e `scout_finished`.
I file Markdown sono quello che una persona legge e scrive, e il motore non li riscrive mai, quindi una modifica non va persa.

## I verbi

```bash
nexgen-local sup start "porta la fase 2 dentro il motore"   # id and folder
nexgen-local sup status                                     # every task, one line each
nexgen-local sup status <id>                                # units, states, verdicts, costs
nexgen-local sup unit <id> <unit> --repo <checkout> --base <ref> --brief <file> \
    --allow 'docs/*.md' [--allow ...] [--verify "<command>"] [--mcp <server>]
nexgen-local sup run <id> <unit> [--harness <name>] [--tab]  # un turno senza finestra; --tab apre una scheda
nexgen-local sup collect <id> <unit>                        # il turno è finito, poi i cancelli e il costo
nexgen-local sup review <id> <unit> --approve "<why>" [--close]  # one review round, APPROVA
nexgen-local sup review <id> <unit> --rework <file>         # one review round, RIMANDA
nexgen-local sup review <id> <unit> --resend [-m <model>]        # deliver the last round that never arrived
nexgen-local sup publish <id> <unit> --repo <checkout> --branch <branch> \
    [--remote origin] [--push | --push-with "<command>"]
nexgen-local sup attach <id> <unit> --role esecutore --session <ses_...> [--harness <name>]
nexgen-local sup costi <id>                                 # total, then by role and by unit
nexgen-local sup costi                                     # the 5h, 7d and 30d windows
nexgen-local sup permessi <id>                              # what the session waits on
nexgen-local sup permessi <id> --per-profilo                # refuse everything waiting
nexgen-local sup permessi <id> --reject <request> [--allow <request> | --always <request>]
nexgen-local sup scout <id> "<question>" [--harness <name>]  # the explorer, on a detached copy
nexgen-local sup rebase <id> <unit>                          # realign on the base's current tip
nexgen-local sup resume <id> [--repo <checkout> --branch <branch>] [--riparti]
nexgen-local sup pulizia [--chiudi]                          # ciò che il motore ha lasciato aperto
nexgen-local sup window-check                               # opens a visible window and leaves it open
```

`sup attach` scrive l'evento nel diario e una riga in `05-costi.jsonl`.
`sup status` e `sup costi` non calcolano mai un prezzo da soli: leggono quello che l'harness ha riportato.

### Da OpenClaw

Il comando del plugin `/sup` risponde con le stesse righe che stampa un terminale: senza argomento dà ogni compito con le sue unità e la spesa delle sessioni, con un id dà quel compito.
È di sola lettura, ed esegue `nexgen-local sup status --json` e `sup costi --json`, così telefono e scrivania non possono discordare.

I verbi che cambiano qualcosa sono tool, uno per verbo: `nexgen_sup_start`, `nexgen_sup_unit`, `nexgen_sup_run`, `nexgen_sup_collect`, `nexgen_sup_review` e `nexgen_sup_publish`.
Ognuno è un involucro sottile del verbo sopra e restituisce le righe stampate dal motore.
Sono registrati `optional` nel plugin, che in OpenClaw significa "non offerto a nessun modello finché un operatore non mette il nome esatto in allowlist".
`nexgen openclaw setup` scrive quella allowlist sull'agente supervisore (`capo`) e su nessun altro, come `agents.entries.capo.tools.alsoAllow`.
Se quell'agente ha già un proprio `tools.allow`, il motore lo lascia stare, perché OpenClaw rifiuta `allow` e `alsoAllow` nello stesso ambito, e `nexgen openclaw plan` nomina i tool da aggiungere lì.

Due tool di sola lettura sono disponibili a qualsiasi agente: `nexgen_sup_status` e `nexgen_sup_costs`.
La guardia del motore vede ognuna di queste chiamate su `before_tool_call`, come ogni altra chiamata a un tool.

## Chi fa cosa

Il supervisore è chiunque possa eseguire i verbi `sup`: una persona, una sessione Claude Code, una sessione OpenCode con la propria finestra.
Al motore non importa quale, perché l'autorità del supervisore sono i verbi e i cancelli, non il modello che li digita.
Ogni passo del supervisore è un file che scrive o un comando che esegue, così chi legge dopo può ricostruire la decisione.

Gli esecutori e il ricognitore sono adattatori, un modulo per harness sotto `supervision_adapters/`.
Il motore non nomina mai un harness nella sua orchestrazione: chiede al registro, e l'adattatore dichiara cosa sa fare.
Un'operazione che un harness non supporta è una capacità messa a falso, e il motore rifiuta con quel nome invece di fingere.
Un ruolo si configura una volta nella corsia, `supervision.roles`, e una singola run può sostituirlo con `--harness`:

```json
{
  "supervision": {
    "roles": {"esecutore": "opencode", "ricognitore": "agy"}
  }
}
```

Quei due valori sono i predefiniti quando la chiave manca.
Un nome che nessuno ha registrato viene rifiutato prima che parta un turno, nominando l'errore di battitura e i nomi che esistono.
`sup run --harness <name>` e `sup scout --harness <name>` usano quell'harness solo per quella run, e il diario registra quale ha davvero eseguito l'unità, così la consegna della revisione torna alla stessa sessione.

| harness | apre una scheda | headless | trova la sessione | invia un turno | stato | costo | token | scrive i permessi | ripresa | ricognizione | libera una cartella | ferma un push | mcp |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `opencode` | sì | sì | sì | sì | sì | sì | sì | sì | sì | sì | sì | profilo di progetto + pushurl bloccata | sì |
| `local` (OpenCode + provider locale) | sì | sì | sì | sì | sì | sì | sì | sì | sì | sì | sì | profilo di progetto + pushurl bloccata | sì |
| `claude` | sì | sì | sì | sì | sì | sì | sì | sì (`.claude/settings.json`) | sì | sì | no | profilo di progetto + pushurl bloccata | no |
| `codex` | sì | sì | sì | sì | sì | no (detto illeggibile) | sì | no (sandbox di avvio) | sì | sì | no | `workspace-write` senza rete + pushurl bloccata | no |
| `openclaw` | sì (una sessione, non una finestra) | sì | sì | sì | sì (l'involucro del turno) | sì | sì | no | sì | no | no | solo pushurl bloccata | no |
| `agy` | sì | sì | sì | sì | sì (tabella dei processi) | no | no | no | sì | sì | no | solo pushurl bloccata | no |
| `dsh` | no (sempre headless) | sì | sì (dal NDJSON del turno) | sì (`--session-id`) | sì (tabella dei processi ed evento `final`) | no | sì | no | sì (il turno dopo) | no | no | solo pushurl bloccata (preset workspace-write, approvazioni negate) | no |

La tabella risponde a "questo harness sa farlo", e non promette che ogni operazione sia utile.
`agy` apre una scheda e scrive il suo resoconto, e trova la sua sessione cercando la cartella della copia o il brief nel database SQLite locale di agy (`~/.gemini/antigravity-cli/conversations/<uuid>.db`) in sola lettura, rifiutando quando è ambiguo.
Ora sa mandare un turno nella conversazione (`agy --mode accept-edits --conversation <id> --print <pointer>`), quindi le correzioni non riaprono una scheda interattiva quando l'unità è partita headless.
Il suo stato occupato si legge dalla tabella dei processi: un processo `agy` con l'id della conversazione nella riga di comando.
Codex sa eseguire e riprendere, ma il suo costo non è esposto in una forma che il motore legga, quindi il pacchetto lo dice invece di scrivere zero.
`openclaw` non apre nessuna finestra propria: la sua sessione è la superficie di lavoro, e la sezione qui sotto dice come viene guidata.
La colonna "ferma un push" è il vero stop dietro le altre: un harness senza profilo di progetto gira comunque in una copia la cui `pushurl` punta a un URL bloccato.

La colonna `mcp` dice se il profilo nella copia può scegliere i server MCP.
Un'unità parte senza nessuno: il supervisore nomina quelli che servono con `sup unit ... --mcp <name>`, ripetibile, e il diario tiene la lista, che `sup status` mostra.
Per un worker OpenCode il profilo è l'`opencode.json` della copia, e spegne ogni server che la configurazione globale del proprietario dichiara e che l'unità non ha ricevuto.
La voce usa la forma nativa di OpenCode 2 (`mcp.servers.<name>` con `disabled`, `type` più `command` o `url`: i campi che `opencode.ai/config.json` richiede a `Mcp.LocalConfigEncoded` e `Mcp.RemoteConfigEncoded`), e ripete il trasporto della definizione globale così resta valida sia che OpenCode unisca le due configurazioni sia che lasci alla voce di progetto il compito di sostituire quella globale.
Variabili d'ambiente, header e credenziali OAuth non vengono copiati: restano nel file globale, che viene solo letto e mai scritto.
Un server che l'unità ha chiesto e che il file globale disattiva viene riacceso nello stesso punto, e un nome che nessuna configurazione dichiara ferma la run con un messaggio che elenca i server esistenti, invece di lasciare l'unità senza il server che ha chiesto.
Claude Code è `no` perché il suo unico interruttore, `--strict-mcp-config`, è un flag di avvio e non un file di progetto; Codex e Antigravity non scrivono alcun profilo di progetto.

## OpenClaw come harness e il Workboard

OpenClaw è l'harness del proprietario: agenti, sessioni mostrate nella sua Control UI e sul suo telefono, e un Workboard di schede.
La supervisione può guidarlo invece di aprire una scheda di terminale, e `--harness openclaw`, oppure `supervision.roles`, è tutto quello che serve per mettere lì un'unità.

**Un turno, una chiave di sessione scelta dal motore.** L'adattatore esegue il turno documentato della CLI:

```bash
openclaw agent --agent <id> --session-key sup-<task>-<unit> --message "<pointer>" --json
```

La chiave è costruita dal compito e dall'unità, così l'aggancio è una ricerca e non una corsa, e la Control UI mostra esattamente il nome che il diario registra.
Il motore non apre nessuna finestra: la sessione è la superficie, il proprietario la guarda dove già guarda i suoi agenti, e una correzione è lo stesso comando di nuovo sulla stessa chiave.
L'agente è `sviluppo` a meno che la corsia non ne nomini un altro:

```json
{
  "supervision": {
    "roles": {"esecutore": "openclaw"},
    "openclaw": {"agent": "sviluppo", "workboard": true}
  }
}
```

**La cartella è il limite onesto.** Un turno che passa dal gateway esegue i suoi tool nella cartella di lavoro dichiarata per quell'agente (`agents.entries.<agent>.cwd`, oppure `agents.defaults.cwd`) e nel suo workspace altrimenti; la CLI documentata non ha un flag per un'altra cartella, e un percorso sull'host fuori da entrambe le radici richiede `operator.admin`.
Quindi il motore chiede a OpenClaw entrambe le cartelle prima che parta qualcosa, accetta la copia dell'unità quando sta sotto una delle due, e rifiuta indicando entrambe le vie pulite quando non lo è:

```bash
openclaw config set agents.entries.<agent>.cwd '~/.local/state/nexgen/local-lane/supervision'
```

Ogni cartella di compito e la sua copia `23-unita-<unit>/` stanno così sotto quella radice, e il bootstrap dell'agente (`AGENTS.md`, `SOUL.md`, memoria) resta dov'è, ed è per questo che `cwd` è il cambiamento più piccolo: puntare `agents.entries.<agent>.workspace` alla cartella del compito sposta anche tutto quello.
La terza via è `openclaw workboard dispatch --admin`, per una cartella fuori da ogni radice configurata.
Due limiti arrivano con la prima: un `cwd` distinto richiede una run senza sandbox (`agents.entries.<agent>.sandbox.mode: off`), perché un turno in sandbox rifiuta una cartella di lavoro diversa dal suo workspace, e la cartella dei tool è la radice del compito, non la copia, quindi ogni percorso nel brief va scritto assoluto.
Una configurazione illeggibile non è una cartella dichiarata: il controllo ripiega sul workspace e rifiuta, così una lettura fallita non diventa mai un permesso.
Un turno che sembra lavoro nell'unità e non lo è: è il guasto che questo rifiuto esiste per prevenire.

**Da dove vengono i numeri.** `openclaw sessions --json` porta i token, il modello e l'orologio.
I soldi di una run non sono in quella lista: sono nell'involucro `--json` che il turno stampa, quindi l'involucro di ogni turno viene aggiunto come una riga di `<lane>/openclaw/<key>.jsonl` e le righe vengono sommate.
Un turno che non ha stampato un costo lascia il costo assente e i token restano al suo posto, con la nota che lo dice; nulla viene mai scritto come zero.
Un processo che porta ancora la chiave significa che il turno sta lavorando; quando esce, la sessione è inattiva.
Pubblicare un'unità, o ritirarla, archivia la sua sessione OpenClaw (`openclaw sessions archive`), e un rifiuto lì è una riga, non un verbo fallito.

**Il Workboard è uno specchio, mai la verità.** Con `openclaw` sulla macchina e lo specchio attivo, `sup unit` crea una scheda per l'unità, con titolo `unità <unit> · <task>`, note che portano la copia, il ramo e il brief in breve, etichette con il compito e l'unità, assegnata all'agente harness. I verbi la muovono:

| passo del motore | stato della scheda |
|---|---|
| `sup unit` | `todo` |
| `sup run` | `running` |
| `sup collect` | `review` |
| `sup review --rework` | `running` |
| `sup publish` | `done` |
| `sup review --approve --close` (ritirata) | `blocked`, con il motivo nella riga |

Il diario resta lo stato: la scheda è una vista, i movimenti sono il meglio che si può, e un tabellone assente, spento (`supervision.openclaw.workboard: false`) o che rifiuta costa una riga stampata e nulla più.
La CLI non ha un verbo di archiviazione, quindi un'unità ritirata è `blocked` con il suo motivo invece di essere archiviata.
Ogni chiamata è `... --json`, e l'interruttore di test del motore (`NEXGEN_DISABLE_HOST_MUTATIONS=1`) ferma lo specchio prima che si cerchi qualcosa, così nessun test scrive in un tabellone vero.

## La copia di lavoro dell'unità

`sup unit` crea un worktree git dal commit risolto di `--base`, su un ramo locale `sup/<task>/<unit>`, dentro la cartella del compito (`23-unita-<unit>/`).
Una cartella per compito significa una pulizia sola, e accanto ai checkout del proprietario non compare nulla.
Il ramo nasce da un commit e non da un nome di ramo, così un ramo che si muove dopo non può cambiare quello che è stato revisionato.

Il push è bloccato dentro quella copia soltanto, con l'unico meccanismo che git offre per worktree: `extensions.worktreeConfig`, un interruttore a livello di repository che non cambia nulla finché non esiste una configurazione di worktree, più `remote.<name>.pushurl = nexgen-sup-disabled://push-blocked` nel `config.worktree` del worktree stesso.
Un push dalla copia fallisce con `remote helper 'nexgen-sup-disabled' aborted session`, e il checkout del proprietario tiene il suo remote vero.
Gli hook git condivisi non vengono mai toccati: vivono nella cartella comune e restano esattamente come li ha installati il proprietario.

`--mcp <name>` è come il supervisore dà all'unità un connettore che le serve davvero.
Senza, il profilo della copia non ha nessun server MCP, e ogni server globale viene spento nella copia per nome, come dice la colonna `mcp` qui sopra: i connettori che il proprietario usa nelle proprie sessioni non sono il budget dell'esecutore.

L'URL di fetch e l'URL di push bloccato di ogni remote vengono scritti nel diario quando l'unità viene creata, e i suoi ref vengono letti con `git ls-remote` su quell'URL.
`sup collect` fa tre domande: i remote della creazione sono ancora configurati con lo stesso URL di fetch e lo stesso URL di push bloccato, l'unità ha aggiunto un remote proprio, e qualche ref remoto punta a un commit del `base..head` di questa unità?
`remote.<name>.pushurl` vince sull'URL di fetch per ogni push, quindi ripuntarlo era un push che il cancello non poteva vedere.
Un remote rimosso, aggiunto o ripuntato è un FAIL che lo nomina, un commit spinto è un FAIL che nomina il ref che lo porta, e un ramo remoto avanzato per un altro motivo, perché è stata pubblicata un'altra unità, non è un guasto: il cancello cerca i commit di questa unità, non un mondo che è rimasto fermo.

La copia non deve sopravvivere alla sua unità: la pubblicazione e un'unità approvata e chiusa senza pubblicazione la rimuovono entrambe, e `sup pulizia` elenca ogni copia o ramo che il diario ha già chiuso e il disco ha ancora.
"La fine del lavoro di un'unità" qui sotto è la regola completa, e il `git worktree remove` manuale è ormai il passo di recupero per una copia che un processo vivo sta tenendo.

## Il turno dell'esecutore

Un harness significa una sessione per compito, e la sessione appartiene alla copia in cui è nata: il motore la riusa solo finché la cartella registrata è la copia di quell'unità.

`sup run` cerca la sessione che il compito possiede già.
Se ce n'è una ed è inattiva (`time.idle` pari o successivo a `time.updated` nell'export dell'harness), il lavoro dell'unità ci entra con `opencode run -s <session> "<pointer>"` e non si avvia nessun turno nuovo.
Se è occupata, l'unità va in coda: il diario registra `unit_queued` con il motivo, `sup status` mostra `in coda`, e il motore lo dice invece di avviare un altro turno.
Un turno nuovo si avvia, senza finestra, quando il compito non ha ancora una sessione per quell'harness, o quando la sessione registrata appartiene alla copia di un'altra unità:

```
opencode run "<pointer>"
```

Il prompt è un puntatore a `20-brief-<unit>.md`, mai il brief stesso, e non viene mai passato un flag `--auto` o `--dangerously-*`: il modello resta quello configurato dal proprietario, e i permessi sono il profilo descritto nella sezione successiva, scritto prima che il turno parta.
La riga di comando è dell'adattatore: un altro harness avvia il proprio turno con i propri flag, e `sup run --harness <name>` ne sceglie uno per questa run senza cambiare il predefinito della corsia.

La sessione che apre un turno nuovo si trova in `opencode session list --format json`, che riporta `id`, `created` e `directory`: la sessione più recente la cui `directory` è la copia dell'unità e il cui `created` è pari o successivo al momento in cui il turno è stato avviato.
Quando nessuna sessione corrisponde entro l'attesa (30 secondi), `sup run` lo dice e l'aggancio resta un `sup attach` manuale: il motore non indovina mai quale sessione appartiene a quale unità.
Il costo della sessione agganciata finisce in `05-costi.jsonl` come ogni altro aggancio.
La ricerca è dell'adattatore: OpenCode elenca il proprio archivio di sessioni, Claude Code legge la propria cartella di transcript, Codex legge il proprio archivio di thread, `openclaw` cerca in `openclaw sessions --json` la chiave scelta dal motore, `agy` legge il proprio database SQLite, e `dsh` legge l'id dal log NDJSON del turno.

Un harness che mostra le proprie sessioni non apre nessuna delle finestre del motore (`window=False` nella sua tabella di capacità): `sup run` avvia il turno da sé, staccato, e aggancia la sessione con la chiave che ha scelto o con l'id che il turno riporta, così l'aggancio non è un'induzione fatta da un orologio.
OpenClaw e `dsh` sono oggi quegli harness.

### Headless è il predefinito: `sup run` e `--tab`

`sup run <id> <unit>` avvia l'unità senza finestra: un turno one-shot senza finestra e senza scheda del terminale, avviato dalla copia dell'unità.
`--tab` è l'opzione esplicita che apre invece una scheda del terminale, il comportamento delle versioni precedenti.
`--headless` resta accettato ed è il predefinito.

`-m <model>` viaggia anche sul turno headless; non viene più rifiutato insieme a `--headless`.

Ogni harness ha una forma headless, e i turni dopo vanno nella stessa sessione:

- `opencode`: `opencode run [-m <model>] <pointer>`; poi `opencode run -s <session> ...`.
- `local`: come `opencode`.
- `claude`: `claude -p --session-id <uuid scelto dal motore> [--model <m>] --permission-mode acceptEdits <pointer>`; poi `claude -p --resume <id> ...`.
- `codex`: `codex exec -C <copy> -s workspace-write --add-dir <cartella del compito> --add-dir <git dir> [-m <m>] <pointer>`; poi `codex exec ... resume <id> <pointer>`. Il limite di Codex su Windows (la sandbox gira come utente separato) resta quello già documentato.
- `agy`: `agy --mode accept-edits [--model <id>] --print <pointer>`; poi `agy --mode accept-edits --conversation <id> --print <pointer>`. `--print` prende il prompt come proprio valore, quindi il prompt sta subito dopo e per ultimo. Verificato dal vivo: il secondo turno con `--conversation` ricorda il primo.
- `openclaw`: invariato, non ha mai aperto finestre.
- `dsh`: sempre headless, descritto sotto.

Il diario registra la consegna come `mode: "turn"`, non come scheda, e nessun processo porta `NEXGEN_SUP_TAB`: `sup collect`, `sup review` e `sup pulizia` non hanno schede da chiudere e non ne cercano.
Quello che il turno scrive su stderr finisce in `60-stderr-<session>.log` nella cartella del compito, il file che ogni turno one-shot già scrive; un turno la cui sessione non esiste ancora accoda in `60-stderr-session.log` finché il suo id non è noto.

Una correzione, una sessione persa o un `--resend` senza sessione aprono la sessione nuova nello stesso modo in cui l'unità era partita: in una scheda solo se l'ultimo avvio dell'unità era una scheda (`mode: "tab"` nel diario), altrimenti headless.
La riapertura di una scheda dopo `tab_closed` vale solo per le unità partite in scheda.

La sessione resta visibile dentro l'app del proprio harness: OpenCode (`opencode session list`), Claude Code (`claude --resume`), Codex, agy, DeepSeek Harness (`~/.dsh/sessions`, la stessa cartella che usa la sua app desktop), OpenClaw.

Un turno headless non ha bisogno di una sessione grafica, perché nessun terminale viene avviato: un supervisore che guida i verbi da un'applicazione senza terminale funziona allo stesso modo.
`--tab` invece la richiede, e su Linux rifiuta senza `DISPLAY` o `WAYLAND_DISPLAY`.

Headless non vuol dire invisibile: nessun terminale mostra il turno, ma la sua sessione è nell'app dell'harness mentre gira e dopo.
Una richiesta di permesso a cui nessuno risponde è il motivo per cui esiste il rilevatore di stallo, e il resoconto, la copia, la riga di costo e ogni cancello restano identici.

Il diario registra la cartella insieme alla sessione (`attached`, `unit_started`, `unit_queued`, `unit_reworked`).
Un'unità cerca prima la sessione registrata per quell'unità; l'ultima sessione del compito per lo stesso harness è un ripiego solo per un'unità che non ne ha mai avuta una.
Il motore riusa una sessione solo quando quella cartella è la copia dell'unità stessa; quando è la copia di un'altra unità scrive `harness_session_foreign` con entrambi i percorsi, e avvia un turno nuovo nella copia giusta solo quando l'harness dice che quella sessione è libera.
Mentre lavora, o mentre l'harness non riesce a leggerla, un giro di revisione va a `unit_queued` con il suo motivo e `--resend` lo consegna dopo.
Il guasto del 2026-10-24 sono state quattro chiamate `sup review --rework` a sessioni occupate, ognuna delle quali apriva una scheda nella stessa copia e ripuntava l'unità su quella nuova.
Il guasto del 2026-10-21 è l'altra metà: la run h5 ha ereditato la sessione inattiva di h4, la cui cartella era già stata rimossa, e l'esecutore ha visto "working directory no longer exists".
Una riga di diario scritta prima che la cartella fosse registrata non ha cartella, e resta riusabile come prima.

Una sessione che l'harness dichiara sparita, perché l'export dice che non esiste, non è una sessione occupata: mettersi in coda su quella sessione vorrebbe dire aspettare per sempre il nulla.
Il diario registra `harness_session_lost` con la sessione e il motivo, quella sessione smette di essere la sessione del compito, e il successivo `sup run` o giro di revisione avvia l'unico turno nuovo.
Un turno si avvia comunque una volta per harness, mai per tentativo.

Un timeout, un archivio bloccato o qualsiasi altra lettura fallita non sono lo stesso fatto: il motore ritenta l'export una volta, e se ancora non riesce a leggere la sessione dice `sessione non verificabile, riprova` e non apre nulla.
Trattare un guasto transitorio come una sessione persa ha lasciato due sessioni al lavoro sullo stesso compito.

## I permessi dell'esecutore

L'esecutore gira sotto un profilo che il motore scrive prima che parta qualsiasi sessione, perché il profilo si decide in anticipo invece di essere concesso richiesta per richiesta.
OpenCode 2 non ha una variabile d'ambiente di configurazione: un `opencode.json` di progetto nella cartella di lavoro è il meccanismo, quindi `sup run` e ogni consegna di revisione scrivono quel file dentro la copia dell'unità, tengono ogni altra chiave che il proprietario ci ha messo (il modello resta del proprietario) e aggiungono il file all'`info/exclude` della copia.
Un progetto che traccia `opencode.json` viene rifiutato invece di essere sovrascritto: il profilo non deve comparire in un diff.

Le regole sono una lista ordinata, e vince l'ultima corrispondenza.
OpenCode riconosce una modifica dentro la cartella della sessione dal suo percorso relativo (`docs/a.md`) e una fuori dal suo percorso assoluto canonico (`C:/.../fuori/vietato.txt`), e `*` corrisponde a qualsiasi sequenza di caratteri, separatori compresi.
Quindi la copia è la forma relativa, ogni forma assoluta viene chiusa dalla sua lettera di unità, la forma `..` viene chiusa per un percorso che esce dalla cartella ma resta nello stesso progetto, e la cartella del compito viene riaperta per ultima perché è lì che va il resoconto:

| azione | risorsa | effetto |
|---|---|---|
| `edit` | `*` | allow |
| `edit` | `?:/*` | deny |
| `edit` | `/*` | deny |
| `edit` | `../*` | deny |
| `edit` | `<task>/*` | allow |
| `shell` | `git push *` | deny |
| `shell` | `git -C * push *` | deny |
| `shell` | `git -c * push *` | deny |

Le modifiche sono ammesse nella copia e nella cartella del compito, e una modifica fuori da lì viene rifiutata.
I comandi sono ammessi tranne un push nelle sue tre grafie, e nulla viene approvato in automatico: una richiesta a cui il profilo non ha già risposto non viene concessa dal motore.
La `pushurl` bloccata della copia resta lo stop duro dietro questo, perché una shell porta l'autorità dell'utente.

Un push rifiutato arriva al modello come `Permission denied: shell`, non come errore git: la chiamata al tool non parte mai, quindi la sessione deve riportarlo come permesso negato e non come repository che rifiuta.

`opencode.json` finisce nell'`info/exclude` della copia, che in un worktree è il file comune del repository: la riga `/opencode.json` vale quindi anche nel checkout del proprietario, e nasconde quel nome anche lì quando il file esiste.

Gli altri harness ricevono la stessa decisione nella propria forma, e nessuno di loro ottiene un bypass generale:

- **Claude Code** legge un `.claude/settings.json` di progetto, scritto nella copia ed escluso con la stessa riga di `info/exclude`.
  Imposta `defaultMode: acceptEdits`, ammette `Edit`, `Write`, `Read` e `Bash(git *)`, nega le tre grafie del push (`Bash(git push *)`, `Bash(git -C * push *)`, `Bash(git -c * push *)`), e aggiunge la cartella del compito a `additionalDirectories` così il resoconto si può scrivere dove deve stare.
  `bypassPermissions` non viene mai usato.
- **Codex** parte con `-s workspace-write -a never`: le scritture sono confinate al workspace e nessuna richiesta di approvazione può diventare un sì.
  La sua capacità di negare un push è dichiarata più debole di quella di OpenCode: la sandbox non ha rete, quindi un push fallisce, ma la `pushurl` bloccata della copia è lo stop che non dipende dall'harness.
  Su Windows la sandbox scrive come utente separato (`CodexSandboxUsers`), quindi il proprietario non può leggere né cancellare il resoconto e la copia: Codex apre una scheda, trova il suo thread e riporta i token, ma il prodotto del lavoro non si può raccogliere.
  Il limite è dichiarato in `Capabilities.limits`; su Linux la sandbox non usa un utente separato.
- **agy** non ha un profilo di permessi di progetto che il motore possa scrivere.
  È un ricognitore, la sua modalità plan è di sola lettura, e la `pushurl` bloccata della copia è l'unico stop.

Un harness che non sa negare un push lo dice nella tabella delle capacità invece di essere descritto come sicuro.
`sup run` scrive il profilo prima che il turno parta e rifiuta quando il profilo non si riesce a scrivere, perché un esecutore senza il suo profilo non è l'esecutore che il proprietario ha configurato.

`sup permessi <id>` elenca quello che la sessione del compito sta aspettando (`opencode api session.permission.list`) senza cambiare nulla.
Una richiesta si può rispondere una alla volta con `--allow`, `--always` o `--reject`, oppure tutte insieme con `--per-profilo`, che rifiuta tutto: il profilo ha già deciso, quindi una domanda che compare ancora è fuori da lui.
Ogni risposta finisce nel diario come `permission_answered` con la sua decisione.

## L'esecutore locale

Un'unità può girare su un modello servito in locale da `llama-server`, attraverso l'adattatore OpenCode con il provider configurato nella corsia.
Il ciclo di vita del server è del motore, perché "prima avvialo a mano" è il passo che si dimentica:

- `sup run` avvia il server quando `/health` non risponde, usando `supervision.local_model.command`, dopo aver controllato la RAM libera e, quando la corsia dichiara una soglia, la VRAM libera.
  Il motore registra di aver avviato il server nel `local-model-server.json` della corsia e nel diario.
- `sup collect` ferma un server che il motore ha avviato, a meno che un'altra unità della corsia non ne abbia ancora bisogno.
  Un server avviato a mano dal proprietario resta dov'è: il motore ferma solo quello che ha avviato.
- Una macchina che non sa rispondere alla domanda sulla memoria viene rifiutata prima che il modello sia caricato.
  Una soglia illeggibile non è una macchina libera.

Il brief di un'unità locale è un piano passo per passo, non un obiettivo: il modello esegue, non progetta.
Il brief nomina ogni file e ogni modifica, e porta i test di accettazione già scritti e rossi.
Il confine dell'unità esclude i file di test, quindi l'esecutore può cambiare solo codice di produzione finché quei test non diventano verdi.
È lo schema "test di un altro, codice del locale":

| chi | scrive | l'esecutore locale può cambiarlo |
|---|---|---|
| il supervisore o un modello frontier | il brief, i test di accettazione | no (fuori da `--allow`) |
| il modello locale | il codice di produzione | sì, dentro `--allow` |

Lo schema esiste per una run misurata il 2026-10-21: il modello locale ha scritto bene una piccola correzione di produzione, ma i suoi test erano inutili, ricerche di stringhe nel sorgente o codice che non compilava, e il suo resoconto dichiarava "100 passed" senza averli eseguiti.
Test scritti da qualcun altro e rossi prima che l'unità parta sono il modo più economico di rendere verificabile quella dichiarazione: `sup collect` esegue gli stessi test, e una run verde è un fatto, non una frase.

Il modello è nominato in `local_model.model` della corsia, e `sup run --harness local` seleziona l'adattatore locale per un'unità. Il diario registra l'harness, così la consegna della revisione torna alla stessa sessione.

## L'adattatore DeepSeek Harness (`dsh`)

`dsh`, registrato anche come `deepseek-harness`, è sempre headless (`window=False`) e non apre mai una finestra:

```bash
dsh --profile headless --patch <file> --permission-mode workspace-write --approval-policy never \
    --add-dir <cartella del compito> --add-dir <git dir> --json <pointer>
```

I turni dopo aggiungono `--session-id session-<uuid>`.

Il modello che `sup` chiede passa in una patch YAML scritta per quella chiamata: `agent-default-model` con `provider` e `model`, insieme a `llm-pi-ai` con la variabile della chiave e tre interruttori privacy (il session log verso DeepSeek spento, la telemetria di sessione OTel `DISABLED`, il plugin package inventory verso DeepSeek spento).
Verificato dal vivo su DeepSeek Harness 0.2.2-rc.5: la patch sovrascrive davvero il modello, e la sessione registra il modello chiesto; un modello che il provider non ha viene rifiutato da dsh con `UNKNOWN_MODEL`, senza ripiego su un altro modello.

Il predefinito è `opencode-go/deepseek-v4.1-flash`.
Un modello senza `provider/` viene rifiutato prima che il turno parta, e così una corsia senza chiave.
La chiave si cerca in `OPENCODE_GO_API_KEY` per `opencode-go`, in `OPENCODE_ZEN_API_KEY` per `opencode`, poi in `OPENCODE_API_KEY`, nell'ambiente e nel deposito dei segreti, e viene passata al processo come `OPENCODE_API_KEY`.

Il primo turno lascia l'id della sessione a dsh: il motore lo legge dal primo evento `session` nel log NDJSON del turno (`<lane state>/dsh-turns/*.ndjson`) e lo aggancia.
Riporta i token (`step_end.usage.totalTokens`), non il denaro, e l'ultimo turno si legge dall'evento `final`.
Le sessioni restano nello store dell'utente, `~/.dsh/sessions`, quindi l'app desktop le mostra.

Il launcher è `dsh` sul `PATH`, altrimenti `%LOCALAPPDATA%\Programs\DeepSeek Harness\resources\runtime\cli\bin\dsh.cmd`.
Il codice condiviso con il seggio dsh del Council è `nexgen_core/dsh_cli.py`.

È una developer preview: ogni release candidate può cambiare flag e formato, e l'adattatore rifiuta in modo esplicito le forme che non riconosce invece di indovinare.
Limite noto, visto dal vivo su Windows con la 0.2.2-rc.5: il sandbox `workspace-write` non lascia scrivere gli oggetti git nel `.git` del checkout nemmeno quando quella cartella è passata con `--add-dir`, quindi i file arrivano nella copia e il commit no.
Finché l'harness non lo corregge, un'unità `dsh` su Windows va bene per lavoro il cui risultato sono i file, e il commit resta al supervisore.

## La quota, prima di partire

Prima che un'unità parta, `sup run` legge le righe di quota gratuite del piano che il suo esecutore spenderebbe (`nexgen_core.quota.percentage_rows`: l'archivio dell'harness, i limiti che Codex riporta da solo e il backend opzionale dalla cache che condivide con il beat orario).
Non apre nessun transcript e non avvia nessun modello.
Al massimo costa una corsa di quel backend ogni dieci minuti, chiunque abbia chiesto per ultimo.

Quando la lettura è corrente e pari o sopra la linea che il proprietario ha dichiarato in `failover.block_at` (95% se quel file non dice altro), l'unità non parte lì.
È il numero del plugin, non un secondo numero: lo stesso file alimenta `before_model_resolve`, quindi un supervisore che leggesse la soglia dell'avviso continuerebbe a mandare unità su un piano che il plugin aveva già smesso di usare.
`routing_tiers` sceglie il candidato worker successivo, saltando ogni modello del piano speso, perché un provider a quella linea è speso per tutto quello che serve, e il diario guadagna una riga `unit_quota` che nomina la lettura, la decisione e dove è andata l'unità:

```
unità <unit>: quota al 97% (5h): passa a gemini-3.8-flash
```

Una lettura vecchia, in cache, assente o illeggibile non blocca mai niente: viene registrata come `quota unknown` e l'unità parte dove stava per partire.
Il motore non ha mai inventato una percentuale, e un dubbio è un motivo peggiore per fermare un'unità di nessun motivo.
Un piano al 97% senza nessun'altra destinazione parte lo stesso, e la stessa riga lo dice.
Un'unità che parte headless può spostarsi su un altro harness solo quando quell'harness sa partire senza finestra, cosa che oggi sanno fare tutti gli adattatori: lo spostamento avviene e il diario registra dove è andata l'unità.
Quando un adattatore non lo sa, lo spostamento viene registrato come non fatto (`decision="headless"`, nota `<harness> non sa partire senza finestra, resto qui`) invece di fallire più tardi a nome di un harness che il proprietario non ha chiesto.

Questo controllo richiede `routing-tiers.yaml`: senza quel file la corsia non ha una lista di candidati verso cui spostarsi, e gira esattamente come dicono le sue impostazioni.
`sup status` non ripete la lettura; il diario è il posto dove vive.

## Sessioni in stallo

Una sessione attiva che non si è mossa è l'altro modo in cui un esecutore sparisce.
`sup status` chiede all'harness ogni unità ancora in corso, e la chiama in stallo quando il `time.updated` della sessione è più vecchio della soglia della corsia e l'ultima riga di diario dell'unità è più vecchia della stessa misura: una sessione il cui orologio discorda da questa macchina non viene accusata solo per quello.
Una sessione che l'harness dichiara ferma è finita, non in stallo, e una sessione che nessuno sa misurare viene lasciata stare.

La soglia è `supervision.stall_minutes`, venti minuti quando non è scritta.
Viene scritta una riga `unit_stalled` per ogni immobilità, con chiave unità, sessione e ultimo aggiornamento della sessione stessa, quindi chiedere di nuovo non scrive nulla; una sessione che si muove e si blocca di nuovo prende una riga nuova, e un'unità che torna al lavoro prende `unit_resumed`.
Questo controllo gira quando qualcuno chiede `sup status`, e si limita a riferire: il turno lo chiude prima il suo guardiano, come descritto in "Chiudere i processi di un turno".
La riga dice:

```
unità <unit>: in stallo da 24.3 minuti (soglia 20), possibile richiesta di permesso o turno appeso
```

## Le impostazioni della corsia

La corsia legge `~/.local/state/nexgen/local-lane/config.json`, sotto la home del motore quando `NEXGEN_HOME` è impostata.
Oggi vengono letti tre gruppi, tutti facoltativi:

```json
{
  "supervision": {
    "allowed_models": ["provider/*", "provider/model"],
    "stall_minutes": 20,
    "turn_timeout_minutes": 240,
    "idle_timeout_minutes": 15,
    "roles": {"esecutore": "opencode", "ricognitore": "agy"},
    "local_model": {
      "model": "provider/model",
      "command": ["llama-server", "-m", "<modello>", "--port", "8080"],
      "port": 8080,
      "health": "http://127.0.0.1:8080/health",
      "ram_free_gb": 2.0,
      "vram_free_gb": 0.0
    }
  }
}
```

`roles` è descritto in "Chi fa cosa".
`turn_timeout_minutes` è la scadenza di un turno: quando passa, il guardiano chiude il turno e tutto quello che ha lasciato, come descritto in "Chiudere i processi di un turno". È 240 minuti quando non è scritta.
`idle_timeout_minutes` è quanto a lungo un turno può non dare segni di vita prima che il guardiano lo chiuda e lo rilanci una volta. È 15 minuti quando non è scritta.
`local_model` è il server su cui gira un'unità `local`: `command` è l'unico campo senza valore predefinito, `port` costruisce l'URL di health quando `health` manca, e le due soglie sono la memoria libera che il motore richiede prima di avviarlo.
Quando `vram_free_gb` è zero il controllo della VRAM viene saltato, perché una macchina senza un `nvidia-smi` utilizzabile non è una macchina senza GPU.
Una soglia che non si riesce a leggere è un rifiuto, mai un via libera.

Un `allowed_models` vuoto o assente significa nessuna restrizione.
Una lista che esiste viene confrontata con il tag che l'harness riporta, `provider/model#variant`, con glob in stile shell, quindi `provider/*` copre ogni modello di quel provider e ogni variante di ognuno.
Con una lista, `sup run` rifiuta una sessione fuori da quella lista prima di inviare qualsiasi cosa (`unit_model_refused`, nominando il modello) e `sup collect` fa fallire il cancello `modello consentito` sullo stesso fatto.
Una sessione che nessuno ha agganciato, o il cui modello non si riesce a leggere, non è su una lista che esiste: "non verificato" non è "ammesso".

## I cancelli del motore

`sup collect` giudica da sé il `base..head` dell'unità.
Il resoconto dell'esecutore non è una prova; ogni riga qui sotto viene letta da git o eseguita dal motore.

| cancello | fallisce quando |
|---|---|
| `confine` | un file toccato, compresi entrambi i nomi di una rinomina, è fuori da ogni glob di `--allow` |
| `file vietati` | `VERSION`, `CHANGELOG.md` o `.github/**` sono cambiati e il confine non li nomina; contano entrambi i nomi di una rinomina |
| `integrità dei test` | un test esistente è stato cancellato, ridotto, saltato o rinominato fuori dal rilevamento (`nexgen_core/discipline/integrity.py`, riusato); un'unità il cui confine contiene `.nexgen-discipline.toml` viene giudicata commit per commit con la configurazione del genitore di ogni commit, quindi un commit che aggiunge l'autorizzazione e toglie il test insieme fallisce, e il motivo del PASS nomina i file ritirati e quanti casi ha ceduto ciascuno |
| `prova per commit` | un commit `fix:` non ha un test che fallisce sul suo genitore (`nexgen_core/discipline/regress.py`, riusato) |
| `identità` | un commit dell'intervallo dell'unità ha autore o committer diverso dall'identità registrata alla creazione; un'unità creata prima di questo controllo, senza identità registrata, passa con una nota |
| `verifica indipendente` | il comando dichiarato manca o esce con codice diverso da zero; è un comando solo, senza operatori di shell (`&&`, `\|\|`, `\|`, `;`, `&`), il suo eseguibile deve venire da fuori della copia dell'unità, un interprete Python parte con `-P` e un ambiente ripulito da `PYTHONPATH`, `PYTHONSTARTUP`, `PYTHONHOME`, `PYTHONUSERBASE`, `PYTEST_ADDOPTS` e `PYTEST_PLUGINS` (la cartella site dell'utente viene lasciata stare, perché è fuori dalla copia), e la run ha un tetto di 1800 secondi che `NEXGEN_SUP_VERIFY_TIMEOUT_S` sposta per una macchina più lenta (un valore che non è un numero positivo viene ignorato) |
| `test raccolti` | la testa raccoglie meno test della base e il file di un node id perso non è nel `test_removal_ok` della base, oppure una raccolta non sa rispondere: i due alberi vengono esportati con `git archive` (senza `.git` dentro), raccolti con `python -P -m pytest --collect-only -q` nello stesso ambiente ripulito, e i due insiemi di node id vengono confrontati con un pavimento che è il conteggio della base stessa, quindi una testa che raccoglie almeno altrettanti test passa e un test rinominato o riparametrizzato viene nominato solo come informazione; un file che il proprietario ha autorizzato viene ritirato e il motivo dice quanti test e da quali file, un aumento è normale, e il `.nexgen-discipline.toml` della testa non viene mai letto, perché un'unità non autorizza se stessa; un'unità il cui confine contiene quel file viene invece giudicata commit per commit, quindi un node id perso è accettato solo dove un commit che ha abbassato il proprio conteggio ha ereditato dal genitore l'autorizzazione del suo file, e una perdita che nessun commit giustifica resta un FAIL che la nomina |
| `lint` | il progetto ha `03-INFRA/scripts/ruff_baseline_check.py` e la copia aggiunge un gruppo di segnalazioni `(file, code)` che la sua baseline committata non aveva; la copia decide solo se il cancello esiste e cosa la sua baseline permette, mentre lo script che gira è quello del motore, quindi un'unità non può indebolire il controllo |
| `configurazione git` | uno dei due file di configurazione della copia è cambiato da quando l'unità è stata creata (il `.git/config` condiviso del repository, che ogni checkout legge, oppure il `config.worktree` del worktree), o la configurazione effettiva porta una chiave `url.*.insteadOf` o `url.*.pushInsteadOf` che la creazione non aveva; vengono nominati il file o la chiave |
| `remoto intatto` | un remote della creazione è sparito, è stato aggiunto, punta a un altro URL di fetch o di push, oppure uno dei suoi ref porta un commit dell'intervallo dell'unità (l'unità ha spinto); un ramo avanzato per un altro motivo non è un guasto |
| `albero pulito` | la copia dell'unità ha lavoro non committato |
| `scansione fughe` | il progetto dichiara la scansione anti-fuga della CI (un workflow nomina `leak_scan.py`) e la scansione esce con codice diverso da zero sul `base..head` dell'unità, oppure lo script e le sue regole (`leak-scan/gitleaks.toml`) non sono dove il workflow dice; un progetto che non dichiara nessuna scansione non aggiunge righe |

Il verdetto è PASS solo quando ogni riga lo è.
Il pacchetto (`40-pacchetto-<unit>.md`) porta il verdetto, le righe dei cancelli, le voci di `test_removal_ok` aggiunte dall'unità, il diffstat, il diff con un tetto dichiarato di 400 righe e il costo della sessione agganciata.
`50-gate-<unit>.txt` porta l'output grezzo, compreso quello stampato dalla verifica.
`sup collect` esce con 1 su FAIL, così uno script lo vede.

Il proprietario autorizza un ritiro nominando il file in `test_removal_ok` dentro `.nexgen-discipline.toml` sulla base, il percorso che `integrity.py` documenta in `HOW_TO_ALLOW`.
Un'unità può portare quel file da sé solo quando il supervisore ha scritto il suo percorso dentro il confine dell'unità alla creazione (`--allow .nexgen-discipline.toml`, o un glob che lo copre): il confine è l'`allow` del diario, quindi un'unità non può darsi il permesso da sola.
Con quel file, entrambi i cancelli dei test percorrono i commit dell'unità come fa l'hook del progetto, con la configurazione del genitore di ogni commit, e un commit che aggiunge l'autorizzazione e toglie il test insieme fallisce.
Senza, la copia della base è l'unica che viene letta e `confine` fa fallire il file.
Il pacchetto elenca le voci aggiunte dall'unità in una sezione propria, perché i cancelli dicono se un ritiro autorizzato è avvenuto, non che qualcuno fosse d'accordo.

Ogni comando git che il motore avvia porta `-c core.longpaths=true`, da un'unica costante in `nexgen_core/discipline/gitutil.py`.
Su Windows git dichiara un argomento prima di risolverlo, quindi una copia dell'unità profonda più un percorso di test lungo faceva fallire il cancello `prova per commit` con `fatal: failed to stat '<sha>:<path>': Filename too long` il 2026-10-24; l'interruttore è inerte su POSIX.

La riga delle fughe è la stessa scansione che gira in CI, sullo stesso intervallo, spostata al punto in cui l'esecutore può ancora correggere: un nome personale in un percorso finto o un numero di sette cifre in un test fermavano il push, dopo che la revisione aveva già detto sì.

Il modello che scrive il brief dice all'esecutore cosa eseguire mentre lavora: i test dei file che ha toccato, `discipline.py check`, `ruff_baseline_check.py` e la scansione delle fughe.
Non l'intera suite: `sup collect` la esegue in modo indipendente, e chiederla all'esecutore due volte raddoppiava l'attesa senza aggiungere un fatto di cui il motore si fidi.

Prima di giudicare, `sup collect` cerca un `30-resoconto-<unit>.md` lasciato dentro la copia.
Un resoconto non tracciato viene spostato nella cartella del compito e lo spostamento viene detto nell'output e nel diario (`report_relocated`).
È l'abitudine dell'esecutore locale che la run del 2026-10-21 ha esposto, dove il resoconto restava un file non tracciato, faceva fallire `albero pulito` e non arrivava mai al revisore.
Un resoconto committato sul ramo fa parte del diff dell'unità e non viene spostato: i cancelli devono giudicare quello che dice.
Quando l'ultimo turno della sessione è finito, `sup collect` chiude la scheda dell'esecutore quando l'unità ne ha una, prima di leggere quel resoconto, perché un harness in sandbox può tenere i file che ha scritto illeggibili al motore finché il suo processo è vivo.
Se la sessione sta ancora lavorando, la scheda resta aperta e l'output lo dice.
Un turno headless non ha schede da chiudere: il suo guardiano chiude l'albero quando il turno finisce, come descritto in "Chiudere i processi di un turno".

Il pacchetto nomina anche i test che questa piattaforma ha saltato tra i file di test toccati dall'unità (`pytest -q -rs` su quei file, con `-n 4` quando pytest-xdist è installato e un tetto di cinque minuti): un test saltato su Windows gira sulla CI Linux, e viceversa.
Un bug solo POSIX della divisione di lazy-mcp è passato su Windows esattamente così, e il revisore ora vede "non verificato su Linux" accanto alla tabella dei cancelli invece di una run verde che non provava nulla.

Un'unità che tocca `conftest.py`, `pytest.ini`, `setup.cfg`, `tox.ini` o la sezione pytest di `pyproject.toml` viene nominata nel pacchetto sotto "Superficie di pytest".
Quei file cambiano come la suite raccoglie ed esegue; il cancello non fallisce per colpa loro, e il revisore li guarda prima di approvare.

I glob sono in stile shell (`fnmatch`), quindi `docs/*.md` e `03-INFRA/**` funzionano entrambi.

Il comando di verifica è un comando solo, non una riga di shell.
Il motore non lo consegna mai a una shell, quindi `&&`, `||`, `|`, `;` e `&` vengono rifiutati prima che parta qualcosa, con il consiglio di scrivere uno script o un comando unico; un `;` dentro un argomento tra virgolette resta un token solo e non viene scambiato per un separatore.
Anche il suo eseguibile deve venire da fuori della copia dell'unità: Windows risolve un nome nudo nella cartella di lavoro prima che nel `PATH`, quindi un `pytest.bat` piantato nella copia risponderebbe al posto del runner vero, e ogni percorso che risolve dentro la copia viene rifiutato nominando il file.
Un interprete Python parte con `-P`, che tiene la cartella di lavoro fuori da `sys.path`: senza, `python -m pytest` importerebbe un `pytest.py` lasciato nella copia dall'unità.
`-P` non tocca `PYTHONPATH`, quindi anche l'ambiente viene ripulito, e la copia non decide nulla su cosa importa l'interprete.

Il conteggio raccolto risponde a una domanda diversa dall'AST: il cancello di integrità conta le funzioni di test che un diff contiene ancora, e un `__test__ = False`, una cartella che pytest non percorre, un `collect_ignore` o un `conftest.py` che esce lasciano quelle funzioni esattamente dove erano.
Il cancello esegue pytest stesso, su due alberi esportati, e confronta i numeri.

## Cosa garantiscono i cancelli, e cosa no

I cancelli intercettano errori e scorciatoie. Non sconfiggono un esecutore deliberatamente ostile che ha una shell.
Ogni controllo qui sopra legge un fatto che l'unità non può riscrivere in silenzio, e ogni rifiuto nomina quello che ha visto.
Questo copre il modo di fallire ordinario di un agente sotto pressione: un test cancellato, un test rinominato fuori dal rilevamento, un'asserzione indebolita, una verifica che non prova nulla, un push che è uscito dalla copia, una configurazione git modificata alle spalle del proprietario.

Non copre un esecutore disposto a mentire nel codice che il motore deve eseguire.
Raccogliere un albero importa il suo `conftest.py` e i suoi moduli di test, e la verifica dichiarata esegue il codice dell'unità: codice scritto per stampare un conteggio finto, per uscire prima della verità e poi ripulire, o per fare `git push <url>` a metà run è un falsario determinato, non un incidente.
`git push <url>` non lascia tracce locali affidabili, quindi nessun cancello qui può vederlo; il cancello del remoto legge i remote prima che parta la verifica, quindi un push fatto dalla verifica stessa non è quello che chiede.

La difesa per quello strato è fuori dai cancelli: il profilo dei permessi dell'harness sotto cui gira l'esecutore, che nega `git push` e le grafie di shell che ci arriverebbero, e la revisione del diff e del resoconto da parte del supervisore.
I cancelli rendono quella revisione economica e difficile da ingannare per caso. Non la sostituiscono, e nessun altro controllo testuale aggiunto qui lo farebbe.

Limiti residui che questo giro lascia aperti:

- una regola `url.*.insteadOf` viene rilevata dalla chiave, non dal valore: cambiare il bersaglio di una regola che la creazione già portava è invisibile, e viene rifiutata solo una chiave nuova;
- il `.git/config` condiviso è anche del proprietario, quindi un proprietario che lo modifica mentre esiste un'unità prende un FAIL che nomina il file.
  `sup rebase <id> <unit>` è il modo esplicito di assorbire quel cambiamento: rifà l'impronta della configurazione e la registra nel diario, così la raccolta successiva giudica la nuova baseline;
- `--prune` viene rifiutato come token in `--push-with`, ma uno script dichiarato può comunque eseguirlo; il controllo dopo il push intercetta la cancellazione a posteriori, e a quel punto il ramo è già sparito dal remoto;
- il cancello della raccolta confronta numeri, non identità: un'unità che cancella un test e ne aggiunge un altro tiene il conteggio e passa questo cancello, mentre il cancello di integrità rifiuta comunque la cancellazione;
- un ramo remoto che un altro attore crea tra la lettura `before` e quella `after` di una pubblicazione viene riportato come cambiamento fatto dal comando.

## Il giro di revisione

`sup review` è il turno del supervisore, ed è un file, non una frase in un terminale.
Ogni chiamata scrive un giro, numerato da 1 e mai riscritto: `45-revisione-<unit>-<round>.md` porta il verdetto, la testa giudicata, il verdetto dell'ultima raccolta su quella testa, e il motivo oppure le correzioni.

`--approve "<reason>"` viene accettato solo quando l'ultimo `sup collect` del motore ha detto PASS e il ramo dell'unità punta ancora al commit che quella raccolta ha giudicato.
Un ramo che si è mosso viene rifiutato con entrambi gli hash, perché un'approvazione deve nominare un commit che una persona possa leggere.
Anche un evento di raccolta senza testa viene rifiutato, con "rifai `sup collect`", e non diventa mai un'approvazione di qualunque commit ci sia.
Il giro registra la testa completa, così uno spostamento successivo è visibile invece di essere dedotto.

`--rework <file>` scrive un giro RIMANDA e manda le correzioni nella sessione dell'esecutore con lo stesso meccanismo di `sup run`: `opencode run -s <session>` quando la sessione è inattiva, `unit_queued` con il giro quando è occupata, e un rifiuto quando il compito non ha nessuna sessione.
La riga di comando porta un puntatore al file di revisione, mai il suo testo.
Il giro di revisione viene scritto prima della consegna, così le correzioni esistono anche quando la sessione non si raggiunge, e l'errore lo dice.

Il numero di un giro avanza solo quando il giro precedente è stato consegnato.
Mentre un giro è in coda o non è mai stato inviato, `--rework` viene rifiutato e dice di usare `--resend`: quel comando consegna di nuovo l'ultimo giro non consegnato, nella sessione se è libera, in coda se è occupata, e avvia l'unico turno nuovo quando il compito non ha ancora una sessione.
Il file del giro e il suo numero non cambiano.

Una consegna non è il processo: l'harness scrive il suo evento quando `opencode run` parte, e un provider può chiudere quel turno con un errore prima che qualsiasi lavoro arrivi alla copia.
Il motore legge l'ultimo turno della sessione dall'archivio dell'harness (`session_message`, la riga `idle`), e quando è fallito ed è più recente della consegna il giro resta in sospeso. `sup status` stampa

```
consegna fallita: provider.content-filter (giro 1, sessione ses_...): le correzioni non sono arrivate
```

una volta per guasto (`unit_delivery_failed` nel diario), e `--resend` lo ripropone.
`--resend -m <model>` manda quella singola consegna a un modello diverso (`opencode run -s <session> -m <model>`; `run` non ha `--variant`), che è la via d'uscita quando il provider rifiuta quello configurato.
Un rinvio che deve aprire una sessione nuova la avvia nello stesso modo in cui è partita l'unità: quando è una scheda, la scheda prende il modello dalla configurazione del proprietario, quindi il verbo dice che il modello non è stato applicato.

`sup status` mostra, per unità, il giro di revisione, se è approvata, la testa pubblicata con il suo ramo, e i server MCP che il supervisore ha assegnato (`mcp: nessuno` quando non ce ne sono).
Mostra anche il costo dell'unità e del compito in una forma sola, la stessa frase che stampa `sup collect`:

```
costo: giro 0.0047 $, unità 0.0047 $, compito 0.0047 $ (modello opencode-go/…)
```

I tre numeri si misurano alla raccolta: il giro è quanto la sessione è cresciuta da quando è partita la consegna del giro stesso, l'unità è quanto è cresciuta dal primo aggancio dell'unità, e il compito è la sessione viva più le sessioni finite prima di lei.
Una cifra che non si riesce a leggere si dice a parole, mai scritta come zero.

## La fine del lavoro di un'unità

Quando un'unità viene raccolta il motore fa le due cose che un lavoratore finito deve: dice quanto è costato il lavoro e si toglie di mezzo.
`sup collect` misura la sessione attraverso l'harness, figli compresi, scrive la stessa riga nel diario (`unit_collected` porta i numeri), in `40-pacchetto-<unit>.md` e nel proprio output, e poi chiude la scheda dell'esecutore quando l'unità ne ha una, con le regole della sezione sulla pubblicazione: mai mentre la sessione sta ancora scrivendo, mai mentre un'altra unità del compito può ancora lavorare, mai un albero che tiene il server condiviso.
Un turno headless non ha schede: il suo guardiano chiude l'albero quando il turno finisce, come descritto qui sotto.

Dopo che una scheda è stata chiusa, una revisione non deve girare senza una finestra.
`sup review --rework` e `--resend` controllano il diario: quando l'ultima parola del motore sulla scheda è `tab_closed`, le correzioni vanno in una scheda visibile aperta sulla sessione che il compito ha già (`opencode <copy> -s <session> --prompt "<pointer>"`, il flag di ripresa di OpenCode 2), non in un `opencode run` nascosto.
Quando la scheda è ancora aperta la consegna resta fuori dallo schermo, come prima.
Un modello nominato con `--resend -m` non può viaggiare dentro una scheda, e il verbo dice che non è stato applicato.
Un'unità partita headless resta così: una correzione, una sessione persa o un `--resend` senza sessione avviano un solo turno headless nuovo.

Il ritiro è la regola del proprietario "a fine lavoro non resta niente", e gira solo quando il lavoro è da qualche parte in modo permanente:

- **`sup publish`** ritira l'unità dopo che i commit sono verificati sul ramo del proprietario, compreso il percorso in cui il push è fallito e i commit sono rimasti in locale.
- **`sup review <id> <unit> --approve "<why>" --close`** ritira un'unità approvata che non verrà pubblicata.
  Prima che il ramo se ne vada, il motore scrive la testa sotto `refs/nexgen/archive/<task>/<unit>`, così la storia resta raggiungibile; un'unità già pubblicata viene rifiutata, perché `sup publish` sa quali commit sono arrivati.

La sequenza è: chiedere all'adattatore dell'unità di liberare quello che il server condiviso tiene per quella cartella (`dispose_location`, `debug.location.evict` di OpenCode), poi `git worktree remove --force` con `core.longpaths=true`, poi `git branch -D`.
La copia va prima del ramo, e una copia che un processo vivo tiene ancora ferma il ritiro con l'ultima riga dell'errore di git.
Niente viene cancellato quando i commit non si trovano in nessun repository noto, o quando un'unità non pubblicata non ha un archivio: il rifiuto nomina l'unità e dice quale comando la chiude.
La cartella del compito con i suoi file Markdown e il diario non viene mai rimossa: è il verbale, non il banco di lavoro.

Un ritiro che fallisce dopo una pubblicazione riuscita non annulla la pubblicazione: il diario registra `unit_retire_failed`, il verbo stampa il motivo, e `sup pulizia` ripropone la copia.
Un'unità pubblicata tiene il suo stato `pubblicata`, o `pubblicata in locale` quando non è stato spinto nulla; il ritiro è un fatto di pulizia, non un esito diverso.

## Pubblicazione

`sup publish <id> <unit> --repo <checkout> --branch <branch> [--remote origin] [--push | --push-with "<command>"]` è l'unico push del motore, e avviene dopo ogni rifiuto che si possa decidere senza toccare nulla:

| rifiuto | quando |
|---|---|
| `non approvata` | l'ultimo giro di revisione non è un'APPROVA |
| `cambiata dopo l'approvazione` | il ramo dell'unità si è mosso dopo l'approvazione |
| `checkout inesistente` / `non è un repository git` | `--repo` non è un checkout |
| `copia dell'unità` | `--repo` è la copia di lavoro dell'unità stessa |
| `il checkout è su …, non su <branch>` | il checkout del proprietario è altrove |
| `checkout sporco` | lavoro non committato nel checkout del proprietario, nominato |
| `bump di versione` | un commit tocca `VERSION` o `CHANGELOG.md`, compresa una rinomina, anche dentro il confine dell'unità |
| `servirebbe un force-push: il motore non lo fa mai` | il ramo remoto scaricato non è un antenato della testa del proprietario |
| `i cancelli del motore, rieseguiti ora, danno FAIL` | i cancelli falliscono sull'intervallo, nominati riga per riga |
| `conflitto` | un cherry-pick va in conflitto; i file vengono nominati, il checkout torna indietro, e l'unità resta approvata e non pubblicata |

L'ordine è deliberato: prima i rifiuti economici e decisivi, e i cancelli vengono rieseguiti sull'intervallo prima del primo cherry-pick, così un cancello che fallisce lascia il checkout del proprietario esattamente com'era.
Anche la domanda sul solo-avanti viene posta prima del cherry-pick, confrontando il ramo remoto scaricato con la testa corrente del proprietario, che è la stessa domanda per la testa nuova.

La pubblicazione ripete i commit dell'unità uno alla volta, dal più vecchio, con `git cherry-pick`, così l'ordine si conserva e la firma segue la configurazione del repository del proprietario: quando `commit.gpgsign` è attivo, i commit pubblicati sono firmati perché è il git del proprietario a firmarli, mai perché il motore inventa una chiave.
`sup publish` stampa `commit.gpgsign` e il segno `%G?` di ogni commit pubblicato, così "firmato" è un fatto che il revisore legge invece di supporre.
Nessun tag viene mai creato, e nessuna opzione di forza esiste in nessun percorso di codice.

Un conflitto esegue `git cherry-pick --abort` e, se la testa si è comunque mossa, riporta il checkout al commit che la pubblicazione ha trovato lì; il rifiuto nomina i file in conflitto e dice che il checkout è tornato indietro.
Il diario registra `unit_publish_failed` con quei file, e nulla registra l'unità come pubblicata o ritirata: la sua copia e il suo ramo restano, così la testa approvata da una revisione è ancora lì da correggere.
Un'unità già spinta sulla stessa testa viene rifiutata invece di essere ripetuta, perché una seconda ripetizione raddoppierebbe i suoi commit.
Un'unità applicata in locale ma mai spinta (`pushed: false`) è l'eccezione: `--push` o `--push-with` controlla che il ramo contenga ancora i commit pubblicati e spinge, senza un secondo cherry-pick.
Quel push manda esattamente la testa pubblicata (`<head>:refs/heads/<branch>`), quindi un commit che il proprietario aggiunge al ramo dopo resta in locale: il motore non pubblica mai quello che non ha revisionato.
Un ramo riscritto in modo che non contenga più la testa pubblicata viene rifiutato nominandolo.
Lo stesso comando senza un flag di push rifiuta dicendo quale flag aggiungere.

Senza `--push` o `--push-with`, la pubblicazione si ferma dopo il cherry-pick locale e stampa l'unico comando che resta.
`--push` esegue `git push --no-follow-tags <remote> <branch>`, così il `push.followTags` di un repository non può mandare tag.

Un push viene dichiarato, poi controllato, mai creduto.
Prima del push il motore legge il ramo remoto e la sua lista di tag; dopo, rilegge entrambi e richiede che il ramo punti esattamente alla testa pubblicata, che il valore letto prima sia un antenato di quello nuovo e che la lista dei tag sia invariata.
Qualunque altra cosa è un guasto nominato (`il comando di pubblicazione ha forzato la storia` oppure `... ha creato o spinto tag`), il diario tiene `pushed: false` e viene stampato il guasto del comando stesso.

`--push-with "<command>"` esegue il comando di pubblicazione del proprietario con le regole di avvio del motore (`nexgen_core.processes`, argv, nessuna shell) dentro il checkout del proprietario, e si applica lo stesso controllo successivo.
Il comando dichiarato viene rifiutato prima di partire quando porta una forza in qualsiasi grafia: `--force`, `-f`, un gruppo di flag brevi che contiene `f`, `--force-with-lease`, `--force-with-lease=<ref>`, `--force-if-includes`, `--mirror`, `--delete`, un refspec che inizia con `+` (forza) o `:` (cancellazione).

Il push va al remote con cui il checkout condivide già la storia, che è quello che `origin` è.
Un checkout shallow non può inizializzare un altro remote vuoto: git stesso rifiuta (`Failed to traverse parents`, poi `shallow update not allowed`), ed è una proprietà del clone, non del motore.

Una volta applicati i commit, `sup publish` chiude la scheda dell'esecutore quando l'unità ne ha una, e `sup review ... --approve ... --close` fa lo stesso quando ritira un'unità approvata.
Il motore ha scritto un marcatore nella shell di quella scheda (`NEXGEN_SUP_TAB`), traccia la scheda per titolo (`esecutore · <unità>`) e per il pid registrato all'apertura, e ferma lei e i suoi figli dal più profondo fino alla shell.
Chiude solo le schede aperte dal motore stesso.
Unità indipendenti con copie e schede separate hanno la scheda chiusa subito quando finiscono, senza aspettare altre unità.
Una scheda condivisa da più unità si chiude solo quando l'ultima unità che la usa è finita, pubblicata, approvata e chiusa, oppure ritirata.
Due cose fermano la chiusura: se un'altra unità che condivide la scheda è ancora aperta, la scheda resta e `tab_left_open` nomina le unità aperte; e rifiuta quando l'albero tiene un `opencode serve` condiviso, dove chiuderla fermerebbe ogni altro worker.
Quando si chiude, `tab_closed` registra i pid.
Una scheda che il proprietario ha già chiuso non è un errore.

Su Windows il turno che il motore avvia per un'unità vive anche dentro un job object con `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, nominato dal compito e dall'unità (`nexgen-sup:<task>:<unit>`).
Il turno viene adottato appena esiste e riceve un handle per duplicazione, così il job vive esattamente quanto il lavoro, e chiuderlo chiude tutto quell'albero con una sola chiamata al kernel invece di un giro della tabella dei processi che può perdere quello nato mentre veniva letto.
Un processo che il job non tiene (un discendente che la catena dei job di questa macchina lascia uscire, o uno creato con `Win32_Process.Create`, il cui genitore è il provider e non l'unità) non si può chiudere dal kernel: quelli vengono fermati uno alla volta con psutil e contati nella riga (`3 fuori dal job`) e in `outside_job` di `tab_closed`, che è la differenza tra un job che ha funzionato e un job che non teneva niente.
Un turno che il kernel non tiene viene consegnato lo stesso, e il diario lo dice con `unit_unjobbed` e il motivo.
Su POSIX non cambia niente: `start_new_session` e il process group fanno lo stesso lavoro.

### Chiudere i processi di un turno

Ogni turno headless parte staccato e senza finestra: `CREATE_NO_WINDOW` su Windows, e su Linux una sessione e un process group propri con `start_new_session`.
Su Windows il turno entra anche nel job object dell'unità (`nexgen-sup:<task>:<unit>`, `KILL_ON_JOB_CLOSE`), come descritto sopra.

Ogni turno ha un guardiano, `nexgen_local.supervision_reaper`: un processo Python staccato e senza finestra che aspetta la fine del turno o la sua scadenza, e poi termina tutto quello che il turno ha lasciato.
Registra i discendenti che vede nascere ogni due secondi, perché su Windows un orfano non è più raggiungibile dal padre morto; su Linux ferma ogni processo del process group del turno, e su Windows i membri del job dell'unità.
Manda prima `terminate` e, dopo tre secondi, `kill` ai superstiti.

Non tocca mai un pid riciclato (confronta il `create_time` registrato alla partenza, con una tolleranza di 0,5 s), sé stesso, né il server condiviso `opencode serve` e i suoi figli, perché ucciderlo chiuderebbe il lavoro delle altre sessioni.
Scrive nel diario del compito `turn_reaped` con `reason` (`finito`, `scaduto` o `inattivo`), `killed`, `spared` e `refused`.
Un turno che il guardiano non riesce a sorvegliare è `turn_unguarded` con il motivo, e un turno che il job non tiene resta `unit_unjobbed`.

### Un turno che smette di dare segni di vita

Un turno headless può bloccarsi prima di stampare qualsiasi cosa o di toccare un file: l'`opencode run` rimasto muto per tre ore il 2026-11-27 è il caso per cui esiste questa guardia.
Ogni trenta secondi il guardiano legge tre segni di vita: il tempo di CPU dell'albero del turno, il numero, la modifica più recente e la dimensione totale dei file nella cartella di lavoro, e la dimensione dei log del turno.
Quando nessuno di questi si è mosso per `supervision.idle_timeout_minutes` (15 minuti quando non è scritta), il turno viene chiuso esattamente come uno scaduto, con tutto il suo albero di processi e la stessa riga `turn_reaped`, con `reason` uguale a `inattivo`.

Un turno inattivo non viene abbandonato al primo stop: una partenza a freddo e un provider che ha buttato una connessione una volta sono comuni, quindi il guardiano rilancia lo stesso turno, sulla sessione che il diario ora nomina quando ne ha una, e scrive `turn_idle_relaunch`.
Un secondo stop per inattività è uno stallo vero: il guardiano scrive `unit_stalled` con `reason="inattivita"` e la soglia, e manda l'avviso `TASK_STALLED` dal megafono, con chiave unità e sessione, così uno stop ripetuto non avvisa due volte dentro la finestra di debounce.
Il rilancio avviene solo quando il guardiano ha la ricetta del turno che ha sorvegliato: un turno avviato da qualcosa che questo motore non sa ricostruire viene segnato in stallo subito, invece di ripartire con un comando diverso.

La scansione della cartella legge al massimo 4000 voci, quindi resta leggera su una copia con una cartella di dipendenze grande; il tempo di CPU intercetta un turno che sta lavorando senza aver ancora scritto.
Un turno in cui si muoverebbe solo la misurazione dell'harness (un modello che pensa, senza output e senza file) non conta come vivo: il segnale è quello della macchina, mai un secondo processo dell'harness avviato dal guardiano.

La scadenza è `supervision.turn_timeout_minutes` nel `config.json` della corsia, 240 minuti quando non è scritta.

Il limite onesto: i processi che il server condiviso `opencode serve --service` avvia per una cartella non sono figli del turno, e `dispose_location` li libera quando l'unità viene ritirata, come descritto sopra.
Il ramo Linux del guardiano è coperto dai test sul codice ma non è ancora stato provato su una macchina Ubuntu reale.

## Riprendere un compito

`sup resume <id>` porta un compito attraverso i passi che il proprietario già esegue a mano: `unità -> esecuzione -> raccolta -> revisione -> pubblicazione`.
Ogni nodo chiama la funzione che chiama il verbo corrispondente, quindi non esiste una seconda implementazione di un passo.
I checkpoint vivono in `60-ripresa.sqlite` nella cartella del compito (`SqliteSaver` di LangGraph), uno per unità: il thread è il compito e l'unità, e `61-ripresa.json` tiene un record di identità per unità con lo SHA-256 dell'obiettivo del compito e del brief dell'unità.

La run si ferma dove serve una persona e lo dice:

| stop | significato |
|---|---|
| `waiting_review` | raccolta PASS, in attesa di `sup review` |
| `waiting_session` | la sessione del compito era occupata: l'unità è in coda |
| `rework` | la revisione ha mandato correzioni nel giro dell'esecutore |
| `waiting_publish` | approvata, ma nessun `--repo`/`--branch` ha nominato il checkout del proprietario |
| `done` | la pubblicazione è stata eseguita |

Una ripresa rientra nel passo che si è fermato, non in quello dopo, così un'attesa viene guardata di nuovo invece di essere saltata; una raccolta già fatta sulla stessa testa non si ripete, una sessione già agganciata non riceve invii, e una run conclusa non riesegue nulla.
L'identità di una run è il compito, l'unità e l'hash del brief, e viene controllata prima di ogni passo: un brief modificato da quando la run è partita rifiuta con un motivo proprio, perché continuare una run le cui istruzioni sono cambiate è peggio che fermarsi.
Un'unità diversa ha semplicemente il proprio checkpoint nello stesso file, quindi due unità di un compito riprendono in modo indipendente.
`--riparti` butta via il checkpoint dell'unità che nomina e nient'altro.

Il nodo di pubblicazione non fa nulla se `--repo` e `--branch` non nominano il checkout del proprietario: la sola approvazione si ferma a `waiting_publish` e stampa il comando che resta.
Con entrambi, chiama lo stesso `sup publish`.

## Riallineare un'unità

`sup rebase <id> <unit>` riporta il ramo dell'unità sulla punta corrente della sua base e registra la nuova base nel diario, così `sup collect` e `sup publish` giudicano l'intervallo giusto.
Su una base avanzata durante l'unità, la vecchia base faceva vedere alla raccolta commit già sul remoto come commit dell'unità; questo comando è la correzione.

È un rebase locale: la punta si legge dal checkout del proprietario, e il motore non scarica nulla.
Una base riscritta, dove la vecchia base non è più un antenato della sua nuova punta, viene rifiutata invece di essere ripetuta, perché ripeterla pubblicherebbe storia che nessuno ha revisionato.
Un'unità pubblicata viene rifiutata: riallinearla riscriverebbe commit che il proprietario ha già.
Lavoro non committato nella copia viene rifiutato prima che si muova qualcosa.

Un conflitto esegue `git rebase --abort`, lascia la copia esattamente com'era e nomina i file: una copia lasciata a metà rebase è una trappola per chi la raccoglie dopo.
In caso di successo il diario guadagna `unit_rebased` con la base precedente, la base nuova, la testa e l'impronta aggiornata di `.git/config` e `config.worktree`.
Quell'impronta è anche il motivo per cui il comando esiste accanto al rebase: un cambiamento legittimo che il proprietario ha fatto alla configurazione viene assorbito con un comando esplicito invece di imporre una nuova unità.
Un ramo già sulla nuova punta la registra e basta, e un comando che non muove né la base né la configurazione non scrive nessuna riga.

## Il ricognitore

`sup scout <id> "<question>" [--harness <name>]` dà al ricognitore, `agy` per impostazione predefinita da `supervision.roles.ricognitore`, una copia usa e getta del repository su cui lavora la prima unità del compito, a un percorso fisso: `<task>/24-ricognizione/`.
La cartella viene ricreata a ogni giro con un clone semplice e `checkout --detach <base>`, così il ricognitore non eredita le modifiche di ieri e non tiene nessun ramo il cui lavoro si possa scambiare per quello dell'esecutore.

Prima che la scheda si apra, quel percorso esatto viene aggiunto a `trustedWorkspaces` in `~/.gemini/antigravity-cli/settings.json` quando non c'è già, con una scrittura atomica, un `.bak` conservato e solo quella chiave toccata.
Il percorso non cambia mai, quindi una voce di fiducia copre ogni giro.

La domanda finisce in `25-brief-ricognizione.md`, e il resoconto che il ricognitore deve scrivere è `26-rapporto-ricognizione-<round>.md`, fuori dalla copia.
La scheda esegue questo stesso motore nel suo ruolo di ricognitore, che avvia il comando scout dell'adattatore: per impostazione predefinita `agy --mode plan --prompt <pointer>`, e un altro harness fornisce il proprio comando di sola lettura (`opencode run --agent plan`).
La modalità plan è di sola lettura, e il puntatore tiene il brief in un file solo.
Il prompt dice che il resoconto è l'unico consegnabile: nessun piano, nessuna conferma, scrivilo subito.
La sola modalità plan non bastava, perché con un brief lungo `agy` scriveva un piano, chiedeva "Proceed" e usciva senza resoconto, mentre lo stesso ricognitore ha completato tre giri su tre quando il prompt lo diceva.
Alla fine del giro il motore chiede a git se la copia è dove era partita (`git status --porcelain`, HEAD invariata), poi chiude la scheda che ha aperto.
Un file tracciato che è cambiato, o una testa mossa, chiudono il giro con codice 1 e una riga `scout_finished` che elenca cosa è cambiato.
Un file non tracciato, una cache o un lock lasciato da un tool, non è un cambiamento al codice: viene elencato in `scout_finished` come `untracked` e non fa fallire il giro.
Il controllo del codice è la prova, non la promessa del ricognitore.

## Cosa il motore rifiuta di eseguire

Il resoconto di un esecutore può chiedere un force-push, un tag, un bump di versione o una modifica a un test esistente, e può anche chiedere qualcosa che il motore non ha mai nominato.
L'unica fonte è la sezione delle richieste del resoconto: un titolo che contiene "richieste", con o senza "dubbi", quindi `## Richieste al proprietario` e `## Dubbi e richieste al proprietario` sono la stessa sezione.
Il brief, una revisione e un file di correzioni sono parole del supervisore e non vengono mai scansionati.

Dentro quella sezione ogni voce di elenco (`- text` o `1. text`) è una richiesta e arriva al proprietario come scritta; una parola chiave la classifica soltanto, e una voce che nessuna parola chiave conosce viene archiviata come `altro`.
Una voce che dice "nessuna richiesta" o "nessun dubbio" non è una richiesta.
I blocchi di codice recintati e le righe citate (`>`) vengono ignorati, quindi incollare un output non è una richiesta, e un paragrafo semplice accanto all'elenco è il resoconto che parla, non una domanda.
Senza sezione nulla viene riconosciuto e il pacchetto dice `sezione delle richieste assente nel resoconto`.
Una riga per unità, tipo e frase va in `07-richieste-proprietario.md`, e una frase già nel file non viene riscritta, così il file non può citare se stesso come richiesta nuova.
`sup collect` stampa le righe che ha scritto, e la decisione resta del proprietario.

## Finestre visibili

`supervision_windows` apre un terminale che esegue un comando e resta aperto; il motore non ne legge mai l'output, e una finestra che non si riesce ad aprire solleva un errore invece di ripiegare su un processo nascosto.
Una scheda aperta dal motore porta il suo marcatore in un punto esatto, e viene guardata solo una shell: su Windows l'assegnazione nella riga di comando (`$env:NEXGEN_SUP_TAB=nexgen-sup:<task>:<role>`), su Linux la variabile esportata nell'ambiente della shell (`/proc/<pid>/environ`, letto con psutil).
Un `grep` o un editor che si limita a citare il marcatore non è una scheda e non viene mai toccato.
Il motore chiude esattamente quella scheda quando il lavoro è finito: alla pubblicazione (`sup publish`), al ritiro (`sup review --approve ... --close`) e durante la pulizia (`sup pulizia --chiudi`).
Una scheda condivisa resta finché l'ultima unità che la usa non è finita, pubblicata, chiusa o ritirata, mentre le schede separate si chiudono con la loro unità.
Viene tracciata per titolo (`esecutore · <unità>`) e pid di apertura, chiude solo schede aperte dal motore e non tocca mai un albero che tiene l'`opencode serve` condiviso.

Un'unità ha una scheda sola.
Il marcatore nomina il compito e il ruolo, quindi quale unità serve una scheda è quello che dice la sua riga di comando: la copia su cui è stata aperta (`23-unita-<unità>`) e il titolo che il motore le ha dato.
Quando `sup run --tab`, `sup review --rework` o `--resend` aprono una scheda per un'unità che ne ha già una, la vecchia scheda viene chiusa prima (`close_unit_tab`, stessa regola del marcatore e stesso rifiuto intorno al server condiviso), e il ritiro chiude la scheda dell'unità prima che la copia se ne vada; un'unità la cui scheda non è mai stata registrata non costa nessuna lettura della tabella dei processi.
Nove schede dimenticate di unità già pubblicate sono state, misurate sul PC del proprietario, il motivo per cui una pulizia non trovava nulla da raccogliere.

Su Linux la scheda è una finestra di terminale la cui shell è la scheda: l'albero si legge con psutil, dalla riga di comando e dall'ambiente di ogni processo, e si ferma dal più profondo, e la finestra segue la sua shell.
Vale la stessa regola sul server condiviso, quindi una macchina in cui il terminale ha avviato `opencode serve` tiene quella finestra.

`sup pulizia` è il verbo per quello che il motore ha lasciato indietro.
Elenca ogni scheda marcata il cui compito è finito, non ha unità, o non è più apribile da questa corsia (una prova in un `NEXGEN_HOME` temporaneo la cui cartella è stata cancellata), e con `--chiudi` le chiude con la stessa regola dal più profondo e lo stesso rifiuto intorno al server condiviso.
Elenca anche, senza toccarli, i processi `opencode` e `agy` che nessuna scheda tiene, e quanti processi pendono sotto `opencode serve`: sono i server MCP di sessioni già chiuse, che è come si comporta OpenCode, quindi la riga dice se il servizio si può riavviare adesso.

Sotto il server conta anche quello che appartiene a una sessione finita, e con `--chiudi` raccoglie esattamente quello: ogni figlio diretto di `opencode serve` nato prima che partisse la sessione viva più vecchia, insieme a tutto il suo albero, un server MCP e il `conhost` sotto di lui compresi.
Senza nessuna sessione viva raccoglie ogni figlio diretto.
Non ferma mai il server, un figlio nato dopo quel momento, una riga di cui nessuno riporta l'ora di nascita, o un processo che non discende dal server.
La stessa raccolta gira da sola alla fine di ogni `sup publish` e di ogni chiusura di unità, così il proprietario non deve ricordarsi il verbo; una raccolta che non riesce a leggere la tabella dei processi è una nota stampata e non ritira mai una pubblicazione già arrivata.

Una scheda è una sessione solo finché la sua unità è viva.
`sup pulizia` elenca le schede di unità il cui diario dice che il lavoro è già arrivato (`pubblicata`, `pubblicata in locale`, `ritirata`) e `--chiudi` le chiude anche quando il compito ha altre unità in corso; una scheda così non conta come sessione attiva, quindi non può tenere in vita mille processi orfani.
Una TUI del proprietario, avviata fuori dal motore e senza marcatore, non viene mai toccata e conta come attiva: la riga lo dice, con l'ora in cui è partita (`una sessione OpenCode senza marcatore, iniziata alle HH:MM, blocca la raccolta`), ed è il motivo per cui non è stato raccolto nulla.

Risponde anche alla regola che non resta niente indietro.
Elenca le copie e i rami di unità il cui diario dice che il lavoro è arrivato (`pubblicata`, `pubblicata in locale`, `ritirata`, approvata e chiusa) e che il disco ha ancora, e `--chiudi` li ritira.
Con una lettura profonda chiede al server condiviso quali cartelle tiene ancora (`opencode api GET /api/debug/location`): una cartella che non esiste più viene elencata con l'ora di avvio del suo processo più vecchio, presa dalla tabella dei processi, e `--chiudi` la sfratta attraverso la stessa API che usa il ritiro.
Ritirare un'unità chiede al server di smaltire prima l'istanza di quella cartella (`POST /instance/dispose`, l'endpoint che nomina l'SDK installato) e solo dopo sfratta la posizione, perché una copia cancellata sotto un'istanza viva lascia i suoi server MCP in esecuzione finché la macchina non si riavvia.
Una cartella che esiste ancora non è mai in quella lista, quindi una sessione attiva e il server del proprietario sono fuori portata.
Il banco dei modelli locali non è del motore: nessun verbo lo ferma tranne l'unità locale che lo ha avviato.

`sup collect` stampa lo stesso fatto come nota, non come cancello: le schede marcate di compiti che questa corsia non ha più restano aperte dopo la raccolta, e quella è rumore e memoria, non un difetto dell'unità in revisione.

- Windows: una sola finestra di Windows Terminal chiamata `nexgen-sup`, e una scheda per sessione (`wt -w nexgen-sup new-tab ...`). Dieci unità sono dieci schede, non dieci finestre.
  Un `;` nel comando viene protetto come `\;`, perché Windows Terminal concatena i propri sottocomandi su quello.
  Senza Windows Terminal il ripiego è una console dedicata (`CREATE_NEW_CONSOLE`), che non ha schede.
- Linux: il primo tra `x-terminal-emulator`, `gnome-terminal`, `konsole`, `xterm`, ognuno con i propri flag, con la shell tenuta viva da `; exec bash`.
  Senza `DISPLAY` e senza `WAYLAND_DISPLAY` rifiuta: una finestra che nessuno può vedere è un processo nascosto con qualche passaggio in più.
- Il comando viene passato come argv, quindi la regola di quoting è quella del motore (`nexgen_core.processes`), non una stringa costruita a mano.
- Niente qui viene ereditato da chi chiama: il terminale lo avvia il motore, quindi un supervisore che lancia i verbi da un'applicazione grafica ottiene le stesse schede.
  Un'unità che non deve avere una finestra è il predefinito: `sup run` avvia un turno headless, e `--tab` è il modo esplicito di vederne uno, come descritto in "Il turno dell'esecutore".

`sup window-check` è la prova manuale su ogni macchina: si apre una finestra, stampa `finestra del supervisore OK` e resta aperta.
Non fa mai parte di un test: i test sostituiscono `Popen` e `which`, quindi nessuna suite apre una finestra.

## Il registro dei costi

Una riga per aggancio in `05-costi.jsonl`: istante, compito, unità, ruolo, harness, modello, sessione, finestra, durata, token in entrata e in uscita (con reasoning e lettura e scrittura della cache), costo in dollari e fonte della misura.
La fonte è l'harness dell'unità, attraverso il suo adattatore: OpenCode e l'esecutore locale tramite `opencode session export <id>`, Claude Code tramite il suo transcript sotto `~/.claude/projects/`, Codex tramite il suo archivio.
Codex riporta token e nessun denaro, e `agy` non riporta nessuno dei due: la riga dice che il numero è illeggibile invece di scrivere zero.

`sup costi` senza id mostra quello che l'archivio dell'harness dice essere stato speso nelle ultime 5 ore, 7 giorni e 30 giorni: ogni riga di sessione in `session_v2`, o nella vecchia tabella `session`, figli compresi, finestrata per `time_updated`.
I dollari di quelle sessioni li dà il binario opzionale [ccusage](https://github.com/ccusage/ccusage) (MIT), il cui lettore OpenCode salta le righe di messaggio che una fork v2 ha copiato dal genitore e prezza un modello dal listino incluso quando l'harness ha scritto zero, che è quello che scrive un abbonamento flat: quando è installato, un `ccusage opencode session --json --offline` risponde una volta ogni dieci minuti; quando manca, o la sua risposta ha una forma che il motore non riconosce, si somma la colonna `cost` dell'archivio esattamente come prima.
L'archivio viene aperto in sola lettura, e un archivio che non si riesce a leggere rende la finestra sconosciuta con il motivo, mai zero.
Nessuna regola di budget vive nel motore: i numeri si mostrano, e cosa farne resta una decisione del proprietario.

I subagenti vengono contati.
`opencode session export` e `session list` non espongono le sessioni aperte da un subagente: `session list` elenca solo le sessioni di primo livello e l'`info` dell'export non ha un campo genitore, quindi gli id figli compaiono solo dentro il testo dei messaggi.
L'harness li tiene nel proprio archivio (`~/.local/share/opencode/opencode.db`, colonna `parent_id`, nelle tabelle `session` e `session_v2`), e quello è l'archivio che il motore legge, in sola lettura, in profondità: il `cost_usd` della riga è la sessione più ogni sessione sotto di lei, con `children`, `children_cost_usd` e `children_source` che ne nominano le parti.
Quando l'archivio manca o è illeggibile la riga dice `figlie non esposte` e tiene il costo del genitore: una fonte non letta non viene mai scritta come "nessun figlio".

Una fonte che non risponde lascia comunque una riga, con `cost_usd: null` e una `note` che ne nomina il motivo.
Una misura mancante non viene mai scritta come zero, e `sup costi` dice quante righe non avevano una fonte.

## Cosa non fanno queste fasi

- Nessuna conservazione delle cartelle di compito basata sull'età: la copia e il ramo di un'unità vengono rimossi quando l'unità è ritirata, ma una vecchia cartella di compito e il suo diario restano finché il proprietario non decide altro.
  La conservazione a 30 giorni è la stessa decisione delle sessioni di ricerca e non è implementata; `sup pulizia` è la pulizia manuale per quello che un'unità chiusa ha lasciato sul disco.
- Il costo si misura solo dove l'harness lo espone.
  Codex riporta token e nessun denaro; `agy` non riporta né token né denaro, ed è per questo che resta un ricognitore.
  Il costo di Claude Code viene dal suo transcript, un file che l'harness può ruotare.
- Nessun tag e nessun rilascio: `sup publish` non ne crea mai, e un commit che tocca `VERSION` o `CHANGELOG.md` viene rifiutato nominandolo.
  Un rilascio resta `nexgen release ship` dal checkout del proprietario.
