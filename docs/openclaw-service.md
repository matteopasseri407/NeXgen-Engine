# OpenClaw gateway service: architecture and operations

🇮🇹 [Italiano](it/openclaw-service.md)

OpenClaw acts as the execution harness for NeXgen Engine. Its gateway daemon keeps background runtime sessions, connects the configured tool providers, and listens on port `18789` by default. The pinned release is the one in `03-INFRA/agent-universal-layer/openclaw/pin.json`.

This page covers the cross-platform service architecture, the Windows 11 behaviours verified in the field, the known pitfalls, and the pairing workflow implemented in `nexgen_core.openclaw_service` and `nexgen_core.openclaw_pairing`.

## Service mechanics by platform

The gateway state directory is resolved in this order: the parent of `$OPENCLAW_CONFIG_PATH`, then `$OPENCLAW_STATE_DIR`, then `~/.openclaw`.

### Windows 11

`openclaw gateway install` generates `<state dir>/gateway.cmd`, which launches:

```cmd
node <openclaw_dir>\dist\index.js gateway --port 18789 --task-supervisor
```

Autostart works in two ways:

1. Task Scheduler: a task named `"OpenClaw Gateway"`, queried with `schtasks /Query /TN "OpenClaw Gateway"` and started with `schtasks /Run /TN "OpenClaw Gateway"`.
2. Startup VBS fallback: when Task Scheduler is unavailable or disabled, an `OpenClaw Gateway.vbs` script goes in the user's Startup folder (`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup`) and runs `gateway.cmd` invisibly through `wscript.exe`.

### Linux

OpenClaw registers a systemd user unit at `~/.config/systemd/user/openclaw-gateway.service`, managed with the usual user-session commands:

```bash
systemctl --user status|start|stop|restart openclaw-gateway.service
```

## Windows 11 pitfalls and verified remedies

`openclaw gateway restart` is unreliable. On Windows it often hangs or leaves orphan child processes, and two gateways end up competing for the same socket. `nexgen_core.openclaw_service` runs a clean kill-and-relaunch cycle instead:

1. Enumerate processes to find every `node` instance running `openclaw` and `dist\index.js gateway`, plus any `cmd.exe` running `gateway.cmd`.
2. Terminate the process tree with `taskkill.exe /PID <pid> /T /F`.
3. Relaunch through `wscript.exe "<startup>\OpenClaw Gateway.vbs"`, or `schtasks /Run /TN "OpenClaw Gateway"` when the task exists.

A cold start of the gateway can take up to five minutes before port `18789` accepts connections. `restart(wait=True, timeout_s=600.0)` polls the TCP port on loopback with a default ten-minute timeout, reports the elapsed seconds when it is ready, and returns a timeout error when it is not.

Editing the `plugins` block of `openclaw.json` while the gateway runs corrupts the running state and the active MCP subscriptions. Use the `stopped()` context manager whenever you mutate the configuration:

```python
from nexgen_core.openclaw_service import OpenClawService

service = OpenClawService()
with service.stopped():
    # Safely modify openclaw.json plugins or MCP settings
    update_openclaw_configuration()
# The gateway is restarted in the finally block, even if an exception was raised
```

The OpenClaw Companion app from the Microsoft Store installs a second gateway as an MSIX package under `%LOCALAPPDATA%\Packages` and drops an `OpenClaw Companion.lnk` shortcut in the Startup folder. With both active, the two keep fighting over port `18789`. `OpenClawService.status()` inspects `%LOCALAPPDATA%\Packages` and the Startup folder; when it finds the app it sets `companion_present: True` and suggests the removal command:

```powershell
Get-AppxPackage *OpenClaw* | Remove-AppxPackage
Remove-Item "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\OpenClaw Companion.lnk" -Force
```

Accidental multiple invocations can leave two or more gateway processes running. `status()` reports `dual_gateway: True` when `process_count > 1`, or when the Store Companion conflicts with an active gateway.

## Device pairing protocol

Phones running the OpenClaw companion app pair with the local gateway in three steps:

1. Show the QR code. `pairing.qr()` returns the command `openclaw qr`. The code has to be rendered in the person's interactive terminal, not captured to standard output.
2. Inspect pending pairings. `pairing.pending()` runs `openclaw devices list --json`, with text parsing as a fallback. Every authentication token, secret and credential key is stripped before the records are returned.
3. Approve the pairing. `pairing.approve(device_id)` approves the pending registration with `openclaw devices approve <device_id>`.

A note on transport: an insecure WebSocket connection (`ws://`) gives restricted capabilities.
