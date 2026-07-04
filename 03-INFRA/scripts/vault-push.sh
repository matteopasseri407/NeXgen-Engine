#!/usr/bin/env bash
# vault-push — commit + pubblicazione dei FILE INFRA del KnowledgeVault sul
# remote configurato (origin), con rebase PULITO sulla divergenza benigna e
# STOP sicuro sui conflitti veri (non forza mai, non fa merge, non perde lavoro).
#
# Ambito: file di codice/config del vault (script, manifest, hook...).
# Le NOTE (markdown di conoscenza) NON passano da qui: si scrivono via MCP
# (vault-library), che serializza con lock e committa sul repo. Una porta per tipo.
#
# Uso:
#   vault-push -m "messaggio di commit" [file ...]
#     - con file:  git add di quei file, poi commit
#     - senza file: committa quanto e' gia' in stage (add a cura del chiamante)
set -u

VAULT="${KNOWLEDGE_VAULT_PATH:-$HOME/KnowledgeVault}"
BRANCH="${KNOWLEDGE_VAULT_BRANCH:-main}"
REMOTE="${KNOWLEDGE_VAULT_REMOTE:-origin}"

MSG=""
FILES=()
while [ $# -gt 0 ]; do
  case "$1" in
    -m) MSG="${2:-}"; shift 2 ;;
    -m*) MSG="${1#-m}"; shift ;;
    --) shift; while [ $# -gt 0 ]; do FILES+=("$1"); shift; done ;;
    *) FILES+=("$1"); shift ;;
  esac
done
[ -z "$MSG" ] && { echo "vault-push: serve -m \"messaggio\""; exit 2; }
cd "$VAULT" || { echo "vault-push: vault non trovato ($VAULT)"; exit 1; }

if [ "${#FILES[@]}" -gt 0 ]; then
  git add -- "${FILES[@]}" || { echo "vault-push: git add fallito"; exit 1; }
fi
if git diff --cached --quiet; then
  echo "vault-push: niente in stage, niente da committare"; exit 0
fi

git commit -q -m "$MSG" || { echo "vault-push: commit fallito"; exit 1; }
echo "vault-push: commit $(git rev-parse --short HEAD)"

# Pubblica sul remote: diretto se fast-forward; altrimenti rebase PULITO
# (solo a working tree pulito); su conflitto vero abortisce e segnala.
if git push "$REMOTE" "$BRANCH" >/dev/null 2>&1; then
  echo "vault-push: push $REMOTE OK"; exit 0
fi
if ! git fetch --prune "$REMOTE" "$BRANCH" >/dev/null 2>&1; then
  echo "vault-push: $REMOTE OFFLINE — il commit resta locale (lo pubblichera' agent-sync)"; exit 1
fi
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "vault-push: $REMOTE rifiutato ma working tree con modifiche non committate — NON rebaso, risolvi a mano"; exit 1
fi
if git rebase "$REMOTE/$BRANCH" >/dev/null 2>&1; then
  if git push "$REMOTE" "$BRANCH" >/dev/null 2>&1; then
    echo "vault-push: push $REMOTE OK (dopo rebase pulito)"; exit 0
  fi
  echo "vault-push: $REMOTE ancora rifiutato dopo rebase — riprova"; exit 1
fi
git rebase --abort >/dev/null 2>&1
echo "vault-push: $REMOTE DIVERGENZA CON CONFLITTO — serve 'git pull --rebase $REMOTE $BRANCH' a mano"; exit 1
