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

## Windows 11: what the engine does, and what it no longer does

The service is OpenClaw's. The engine asks for it with `openclaw gateway install`,
`start`, `stop`, `restart`, `status --json` and `uninstall`, and keeps only the
policy around those calls: the status JSON read into the shape the setup and the
doctor already use, a dry run that runs nothing, the Store Companion check below,
and the wait for the port after a restart. OpenClaw's own recovery verifies the
recorded PID and process start time before terminating anything, confirms the exit
and the release of state ownership, retries a transient SQLite share, and the task
supervisor it generates propagates the child's exit result. The engine used to
drive a Startup VBS, `schtasks` and a `taskkill` loop beside that service, and two
mechanisms registering the same daemon is how a gateway ended up invisible to both.

A cold start can take up to five minutes before port `18789` accepts connections.
OpenClaw's own readiness check may give up before that, so `restart(wait=True,
timeout_s=600.0)` treats `restart-health-failed` as still starting and waits on the
engine's own ten-minute budget, reporting the elapsed seconds when it is ready and
a timeout error when it is not. A restart OpenClaw refuses is reported as it came,
and nothing is started beside it.

`nexgen openclaw doctor` adds one line when something else on this machine can
start a gateway too, which is a registration an older release or a second profile
left behind. It names the registration and prints the command that removes it, and
the engine never runs that command: it did not create the registration. A node host
is left out of the line, because it connects out and opens no port here.

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
