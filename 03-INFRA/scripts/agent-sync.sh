#!/usr/bin/env bash
# agent-sync — allinea i runtime degli agenti AI (Claude Code, Codex, Antigravity)
# al layer universale del KnowledgeVault. Cloud-first via Oracle, ma SEMPRE
# funzionante offline: la fonte operativa è la copia locale del vault;
# il cloud serve solo a sincronizzare i due device.
#
# Idempotente, ma diviso in corsie:
#   agent-sync pull     = pull cloud + healthcheck, senza toccare runtime CLI
#   agent-sync guard    = pull + rigenera runtime CLI + healthcheck, senza push
#   agent-sync apply    = alias esplicito di guard, per uso manuale
#   agent-sync publish  = pubblica commit locali gia' fatti, senza provisioning
#   agent-sync doctor   = solo healthcheck/alert, senza pull/provisioning
#   agent-sync full     = vecchio comportamento completo, solo manuale
# Senza argomenti resta "full" per compatibilita'. Il timer deve chiamare "guard".
# Non fa mai auto-commit di contenuti: committa chi scrive (agenti o the user).
set -u

HOME_DIR="${HOME:-$HOME}"
VAULT="${KNOWLEDGE_VAULT_PATH:-$HOME_DIR/KnowledgeVault}"
REMOTE="${KNOWLEDGE_VAULT_REMOTE:-origin}"
BRANCH="${KNOWLEDGE_VAULT_BRANCH:-main}"
# ── Vault 2.1 — separazione Engine/Dati (Strangler Fig, 2026-07-04) ──────────
# Confine esplicito tra il MOTORE (codice neutro, in futuro pacchettizzabile in
# un repo/branch sanificato) e i DATI (vault personale o aziendale). Oggi i due
# coabitano, quindi i DEFAULT riproducono ESATTAMENTE i path storici: senza le
# env var il comportamento e' identico a prima, zero rottura.
# Domani il motore si sposta settando solo AGENT_ENGINE_ROOT (es. una cartella
# 03-INFRA/Vibecoder-Engine o un repo dedicato montato altrove) e i dati con
# AGENT_VAULT_DATA, senza toccare piu' una riga di questo script.
AGENT_VAULT_DATA="${AGENT_VAULT_DATA:-$VAULT}"
AGENT_ENGINE_ROOT="${AGENT_ENGINE_ROOT:-$VAULT/03-INFRA}"
ENGINE_SCRIPTS="$AGENT_ENGINE_ROOT/scripts"
UL="$AGENT_ENGINE_ROOT/agent-universal-layer"
AG="$HOME_DIR/.agents/skills"
LOG_DIR="$HOME_DIR/.local/state"
LOG="$LOG_DIR/agent-sync.log"
mkdir -p "$LOG_DIR" "$AG"

log() { printf '%s %s\n' "$(date -Is)" "$*" >>"$LOG"; }

MODE="${1:-full}"
case "$MODE" in
  pull)
    DO_PULL=1; DO_APPLY=0; DO_PUSH=0; DO_CREDS=0; DO_HEALTH=1 ;;
  guard|apply)
    DO_PULL=1; DO_APPLY=1; DO_PUSH=0; DO_CREDS=0; DO_HEALTH=1 ;;
  publish)
    DO_PULL=0; DO_APPLY=0; DO_PUSH=1; DO_CREDS=0; DO_HEALTH=0 ;;
  doctor)
    DO_PULL=0; DO_APPLY=0; DO_PUSH=0; DO_CREDS=0; DO_HEALTH=1 ;;
  full)
    DO_PULL=1; DO_APPLY=1; DO_PUSH=1; DO_CREDS=1; DO_HEALTH=1 ;;
  -h|--help|help)
    cat <<'EOF'
agent-sync modes:
  pull     Pull the KnowledgeVault from the remote and run healthcheck. Does not rewrite CLI runtime files.
  guard    Recurring safe propagation: pull, regenerate CLI runtime files, run healthcheck. Does not push.
  apply    Same as guard, explicit manual name for provisioning.
  publish  Push already-committed local vault changes to the remote (and mirror origin if configured).
  doctor   Run healthcheck/alerts only.
  full     Legacy full run: pull, apply runtime files, publish, creds, healthcheck.

Default without arguments: full, for backward compatibility.
The recurring timer should use: agent-sync guard
EOF
    exit 0 ;;
  *)
    printf 'agent-sync: unknown mode: %s\nUse: agent-sync --help\n' "$MODE" >&2
    exit 2 ;;
