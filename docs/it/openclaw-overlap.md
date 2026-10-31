# Mappa delle sovrapposizioni: NeXgen Engine e OpenClaw

🇬🇧 [English](../openclaw-overlap.md)

Dove finisce NeXgen Engine e dove comincia OpenClaw: quali capacità si sovrappongono e quale lato tiene ciascuna. La release fissata di OpenClaw è quella in `03-INFRA/agent-universal-layer/openclaw/pin.json`.

## Il confine

OpenClaw è l'harness per l'interazione con la persona e per l'esecuzione degli agenti. NeXgen Engine tiene:

- **Council**: revisione tra pari e consiglio indipendenti su più modelli.
- **Supervisione (`sup`)**: harness di attività con verifica e controllo indipendenti.
- **La gabbia**: guardrail di sicurezza, postura di autorizzazione e contenimento dei modelli locali.
- **Model Governor**: routing basato sulla quota degli abbonamenti e sulla disponibilità, catene di riserva.
- **Prove**: verifica delle risposte contro ricevute di recupero reali.
- **Libreria canonica di skill e server MCP**: sorgenti canoniche, fissaggio delle versioni, depwatch, propagazione.
- **Il Vault e la memoria canonica**: persistenza durevole su note Markdown versionate, CAS, igiene, push.
- **Servizi operativi di terzi**: servizi ausiliari containerizzati (n8n, vault-mcp, OCR).
- **Canali di aggiornamento del motore**: autoverifica e aggiornamento del codice del motore.
- **Sincronizzazione tra CLI e tra macchine**: rendering fail-closed e propagazione su Linux e Windows per ogni CLI supportata (Claude Code, Codex, Antigravity, OpenCode, OpenClaw).

OpenClaw possiede direttamente tutto il resto:

- Interfaccia web grafica, app desktop e app per il telefono
- Esecuzione degli agenti, cicli interattivi, invio dei prompt
- Workboard
- Canali di messaggistica (Telegram, WhatsApp, Discord, Slack, Matrix e gli altri)
- Memoria di sessione, trascrizioni, ricerca semantica, dreaming
- Skill Workshop
- Cron, scheduler e automazioni pianificate
- Accoppiamento e gestione dei nodi distribuiti
- Voce e sintesi vocale (TTS/STT)
- Tracciamento dettagliato di utilizzo, token e costi
- Ricerca ed esecuzione degli strumenti
- Approvazioni interattive

Il codice del motore che duplica una capacità di OpenClaw viene ritirato o delegato.

## Inventario delle sovrapposizioni

