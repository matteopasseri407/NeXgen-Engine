# KnowledgeVault Write Architecture

Principle: **one door per kind of thing.** Cloud-first: the remote backend is the source of truth, the local filesystem is a read-only mirror + offline parachute.

## The two doors

- **Notes / knowledge (markdown)** → ONLY via the `vault-library` MCP (`create_note`, `append_note`, `update_note`). The MCP serializes writes with a lock (`flock`) and an `expected_hash` check, and commits directly to the remote bare repo as author "Vault MCP". Agents **never commit notes by hand with git**.
- **Infra files (scripts, manifests, hooks, config)** → `vault-push -m "message" <file...>`: git commit + push to the remotes with a clean rebase, a safe STOP on real conflicts (never forces).

## Live components

- **`vault-library` MCP** (remote backend, container `vault-mcp`, `:rw`): serialized note writes, commits to the bare repo.
- **`cloud-pull.service`** (enabled): refreshes the local mirror by pulling from the remote backend.
- **`agent-sync.timer` / Windows scheduled task `KnowledgeVault Agent Sync`**: `guard` mode, i.e. cloud pull + automatic propagation of runtime derivatives + healthcheck, with no automatic push. `apply` is the manual alias of guard. Publishing already-made local commits: `publish` or `vault-push`. The old full run: `full`, manual only.
- **`vault-push`** (`03-INFRA/scripts/vault-push.sh`, symlinked into `~/.local/bin`): publishes infra files.

## Retired

- **`autosync.service`** (a filesystem watchdog that auto-committed every 60s): REMOVED. It was the "second door" that generated commits blindly. Inert code left behind in `~/.local/share/knowledge-vault-autosync`, not run anymore.

## Golden rules

1. One source of truth for everything; everything else is generated or a read-only mirror.
2. Notes → MCP; infra → `vault-push`. Never two doors on the same thing.
3. Volatile data (e.g. a calendar agenda) is never versioned: read it live from the MCP connectors instead. The n8n workflow that used to sync it into the vault was archived for this reason.

## Known follow-ups

- Move any plaintext tokens from CLI settings into env vars, so no config file holds a secret literally.
- Windows: `guard/publish/full` mode splitting has landed in `agent-sync.ps1`, but fully wiring `render.py` in place of the hardcoded MCP section remains a follow-up.
