#!/usr/bin/env bash
# agent-doctor — verifica che TUTTI gli agenti siano davvero allineati e operativi.
# Read-only: non modifica nulla. Exit 0 se nessun FAIL, 1 altrimenti.
# Uso:
#   agent-doctor.sh             report leggibile (colori, sezioni)
#   agent-doctor.sh --summary   una riga sintetica (per digest/notifiche)
set -u

VAULT="${KNOWLEDGE_VAULT_PATH:-$HOME/KnowledgeVault}"
UL="$VAULT/03-INFRA/agent-universal-layer"
CANON="$UL/instructions/AGENTS.md"
REMOTE="${KNOWLEDGE_VAULT_REMOTE:-origin}"
BRANCH="${KNOWLEDGE_VAULT_BRANCH:-main}"
OCJSON="$HOME/.config/opencode/opencode.json"
PASS=0; WARN=0; FAILN=0; FAILS=""

QUIET=0
STRICT=0
for arg in "$@"; do
  case "$arg" in
    --summary) QUIET=1 ;;
    --strict) STRICT=1 ;;
    -h|--help)
      cat <<'EOF'
agent-doctor.sh [--summary] [--strict]

Default: fast structural and service health checks.
--summary: one-line output for alerting.
--strict: add real CLI consumer checks, including OpenCode MCP list,
          Antigravity global MCP path, and vault-ocr stdio framing.
EOF
      exit 0 ;;
  esac
done

ok()   { PASS=$((PASS+1)); [ "$QUIET" = 1 ] || printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { WARN=$((WARN+1)); [ "$QUIET" = 1 ] || printf '  \033[33m⚠\033[0m %s\n' "$*"; }
fail() { FAILN=$((FAILN+1)); FAILS="${FAILS}${FAILS:+, }$*"; [ "$QUIET" = 1 ] || printf '  \033[31m✗\033[0m %s\n' "$*"; }
sec()  { [ "$QUIET" = 1 ] || printf '\n\033[1m%s\033[0m\n' "$*"; }
code() { curl -s -o /dev/null -m 6 -w '%{http_code}' "$@" 2>/dev/null || echo 000; }

[ "$QUIET" = 1 ] || printf '\033[1m=== agent-doctor: verifica allineamento agenti ===\033[0m\n'

sec "Host"
case "$(uname -s)" in Linux) HOST=linux;; Darwin) HOST=mac;; *) HOST=other;; esac
ok "rilevato: $HOST ($(hostname), $(uname -s))"

sec "Vault (memoria) — git vs Oracle (hub) + mirror GitHub"
if git -C "$VAULT" rev-parse >/dev/null 2>&1; then
  git -C "$VAULT" fetch --prune "$REMOTE" "$BRANCH" >/dev/null 2>&1 || warn "fetch $REMOTE non riuscito (offline?)"
  b=$(git -C "$VAULT" rev-list --count "$BRANCH..$REMOTE/$BRANCH" 2>/dev/null || echo '?')
  a=$(git -C "$VAULT" rev-list --count "$REMOTE/$BRANCH..$BRANCH" 2>/dev/null || echo '?')
  d=$(git -C "$VAULT" status --porcelain --untracked-files=no 2>/dev/null | wc -l | tr -d ' ')
  [ "$b" = 0 ] && ok "allineato a $REMOTE/$BRANCH (0 indietro)" || fail "indietro di $b commit dal cloud"
  [ "$a" = 0 ] && ok "nessun commit locale non pubblicato" || warn "$a commit locali non pubblicati"
  [ "$d" = 0 ] && ok "working tree pulita (file tracciati)" || warn "$d file tracciati non committati (bloccano il pull)"
else
  fail "il vault non è un repo git: $VAULT"
fi