| Componente del motore | Cosa fa il motore | Funzione di OpenClaw | Verdetto | Perché |
|---|---|---|---|---|
| `nexgen shell` / `repl` / `interactive`<br>`03-INFRA/scripts/nexgen_core/tools/shell.py`<br>`03-INFRA/scripts/nexgen_core/cli/engine.py` | REPL interattiva nel terminale (`cmd.Cmd`) con un menu numerato sui verbi del motore. | `openclaw tui` (`openclaw/docs/cli/tui.md`, `openclaw/docs/web/tui.md`), Web Dashboard (`openclaw/docs/cli/dashboard.md`), Control UI (`openclaw/docs/web/control-ui.md`). | **ritirare** | Le interfacce interattive le fornisce OpenClaw. La REPL del motore era codice duplicato ed è stata rimossa. |
| `nexgen info -i` / `--interactive`<br>`03-INFRA/scripts/nexgen_core/cli/engine.py` | Avviava una shell interattiva dopo aver stampato lo stato. | `openclaw tui`, Control UI. | **ritirare** | `--interactive` avviava `nexgen shell`. Ora stampa una riga che rimanda alla TUI di OpenClaw. |
| `nexgen info` / `status`<br>`03-INFRA/scripts/nexgen_core/tools/info.py`<br>`03-INFRA/scripts/nexgen_core/cli/engine.py` | Dashboard testuale su allineamento tra CLI, stato della sincronizzazione e salute della memoria. | `openclaw status` (`openclaw/docs/cli/status.md`), Dashboard (`openclaw/docs/web/dashboard.md`). | **tenere** | `nexgen info` sorveglia l'ecosistema multi-CLI (Claude, Codex, Antigravity, OpenCode, OpenClaw). OpenClaw ispeziona solo se stesso. |
| `nexgen-local run <domanda>`<br>`03-INFRA/scripts/nexgen_local/cli.py`<br>`03-INFRA/scripts/nexgen_local/cmds/run.py` | Ciclo autonomo di risposta alle domande con modelli locali. | `openclaw agent` (`openclaw/docs/cli/agent.md`), esecuzione runtime (`openclaw/docs/openclaw-agent-runtime.md`), chat TUI e web. | **ritirare** | OpenClaw è l'harness per conversare con agenti e modelli. Il comando è ritirato in favore di `openclaw agent` e `openclaw tui`; `run_lane` resta come motore di valutazione interno per `eval` e `tier evaluate`. |
| Connettori della gabbia locale (`calendar.py`, `drive.py`, `gmail.py`, `outlook.py`, `web.py`)<br>`03-INFRA/scripts/nexgen_local/connectors/` | Connettori che i modelli locali usano dentro la gabbia di contenimento. | Server MCP standard (`openclaw/docs/cli/mcp.md`) e strumenti del browser (`openclaw/docs/tools/browser/agent-tools.md`). | **tenere** | Sono strumenti interni della gabbia (`nexgen_local/tools.py`), non un duplicato di OpenClaw. Un'eventuale consolidazione è una questione interna del motore. |
| Upload su Drive e MCP (`drive-propose`, `drive-upload`, `drive-mcp`)<br>`03-INFRA/scripts/nexgen_local/cmds/drive.py`<br>`03-INFRA/scripts/nexgen_local/drive_mcp.py` | Preparazione, upload e server MCP stdio per Google Drive. | Trasferimento file nativo (`openclaw/docs/cli/file-transfer.md`) o un MCP di Google Workspace. | **tenere** | `drive-mcp` è un server MCP del motore, distribuito dalla libreria canonica a ogni runtime. |
| Timer dei guardiani e battito (`agent-sync.timer`, `agent-heartbeat.timer`)<br>`03-INFRA/scripts/nexgen_core/scheduler.py` | Registra timer systemd o Attività pianificate per la sincronizzazione fail-closed e la vitalità del motore. | `openclaw cron` / automations (`openclaw/docs/cli/cron.md`, `openclaw/docs/automation/cron-jobs.md`). | **tenere** | I timer del motore tengono le macchine convergenti e i guardiani vivi anche quando OpenClaw è fermo o non installato. |
| Cron e pianificazioni dell'utente | Il motore non ha cron utente. | `openclaw cron` (`openclaw/docs/cli/cron.md`, `openclaw/docs/automation/cron-jobs/managing-jobs.md`). | **delegare** | Le automazioni pianificate sono interamente del sottosistema cron di OpenClaw. |
| Notifiche e avvisi (Megafono)<br>`03-INFRA/scripts/nexgen_core/megaphone.py`<br>`nexgen notify-failure`<br>`nexgen bootstrap-alerts` | Avvisi operativi per guasti del motore (guardiani, doctor). Instrada prima attraverso la CLI di OpenClaw, con trasporti diretti come riserva. | `openclaw message send` (`openclaw/docs/cli/message.md`), routing dei canali (`openclaw/docs/channels/channel-routing.md`). | **tenere, come trasporto di riserva** | Quando il gateway è su un'altra macchina o irraggiungibile, un host deve comunque consegnare gli avvisi critici. `megaphone.py` prova già prima OpenClaw e tiene i trasporti diretti come via d'emergenza. |
| Briefing di sessione (`briefing.mjs`, `vault-briefing.txt`)<br>`03-INFRA/agent-universal-layer/hooks/openclaw-guardrail-plugin/briefing.mjs` | Inietta il contesto del Vault all'inizio della sessione e dopo una compaction. | Hook di avvio sessione, prompt di bootstrap, compaction del contesto (`openclaw/docs/openclaw-agent-runtime.md`). | **tenere** | Il briefing porta le regole canoniche del Vault tra le CLI, come hook del plugin nell'harness. |
| Checkpoint di continuità di sessione (`checkpoint.mjs`)<br>`03-INFRA/agent-universal-layer/hooks/openclaw-guardrail-plugin/checkpoint.mjs` | Scrive `openclaw-checkpoint.json` alla chiusura della sessione, così Claude Code può riprendere il contesto. | Gestione delle sessioni (`openclaw/docs/cli/sessions.md`), trascrizioni (`openclaw/docs/cli/transcripts.md`). | **tenere / delegare** | Lo stato di sessione lo gestisce OpenClaw; il checkpoint esiste per il passaggio tra runtime verso Claude Code. |
| Memoria persistente e semantica (`nexgen vault *`)<br>`03-INFRA/scripts/nexgen_core/cli/vault_cmds.py` | Gestisce il KnowledgeVault (push, map, groom, ciclo di vita). | `openclaw memory` (`openclaw/docs/cli/memory.md`, `openclaw/docs/concepts/memory.md`). | **tenere** | Il Vault è la memoria persistente autorevole su Git. OpenClaw gestisce memoria di sessione e dreaming. |
| Configurazione di modelli e profili di autenticazione degli agenti | Genera la base in `openclaw.json` (`profile.py`, `setup_agents.py`). | `openclaw models` (`openclaw/docs/cli/models.md`), `openclaw models auth`, `openclaw models scan`, `openclaw secrets`. | **delegare** | La configurazione dei modelli dal vivo, la scansione e i profili di autenticazione per agente appartengono ai comandi nativi `openclaw models`. |
| Model Governor e riserva di quota (`governor.py`, `governor.mjs`)<br>`03-INFRA/scripts/nexgen_core/openclaw/governor.py` | Calcola e applica le catene di riserva per proteggere gli abbonamenti flat ed evitare gli sforamenti. | Fallback statici dei modelli in `openclaw.json` (`openclaw/docs/concepts/models.md`). | **tenere** | Dominio del motore: routing, quota degli abbonamenti, riserva. |
| Quote degli abbonamenti (`nexgen quota`)<br>`03-INFRA/scripts/nexgen_core/quota.py` | Legge utilizzo e finestre di reset degli abbonamenti flat (Claude, OpenAI, Codex, Antigravity) per nutrire il Governor. | Finestre di utilizzo dei provider (`openclaw/docs/concepts/usage-tracking.md`). | **tenere** | Nutre il Governor perché le catene di riserva siano scelte in modo deterministico, senza spendere token a pagamento. |
| Tracciamento generale di token e costi | `nexgen-local sup costi` traccia solo i costi delle unità supervisionate. | `/usage cost`, `/status`, `openclaw status --usage` (`openclaw/docs/concepts/usage-tracking.md`, `openclaw/docs/reference/api-usage-costs.md`). | **delegare** | Il monitoraggio ampio di token, costi, spesa per provider e tendenze appartiene a OpenClaw. |
| Gestione canonica delle skill (`nexgen skill *`)<br>`03-INFRA/scripts/nexgen_core/cli/skill_cmds.py`<br>`03-INFRA/scripts/nexgen_core/skills.py` | Catalogo canonico multipiattaforma (`skills.manifest.yaml`), verifica dei pin, depwatch, propagazione. | `openclaw skills` (`openclaw/docs/cli/skills.md`), Skill Workshop (`openclaw/docs/tools/skill-workshop/personal-library.md`). | **tenere** | La libreria canonica è responsabilità del motore. OpenClaw crea skill in modo interattivo; il motore le adotta. |
| Adozione delle skill dal Workshop (`nexgen openclaw adopt --skills`)<br>`03-INFRA/scripts/nexgen_core/openclaw/adopt.py`<br>`workshop.mjs` | Adotta nel catalogo canonico le skill create nello Skill Workshop. | Skill Workshop (`openclaw/docs/tools/skill-workshop/personal-library.md`). | **delegare / tenere** | OpenClaw fornisce l'ambiente di scrittura; il motore promuove le skill adottate agli altri runtime. |
| Misura e gabbia dei modelli locali (`nexgen-local eval`, `nexgen-local tier`)<br>`03-INFRA/scripts/nexgen_local/cmds/run.py`<br>`03-INFRA/scripts/nexgen_local/cmds/tier.py` | Esegue suite di trappole avversarie (prompt injection, confabulazione) per assegnare i livelli di contenimento. | OpenClaw non ha suite di contenimento avversarie. | **tenere** | Dominio del motore: la gabbia e il controllo delle prove. |
| Doctor e diagnostica di sistema (`nexgen doctor`)<br>`03-INFRA/scripts/nexgen_core/doctor.py` | Verifica allineamento tra CLI, remote git, deriva degli MCP, regole invarianti. | `openclaw doctor` (`openclaw/docs/cli/doctor.md`). | **tenere** | Ogni doctor valida il proprio dominio: `openclaw doctor` controlla l'installazione di OpenClaw, `nexgen doctor` la convergenza dell'intero ecosistema multi-CLI. |
| Accoppiamento di nodi e dispositivi (`nexgen openclaw pair`, `node`)<br>`03-INFRA/scripts/nexgen_core/cli/openclaw_cmds.py` | Comandi comodi per accoppiare nodi e telefoni. | `openclaw nodes` (`openclaw/docs/cli/nodes.md`), `openclaw pairing` (`openclaw/docs/cli/pairing.md`), `openclaw devices` (`openclaw/docs/cli/devices.md`). | **delegare** | Il motore offre una scorciatoia; la registrazione dei dispositivi e l'accoppiamento crittografico sono di OpenClaw. |
| Aggiornamenti del software del motore (`nexgen update`)<br>`03-INFRA/scripts/nexgen_core/updater.py` | Aggiorna NeXgen Engine dal repository sorgente o da una release. | `openclaw update` (`openclaw/docs/cli/update.md`). | **tenere** | Ogni aggiornatore aggiorna il proprio codice. |

