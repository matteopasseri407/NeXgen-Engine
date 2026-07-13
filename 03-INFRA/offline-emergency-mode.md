---
tags:
  - infra
  - agents
  - runbook
status: active
type: reference
---

# Emergency Mode — Remote Backend Down or Machine Offline

When the remote backend is unreachable, these fall together: note writes (MCP `vault-library`), semantic RAG, Firecrawl (the default web lane), Vault OCR (the default OCR lane, same remote backend), and n8n. The local layer keeps working in read mode. This runbook says what to do and what not to do, so nobody has to improvise.

## First: figure out what is down

- Internet works but `ssh <remote-alias>` fails: the VPS itself is down, see "Remote recovery" below.
- No network at all: the machine is offline, follow the section below.

## What still works

- The local mirror of the vault, in full READ mode: notes, skills, INDEX, manifest.
- CLI agents with cloud models, as long as internet works even if the remote backend does not.
- If internet is also down: the locally-configured worker via Ollama (see `agent-universal-layer/instructions/LOCAL-WORKER.md` for the model and setup), if one is configured, is the only agent still operational. With no local model configured, offline means no agent at all until connectivity returns.

## Rules during an emergency

1. NO writes to the local vault mirror: it stays read-only even in an emergency.
2. Work produced in the meantime is parked APPEND-ONLY outside the vault, in `~/vault-outbox/` (Windows: `%USERPROFILE%\vault-outbox\`), one file per topic, `YYYY-MM-DD-topic.md`, content already compressed and ready to pour back in. Create the folder if it doesn't exist.
3. Web: Firecrawl is down, so local, read-only, anonymous headless browsing is allowed (a carve-out already granted by the bootstrap rules). Anything interactive or authenticated stays in the shared visible Chrome.
4. n8n down: do not replicate workflows locally, note it in the outbox and wait.
5. No secret rotations or config changes during an emergency.

## Re-entry, when the remote backend returns

1. `agent-sync` (pulls the refreshed mirror).
2. Pour the files from `~/vault-outbox/` back into the vault VIA MCP (`append_note`, or `update_note` with `expected_hash`), then empty the outbox.
3. `agent-doctor` to confirm alignment.

## Remote recovery (if the VPS itself is down)

- Known precedent: OOM from a container with no memory cap, a past incident. Never launch containers without `--memory`.
- Restart from the cloud provider's web console, then `ssh <remote-alias> "sudo docker ps"` and health-check the containers (`vault-mcp`, `vault-semantic`, `n8n`, the Firecrawl stack, `vault-ocr-api`).