sec "Istruzioni canoniche (AGENTS.md unico, Claude pointer anti-duplicazione)"
[ -f "$CANON" ] && ok "canonico presente" || fail "manca il canonico $CANON"
CLAUDE_FILE="$HOME/CLAUDE.md"
if [ -f "$CLAUDE_FILE" ] && [ ! -L "$CLAUDE_FILE" ] && grep -Fq "$CANON" "$CLAUDE_FILE" && grep -Fq "compatibility pointer" "$CLAUDE_FILE"; then
  ok "Claude pointer -> AGENTS.md canonico"
else
  fail "Claude.md deve essere un pointer leggero, non una copia/symlink del canonico ($CLAUDE_FILE)"
fi
# NOTA (2026-07-01): Antigravity legge davvero ~/.gemini/config/AGENTS.md, NON
# ~/ANTIGRAVITY.md — quel symlink esiste (pattern copiato da Codex) ma non è mai
# stato letto dall'app, e questo check dava un falso "ok" da giorni. Verificato
# solo con un probe comportamentale reale (agy -p), non con l'esistenza del
# symlink: questo loop prova solo il CABLAGGIO, non che la CLI onori il file.
# Se torna a succedere un "chiuso" fantasma, il sospetto numero uno è questo.
for pair in "Codex:$HOME/.codex/AGENTS.md" "Antigravity:$HOME/.gemini/config/AGENTS.md"; do
  name="${pair%%:*}"; f="${pair#*:}"
  if [ "$(readlink -f "$f" 2>/dev/null)" = "$(readlink -f "$CANON" 2>/dev/null)" ]; then
    ok "$name → AGENTS.md canonico"
  else
    fail "$name NON punta al canonico ($f)"
  fi
done
if [ -f "$OCJSON" ]; then
  grep -q "instructions/AGENTS.md" "$OCJSON" && ok "OpenCode instructions → AGENTS.md" || fail "OpenCode instructions NON puntano ad AGENTS.md"
else
  fail "manca $OCJSON"
fi

sec "Utility deterministiche agenti"
if command -v agent-now >/dev/null 2>&1; then
  now_payload="$(agent-now --json 2>/dev/null || true)"
  if printf '%s\n' "$now_payload" | grep -q '"source": "system_clock"' && printf '%s\n' "$now_payload" | grep -q '"local_time"'; then
    ok "agent-now disponibile e funzionante"
  else
    fail "agent-now presente ma output non valido"
  fi
else
  fail "agent-now non in PATH (lancia agent-sync.sh)"
fi

