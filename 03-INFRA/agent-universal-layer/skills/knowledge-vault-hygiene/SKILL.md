---
name: knowledge-vault-hygiene
description: Use whenever updating the user's KnowledgeVault, saving memory/vault notes, writing durable runbooks or project state, auditing vault noise, or deciding whether operational findings should be persisted. Enforces concise, stable, non-sensitive, low-noise vault updates instead of raw debug diaries.
---

# Knowledge Vault Hygiene

Use the KnowledgeVault as durable operational memory, not as a transcript, log sink, or scratchpad. Every Codex/Claude Code session should be aware that the vault exists as the user's memory layer.

## Vault Location

Use the local KnowledgeVault for the current machine:

- Linux/Mac: `<vault-root>`
- Windows: `%USERPROFILE%\KnowledgeVault`
- If agent instructions define a different local vault path, use that path.

## When To Consult The Vault

Consult the local KnowledgeVault when the user asks, and also at the agent's discretion whenever:

- the answer depends on the user-specific preferences, projects, infrastructure, career context, or prior decisions
- you feel uncertain, lack a key piece of context, or would otherwise make a broad assumption
- a task touches the remote backend, n8n, local agent tooling, the vault itself, portfolio/career material, or long-running personal projects
- you are about to write durable documentation and need the current canonical state

Retrieval rule: read `00-START-HERE.md` first, then only the single most relevant note. Do not preload the vault or load broad context unless the task genuinely requires consolidation.

## Decision Rule

Before writing to the vault, ask:

1. Will this still matter in weeks or months?
2. Will a future agent or the user reuse it to make a decision, restore a setup, run a command, understand project state, or avoid repeating work?
3. Is it non-sensitive and safe to sync?

If any answer is no, do not persist it unless the user explicitly asked to save it.

## What Belongs

Store stable, reusable facts:

- current canonical setup or architecture
- final diagnosis and fixed root cause
- commands/procedures that should be reused
- rollback or recovery steps
- project state, decisions, constraints, preferences
- non-sensitive inventory and paths needed for restoration
- unresolved risks or follow-up items that will matter later

For debugging-heavy work, write the compressed outcome:

- symptom
- root cause
- final fix
- canonical verification command/result
- current state
- rollback note if useful

## What Does Not Belong

Do not store:

- raw logs, full stack traces, terminal transcripts, or whole conversations
- every failed attempt or micro-run of debug
- temporary metrics that will expire quickly
- "then I ran X, then Y failed, then Z" diary entries
- duplicate copies of repo docs, README files, or generated output
- throwaway filenames, screenshots, exports, cache paths, or local clutter
- secrets, private keys, passwords, bearer tokens, session tokens, `.env` values, or credential dumps

Short excerpts are acceptable only when they are the minimal evidence needed to recognize a recurring failure.

## Update Style

- Prefer updating an existing canonical note over creating a new note.
- Keep entries short: usually 5-20 lines for a completed task.
- If a topic is becoming large, split only stable procedures into a dedicated runbook and leave a short link from the hub note.
- Replace stale details instead of appending contradictory history.
- As a project advances, progressively compress the project note: remove or collapse superseded plans, wrong assumptions, completed TODOs, stale logs, and old debugging branches that no longer help future decisions. Keep the current state, the reusable runbook, the final decisions, the verified commands, and the live backlog.
- Do not create one commit per micro-observation when working locally; consolidate related updates before publishing when possible.
- Preserve the cloud-first policy: pull safely, avoid overwriting local changes, publish intentionally.

## Lifecycle Cleanup Protocol

Use this protocol when the user asks to clean the vault or when an audit shows stale, oversized, duplicated, or noisy notes.

Start with a read-only pass:

- run or inspect the local lifecycle audit if available, for example `python3 03-INFRA/scripts/vault-lifecycle-audit.py --today YYYY-MM-DD`
- inspect headings with `rg -n "^(#|##|###) " <note>`
- read only the relevant slices before editing
- check current canonical neighbors before deciding that content is stale

Deletion rule:

- delete immediately only obvious junk: test notes, empty notes, cache, temporary exports, duplicate generated files, obsolete scratch artifacts, or content already fully absorbed elsewhere
- do not delete ambiguous historical material; archive it instead
- never delete secrets history, backup material, or recovery notes just because they are old

Compression pattern for oversized active notes:

1. Move the full old note to an archive path such as `<area>/archive/<slug>-archive-YYYY-MM-DD.md`.
2. Mark the archive with `status: archive`, `type: archive`, `last_reviewed`.
3. Recreate the active note as the short current map: purpose, current state, rules, canonical commands, live risks, and links.
4. Update `00-START-HERE.md`, `99-INDEX/vault-cleanup-backlog.md`, and any obvious hub pointer.
5. Regenerate the note index when the repo provides a script.
6. Run diff checks, the lifecycle audit, then commit and push.

Architecture maps are live contracts. If a cleanup touches sync, MCP, agents, doctor, healthcheck, tunnels, CRM, or write policy, the relevant map must be updated or linked. A structural change without an updated map or pointer is incomplete.

## Red Flags

Pause and compress before writing if the draft contains:

- timestamps for many individual attempts
- long command outputs
- more history than current operating guidance
- raw error blocks longer than a few lines
- values that are useful only for the current hour
- sensitive paths or credential-adjacent material

The preferred final shape is: "Here is the state, here is the rule, here is the command, here is the rollback."
