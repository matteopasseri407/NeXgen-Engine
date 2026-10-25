# Machine Roles: Which Command to Run

NeXgen Engine supports three distinct, idempotent machine roles. Running the installer with the role matching your machine takes it from zero to ready in a single command, and can be rerun safely at any time.

---

### 1. Headless Linux Server (`server`)
**What machine do you have?**
An always-on Linux server, home lab, VPS, or cloud instance without a monitor.

**Which command to run:**
```bash
sh install.sh --role server
```

**What it does:**
- Verifies system requirements and obtains Python 3.11+ via user-mode `uv` if missing.
- Refuses unsafe headless configurations (e.g. passwordless `sudo` or user in `docker` group).
- Writes the agents' sudo policy to `/etc/sudoers.d/nexgen-agents`: fifteen named commands (container logs and restarts, the compose file in your home, `systemctl` status and the two restarts, `journalctl`, `apt`), never root, and never wider than that list. `visudo -cf` reads the file before anything privileged happens. Without passwordless `sudo` it prints the file and the one `sudo install` command, because an unattended install that stops for a password is an install that hangs.
- Enables systemd user lingering (`loginctl enable-linger`).
- Configures OpenClaw gateway on loopback (`127.0.0.1:18789`) and exposes it securely across your tailnet via Tailscale Serve over HTTPS. No external ports are opened.
- Hardens state directory permissions (`chmod 0700` / clears group and world write).
- Deposits and verifies the `nexgen` engine plugin entrypoint.
- Generates or updates `$OPENCLAW_STATE_DIR/.env` (`~/.openclaw/.env`, permissions `0600`), preserving existing user-defined keys and adding only missing variables referenced by configuration and mounted MCP servers. Never logs secret values.
- Starts the gateway as a background user service.
- Applies squad configuration (`squad.yaml`) and registers model provider API keys via standard input.

---

### 2. Satellite PC / Laptop (`node`)
**What machine do you have?**
A desktop, laptop, or work PC that delegates central orchestration to your server gateway and connects as an execution node.

**Which command to run:**
Mint a single-use join target on your gateway host first:
```bash
openclaw devices join-code
```
Then run the installer on the node machine:
On Linux / macOS:
```bash
sh install.sh --role node --target <join-code-or-url>
```
On Windows:
```powershell
.\install.ps1 -Role node -Target <join-code-or-url>
```

**What it does:**
- Configures the local machine profile as `MINIMAL` (secondary node).
- Synchronizes local developer harnesses (Claude Code, Codex, OpenCode, Antigravity), skills, and MCP connectors.
- Connects the node host as an OS service using `openclaw connect --service --session-host --target-file <path>`, protecting single-use join credentials from process argument inspection.
- Probes connection status in production using `openclaw node status --json`, reporting progress every few seconds.
- On timeout, emits clear diagnosis with documented recovery commands (`openclaw node status`, `openclaw node restart`, `openclaw node run --session-host`, or minting a new code via `openclaw devices join-code` on the gateway).
- Instructs the operator on the exact entry to declare in the central `squad.yaml` on the server (`tools.exec: host: node, node: <name>`), keeping agent definition centralized without RPC mutation from nodes.
- With `--exec-policy full` (`-ExecPolicy full` on Windows), also aligns this PC's own exec approvals.

### Why the node role can align its exec approvals

An agent that runs its commands on a node sends them to that PC, and that PC enforces its own approvals before anything starts.
Its policy is not the gateway's: it is the approvals document on the node host, and the effective result is the stricter of the two.
A node is normally a machine with nobody sitting at it, so an approval prompt raised there can only be answered by the fallback, whose documented default is `deny`.
The command then fails with `exec denied: request-failed`, which says nothing about the cause.

`--exec-policy full` writes the body OpenClaw documents for a host that must never stop on an approval, locally on the node, with `openclaw approvals set --stdin`:

```json
{"version": 1, "defaults": {"security": "full", "ask": "off", "askFallback": "full"}}
```

Without the flag nothing is changed: those approvals belong to the person, and a node whose approvals are meant to stay strict must keep them.
`nexgen openclaw doctor --nodes`, run on the gateway, reads them instead of writing them, and warns when the node is stricter than what an agent asks for; the two commands are the same body, from opposite sides.

#### Hai già NeXgen su questo PC?

Se questo computer ha già NeXgen installato (ad esempio come standalone o con un gateway OpenClaw locale):

1. **Prima guarda, poi tocca (`--check` / `-Check`):**
   Su Linux / macOS:
   ```bash
   sh install.sh --role node --check
   ```
   Su Windows (PowerShell):
   ```powershell
   .\install.ps1 -Role node -Check
   ```
   Stampa il piano dettagliato riga per riga senza scrivere nulla: versione attuale e in arrivo del motore, presenza di gateway o attività pianificate, stato del nodo, cosa verrà fermato e cosa rimarrà invariato.

2. **Passaggio a nodo:**
   Crea un codice di aggancio monouso sul server (`openclaw devices join-code`), poi esegui sul PC:
   Su Linux / macOS:
   ```bash
   sh install.sh --role node --join <join-url>
   ```
   Su Windows (PowerShell):
   ```powershell
   .\install.ps1 -Role node -Join <join-url>
   ```
   (In alternativa puoi impostare la variabile d'ambiente `NEXGEN_JOIN_URL`.)

**Cosa aspettarsi:**
- **Aggiornamento motore:** il PC viene prima aggiornato all'ultima versione del motore tramite il canale esistente (`docs/upgrade.md`), poi sincronizzato; mai una seconda copia del motore accanto alla prima.
- **Gateway locale fermato in sicurezza:** se era attivo un gateway OpenClaw locale, viene fermato e disattivato all'avvio con i comandi documentati (`openclaw gateway stop` e disinstallazione del servizio; le vecchie attività pianificate di Windows o file `.vbs` del gateway vengono rimossi).
- **Stato preservato intatto:** la cartella `~/.openclaw` (configurazione, cronologia sessioni, memoria), agenti, skill e MCP rimangono intatti. Nessuna cancellazione di cartelle né comandi distruttivi.
- **Rilanciabile e reversibile:** se rilanciato con lo stesso gateway, rileva che il PC è già nodo (riavviando il servizio con `openclaw node start` se fermo) ed esegue solo il sync. Per tornare a standalone con il gateway locale riattivato, basta eseguire `sh install.sh --role standalone` / `.\install.ps1 -Role standalone`.
- **Riepilogo finale:** una schermata con gateway collegato, nome del nodo, servizi fermati, dati conservati e il frammento da inserire in `squad.yaml` sul server.

---

### 3. Standalone PC (`standalone`)
**What machine do you have?**
A standalone desktop or laptop running its own local OpenClaw gateway and local assistants.

**Which command to run:**
On Linux / macOS:
```bash
sh install.sh --role standalone
```
On Windows:
```powershell
.\install.ps1 -Role standalone
```

**What it does:**
- Sets up local user profile including `openclaw` in the installed CLI registry alongside Claude Code, Codex, OpenCode, and Antigravity.
- Provisions local OpenClaw gateway and developer tools.
- Hardens state directory permissions, verifies plugin deposition, and synchronizes skills and MCP servers.
- Updates `~/.openclaw/.env` (0600) for referenced provider keys without overwriting existing settings.
- Preserves your custom agent names and applies `squad.yaml` if present.
