# Uninstall

🇮🇹 [Italiano](it/uninstall.md)

No single command removes everything. Removal is manual, because every write this project makes patches a file you
own (see [what-gets-written.md](what-gets-written.md)) rather than installing a system package. This page undoes each
piece in turn.

## 1. Stop the recurring sync (MULTI profile, Linux with systemd)

This stops the timers and deletes the unit files this project created under `~/.config/systemd/user/`: the sync
guard, the liveness heartbeat and the failure-alert template. Nothing outside that folder is touched, and rerunning
the MULTI setup recreates them:

```bash
systemctl --user disable --now agent-sync.timer agent-heartbeat.timer
rm -f ~/.config/systemd/user/agent-sync.service ~/.config/systemd/user/agent-sync.timer \
      ~/.config/systemd/user/agent-heartbeat.service ~/.config/systemd/user/agent-heartbeat.timer \
      ~/.config/systemd/user/agent-alert@.service
systemctl --user daemon-reload
```

On Windows the MULTI provisioner registers scheduled tasks through `schtasks.exe`, backed by the installed
`~/.local/bin/agent-sync.cmd` shim behind a hidden `.vbs` wrapper, instead of systemd units. Remove them with:

```powershell
schtasks /Delete /TN "KnowledgeVault Agent Sync" /F
schtasks /Delete /TN "KnowledgeVault Agent Sync Logon" /F
schtasks /Delete /TN "KnowledgeVault Agent Heartbeat" /F
```

## 2. Restore per-CLI config from the backups, or edit by hand

Before `nexgen sync` or `nexgen guard` overwrites a file it manages, it saves the previous version beside it. Two
naming conventions are in use, so look for both: the MCP renderer (`nexgen_core/renderer.py`) writes
`<file>.bak-<timestamp>` for each per-CLI MCP config, while the guard cycle and the per-CLI permission and guardrail
writers write `<file>.pre-<reason>-<timestamp>.bak` for the files they rewrite. Look next to:

- `~/.claude.json` (the `mcpServers` entries this project added), `.bak-<timestamp>`
- `~/.codex/` config, `.bak-<timestamp>`
- `opencode.json` or `opencode.jsonc` (its `mcp.servers` section, `plugins` and `permissions` entries),
  `.bak-<timestamp>`
- `~/.gemini/antigravity/mcp_config.json`, `.bak-<timestamp>`
- `~/CLAUDE.md`, `~/.codex/AGENTS.md`, `~/.gemini/config/AGENTS.md`, `~/.config/opencode/AGENTS.md` (the
  instructions pointer), `.pre-instructions-<timestamp>.bak`
- `~/.claude/settings.json` (the checkpoint hook, and the permission posture if your vault declared one),
  `.pre-permissions-<timestamp>.bak`

MCP and permission backups keep the three newest, so the copy from before this project first ran may already be gone.
Instruction-pointer backups are not rotated and accumulate.

If a `.bak` file exists from before this project touched the config, restore it. If not, remove only the MCP entries
and the `instructions` or bootstrap pointer this project added; the table in
[what-gets-written.md](what-gets-written.md) names the field per CLI.

## 3. Remove the bootstrap pointer files

These are the per-CLI bootstrap files this project pointed at its instructions. Remove them only if you do not also
use them for something else: deleting one wipes your own content in the same file, not just this project's pointer.

```bash
rm -f ~/CLAUDE.md ~/.codex/AGENTS.md ~/.gemini/config/AGENTS.md
```

## 4. Remove the skill folders this project copied in

There is no fixed list. Which skills exist depends on your own `skills.manifest.yaml`, which is data and not part of
the engine; a fresh install has none until you add some. Check the catalog, the library and each CLI's runtime folder,
and remove only what you recognise as coming from your manifest:

```bash
cat ~/.agents/skills/INDEX.md       # managed catalog, not a skill-body root
ls ~/.agents/skill-library           # managed skill bodies
rm -rf ~/.agents/skill-library/<skill-name>
rm -rf ~/.claude/skills/<skill-name> ~/.codex/skills/<skill-name> ~/.gemini/antigravity-cli/skills/<skill-name>
```

`nexgen skill index` regenerates `~/.agents/skills/INDEX.md` (historical name `skills-sync.py --index`). Do not delete
the whole `~/.agents/skills/` folder if another tool uses its catalog; remove only the managed library folders you
recognise.

## 5. Local-Full mode: tear down the local Docker stack

Do this before step 6, because the compose files it needs live inside the vault clone you are about to delete. If you
ran the connectors on this machine's Docker (`nexgen stack up`), `nexgen stack down` stops them and leaves their data
volumes intact. To remove that data too, back up what you want to keep first (n8n workflows, OCR state), then run
`docker compose down -v` yourself inside each stack's folder under `03-INFRA/deploy/<service>/`.

## 6. Remove the vault clone

This is the destructive step. `~/KnowledgeVault` holds the engine and everything you wrote into it: notes, projects
and `99-INDEX/USER-PROFILE.md`. Deleting it deletes all of that, with no undo unless you have your own backup or a
separate Git remote. Back up anything under `01-NOTES/`, `02-PROJECTS/` or `04-NOW/` that you want to keep, then:

```bash
rm -rf ~/KnowledgeVault   # or wherever you cloned it
```

## 7. Cloud-Server mode: tear down the VPS stack separately

If you deployed `03-INFRA/deploy/` (n8n, OCR, vault-mcp) on a VPS, that is a separate Docker Compose stack on a
separate machine. Nothing here reaches it over the network, so you tear it down yourself. Back up what you want to
keep first, because `docker compose down -v` in that stack's folder deletes its containers and its volumes.

## 8. State and secrets left behind

- `~/.nexgen-engine/`: the machine-local state directory (sync lock file, liveness timestamp, the doctor's cache, the
  dependency-watch report). Safe to delete, plain text, no secrets.
- `99-SECRETS/`: only relevant if it lived inside the clone you removed in step 6. A GPG archive you copied elsewhere
  is untouched by any of the above.