esac

log "agent-sync: start mode=$MODE"

# ── 1. Pull dal cloud (best-effort: offline non blocca nulla) ────────────────
if [ "$DO_PULL" = 1 ]; then
  if [ -x "$ENGINE_SCRIPTS/sync-vault-from-oracle.sh" ]; then
    # helper di sync dedicato (setup avanzato): usato solo se presente
    if "$ENGINE_SCRIPTS/sync-vault-from-oracle.sh" >>"$LOG" 2>&1; then
      log "pull: ok (helper dedicato)"
    else
      log "pull: cloud non raggiungibile o stato non sincronizzabile — continuo con la copia locale"
    fi
  elif git -C "$AGENT_VAULT_DATA" remote get-url "$REMOTE" >/dev/null 2>&1; then
    # caso standard: git pull --ff-only dal remoto configurato ($REMOTE, default origin)
    if git -C "$AGENT_VAULT_DATA" pull --ff-only "$REMOTE" "$BRANCH" >>"$LOG" 2>&1; then
      log "pull: ok (git pull --ff-only da $REMOTE)"
    else
      log "pull: $REMOTE non raggiungibile o non fast-forward — continuo con la copia locale"
    fi
  else
    log "pull: nessun remoto '$REMOTE' configurato — solo copia locale (ok per single-machine)"
  fi
fi

if [ "$DO_APPLY" = 1 ]; then

# ── 2. Istruzioni: un solo file canonico, pointer Claude anti-duplicazione ───
CANON="$UL/instructions/AGENTS.md"
write_claude_pointer() {
  target="$HOME_DIR/CLAUDE.md"
  tmp="$(mktemp)" || return 1
  cat >"$tmp" <<EOF
# Claude compatibility pointer

Canonical instructions live at:
$CANON

At session start, read and follow that file when the user-specific agent policy is needed.
Do not duplicate the full bootstrap in CLAUDE.md.
EOF
  if [ -f "$target" ] && [ ! -L "$target" ] && cmp -s "$tmp" "$target"; then
    rm -f "$tmp"
    return 0
  fi
  rm -f "$target"
  mv "$tmp" "$target" && log "istruzioni: scritto pointer Claude $target"
}

if [ -f "$CANON" ]; then
  write_claude_pointer
  # NB (2026-07-01): Antigravity legge DAVVERO ~/.gemini/config/AGENTS.md
  # (verificato con probe comportamentale); ~/ANTIGRAVITY.md era un cablaggio
  # morto copiato dal pattern Codex e non viene più gestito.
  for f in "$HOME_DIR/.gemini/config/AGENTS.md" "$HOME_DIR/.codex/AGENTS.md"; do
    if [ "$(readlink -f "$f" 2>/dev/null)" != "$(readlink -f "$CANON")" ]; then
      mkdir -p "$(dirname "$f")"
      ln -sfn "$CANON" "$f" && log "istruzioni: ricollegato $f"
    fi
  done
  # pulizia una-tantum del symlink morto (solo se è ancora il NOSTRO vecchio link)
  if [ -L "$HOME_DIR/ANTIGRAVITY.md" ] && [ "$(readlink -f "$HOME_DIR/ANTIGRAVITY.md")" = "$(readlink -f "$CANON")" ]; then
    rm -f "$HOME_DIR/ANTIGRAVITY.md" && log "istruzioni: rimosso symlink morto ~/ANTIGRAVITY.md (Antigravity non lo legge)"
  fi
else
  log "ATTENZIONE: manca $CANON — istruzioni non ricollegate"
fi

# ── 2.5. Antigravity MCP: unifica la configurazione dei server MCP tra i vari runtime ─
MC_SRC="$HOME_DIR/.gemini/antigravity/mcp_config.json"
if [ -f "$MC_SRC" ]; then
  for f in "$HOME_DIR/.gemini/antigravity-cli/mcp_config.json" "$HOME_DIR/.gemini/antigravity-ide/mcp_config.json" "$HOME_DIR/.gemini/config/mcp_config.json"; do
    d="$(dirname "$f")"
    mkdir -p "$d"
    if [ "$(readlink -f "$f" 2>/dev/null)" != "$(readlink -f "$MC_SRC")" ]; then
      ln -sfn "$MC_SRC" "$f" && log "mcp: ricollegato $f"
    fi
  done
fi