sec "Connettori MCP — raggiungibilità"
c=$(code http://127.0.0.1:5678/healthz); [ "$c" = 200 ] && ok "n8n-mcp (5678): $c" || fail "n8n-mcp (5678): $c"
c=$(code http://127.0.0.1:33002/); { [ "$c" = 200 ] || [ "$c" = 302 ]; } && ok "firecrawl (33002): $c" || fail "firecrawl (33002): $c"
c=$(code http://127.0.0.1:33003/health); [ "$c" = 200 ] && ok "vault-ocr (33003): $c" || fail "vault-ocr (33003): $c"
if [ -n "${VAULT_LIBRARY_URL:-}" ]; then
  c=$(code -H "Authorization: Bearer ${VAULT_LIBRARY_TOKEN:-}" "$VAULT_LIBRARY_URL")
  { [ "$c" != 000 ] && [ "$c" != 401 ] && [ "$c" != 403 ]; } && ok "vault-library: $c (vivo)" || fail "vault-library: $c"
else
  warn "VAULT_LIBRARY_URL non in env"
fi
# RAG semantico: il check passa dal container MCP (stessa corsia degli agenti),
# così becca anche il footgun "container ricreato fuori dalla rete oracle_default"
# che un curl su :8089 non vedrebbe.
rag_c="$(ssh -o BatchMode=yes -o ConnectTimeout=6 ${REMOTE_ALIAS:-} \
  "docker exec oracle-vault-mcp-1 python3 -c \"import urllib.request as u; print(u.urlopen('http://vault-semantic:8080/health', timeout=5).status)\"" 2>/dev/null || true)"
[ "$rag_c" = "200" ] && ok "vault-semantic RAG (corsia MCP): 200" || fail "vault-semantic RAG (corsia MCP): ${rag_c:-KO}"
command -v npx >/dev/null 2>&1 && ok "playwright: npx disponibile" || warn "npx non in PATH (playwright MCP)"

sec "Token in env"
for v in N8N_MCP_TOKEN VAULT_LIBRARY_TOKEN VAULT_LIBRARY_URL; do
  [ -n "${!v:-}" ] && ok "$v presente" || fail "$v mancante"
done
[ -n "${DEEPSEEK_API_KEY:-}" ] && ok "DEEPSEEK_API_KEY presente" || warn "DEEPSEEK_API_KEY mancante (solo fallback/batch diretto DeepSeek; OpenCode Go resta ok)"

sec "MCP configurati nei runtime (Vault 2.0 drift detection)"
if command -v python3 >/dev/null 2>&1 && [ -f "$UL/mcp/render.py" ]; then
  render_out="$(python3 "$UL/mcp/render.py" 2>/dev/null)"
  drift_scan="$render_out"
  claude_pending=0
  if pgrep -x claude >/dev/null 2>&1; then
    claude_section="$(printf '%s\n' "$render_out" | awk '/^========== CLAUDE ==========/{p=1; next} /^========== /{p=0} p')"
    if printf '%s\n' "$claude_section" | grep -Eq '\[(DIFF|MANCA|MISSING|ERROR)\]'; then
      claude_pending=1
      drift_scan="$(printf '%s\n' "$render_out" | awk '/^========== CLAUDE ==========/{p=1; next} /^========== /{p=0} !p')"
    fi
  fi
  if printf '%s\n' "$drift_scan" | grep -Eq '\[(DIFF|MANCA|MISSING|ERROR)\]'; then
    fail "rilevato drift MCP rispetto al manifest canonico"
    # Estraiamo le righe incriminate per mostrarle nel report
    drift_lines="$(printf '%s\n' "$drift_scan" | grep -E '\[DIFF\]|\[MANCA\]|\[MISSING\]|\[ERROR\]')"
    [ "$QUIET" = 1 ] || printf '%s\n' "$drift_lines" | while IFS= read -r line; do warn "drift detail: $line"; done
  elif [ "$claude_pending" = 1 ]; then
    warn "Claude MCP non allineato ma Claude e' attivo: verra' scritto dal prossimo agent-sync a Claude chiuso"
  else
    ok "configurazioni MCP 100% allineate al manifest canonico"
  fi
else
  warn "python3 o render.py non trovati, salto la verifica drift MCP"
fi

if [ "$STRICT" = 1 ]; then
  sec "CLI consumer conformance (--strict)"
  AG_SRC="$HOME/.gemini/antigravity/mcp_config.json"
  AG_GLOBAL="$HOME/.gemini/config/mcp_config.json"
  if [ "$(readlink -f "$AG_GLOBAL" 2>/dev/null)" = "$(readlink -f "$AG_SRC" 2>/dev/null)" ]; then
    ok "Antigravity global MCP path -> sorgente generata"
  else
    fail "Antigravity global MCP path NON punta alla sorgente generata ($AG_GLOBAL)"
  fi
  if [ -s "$AG_GLOBAL" ]; then
    ok "Antigravity global mcp_config.json non vuoto"
  else
    fail "Antigravity global mcp_config.json vuoto o mancante"
  fi
  if command -v python3 >/dev/null 2>&1 && [ -s "$AG_GLOBAL" ]; then
    ag_missing="$(python3 - "$AG_GLOBAL" <<'PY' 2>/dev/null || true
import json, sys
path = sys.argv[1]
expected = {"firecrawl", "n8n-mcp", "vault-library", "vault-ocr"}
data = json.load(open(path, encoding="utf-8"))
got = set((data.get("mcpServers") or {}).keys())
print(",".join(sorted(expected - got)))
PY
)"
    [ -z "$ag_missing" ] && ok "Antigravity global contiene i server MCP core" || fail "Antigravity global manca server MCP core: $ag_missing"
  fi

  if command -v opencode >/dev/null 2>&1; then
    oc_tmp="$(mktemp)"
    if command -v setsid >/dev/null 2>&1; then
      setsid timeout -k 5s 25s opencode mcp list >"$oc_tmp" 2>&1
      oc_rc=$?
    else
      timeout -k 5s 25s opencode mcp list >"$oc_tmp" 2>&1
      oc_rc=$?
    fi
    oc_out="$(cat "$oc_tmp" 2>/dev/null)"
    rm -f "$oc_tmp"
    oc_missing=""
    for srv in firecrawl n8n-mcp vault-library vault-ocr; do
      printf '%s\n' "$oc_out" | grep -F "$srv" | grep -Fqi "connected" || oc_missing="${oc_missing}${oc_missing:+, }$srv"
    done
    if [ "$oc_rc" = 124 ] || [ "$oc_rc" = 137 ]; then
      fail "OpenCode mcp list timeout in strict check"
    elif [ -z "$oc_missing" ]; then
      ok "OpenCode mcp list vede i server core connected"
    else
      fail "OpenCode mcp list non conferma: $oc_missing"
    fi
  else
    warn "opencode non in PATH, salto test consumer OpenCode"
  fi

  OCR_MCP="$UL/ocr/mcp/vault_ocr_mcp.py"
  if command -v python3 >/dev/null 2>&1 && [ -f "$OCR_MCP" ]; then
    if python3 - "$OCR_MCP" <<'PY' >/dev/null 2>&1; then
import json, subprocess, sys

script = sys.argv[1]
cmd = ["python3", script]
requests = [
    {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "doctor", "version": "0"}}},
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
    {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
]

proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
proc.stdin.write("".join(json.dumps(req, separators=(",", ":")) + "\n" for req in requests))
proc.stdin.close()
jsonl = [json.loads(line) for line in proc.stdout if line.strip()]
assert proc.wait(timeout=15) == 0
assert jsonl[0]["result"]["serverInfo"]["name"] == "vault-ocr"
assert any(tool["name"] == "ocr_healthcheck" for tool in jsonl[1]["result"]["tools"])

proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
for req in requests:
    data = json.dumps(req, separators=(",", ":")).encode()
    proc.stdin.write(b"Content-Length: " + str(len(data)).encode() + b"\r\n\r\n" + data)
proc.stdin.close()
raw = proc.stdout.read()
assert proc.wait(timeout=15) == 0
responses = []
i = 0
while i < len(raw):
    end = raw.index(b"\r\n\r\n", i)
    headers = raw[i:end].decode().split("\r\n")
    length = int([h for h in headers if h.lower().startswith("content-length:")][0].split(":", 1)[1].strip())
    start = end + 4
    responses.append(json.loads(raw[start:start + length]))
    i = start + length
assert responses[0]["result"]["serverInfo"]["name"] == "vault-ocr"
assert any(tool["name"] == "ocr_healthcheck" for tool in responses[1]["result"]["tools"])
PY
      ok "vault-ocr stdio framing OK (JSONL + Content-Length)"
    else
      fail "vault-ocr stdio framing test fallito"
    fi
  else
    warn "vault-ocr MCP wrapper non trovato, salto framing test"
  fi
fi

sec "Browser condiviso e defaults"
if [ "$HOST" = linux ] && command -v xdg-settings >/dev/null 2>&1; then
  db=$(xdg-settings get default-web-browser 2>/dev/null)
  [ "$db" = "agent-chrome.desktop" ] && ok "default browser di sistema → agent-chrome" || warn "default browser di sistema = ${db:-?} (atteso agent-chrome.desktop)"
fi

