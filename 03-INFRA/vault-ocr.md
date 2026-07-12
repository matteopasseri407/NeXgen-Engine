---
tags:
  - infra
  - agents
  - ocr
  - mcp
---

# Vault OCR

Self-hosted OCR lane for the user's agent layer. It turns image-contained text into Markdown without spending model vision quota repeatedly.

## Current State

- Status: live on the remote backend as `vault-ocr-api`.
- Engine: RapidOCR `3.9.1` with ONNX Runtime `1.27.0`.
- Remote bind: `<remote-alias>:127.0.0.1:<ocr-remote-port>`.
- Local tunnel: `127.0.0.1:<ocr-tunnel-port> -> <remote-alias>:127.0.0.1:<ocr-remote-port>`, systemd user unit `vault-ocr-<remote>-tunnel.service`.
- MCP server: `vault-ocr`, registered in `03-INFRA/agent-universal-layer/mcp/manifest.yaml`.
- L0 wrapper: `vault-ocr-local`.
- Source: `03-INFRA/agent-universal-layer/ocr/`.

## Agent Rule

Use source text first when it exists. A log file, config file, CSV, PDF text layer, or command output beats OCR and vision.

Use OCR first for images that are really text: terminal screenshots, log/error screenshots, config screenshots, simple tables, and printed or scanned documents.

Use vision directly when the meaning is visual or spatial: diagrams, UI judgment, layout review, handwriting, messy photos, and charts where shape matters.

If an image is already pasted into the chat, the vision cost for that read has already been spent. Do not OCR just to read it again. OCR only when the text should become durable, searchable vault knowledge.

Persist OCR output only through `vault-library`. The OCR service extracts text and metadata, it never writes notes or commits to the vault.

Batch images sequentially. The remote backend is shared with n8n, Firecrawl, vault MCP, and semantic search.

If OCR is down, fall back to vision for non-sensitive content and report the outage. For sensitive documents, do not send them to cloud vision automatically.

In a **Local-Only setup** (no remote backend), OCR is absent. Model vision is the default for image-contained text. See `99-INDEX/USER-PROFILE.md` for the user's architecture.

## Commands

Health:

```bash
vault-ocr-local status
curl -fsS http://127.0.0.1:<ocr-tunnel-port>/health
```

Extract text:

```bash
vault-ocr-local extract /path/to/image.png
vault-ocr-local extract /path/to/image.png --json
```

Restart tunnel on Linux:

```bash
systemctl --user restart vault-ocr-<remote>-tunnel.service
```

Remote service checks:

```bash
ssh <remote-alias> "docker ps --filter name=vault-ocr-api"
ssh <remote-alias> "curl -fsS http://127.0.0.1:<ocr-remote-port>/health"
ssh <remote-alias> "docker logs --tail 80 vault-ocr-api"
```

Deploy from canonical source:

```bash
rsync -az --delete 03-INFRA/agent-universal-layer/ocr/api/ <remote-alias>:<remote-home>/vault-ocr-api/
ssh <remote-alias> "cd <remote-home>/vault-ocr-api && docker compose -f docker-compose.remote.yml up -d --build"
```

## Resource Guardrails

The container is limited to `1.50` CPU and `2g` memory. Keep one worker. Do not parallelize batch OCR unless there is a measured reason.



## Rollback

```bash
ssh <remote-alias> "cd <remote-home>/vault-ocr-api && docker compose -f docker-compose.remote.yml down"
systemctl --user disable --now vault-ocr-<remote>-tunnel.service
```
