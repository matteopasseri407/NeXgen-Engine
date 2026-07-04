---
tags:
  - index
  - entry
status: active
type: index
---

# Start Here

This is the entry point to your Knowledge Vault. Agents read this file at the start of a session to orient themselves before any task.

## What this Vault is

The Knowledge Vault is your durable memory layer: a Git-backed collection of Markdown notes that agents (Claude, Codex, OpenCode, Gemini, etc.) read and write on your behalf. It is not a chat transcript, a log sink, or a scratchpad — it is structured, curated memory.

## How it is organized

- `99-INDEX/` — the map: your profile, retrieval protocol, this entry point.
- `00-START-HERE.md` — this file (orientation).
- `01-NOTES/` — general knowledge notes.
- `02-PROJECTS/` — active and past projects, one canonical note each.
- `03-INFRA/` — the engine: the agent bootstrap (`AGENTS.md`), MCP config, skills, scripts, runbooks.
- `04-NOW/current-focus.md` — what you are working on right now. Agents check this at the first relevant turn.
- `99-SECRETS/` — encrypted secret archive (Git-ignored except for the registry; never commit plaintext secrets).

## What agents do at session start

1. Read `99-INDEX/USER-PROFILE.md` — who you are, your hardware, your paths, your preferences.
2. Read this file (`00-START-HERE.md`).
3. Read `04-NOW/current-focus.md` — your current focus.
4. Apply the retrieval protocol (`99-INDEX/agent-retrieval-protocol.md`) when a task touches your world.

## First-time setup

If you just cloned this repository, open `INIT.md` and paste its content into a new chat with your LLM. It will run the guided installer and fill in `99-INDEX/USER-PROFILE.md` for you.
