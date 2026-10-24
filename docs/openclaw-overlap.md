# Mappa delle sovrapposizioni: NeXgen Engine e OpenClaw

Questo documento definisce il confine architetturale tra il motore NeXgen e il telaio OpenClaw (2026.10.6), censendo ogni funzione sovrapposta e motivando la scelta tra **ritira**, **delega** e **tieni**.

## Principio architetturale

OpenClaw è il **telaio** di esecuzione e interazione. Al motore NeXgen rimangono **esclusivamente**:
1. **Council**: consulto e revisione multi-modello indipendente.
2. **Supervisione (`sup`)**: gestione compiti con verifica e gatekeeping indipendente.
3. **La gabbia**: guardrail di sicurezza, postura delle autorizzazioni, gabbia di contenimento del modello locale.
4. **Governor dei modelli**: routing basato su disponibilità e stato quote degli abbonamenti, catene di fallback.
5. **Prova delle affermazioni (evidence)**: verifica fattuale delle risposte contro ricevute reali di recupero/esecuzione.
6. **Libreria canonica di skill e MCP**: manutenzione delle fonti canoniche, pin delle versioni verificate, depwatch, propagazione.
7. **Il Vault e la memoria canonica**: persistenza duratura su file Markdown versionati, CAS, igiene e push.
8. **Servizi terzi**: gestione stack ausiliari containerizzati (n8n, vault-mcp, OCR).
9. **Canali di aggiornamento del motore**: verifica e aggiornamento del codice del motore stesso.
10. **Sincronizzazione cross-CLI e cross-macchina**: rendering e allineamento fail-closed delle configurazioni tra Linux e Windows su tutte le CLI supportate (Claude Code, Codex, Antigravity, OpenCode, OpenClaw).

**Tutto il resto appartiene a OpenClaw** e viene usato direttamente:
- Interfaccia grafica web, app desktop e mobile/telefono
- Esecuzione agenti, loop di interazione e instradamento
- Workboard
- Canali di messaggistica (Telegram, WhatsApp, Discord, Slack, Matrix, ecc.)
- Memoria di sessione, transcripts, semantica e dreaming
- Skill Workshop
- Cron, scheduler e automazioni pianificate
- Gestione dei nodi distribuiti
- Voce e sintesi vocale (TTS/STT)
- Tracciamento dettagliato di uso, token e costi
- Tool Search ed esecuzione strumenti
- Approvazioni interattive

I doppioni del motore che replicano capacità già coperte da OpenClaw vengono ritirati o delegati.

---

## Tabella di inventario delle sovrapposizioni