sec "Skill"
# Non basta contare le voci: un symlink self-loop/rotto (bug humanizer 2026-07-01)
# "esiste" per ls ma è vuoto per chi lo legge. [ -e ] fallisce su ELOOP/rotto.
n=0; broken=""
for s in "$HOME/.agents/skills"/*; do
  [ -L "$s" ] || [ -d "$s" ] || continue
  if [ -e "$s" ]; then n=$((n+1)); else broken="$broken $(basename "$s")"; fi
done
[ "${n:-0}" -gt 0 ] && ok "$n skill leggibili in ~/.agents/skills" || fail "nessuna skill leggibile in ~/.agents/skills"
[ -n "$broken" ] && fail "skill ROTTE (symlink self-loop/pendente):$broken — ripara con: python3 \$VAULT/03-INFRA/scripts/skills-sync.py --apply"
# I runtime devono risolvere le skill essenziali (dal manifest) fino a un SKILL.md vero.
ess_ok=1
for ess in humanizer knowledge-vault-hygiene frontend-design; do
  for rt in "$HOME/.claude/skills" "$HOME/.codex/skills"; do
    [ -d "$rt" ] || continue
    if [ ! -f "$rt/$ess/SKILL.md" ]; then
      ess_ok=0; fail "$rt/$ess: SKILL.md NON leggibile (link rotto o skill mancante)"
    fi
  done
done
[ "$ess_ok" = 1 ] && ok "skill essenziali risolvono a un SKILL.md vero in claude+codex"

sec "OpenCode config"
if command -v node >/dev/null 2>&1 && [ -f "$OCJSON" ]; then
  if node -e "JSON.parse(require('fs').readFileSync('$OCJSON','utf8'))" 2>/dev/null; then
    ok "opencode.json: JSON valido"
    grep -q 'opencode-go/deepseek-v4-pro' "$OCJSON" && ok "default = opencode-go/deepseek-v4-pro (Go)" || warn "default model non è opencode-go/deepseek-v4-pro"
    grep -q '"ollama"' "$OCJSON" && warn "provider ollama nel file condiviso (atteso solo DeepSeek: local è Windows-only)" || ok "provider = solo DeepSeek (niente local nel condiviso)"
  else
    fail "opencode.json: JSON NON valido"
  fi
fi

sec "Modello locale (host-aware)"
if [ "$HOST" = linux ]; then
  if ss -ltn 2>/dev/null | grep -q ':11434'; then
    ok "Ollama attivo sul laptop (fallback locale d'emergenza qwen, non worker di routing)"
  else
    ok "Ollama non in ascolto (ok: fallback d'emergenza on-demand)"
  fi
fi

sec "Claude hooks (vault checkpoint/briefing)"
SETTINGS="$HOME/.claude/settings.json"
if [ -f "$SETTINGS" ]; then
  if command -v jq >/dev/null 2>&1; then
    cnt=$(jq -r '[(.hooks.SessionStart[]?.hooks[]?.command), (.hooks.PreCompact[]?.hooks[]?.command)] | map(select(test("claude-vault-checkpoint"))) | length' "$SETTINGS" 2>/dev/null || echo 0)
    if [ "${cnt:-0}" -ge 2 ]; then ok "hook checkpoint/briefing su SessionStart + PreCompact"; else fail "hook vault-checkpoint mancante in settings.json (lancia agent-sync.sh)"; fi
  else
    warn "jq assente: salto il check hook Claude"
  fi
else
  warn "settings.json Claude assente (Claude non installato qui?)"
fi

if [ "$QUIET" = 1 ]; then
  line="agent-doctor [$HOST] PASS=$PASS WARN=$WARN FAIL=$FAILN"
  [ "$FAILN" -gt 0 ] && line="$line | FAIL: $FAILS"
  printf '%s\n' "$line"
else
  sec "Riepilogo"
  printf "  \033[32mPASS=%s\033[0m  \033[33mWARN=%s\033[0m  \033[31mFAIL=%s\033[0m\n" "$PASS" "$WARN" "$FAILN"
  [ "$FAILN" -eq 0 ] && printf "  → \033[32mallineamento VERIFICATO\033[0m\n" || printf "  → \033[31mci sono FAIL da sistemare\033[0m\n"
fi
[ "$FAILN" -eq 0 ]
