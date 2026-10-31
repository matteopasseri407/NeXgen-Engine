# Machine roles: which command to run

🇮🇹 [Italiano](it/roles.md)

NeXgen Engine has three machine roles.
Each one is idempotent: the installer takes the machine from zero to ready in one command, and you can rerun it at any time.

## 1. Headless Linux server (`server`)

An always-on Linux server, home lab, VPS or cloud instance without a monitor.

```bash
sh install.sh --role server
```

What it does:

- Needs Python 3.11+ and the matching venv package (`sudo apt install python3-venv` on Ubuntu 24.04, or `python3.X-venv`): the engine builds its own environment with it, and without it every alignment fails with `No module named pip`.
  It verifies the system requirements and obtains Python 3.11+ through user-mode `uv` when it is missing.
- Refuses unsafe headless configurations, such as passwordless `sudo` or a user in the `docker` group.
- Writes the agents' sudo policy to `/etc/sudoers.d/nexgen-agents`: fifteen named commands (container logs and restarts, the compose file in your home, `systemctl` status and the two restarts, `journalctl`, `apt`), never root, never wider than that list.
  `visudo -cf` reads the file before anything privileged happens.
  Without passwordless `sudo` it prints the file and the one `sudo install` command, because an unattended install that stops for a password is an install that hangs.
- Enables systemd user lingering (`loginctl enable-linger`).
- Configures the OpenClaw gateway on loopback (`127.0.0.1:18789`) and exposes it across your tailnet through Tailscale Serve over HTTPS. No external ports are opened.
- Hardens state directory permissions (`chmod 0700`, and clears group and world write) and deposits and verifies the `nexgen` engine plugin entrypoint.
- Generates or updates `$OPENCLAW_STATE_DIR/.env` (`~/.openclaw/.env`, permissions `0600`), preserving existing user-defined keys and adding only the missing variables that configuration and mounted MCP servers reference.
  It never logs secret values.
- Starts the gateway as a background user service.
- Applies the squad configuration (`squad.yaml`) and registers model provider API keys through standard input.
- Treats its checkout of the Vault as read-only: the role is recorded on the machine, `nexgen pull` only fast-forwards that clone, anything local is parked on a `quarantine/diverged-*` branch, and `vault-push` refuses to publish from it.
  Notes are written through `vault-library`, which owns the worktree that publishes.
- Records the role in the machine's own state as soon as the setup configures the gateway. `nexgen role` prints it, and a machine already in service records it with `nexgen role set server` instead of a reinstall.

When you enter this machine as the gateway's user, enter its home too (`sudo -iu <user>`, or `cd` before running anything that talks to OpenClaw).
OpenClaw starts a small helper with the directory it was called from, and a directory that user cannot read makes it fail with `spawn <node> EACCES`, which reads like a broken Node installation and is only a wrong working directory.

That `sudo -iu` session has no `XDG_RUNTIME_DIR` and no `DBUS_SESSION_BUS_ADDRESS`, even with lingering on, so `systemctl --user` cannot reach the user bus.
The installer notices, and when `/run/user/<uid>/bus` exists it points the run at that bus instead of stopping at step 1/9.
If the message still appears, check the bus with `ls -l /run/user/$(id -u)/bus` and that `loginctl enable-linger <user>` has run.

Add `--check` to see this role's plan without writing anything: no launcher, no sudoers file, no gateway.

## 2. Satellite PC or laptop (`node`)

A desktop, laptop or work PC that delegates central orchestration to your server gateway and connects as an execution node.

Mint a single-use join target on the gateway host first:

```bash
openclaw devices join-code
```

Then run the installer on the node machine.
On Linux and macOS:

```bash
sh install.sh --role node --target <join-code-or-url>
```

On Windows:

```powershell
.\install.ps1 -Role node -Target <join-code-or-url>
```

What it does:

- Installs the pinned OpenClaw release before anything asks this PC whether it is a node: the same release and the same Node check that `nexgen openclaw setup` runs (`03-INFRA/agent-universal-layer/openclaw/pin.json`).
  A PC with an older or different version is realigned to the pin, and a Node outside the pin's range stops the role with the setup's own message instead of leaving a half-configured machine.
- Configures the local machine profile as `MINIMAL` (secondary node).
- Synchronizes local developer harnesses (Claude Code, Codex, OpenCode, Antigravity), skills and MCP connectors.
- Connects the node host as an OS service with `openclaw connect --service --session-host --target-file <path>`, which keeps single-use join credentials out of process argument inspection.
- Probes the connection in production with `openclaw node status --json`, reporting progress every few seconds.
- On timeout, prints a diagnosis with the documented recovery commands (`openclaw node status`, `openclaw node restart`, `openclaw node run --session-host`, or a new code from `openclaw devices join-code` on the gateway).
- Tells the operator the exact entry to declare in the central `squad.yaml` on the server (`tools.exec: host: node, node: <name>`), so agent definitions stay centralised without RPC mutation from nodes.
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

Without the flag nothing changes: those approvals belong to the person, and a node whose approvals are meant to stay strict must keep them.
`nexgen openclaw doctor --nodes`, run on the gateway, reads them instead of writing them and warns when the node is stricter than what an agent asks for.

### Already have NeXgen on this PC?

If this computer already runs NeXgen, for example as a standalone or with a local OpenClaw gateway:

1. **Look first, then touch (`--check` / `-Check`).**
   On Linux and macOS:

   ```bash
   sh install.sh --role node --check
   ```

   On Windows (PowerShell):

   ```powershell
   .\install.ps1 -Role node -Check
   ```

   It prints the detailed plan line by line without writing anything: the engine version now and after, whether a gateway or scheduled tasks exist, the node state, what will be stopped and what will stay as it is.
   It does not install OpenClaw; the pinned release is installed on the first real `--role node` run, without `--check`.

2. **Switch to node.**
   Create a single-use join code on the server (`openclaw devices join-code`), then run on the PC:
   On Linux and macOS:

   ```bash
   sh install.sh --role node --join <join-url>
   ```

   On Windows (PowerShell):

   ```powershell
   .\install.ps1 -Role node -Join <join-url>
   ```

   You can set the `NEXGEN_JOIN_URL` environment variable instead.

What to expect:

- **Engine upgrade:** the PC is first updated to the current engine version through the existing channel ([docs/upgrade.md](upgrade.md)), then synchronised. There is never a second copy of the engine beside the first.
- **Local gateway stopped safely:** a local OpenClaw gateway that was running is stopped and disabled at startup with the documented commands (`openclaw gateway stop` and service uninstall; old Windows scheduled tasks or gateway `.vbs` files are removed).
- **State preserved intact:** the `~/.openclaw` folder (configuration, session history, memory), agents, skills and MCP stay as they are. No folder is deleted and no destructive command runs.
- **Rerunnable and reversible:** running it again against the same gateway detects that the PC is already a node (restarting the service with `openclaw node start` if it is down) and only syncs.
  To go back to standalone with the local gateway running again, run `sh install.sh --role standalone` or `.\install.ps1 -Role standalone`.
- **Final summary:** one screen with the connected gateway, the node name, the services stopped, the data kept, and the fragment to add to `squad.yaml` on the server.

## 3. Standalone PC (`standalone`)

A standalone desktop or laptop running its own local OpenClaw gateway and local assistants.

On Linux and macOS:

```bash
sh install.sh --role standalone
```

On Windows:

```powershell
.\install.ps1 -Role standalone
```

What it does:

- Sets up the local user profile, with `openclaw` in the installed CLI registry alongside Claude Code, Codex, OpenCode and Antigravity.
- Provisions the local OpenClaw gateway and the developer tools.
- Hardens state directory permissions, verifies the plugin deposition, and synchronizes skills and MCP servers.
- Updates `~/.openclaw/.env` (0600) for the referenced provider keys without overwriting existing settings.
- Preserves your custom agent names and applies `squad.yaml` when it is present.