## Decisioni operative per il backend locale `agy`

`agy` gira in due posti contemporaneamente: la corsia di supervisione lo avvia direttamente, e OpenClaw avvia la stessa CLI attraverso il plugin di backend CLI `google-antigravity-cli`. Entrambi devono passargli la stessa postura, gli stessi strumenti e la stessa lista di modelli.

| Fatto | Fuori da OpenClaw | Dentro OpenClaw |
|---|---|---|
| Modalità di esecuzione | `agy --mode accept-edits` | il plugin gestisce il proprio argv |
| Giro in sola lettura | `agy --mode plan` | un turno per messaggio: nessuna modalità scout |
| Modello | `agy --model <id>`, da `sup run --model` | la rotta `google-antigravity-cli/<id>` |
| Server MCP | `~/.gemini/antigravity-cli/mcp_config.json` | lo stesso file: il plugin avvia `agy` nella home dell'utente |

### `permissionMode` non è `--mode`

Il plugin dichiara tre modalità di permesso, e nessuna è una modalità di esecuzione. `skip` è l'autoapprovazione di `agy` (`--dangerously-skip-permissions`), `sandbox` accende la sandbox del terminale della CLI (`--sandbox`), e `settings` lascia l'approvazione al file di impostazioni di `agy`. `--mode accept-edits|auto|plan` è un altro asse: dice se la CLI può modificare file, non se una persona approva una chiamata a uno strumento.

