# What gets written

This lists every file the installer (`INIT.md`), `install.sh`, and the MULTI-profile scripts (`agent-sync`, `render.py`) can create or modify outside this repo. Nothing here touches files outside your home directory, and nothing runs with elevated privileges.

## `install.sh`

Writes nothing. It only creates missing scaffold folders inside the repo itself (`01-NOTES/`, `02-PROJECTS/`, `04-NOW/`, `99-INDEX/`, `99-SECRETS/`, each with a `.gitkeep`) if they're missing from your clone, which normally does not happen on a full clone.

## `INIT.md` (the AI-guided installer, any profile)

- `99-INDEX/USER-PROFILE.md`: your profile, hardware, CLI/machine list, and architecture choice.
- Optionally `04-NOW/current-focus.md` or a note under `01-NOTES/`/`02-PROJECTS/`, if you choose to have it ingest a document (CV, project brief, brand rules) during setup.

## Per-CLI bootstrap and MCP config (MINIMAL: done by the agent by hand; MULTI: done by `agent-sync`/`render.py`)

| CLI | Bootstrap file | MCP config | Skills folder |
|---|---|---|---|
| Claude Code | `~/CLAUDE.md` (pointer to this repo's `AGENTS.md`) | `mcpServers` field in `~/.claude.json` | `~/.claude/skills/` |
| Codex | `~/.codex/AGENTS.md` | Codex's own config file | `~/.codex/skills/` |
| OpenCode | `instructions` field in `opencode.json` | MCP section of the same `opencode.json` | shared hub `~/.agents/skills/` |
| Antigravity | `~/.gemini/config/AGENTS.md` | `~/.gemini/antigravity/mcp_config.json` | `~/.gemini/skills/` |

These are patches to files that must already exist (each CLI creates its own default config the first time you open it). Nothing here creates a CLI's config file from scratch; if a chosen CLI has never been opened, that step is skipped for it.

## MULTI profile only, additional writes by `agent-sync`

- `~/.config/systemd/user/agent-sync.service` and `agent-sync.timer`: a recurring user-level timer that runs `agent-sync guard` (pull + regenerate CLI runtime files + healthcheck, no push). Only on Linux/systemd.
- Before overwriting a file it manages, `agent-sync` copies the previous version alongside it with a `.pre-<reason>-<timestamp>.bak` suffix in the same folder.
- `~/.local/state/agent-sync.log`: a plain-text run log.
- `~/ANTIGRAVITY.md`: removed if present as a dead symlink (Antigravity doesn't read that path).

## `99-SECRETS/`

Local only. `agent-sync`/agents may write to `99-SECRETS/archive/master-secrets.md.gpg` (GPG-encrypted, git-ignored) and `99-SECRETS/secrets-registry.md` (names and env vars only, never values, tracked in git). See `99-SECRETS/README.md` for the workflow.

## What this never does

No sudo, no changes outside your home directory, no telemetry, no network call you didn't configure (Cloud-Server mode only reaches the VPS you point it at over the SSH tunnel you set up), and no push to a git remote unless you or an agent explicitly runs a publish step.
