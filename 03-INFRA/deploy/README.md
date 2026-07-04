# Deploy — self-hosted stack (Cloud-Server mode)

This directory deploys the remote backend services that the Cloud-Server
install mode relies on: **n8n** (automation), **Firecrawl** (self-hosted
scraping/search), and **Vault OCR** (self-hosted text extraction).

A **Local-Only** install does NOT need any of this — everything runs on the
workstation with native CLI search, model vision for OCR, and no remote
automations.

## What is here

```
deploy/
├── bootstrap-vps.sh        # one-command deploy on the VPS
├── .env.example            # copy to .env, fill in secrets
├── n8n/
│   └── docker-compose.yml  # n8n automation engine
├── firecrawl/
│   └── docker-compose.yml  # Firecrawl API + worker + Redis + Playwright
└── ocr/
    ├── docker-compose.yml  # Vault OCR API (RapidOCR)
    ├── api/                # OCR service source (FastAPI + RapidOCR)
    └── mcp/                # OCR MCP server (stdio bridge)
```

## Deploy on a VPS

```bash
# on the VPS
git clone https://github.com/<github-user>/NeXgen-Vault-OL.git
cd NeXgen-Vault-OL/03-INFRA/deploy
cp .env.example .env        # edit and fill in secrets
bash bootstrap-vps.sh
```

Requirements on the VPS: Docker and docker-compose.

Each stack binds to `127.0.0.1` only — they are NOT exposed on the public
interface. You reach them from your workstation over SSH tunnels (see
`03-INFRA/remote-automation.md`).

## Reach the stacks from your workstation

Create persistent SSH tunnels (port variables come from
`99-INDEX/USER-PROFILE.md`):

```bash
ssh -L 127.0.0.1:<n8n-tunnel-port>:127.0.0.1:5678 \
    -L 127.0.0.1:<firecrawl-tunnel-port>:127.0.0.1:3002 \
    -L 127.0.0.1:<ocr-tunnel-port>:127.0.0.1:3033 \
    <remote-alias> -N
```

Or run them as systemd user units (recommended for always-on use).

## Vault MCP (optional)

The `vault-library` MCP server (Git-backed read/write to the vault from
agents) is a separate component. If you want agents to write notes via MCP
rather than direct git, deploy it alongside this stack and point it at a
bare vault repo on the VPS. The deployment source for that container is not
bundled here; see `03-INFRA/vault-write-architecture.md` for the write model
(note → MCP, one door per type).

## Resource notes

- n8n: 1g memory limit.
- Firecrawl: ~2.5g total (API + worker + Redis + Playwright).
- OCR: 2g memory, 1.5 CPU (RapidOCR model load). Keep one worker.
- Add `--memory` caps to any extra container you add; an uncapped OOM can
  take down the whole VPS.