Il setup scrive quindi `permissionMode: "sandbox"`, mantiene una modalità protetta già in vigore (`sandbox` o `settings`) e non scrive mai `skip`. `nexgen openclaw doctor` avvisa quando trova `skip`, o nessuna modalità, perché `skip` è il predefinito del plugin. Il motore non abilita né disabilita mai il plugin: allinea l'unica impostazione dentro una voce che il proprietario ha già approvato.

Una differenza resta, ed è dichiarata invece che nascosta. La corsia di supervisione passa `--mode accept-edits` nella scheda che apre, mentre il plugin lascia la modalità di esecuzione al proprio argv, dove il motore non indovina. Entrambi i lati tengono la stessa regola, che `agy` non approva mai da solo le proprie chiamate a strumenti, e la modalità che il motore può verificare è quella che scrive.

### Un solo file MCP, senza il gateway

`agy` legge `~/.gemini/antigravity-cli/mcp_config.json`, e il motore scrive quel file dal manifest canonico (`~/.gemini/antigravity/mcp_config.json`, distribuito alle cartelle che Antigravity legge).

Quando OpenClaw pilota il backend locale `agy`, il renderer di antigravity scrive i server che OpenClaw monta direttamente e lascia fuori `lazy-mcp`: la voce del gateway appartiene a OpenClaw e porta la sua identità di CLI (`LAZY_MCP_CLI=openclaw`), che non è quella che servirebbe ad `agy`, e `agy` raggiunge quei connettori direttamente attraverso questo file. Il plugin avvia la stessa CLI dalla stessa home, quindi un file deve servire entrambe le vite. È l'unico dialetto in cui un server dichiarato `exposure: lazy` non è servito dal gateway: dei due contesti che leggono il file, vince quello che non può usare questo gateway.

