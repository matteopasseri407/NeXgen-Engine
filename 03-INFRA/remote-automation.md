---
tags:
  - infra
  - automation
  - n8n
status: active
type: runbook
---

# Remote Automation Backend (n8n)

Operational map of the n8n automation backend, when the user has configured a Cloud-Server setup. A Local-Only setup has no remote backend — see `99-INDEX/USER-PROFILE.md` for the user's architecture.

## When to use it

Use the remote backend when the user asks, or when the task touches automations, workflows, integrations, webhooks, scheduled jobs, lead/process operations, or runtime state that may already exist in n8n. Do not connect to or modify it for unrelated tasks.

Before modifying, publishing, diagnosing or verifying any workflow, read `03-INFRA/n8n-workflow-regression-runbook.md`.

## Deployment

The self-hosted stack (n8n, Firecrawl, OCR) deploys from `03-INFRA/deploy/`. Run `bootstrap-vps.sh` on the VPS to bring it up. See `03-INFRA/deploy/README.md`.

## Connection model

The remote backend is reached over SSH (administration) and a local SSH tunnel (UI, API, MCP). Never point CLIs or IDEs at the remote public IP for MCP — always go through the local tunnel.

- SSH alias (administration): see `USER-PROFILE.md`.
- Local tunnel (UI, API, MCP): `127.0.0.1:<n8n-tunnel-port>` → remote `127.0.0.1:5678`.
- MCP endpoint: `http://127.0.0.1:<n8n-tunnel-port>/mcp-server/http`, bearer token via env (`N8N_MCP_TOKEN`).

## Read-only inspection

```bash
ssh <remote-alias> "sudo docker ps"
ssh <remote-alias> "sudo docker logs --tail 200 n8n-n8n-1"
```

## Containers (typical stack)

- `n8n` — the automation engine.
- `vault-mcp` — the trusted agent MCP endpoint (Git-backed read/write to the vault).
- `vault-semantic` — the semantic RAG layer (optional).
- `firecrawl-api`, `firecrawl-worker` — the self-hosted scraping/search lane (optional).
- `vault-ocr-api` — the self-hosted OCR lane (optional).

## Secrets and credentials

- n8n credentials live inside n8n's encrypted store, not in the vault.
- The MCP bearer token (`N8N_MCP_TOKEN`) is a secret — reference it by env var name, never by value, in notes and configs.
- Alert credentials (e.g. messaging bot tokens) are distributed on-demand from the backend, not stored in the vault.

## Backups

- Workflow backups: `[remote-home]/n8n/backups/`.
- The user's vault is backed up separately by the vault sync pipeline.

## Notes correlated

- `03-INFRA/n8n-workflow-regression-runbook.md`
- `03-INFRA/firecrawl.md`
- `03-INFRA/vault-ocr.md`
- `03-INFRA/offline-emergency-mode.md`