# ── 2.6. OpenCode: config autonoma su Linux (evita i wrapper Windows in opencode.json) ──
# La configurazione di OpenCode locale (~/.config/opencode/opencode.json) non viene
# sovrascritta per preservare i comandi nativi Linux dei connettori MCP.

# ── 2.7. Utility deterministiche agenti ─────────────────────────────────────
LOCAL_BIN="$HOME_DIR/.local/bin"
mkdir -p "$LOCAL_BIN"
NOW_SRC="$ENGINE_SCRIPTS/agent-now.sh"
if [ -f "$NOW_SRC" ]; then
  chmod +x "$NOW_SRC" 2>/dev/null || true
  if [ "$(readlink -f "$LOCAL_BIN/agent-now" 2>/dev/null)" != "$(readlink -f "$NOW_SRC" 2>/dev/null)" ]; then
    ln -sfn "$NOW_SRC" "$LOCAL_BIN/agent-now" && log "utils: ricollegato agent-now"
  fi
fi

# vault-push: helper di pubblicazione dei file infra (commit + push rebase-pulito)
VP_SRC="$ENGINE_SCRIPTS/vault-push.sh"
if [ -f "$VP_SRC" ]; then
  chmod +x "$VP_SRC" 2>/dev/null || true
  if [ "$(readlink -f "$LOCAL_BIN/vault-push" 2>/dev/null)" != "$(readlink -f "$VP_SRC" 2>/dev/null)" ]; then
    ln -sfn "$VP_SRC" "$LOCAL_BIN/vault-push" && log "utils: ricollegato vault-push"
  fi
fi

OCR_SRC="$ENGINE_SCRIPTS/vault-ocr-local.sh"
if [ -f "$OCR_SRC" ]; then
  chmod +x "$OCR_SRC" 2>/dev/null || true
  if [ "$(readlink -f "$LOCAL_BIN/vault-ocr-local" 2>/dev/null)" != "$(readlink -f "$OCR_SRC" 2>/dev/null)" ]; then
    ln -sfn "$OCR_SRC" "$LOCAL_BIN/vault-ocr-local" && log "utils: ricollegato vault-ocr-local"
  fi
fi

# ── 2.75. Timer agent-sync: guardia ricorrente additiva ─
# Il timer non deve piu' fare il vecchio giro completo con push/rebase/creds,
# ma deve ancora propagare automaticamente manifest, skill e istruzioni.
# La corsia ricorrente e' `agent-sync guard`: pull + apply + doctor, senza push.
if [ "$(uname -s)" = Linux ]; then
  UNIT_DIR="$HOME_DIR/.config/systemd/user"
  mkdir -p "$UNIT_DIR"
  SVC="$UNIT_DIR/agent-sync.service"
  TMR="$UNIT_DIR/agent-sync.timer"
  changed_units=0
  tmp="$(mktemp)" || tmp=""
  if [ -n "$tmp" ]; then
    cat >"$tmp" <<'EOF'
[Unit]
Description=KnowledgeVault agent sync guard (pull + apply + healthcheck, no publish)

[Service]
Type=oneshot
ExecStart=%h/.local/bin/agent-sync guard
EOF
    if ! cmp -s "$tmp" "$SVC" 2>/dev/null; then
      [ -f "$SVC" ] && cp -f "$SVC" "$SVC.pre-pull-mode-$(date +%Y%m%d-%H%M%S).bak"
      mv "$tmp" "$SVC" && changed_units=1 && log "systemd: agent-sync.service impostato su pull mode"
    else
      rm -f "$tmp"
    fi
  fi
  tmp="$(mktemp)" || tmp=""
  if [ -n "$tmp" ]; then
    cat >"$tmp" <<'EOF'
[Unit]
Description=agent-sync guard ogni 30 minuti e poco dopo il login

[Timer]
OnStartupSec=3min
OnUnitActiveSec=30min
Persistent=true

[Install]
WantedBy=timers.target
EOF
    if ! cmp -s "$tmp" "$TMR" 2>/dev/null; then
      [ -f "$TMR" ] && cp -f "$TMR" "$TMR.pre-pull-mode-$(date +%Y%m%d-%H%M%S).bak"
      mv "$tmp" "$TMR" && changed_units=1 && log "systemd: agent-sync.timer aggiornato"
    else
      rm -f "$tmp"
    fi
  fi
  if [ "$changed_units" = 1 ] && command -v systemctl >/dev/null 2>&1; then
    systemctl --user daemon-reload >>"$LOG" 2>&1 || log "systemd: daemon-reload user fallito (best-effort)"
  fi
