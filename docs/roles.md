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