| Componente motore (file e comando) | Cosa fa il motore | Funzione OpenClaw (documentazione installata citata) | Verdetto | Motivazione e prova |
|---|---|---|---|---|
| `nexgen shell` / `repl` / `interactive`<br>`03-INFRA/scripts/nexgen_core/tools/shell.py`<br>`03-INFRA/scripts/nexgen_core/cli/engine.py` | REPL interattiva da terminale (`cmd.Cmd`) con menu numerato per eseguire comandi del motore. | `openclaw tui` (`openclaw/docs/cli/tui.md`, `openclaw/docs/web/tui.md`), Web Dashboard (`openclaw/docs/cli/dashboard.md`), Control UI (`openclaw/docs/web/control-ui.md`). | **ritira** | OpenClaw fornisce interfacce utente complete (TUI da terminale e Web Control UI). La REPL del motore è un doppione sterile e ridondante. |
| `nexgen info -i` / `--interactive`<br>`03-INFRA/scripts/nexgen_core/cli/engine.py` | Avvia la shell interattiva del motore al termine della visualizzazione dello stato. | `openclaw tui` (`openclaw/docs/cli/tui.md`), Control UI (`openclaw/docs/web/control-ui.md`). | **ritira** | L'opzione `--interactive` avviava `nexgen shell`, ora ritirata. L'interfaccia interattiva compete a OpenClaw. |
| `nexgen info` / `status`<br>`03-INFRA/scripts/nexgen_core/tools/info.py`<br>`03-INFRA/scripts/nexgen_core/cli/engine.py` | Mostra dashboard testuale dell'architettura multi-CLI del motore, stato sync, moduli e memoria. | `openclaw status` (`openclaw/docs/cli/status.md`), Dashboard (`openclaw/docs/web/dashboard.md`). | **tieni** | `nexgen info` monitora lo stato del motore e la coerenza tra tutte le CLI (Claude, Codex, Antigravity, OpenCode, OpenClaw). OpenClaw conosce solo se stesso. |
| `nexgen-local run <question>`<br>`03-INFRA/scripts/nexgen_local/cmds/run.py`<br>`03-INFRA/scripts/nexgen_local/engine.py` | Esegue un loop autonomo di query-answering con modello locale e connettori incorporati (vault, web, drive, ecc.). | `openclaw agent` (`openclaw/docs/cli/agent.md`), runtime di esecuzione agenti (`openclaw/docs/openclaw-agent-runtime.md`), chat TUI/web. | **ritira** | OpenClaw è l'harness designato per chattare con agenti e modelli locali/remoti. Il motore non deve eseguire loop autonomi di conversazione per l'utente. |
| Connettori locali incorporati (`calendar.py`, `drive.py`, `gmail.py`, `outlook.py`, `web.py`)<br>`03-INFRA/scripts/nexgen_local/connectors/` | Connettori API ad hoc per Google Workspace, Microsoft Graph e scraping web integrati nel motore. | Server MCP standard (`openclaw/docs/cli/mcp.md`, `openclaw/docs/tools/code-mode/tool-surface.md`) e integrazione browser/web (`openclaw/docs/tools/browser/agent-tools.md`). | **ritira** (in richieste) | I connettori scritti a mano duplicavano i server MCP standard (`workspace-mcp`, `outlook`). La migrazione a MCP standard elimina codice duplicato. |
| Caricamento Drive (`drive-propose`, `drive-upload`, `drive-mcp`)<br>`03-INFRA/scripts/nexgen_local/cmds/drive.py`<br>`03-INFRA/scripts/nexgen_local/drive_mcp.py` | Staging e upload file verso Google Drive con server stdio MCP dedicato. | File transfer nativo (`openclaw/docs/cli/file-transfer.md`) o server MCP standard Google Workspace montati via OpenClaw. | **ritira** (in richieste) | Ritiro rischioso: richiede aggiornamento coordinato del manifest MCP e delle autorizzazioni utente; gestito tramite richiesta al supervisore. |
| Timer del guardiano e battito (`agent-sync.timer`, `agent-heartbeat.timer`)<br>`03-INFRA/scripts/nexgen_core/scheduler.py` | Registra timer systemd / Scheduled Tasks per sincronizzazione e heartbeat fail-closed del motore. | `openclaw cron` / `automations` (`openclaw/docs/cli/cron.md`, `openclaw/docs/automation/cron-jobs.md`). | **tieni** | I timer del motore garantiscono l'allineamento della macchina e la liveness dei guardiani anche se OpenClaw è spento o non installato. Appartiene al punto 10 (sync). |
| Cron e automazioni applicative dell'utente | Nessun comando cron utente nel motore (delegato a livello concettuale). | `openclaw cron` (`openclaw/docs/cli/cron.md`, `openclaw/docs/automation/cron-jobs/managing-jobs.md`, `openclaw/docs/automation/cron-jobs/schedules.md`). | **delega** | Qualsiasi pianificazione ricorrente per conto dell'utente, agenti o invio periodico è gestita interamente dallo scheduler di OpenClaw. |
| Notifiche e allarmi (`Megaphone`)<br>`03-INFRA/scripts/nexgen_core/megaphone.py`<br>`nexgen notify-failure`<br>`nexgen bootstrap-alerts` | Gestione allarmi operativi del motore (fallimento guardie, doctor). Invia prima via OpenClaw CLI, con fallback Telegram diretto, webhook e desktop. | `openclaw message send` (`openclaw/docs/cli/message.md`), Channel Routing (`openclaw/docs/channels/channel-routing.md`, `openclaw/docs/channels/index.md`). | **delega** | Il motore mantiene la logica di debounce per i propri allarmi interni, ma delega il trasporto primario a OpenClaw (`openclaw message send`). Nel tempo i trasporti diretti terzi andranno ritirati. |
| Briefing di sessione (`briefing.mjs`, `vault-briefing.txt`)<br>`03-INFRA/agent-universal-layer/hooks/openclaw-guardrail-plugin/briefing.mjs` | Inietta il contesto essenziale del Vault all'avvio sessione e dopo compattazione. | Session start hooks, bootstrap prompts, context compaction (`openclaw/docs/openclaw-agent-runtime.md`, `openclaw/docs/automation/hooks/writing-hooks.md`). | **tieni** | Il briefing richiama la memoria canonica e le regole del Vault su ogni CLI (Claude, OpenClaw). È implementato come hook integrato nel telaio. |
| Checkpoint di continuità sessione (`checkpoint.mjs`)<br>`03-INFRA/agent-universal-layer/hooks/openclaw-guardrail-plugin/checkpoint.mjs` | Scrive `openclaw-checkpoint.json` al termine sessione per consentire a Claude Code di riallacciarsi. | Session management nativo (`openclaw/docs/cli/sessions.md`), transcripts (`openclaw/docs/cli/transcripts.md`), dreaming (`openclaw/docs/concepts/dreaming.md`). | **tieni** / **delega** | La memoria di sessione appartiene a OpenClaw; il file di stato serve solo da ponte cross-runtime tra OpenClaw e Claude Code. |
| Memoria semantica e persistente (`nexgen vault *`)<br>`03-INFRA/scripts/nexgen_core/cli/vault_cmds.py` | Gestisce il KnowledgeVault (push, map, groom, lifecycle, quarantena). | `openclaw memory` (`openclaw/docs/cli/memory.md`, `openclaw/docs/concepts/memory.md`). | **tieni** | Il Vault è la fonte autorevole della memoria canonica persistente su Git. OpenClaw gestisce la memoria di sessione ed estrae tramite dreaming, ma il Vault rimane l'ancora di verità. |
| Gestione modelli e profili auth per agente | Genera la configurazione iniziale in `openclaw.json` (`profile.py`, `setup_agents.py`). | `openclaw models` (`openclaw/docs/cli/models.md`), `openclaw models auth`, `openclaw models scan`, `openclaw secrets` (`openclaw/docs/cli/secrets.md`). | **delega** | La configurazione viva, la scansione e l'autenticazione dei modelli per singolo agente è gestita nativamente dai comandi `openclaw models`. |
| Governor dei modelli e fallback quote (`governor.py`, `governor.mjs`)<br>`03-INFRA/scripts/nexgen_core/openclaw/governor.py` | Calcola e applica catene di fallback per preservare gli abbonamenti ed evitare sforamenti di spesa. | Fallback statici in `openclaw.json` (`openclaw/docs/concepts/models.md`). | **tieni** | Rientra esplicitamente nei compiti riservati al motore: "governor dei modelli (routing, quota degli abbonamenti, fallback)". |
| Quota abbonamenti (`nexgen quota`)<br>`03-INFRA/scripts/nexgen_core/quota.py` | Legge l'utilizzo e il tempo al reset degli abbonamenti (Claude, OpenAI, Codex, Antigravity) per informare il Governor. | Provider usage windows (`openclaw/docs/concepts/usage-tracking.md`). | **tieni** | Necessario al Governor per calcolare deterministicamente le catene di fallback senza spendere token a pagamento. |
| Tracciamento token e costi generali di sessione | `nexgen-local sup costi` traccia solo i costi dei compiti supervisionati. | `/usage cost`, `/status`, `openclaw status --usage` (`openclaw/docs/concepts/usage-tracking.md`, `openclaw/docs/reference/api-usage-costs.md`, Control UI Usage). | **delega** | Tutto il monitoraggio generale di costi, token, spesa per provider e trend appartiene nativamente a OpenClaw. |
| Gestione delle skill canoniche (`nexgen skill *`)<br>`03-INFRA/scripts/nexgen_core/cli/skill_cmds.py`<br>`03-INFRA/scripts/nexgen_core/skills.py` | Catalogo canonico multipiattaforma (`skills.manifest.yaml`), verifica pin, depwatch, propagazione. | `openclaw skills` (`openclaw/docs/cli/skills.md`), Skill Workshop (`openclaw/docs/tools/skill-workshop/personal-library.md`). | **tieni** | La libreria canonica e la sua manutenzione sono riservate al motore ("libreria canonica di skill e MCP"). OpenClaw crea le skill a caldo nello Skill Workshop; il motore le adotta. |
| Adozione skill da Skill Workshop (`nexgen openclaw adopt --skills`)<br>`03-INFRA/scripts/nexgen_core/openclaw/adopt.py`<br>`workshop.mjs` | Rileva le skill create in OpenClaw Skill Workshop e le integra nel catalogo canonico. | Skill Workshop (`openclaw/docs/tools/skill-workshop/personal-library.md`). | **delega** / **tieni** | OpenClaw è l'ambiente di authoring (Skill Workshop); il motore garantisce la promozione canonica verso tutti gli altri runtime. |
| Misurazione e gabbia modelli locali (`nexgen-local eval`, `nexgen-local tier`)<br>`03-INFRA/scripts/nexgen_local/cmds/run.py`<br>`03-INFRA/scripts/nexgen_local/cmds/tier.py` | Esegue suite di trappole avversarie (iniezioni prompt, confabulazioni) per assegnare tier di contenimento. | Nessuna suite di gabbia avversaria in OpenClaw. | **tieni** | Rientra esplicitamente in "la gabbia (guardrail, postura, gabbia del modello locale)" e "evidence". |
| Doctor e diagnostica generale (`nexgen doctor`)<br>`03-INFRA/scripts/nexgen_core/doctor.py` | Verifica allineamento cross-CLI, git remotes, MCP drift, regole invarianti. | `openclaw doctor` (`openclaw/docs/cli/doctor.md`). | **tieni** | Ciascun doctor controlla il proprio dominio: `openclaw doctor` valida l'installazione di OpenClaw, `nexgen doctor` valida la convergenza dell'intero sistema. |
| Accoppiamento nodi e dispositivi (`nexgen openclaw pair`, `node`)<br>`03-INFRA/scripts/nexgen_core/cli/openclaw_cmds.py` | Comandi wrapper per facilitare il pairing con nodi e telefoni. | `openclaw nodes` (`openclaw/docs/cli/nodes.md`), `openclaw pairing` (`openclaw/docs/cli/pairing.md`), `openclaw devices` (`openclaw/docs/cli/devices.md`). | **delega** | Il motore offre una scorciatoia ergonomica, ma l'intero sistema di pairing crittografico e registrazione nodi appartiene a OpenClaw. |
| Aggiornamenti software (`nexgen update`)<br>`03-INFRA/scripts/nexgen_core/updater.py` | Aggiorna il pacchetto NeXgen Engine dal repo sorgente o release. | `openclaw update` (`openclaw/docs/cli/update.md`). | **tieni** | Ciascun aggiornatore aggiorna il rispettivo software ("i canali di aggiornamento del motore"). |

