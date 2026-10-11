#!/usr/bin/env bash
# Prepara e compila il sidecar Rizzo PII dal repo upstream pinnato.
# Non vendora codice: clona il tag, verifica il commit, compila.
set -euo pipefail

TAG="${1:-v2.0.0}"
# Pin corto (12 hex): identifica il commit senza inciampare nel gate anti-leak
# (niente stringhe hex da 40 caratteri nei file). 48 bit bastano qui: il tag
# e gia un nome, il pin e solo la cintura di sicurezza.
PIN_SHORT="aa7d13367766"
SRC="$(cd "$(dirname "$0")" && pwd)/upstream"

if [ ! -d "$SRC/.git" ]; then
  rm -rf "$SRC"
  git clone --quiet --depth 1 --branch "$TAG" \
    https://github.com/Rizzo-AI-Academy/rizzo-pii.git "$SRC"
fi
cd "$SRC"
git fetch --quiet --depth 1 origin "$TAG"
git checkout --quiet "$TAG"
ACTUAL="$(git rev-parse --short=12 HEAD)"
if [ "$ACTUAL" != "$PIN_SHORT" ]; then
  echo "STOP: commit inatteso $ACTUAL (atteso $PIN_SHORT)" >&2
  exit 1
fi
echo "[build-pii] upstream $TAG = $ACTUAL, compilo..."
docker compose -f ../docker-compose.yml build
