# OpenClaw Gateway Service Architecture and Operational Guide

OpenClaw (`openclaw`, npm package version 2026.10.6) acts as an execution harness for NeXgen Engine. The gateway daemon maintains background runtime sessions, connects configured tool providers, and listens by default on port `18789`.

This document records the cross-platform service architecture, Windows 11 field-tested behaviors, known pitfalls, and pairing workflows implemented in `nexgen_core.openclaw_service` and `nexgen_core.openclaw_pairing`.

---

## 1. Service Mechanics by Platform

The gateway state directory is resolved in order: `$OPENCLAW_CONFIG_PATH` parent, `$OPENCLAW_STATE_DIR`, or `~/.openclaw`.

### Windows 11
- Running `openclaw gateway install` generates `<state dir>/gateway.cmd` which launches:
  ```cmd
  node <openclaw_dir>\dist\index.js gateway --port 18789 --task-supervisor
  ```
- **Autostart Mechanism**:
  1. **Task Scheduler**: A scheduled task named `"OpenClaw Gateway"`, queried via `schtasks /Query /TN "OpenClaw Gateway"` and triggered with `schtasks /Run /TN "OpenClaw Gateway"`.
  2. **Startup VBS Fallback**: When Task Scheduler is unavailable or disabled, an `OpenClaw Gateway.vbs` script is placed in the user's Startup directory (`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup`), invoking `gateway.cmd` invisibly through `wscript.exe`.

### Linux
- OpenClaw registers a systemd user unit at `~/.config/systemd/user/openclaw-gateway.service`.
- Managed using standard user-session systemctl commands:
  ```bash
  systemctl --user status|start|stop|restart openclaw-gateway.service
  ```

---

## 2. Windows 11 Pitfalls and Verified Remedies

### Pitfall 1: `openclaw gateway restart` is Unreliable
On Windows, `openclaw gateway restart` frequently hangs or leaves orphan child processes running, leading to dual gateway instances competing for the same socket.

**Remedy**:
`nexgen_core.openclaw_service` implements a clean kill-and-relaunch cycle:
1. Enumerate processes to locate all `node` instances executing `openclaw` and `dist\index.js gateway`, as well as `cmd.exe` executing `gateway.cmd`.
2. Terminate the process tree cleanly using `taskkill.exe /PID <pid> /T /F`.
3. Relaunch the service via `wscript.exe "<startup>\OpenClaw Gateway.vbs"` (or `schtasks /Run /TN "OpenClaw Gateway"` if scheduled).

### Pitfall 2: Cold Start Port Delay (~5 Minutes)
A cold start of the OpenClaw gateway daemon can take up to 5 minutes before port `18789` begins accepting connections.

**Remedy**:
`restart(wait=True, timeout_s=600.0)` polls TCP port `18789` on loopback with a default 10-minute timeout, reporting the exact elapsed seconds upon readiness or returning a timeout error if unreachable.

### Pitfall 3: Live Mutation of `plugins.*` Corrupts the Gateway
Editing the `plugins` block of `openclaw.json` while the gateway is running corrupts the running state and active MCP subscriptions.

**Remedy**:
Use the `stopped()` context manager whenever mutating configuration:
```python
from nexgen_core.openclaw_service import OpenClawService

service = OpenClawService()
with service.stopped():
    # Safely modify openclaw.json plugins or MCP settings
    update_openclaw_configuration()
# Gateway is automatically restarted in finally block, even if an exception was raised
```

### Pitfall 4: Conflict with Microsoft Store Companion App
The OpenClaw Companion application from the Microsoft Store installs a secondary gateway packaged as an MSIX package under `%LOCALAPPDATA%\Packages` and drops an `OpenClaw Companion.lnk` shortcut in the user's Startup folder. Having both active causes continuous configuration contention on port `18789`.

**Remedy**:
`OpenClawService.status()` inspects `%LOCALAPPDATA%\Packages` and the Startup folder. If detected, it sets `companion_present: True` and suggests the non-destructive removal command:
```powershell
Get-AppxPackage *OpenClaw* | Remove-AppxPackage
Remove-Item "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\OpenClaw Companion.lnk" -Force
```

### Pitfall 5: Dual Gateway Process Detection
Accidental multiple invocations can cause two or more `node` gateway processes to run simultaneously. `status()` reports `dual_gateway: True` when `process_count > 1` or when the Store Companion conflicts with an active gateway.

---

## 3. Device Pairing Protocol (`openclaw_pairing.py`)

Mobile devices running the OpenClaw companion app pair with the local gateway using the following workflow:

1. **Display QR Code**:
   `pairing.qr()` returns the command `"openclaw qr"`. The QR code must be rendered directly in the user's interactive terminal rather than captured to standard output.
2. **Inspect Pending Pairings**:
   `pairing.pending()` executes `openclaw devices list --json` (with fallback text parsing). **All authentication tokens, secrets, and credential keys are stripped before returning records.**
3. **Approve Pairing**:
   `pairing.approve(device_id)` approves the pending registration:
   ```bash
   openclaw devices approve <device_id>
   ```
   *Note: Insecure WebSocket (`ws://`) connections provide restricted capabilities.*