fi

# ── 2.8. MCP unificati (Vault 2.0 Fase 1): config CLI generate dal manifest ──
# Fonte unica: $UL/mcp/manifest.yaml → generatore render.py → 4 dialetti.
# I 3 file statici (OpenCode/Antigravity/Codex) vengono riallineati in modo
# idempotente (backup + guard, no-op se gia' conformi). Claude si autogestisce
# .claude.json a caldo: per lui SOLO --diff come sentinella, non si scrive mai.
MCP_GEN="$UL/mcp/render.py"
if [ -f "$MCP_GEN" ] && command -v python3 >/dev/null 2>&1; then
  for cli in opencode antigravity codex; do
    if python3 "$MCP_GEN" --write "$cli" >>"$LOG" 2>&1; then
      log "mcp-gen: $cli allineato al manifest"
    else
      log "mcp-gen: $cli NON allineato (best-effort, continuo)"
    fi
  done
  # Claude riscrive .claude.json a caldo: lo riallineo SOLO se NESSUNA sessione
  # Claude e' attiva, per non sovrascriverlo sotto i piedi. A Claude attivo resta
  # la sola sentinella. write_claude e' comunque fail-safe (non scrive nel dubbio).
  if pgrep -x claude >/dev/null 2>&1; then
    log "mcp-gen: claude ATTIVO -> non tocco .claude.json a caldo (solo sentinella)"
  elif python3 "$MCP_GEN" --write claude >>"$LOG" 2>&1; then
    log "mcp-gen: claude allineato (era chiuso)"
  else
    log "mcp-gen: claude non allineato (best-effort)"
  fi
  diag="$(python3 "$MCP_GEN" 2>/dev/null | tail -1)"
  drift="$(printf '%s' "$diag" | sed -n 's/.*combaciano, \([0-9]\{1,\}\) con differenze.*/\1/p')"
  extra="$(printf '%s' "$diag" | sed -n 's/.*differenze, \([0-9]\{1,\}\) fuori manifest.*/\1/p')"
  [ "${drift:-0}" -gt 0 ] && log "mcp-gen: SENTINELLA — $drift server divergono dal manifest"
  [ "${extra:-0}" -gt 0 ] && log "mcp-gen: NOTA — $extra server fuori manifest (conservati): registrarli in manifest.yaml per propagarli ovunque"
  # Notifica del drift: NON qui (consolidamento 2026-07-04). agent-sync resta MUTO:
  # esegue e logga soltanto. L'unico megafono è agent-healthcheck (via agent-doctor,
  # con debounce e formato umano), chiamato a fine giro. Una sola superficie di alert.
fi

