# Agent Layer Guardians Map

Operational companion to [[agentic-layer-concept-map]]: the "observability" view of the layer, i.e. who executes, who diagnoses, and who alerts.

## Golden rule

One single place decides whether something is broken, one single place tells the user, everything else executes silently.

## The three roles

- **A brain, `agent-doctor`.** Full diagnosis: git vs remote, MCP reachability, MCP drift via `render.py`, canonical instructions, tokens in env, skills, resolved local worker, Claude hooks, strict-check of real consumers. It is the only judge and the only command meant to be run by hand: `agent-doctor` (or `--summary` for the one-line `PASS/WARN/FAIL`).
- **A megaphone, `agent-healthcheck`.** The ONLY thing authorized to notify. It queries the doctor and alerts only on FAIL, with debounce (immediately if the problem is new, once a day if it persists), plain-language message with `[technical: ...]` appended. Transport order: messaging bot, webhook, desktop `notify-send`, log.
- **A clock, `agent-sync.timer`** (Linux every 30 min, a scheduled task on Windows). The only recurring scheduler. It runs `agent-sync guard` = pull + apply derived config + healthcheck. `agent-sync` only executes and logs, it does NOT notify.

## Consolidation pass (single megaphone)

`agent-sync` used to notify MCP drift on its own (an inline sentinel, no debounce, separate sender), duplicating what `agent-doctor` already computes and `agent-healthcheck` already announces at the end of each run. That path was removed: there is now ONE single alert surface, and `agent-sync` stays silent. Verified: the only `notify-send` call in the layer lives in `agent-healthcheck.sh`.

## Automatic run flow

`agent-sync.timer` triggers `agent-sync guard`:

1. pull the vault from the remote
2. apply: MCP config (`render.py`, additive) + skills (`skills-sync.py`)
3. `agent-healthcheck` queries `agent-doctor` and notifies ONLY on FAIL

## Inventory

| Guardian | Role | What it does |
|---|---|---|
| `agent-sync` (+ `.timer`) | clock | recurring run: pull, apply config and skills, calls the healthcheck. Silent. |
| `agent-doctor` | brain | full alignment diagnosis; the only judge |
| `agent-healthcheck` | megaphone | notifies only on FAIL, with debounce and human-readable format; the only alert |
| `render.py` | executor | generates MCP configs from the manifest (additive); computes drift |
| `skills-sync.py` | executor | propagates skills from the manifest to every machine |
| `skill-check` | executor | advisory security check of a skill (SkillSpector) |
| `sync-vault-from-remote` | executor | pulls the vault from the remote before apply |
| `n8n-vault-backup` | executor | nightly backup of n8n workflows (cron on the remote backend) |
| `sync-job-pipeline` | executor | refreshes the job-search dashboard |
| tunnels to the remote | executor | persistent SSH tunnels (OCR, n8n, firecrawl) |

## Extension principle

A new check goes INSIDE `agent-doctor`, not into a new script that notifies on its own. Anything new that needs to reach the user goes through `agent-healthcheck`. Never add `notify-send` anywhere else: it would break the single-megaphone rule and bring back the scattered noise this consolidation removed.