### Il selettore dei modelli su ogni agente

`model` su un agente è solo il suo predefinito. `nexgen openclaw setup` registra un carattere jolly per ogni provider raggiungibile in `agents.defaults.modelPolicy.allow`, e la squadra registra ogni modello che nomina, così il selettore di ogni agente elenca tutti i modelli che questa macchina può eseguire, compreso il backend locale `agy`. Un runtime di CLI è un pin sul modello, non un provider in quella lista: un login Claude dà `anthropic/*` con `agents.defaults.models["anthropic/*"]` fissato a `claude-cli`, e `agents.defaults.model` nomina il modello dell'assistente come primario di sistema, così `doctor --fix` di OpenClaw trova la forma verso cui avrebbe migrato e non cambia nulla. La regola e le sue ragioni stanno in `nexgen_core/openclaw/model_policy.py`; quello che il proprietario vede è in [La squadra come dato](squad.md#scelta-del-modello-per-agente).

## Cosa è stato ritirato

`nexgen shell`, con gli alias `interactive` e `repl`:

- `03-INFRA/scripts/nexgen_core/tools/shell.py` non c'è più.
- `nexgen shell` e `nexgen info -i` stampano un avviso di rinvio: `The interactive shell has been retired: use 'openclaw tui' or the OpenClaw Dashboard instead.`
- I test interni della REPL ormai inutili (`test_nexgen_toolshellsplit_pinning.py`) sono stati rimossi; i test del rinvio e del parser restano in `test_nexgen_info_shell.py`.

`nexgen-local run <domanda>`:

- Il comando rivolto alla persona è ritirato. Eseguendolo stampa `nexgen-local run è stato ritirato: usa 'openclaw agent' o 'openclaw tui' (l'esecuzione degli agenti appartiene a OpenClaw).`
- `run` è uscito dalla lista dei sottocomandi di `nexgen-local --help` (`EXPECTED_COMMANDS` in `test_nexgen_local_help_surface.py`) ed è registrato in `RETIRED_COMMANDS`.
- `run_lane` resta in `nexgen_local/engine.py` come driver interno per `eval` e `tier evaluate`.