---

## Decisioni di ritiro e delega

### 1. Ritiri sicuri eseguiti in questa unità
- **`nexgen shell` (e alias `interactive`, `repl`)**:
  - Rimosso `03-INFRA/scripts/nexgen_core/tools/shell.py`.
  - In `03-INFRA/scripts/nexgen_core/cli/engine.py`, il comando `nexgen shell` e l'opzione `-i` di `nexgen info` lasciano un messaggio di reindirizzamento chiaro:
    `Use 'openclaw tui' or the OpenClaw Dashboard: the interactive shell belongs to OpenClaw.`
  - Rimossi i test isolati dedicati alla REPL interna (`test_nexgen_toolshellsplit_pinning.py`, `test_nexgen_info_shell.py`), sostituiti da test sul messaggio di reindirizzamento.

### 2. Funzioni delegate e richieste per il supervisore
- **Ritiro dei connettori legacy e di `drive-mcp` nella corsia locale**:
  - I moduli in `03-INFRA/scripts/nexgen_local/connectors/` (`calendar.py`, `drive.py`, `gmail.py`, `outlook.py`, `token_store.py`, `web.py`) e i verbi `drive-propose`, `drive-upload`, `drive-mcp` sono candidati al ritiro definitivo.
  - *Motivo della richiesta al supervisore*: `drive-mcp` è censito in `03-INFRA/agent-universal-layer/mcp/manifest.yaml` e toccato da `tools_profile.py` (su cui lavora un'altra unità in parallelo). Il ritiro richiede una migrazione pulita delle configurazioni MCP.
- **Ritiro del verbo interattivo `nexgen-local run`**:
  - Il comando `nexgen-local run` è candidato al ritiro con messaggio verso `openclaw agent` o la TUI di OpenClaw. La logica interna `run_lane` rimane disponibile per l'esecuzione headless delle suite di misura `eval` e `tier evaluate`.
- **Semplificazione Megaphone verso i canali OpenClaw**:
  - `Megaphone` già tenta prima la consegna tramite `openclaw message send`. Proposta di ritirare il trasporto Telegram diretto interno una volta verificata la stabilità di OpenClaw sui server e sulle macchine desktop.
