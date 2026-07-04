#!/usr/bin/env bash
# agent-healthcheck — alert raggruppato: avvisa SOLO se qualcosa non va (FAIL).
# Eseguito da agent-sync ad ogni giro, ma INVIA solo:
#   - subito quando compare/cambia un problema (FAIL), e
#   - come promemoria 1/giorno se il problema persiste.
# Nessun report "verde" di routine.
# Contenuto = riepilogo di agent-doctor (include drift sync, MCP, istruzioni, token, skill...).
# Trasporto (primo disponibile): Telegram diretto (env) > webhook > notify-send desktop > log.
set -u

VAULT="${KNOWLEDGE_VAULT_PATH:-$HOME/KnowledgeVault}"
DOCTOR="$VAULT/03-INFRA/scripts/agent-doctor.sh"
STATE_DIR="$HOME/.local/state"
HB_FILE="$STATE_DIR/agent-healthcheck.state"
LOG="$STATE_DIR/agent-sync.log"
INTERVAL="${AGENT_HEALTHCHECK_INTERVAL:-86400}"   # routine: 1/giorno
HOSTN="$(hostname)"
mkdir -p "$STATE_DIR"

[ -x "$DOCTOR" ] || exit 0
now=$(date +%s)

summary="$("$DOCTOR" --summary 2>/dev/null | tail -1)"
[ -n "$summary" ] || exit 0

problem=0
printf '%s' "$summary" | grep -q 'FAIL=[1-9]' && problem=1
# firma stabile (toglie i numeri lunghi tipo timestamp), per rilevare un problema NUOVO
sig="$(printf '%s' "$summary" | tr -d ' ')"

last=0; last_sig=""
if [ -f "$HB_FILE" ]; then
  last="$(sed -n '1p' "$HB_FILE" 2>/dev/null)"
  last_sig="$(sed -n '2p' "$HB_FILE" 2>/dev/null)"
fi
case "$last" in ''|*[!0-9]*) last=0 ;; esac

# Invia SOLO se qualcosa non va (FAIL). Nessun report verde di routine.
if [ "$problem" != 1 ]; then
  printf '%s\nok\n' "$now" > "$HB_FILE"
  exit 0
fi

send=0
[ "$sig" != "$last_sig" ] && send=1                  # problema nuovo o cambiato -> subito
[ $(( now - last )) -ge "$INTERVAL" ] && send=1      # promemoria se il problema persiste (1/giorno)
[ "$send" = 1 ] || exit 0

# Messaggio in linguaggio umano (per the user); il dettaglio tecnico resta in coda
# così può girarlo a un agente com'è. Le righe ✗ vengono dal run completo del doctor.
failn="$(printf '%s' "$summary" | sed -n 's/.*FAIL=\([0-9]\{1,\}\).*/\1/p')"
fail_lines="$("$DOCTOR" 2>/dev/null | sed 's/\x1b\[[0-9;]*m//g' | grep '✗' | sed 's/^[[:space:]]*✗[[:space:]]*/• /' | head -6)"
[ -n "$fail_lines" ] || fail_lines="• dettaglio non disponibile (vedi log)"

msg="🔴 ${failn:-Alcuni} controlli automatici degli agenti sono falliti su ${HOSTN} — $(date '+%d/%m %H:%M')

Cosa non va:
${fail_lines}

Cosa fare tu: niente a mano. Apri Claude e incolla:
«gira agent-doctor e sistema i FAIL»

[tecnico: ${summary}]"

sent=0
if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
  curl -s -m 10 "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
    --data-urlencode "text=${msg}" >/dev/null 2>&1 && sent=1
elif [ -n "${VAULT_ALERT_WEBHOOK:-}" ]; then
  curl -s -m 10 -X POST "$VAULT_ALERT_WEBHOOK" \
    --data-urlencode "host=${HOSTN}" \
    --data-urlencode "text=${msg}" >/dev/null 2>&1 && sent=1
fi
if [ "$sent" -ne 1 ] && command -v notify-send >/dev/null 2>&1; then
  notify-send -u critical -a agent-healthcheck "Agenti: qualcosa non va" "$msg" >/dev/null 2>&1 && sent=1
fi
[ "$sent" -ne 1 ] && printf '%s healthcheck (nessun transport): %s\n' "$(date -Is)" "$summary" >> "$LOG"

printf '%s\n%s\n' "$now" "$sig" > "$HB_FILE"
exit 0
