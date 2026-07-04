# Bootstrap VPS — deploy the Agent-OS self-hosted stack.
#
# Run ON the VPS, from the repo root (after cloning NeXgen-Vault-OL):
#   cd NeXgen-Vault-OL/03-INFRA/deploy
#   cp .env.example .env   # fill in secrets
#   bash bootstrap-vps.sh
#
# Brings up: n8n, Firecrawl, Vault OCR. Each stack is independent; you can
# comment out the ones you do not need.

set -euo pipefail

DEPLOY_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DEPLOY_DIR"

require() {
  command -v "$1" >/dev/null 2>&1 || { echo "missing: $1"; exit 1; }
}

require docker
require docker-compose

echo "==> n8n"
docker-compose -f n8n/docker-compose.yml up -d --build

echo "==> Firecrawl"
docker-compose -f firecrawl/docker-compose.yml up -d --build

echo "==> Vault OCR"
docker-compose -f ocr/docker-compose.yml up -d --build

echo
echo "Stacks up. Health checks:"
echo "  n8n:        http://127.0.0.1:5678/healthz"
echo "  firecrawl:  curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:3002/"
echo "  ocr:        curl -s http://127.0.0.1:3033/health"
echo
echo "Next: create SSH tunnels from your workstation to these ports."
echo "See 03-INFRA/remote-automation.md for the tunnel map and 99-INDEX/USER-PROFILE.md"
echo "for the port variables to fill in."