# ── 3. Skill custom del vault → root universale ~/.agents/skills ─────────────
if [ -d "$UL/skills" ]; then
  for d in "$UL/skills"/*/; do
    [ -d "$d" ] || continue
    s="$(basename "$d")"
    if [ "$(readlink "$AG/$s" 2>/dev/null)" != "${d%/}" ]; then
      rm -rf "$AG/$s"
      ln -sfn "${d%/}" "$AG/$s" && log "skill vault: ricollegata $s"
    fi
  done
fi

# ── 3.5. Catalogo skill universale (lazy-loading per TUTTE le CLI) ──────────
# Rigenera ~/.agents/skills/INDEX.md (nome + descrizione per ogni skill): ogni
# agente, anche senza formato skill (Antigravity/OpenCode/locale), lo consulta
# e apre la SKILL.md solo quando il task la richiede (vedi AGENTS.md).
SKILLS_SYNC="$ENGINE_SCRIPTS/skills-sync.py"
if [ -f "$SKILLS_SYNC" ] && command -v python3 >/dev/null 2>&1; then
  # --apply (idempotente, additivo) installa anche le skill github del manifest
  # e rigenera l'INDEX: senza, una skill registrata arriva solo dove qualcuno
  # lancia apply a mano (buco humanizer su Windows, 2026-07-03).
  python3 "$SKILLS_SYNC" --apply >>"$LOG" 2>&1 || log "skills-manifest: apply fallito (best-effort)"
fi

# ── 4. Root universale → runtime degli agenti ────────────────────────────────
# Collega ogni skill universale dentro .claude/skills e .codex/skills,
# rispettando le esclusioni per-provider (lazy loading: la skill resta in
# ~/.agents/skills e l'agente la legge on-demand, ma non viene precaricata).
# Le skill specifiche di un provider (directory reali non presenti in .agents)
# non vengono mai toccate.
for rt in "$HOME_DIR/.claude/skills" "$HOME_DIR/.codex/skills"; do
  [ -d "$rt" ] || continue
  # GUARDIA (fix 2026-07-01, bug self-loop humanizer/frontend-design): se il
  # runtime è un symlink all'INTERA hub ~/.agents/skills, i comandi qui sotto
  # passerebbero ATTRAVERSO il symlink: `rm -rf $rt/$s` cancellerebbe i byte
  # veri nell'hub e `ln -sfn` creerebbe un self-loop. In quel caso convertiamo
  # il runtime in cartella reale (unico modello in cui le esclusioni lazy
  # funzionano davvero) e proseguiamo coi link per-skill.
  if [ -L "$rt" ] && [ "$(readlink -f "$rt")" = "$(readlink -f "$AG")" ]; then
    rm "$rt" && mkdir -p "$rt" && log "runtime: $rt era symlink all'hub — convertito in cartella reale (per-skill link + esclusioni attive)"
  fi
  case "$rt" in
    */.claude/*) EXCL="$UL/skills-exclude-claude.txt" ;;
    */.codex/*)  EXCL="$UL/skills-exclude-codex.txt" ;;
    *)           EXCL="" ;;
  esac
  for d in "$AG"/*/; do
    [ -e "$d" ] || continue
    s="$(basename "$d")"
    if [ -n "$EXCL" ] && [ -f "$EXCL" ] && grep -qxF "$s" "$EXCL"; then
      if [ -L "$rt/$s" ]; then
        rm -f "$rt/$s" && log "runtime: $s esclusa da $rt (lazy)"
      fi
      continue
    fi
    if [ ! -L "$rt/$s" ]; then
      rm -rf "$rt/$s"
      ln -sfn "$AG/$s" "$rt/$s" && log "runtime: ricollegata $s in $rt"
    fi
  done
done

