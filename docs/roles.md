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
- Deposits and verifies the `nexgen` engine plugin.
- Generates `~/.openclaw/gateway.systemd.env` (permissions `0600`) containing only the environment variables referenced by configuration and mounted MCP servers.
- Registers systemd drop-in unit and starts the gateway as a background user service.
- Applies squad configuration (`squad.yaml`) and registers model provider API keys via standard input.

---

### 2. Satellite PC / Laptop (`node`)
**What machine do you have?**
A desktop, laptop, or work PC that delegates central orchestration to your server gateway and connects as an execution node.

**Which command to run:**
On Linux / macOS:
```bash
sh install.sh --role node
```
On Windows:
```powershell
.\install.ps1 -Role node
```

If connecting directly with gateway credentials:
```bash
sh install.sh --role node --gateway-url wss://my-server.ts.net --gateway-token <token> --node-name my-laptop
```

**What it does:**
- Configures the local machine profile as `MINIMAL` (secondary node).
- Synchronizes local developer harnesses (Claude Code, Codex, OpenCode, Antigravity), skills, and MCP connectors.
- Registers the node's agent on the gateway via Gateway RPC protocol (`openclaw/gateway_rpc.py`) without directly modifying the remote gateway's disk files.
- Provides periodic status reporting while waiting for connection, with recovery guidance if connection times out.

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
- Sets up local user profile and authoritative remotes.
- Provisions local OpenClaw gateway and developer tools.
- Hardens state directory permissions, verifies plugin deposition, and synchronizes skills and MCP servers.
- Preserves your custom agent names and applies `squad.yaml` if present.