# ── 4.5. Claude hooks: deploy lo script del vault + merge dei trigger in settings.json ──
# Universale come su Windows: stesso hook, idempotente, preserva gli altri hook.
HOOK_SRC="$UL/hooks/claude-vault-checkpoint.mjs"
CLAUDE_DIR="$HOME_DIR/.claude"
HOOK_DST="$CLAUDE_DIR/claude-vault-checkpoint.mjs"
SETTINGS="$CLAUDE_DIR/settings.json"
if [ -f "$HOOK_SRC" ] && [ -d "$CLAUDE_DIR" ]; then
  if ! cmp -s "$HOOK_SRC" "$HOOK_DST" 2>/dev/null; then
    cp -f "$HOOK_SRC" "$HOOK_DST" && log "claude-hooks: deployato $HOOK_DST"
  fi
  if [ -f "$SETTINGS" ] && command -v jq >/dev/null 2>&1; then
    CMD="node \"$HOOK_DST\""
    tmp="$(mktemp)"
    if jq --arg c "$CMD" '
        .hooks = (.hooks // {})
        | .hooks.SessionStart = ((.hooks.SessionStart // []) | if any(.[]?; .hooks[]?.command == $c) then . else . + [{hooks:[{type:"command",command:$c,timeout:5}]}] end)
        | .hooks.PreCompact   = ((.hooks.PreCompact   // []) | if any(.[]?; .hooks[]?.command == $c) then . else . + [{hooks:[{type:"command",command:$c,timeout:5}]}] end)
      ' "$SETTINGS" >"$tmp" 2>/dev/null && [ -s "$tmp" ]; then
      if ! cmp -s "$tmp" "$SETTINGS"; then
        cp -f "$SETTINGS" "$SETTINGS.pre-hooks-$(date +%Y%m%d-%H%M%S).bak"
        mv "$tmp" "$SETTINGS" && log "claude-hooks: merge SessionStart/PreCompact in $SETTINGS"
      else
        rm -f "$tmp"
      fi
    else
      rm -f "$tmp"; log "claude-hooks: merge jq fallito o jq assente, settings.json invariato"
    fi
  fi
fi

fi # DO_APPLY

# ── 5. Push di commit locali già fatti (mai auto-commit di file sporchi) ─────
# Le due workstation (Linux/Windows) condividono lo stesso branch su Oracle.
# Se l'altra ha pubblicato nel frattempo, il push viene RIFIUTATO (non-fast-forward):
# è diverso da "Oracle offline". In quel caso facciamo un rebase PULITO e ritentiamo,
# così la divergenza benigna (file diversi sulle due macchine) si risolve da sola.
# Solo un conflitto VERO (stesse righe) resta manuale: rebase --abort + lo segnala il
# healthcheck. Mai un merge automatico, mai perdita di lavoro.
if [ "$DO_PUSH" = 1 ] && git -C "$AGENT_VAULT_DATA" rev-parse --verify "$REMOTE/$BRANCH" >/dev/null 2>&1; then
  ahead="$(git -C "$AGENT_VAULT_DATA" rev-list --count "$REMOTE/$BRANCH..$BRANCH" 2>/dev/null || echo 0)"
  if [ "${ahead:-0}" -gt 0 ]; then
    push_ok=0
    if git -C "$AGENT_VAULT_DATA" push "$REMOTE" "$BRANCH" >>"$LOG" 2>&1; then
      push_ok=1; log "push: $ahead commit pubblicati su $REMOTE"
    elif git -C "$AGENT_VAULT_DATA" fetch --prune "$REMOTE" "$BRANCH" >>"$LOG" 2>&1; then
      # Oracle è raggiungibile (fetch ok) → il push è stato rifiutato, non offline.
      if [ -n "$(git -C "$AGENT_VAULT_DATA" status --porcelain --untracked-files=no)" ]; then
        log "push: rifiutato ma working tree con modifiche tracciate non committate — non rebaso, risolvi a mano"
      elif git -C "$AGENT_VAULT_DATA" rebase "$REMOTE/$BRANCH" >>"$LOG" 2>&1; then
        if git -C "$AGENT_VAULT_DATA" push "$REMOTE" "$BRANCH" >>"$LOG" 2>&1; then
          push_ok=1; log "push: divergenza risolta con rebase pulito e pubblicata su $REMOTE"
        else
          log "push: ancora rifiutato dopo rebase — riproverò al prossimo giro"
        fi
      else
        git -C "$AGENT_VAULT_DATA" rebase --abort >/dev/null 2>&1
        log "push: DIVERGENZA CON CONFLITTI — serve 'git pull --rebase' manuale (lo segnala il healthcheck)"
      fi
    else
      log "push: $REMOTE non raggiungibile (offline) — i commit restano locali, riproverò"
    fi
    # origin = MIRROR della linea oracle: se rifiuta (diverso da offline), si
    # riallinea con force-with-lease, mai rebasando la storia gia' su oracle.
    if [ "$push_ok" = 1 ] && ! git -C "$AGENT_VAULT_DATA" push origin "$BRANCH" >>"$LOG" 2>&1; then
      if git -C "$AGENT_VAULT_DATA" fetch --prune origin "$BRANCH" >>"$LOG" 2>&1 \
         && git -C "$AGENT_VAULT_DATA" push --force-with-lease origin "$BRANCH" >>"$LOG" 2>&1; then
        log "push: origin (mirror) riallineato alla linea oracle (force-with-lease)"
      else
        log "push: GitHub (origin) non raggiungibile o lease scaduta — riproverò al prossimo giro"
      fi
    fi
  fi
fi

# ── 6. Auto-provisioning creds alert (2 macchine fidate) + healthcheck raggruppato ───────────
if [ "$DO_CREDS" = 1 ]; then
  [ -x "$ENGINE_SCRIPTS/ensure-alert-creds.sh" ] && "$ENGINE_SCRIPTS/ensure-alert-creds.sh" >>"$LOG" 2>&1
fi
TG_CONF="$HOME_DIR/.config/environment.d/91-telegram-alert.conf"
[ -f "$TG_CONF" ] && { set -a; . "$TG_CONF"; set +a; }
if [ "$DO_HEALTH" = 1 ] && [ -x "$ENGINE_SCRIPTS/agent-healthcheck.sh" ]; then
  "$ENGINE_SCRIPTS/agent-healthcheck.sh" >>"$LOG" 2>&1 || log "healthcheck: non riuscito (best-effort)"
fi

dirty="$(git -C "$AGENT_VAULT_DATA" status --porcelain 2>/dev/null | wc -l)"
[ "$dirty" -gt 0 ] && log "nota: $dirty file non committati nel vault (non li tocco)"

log "agent-sync: completato mode=$MODE"
exit 0
