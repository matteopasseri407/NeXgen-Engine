#!/usr/bin/env bash
# NeXgen Vault (Agent-OS) — bootstrap / preflight
# ---------------------------------------------------------------------------
# Deterministic first-run helper. It does NOT replace the AI-guided installer
# (INIT.md): it does the mechanical part — check prerequisites, verify the
# vault scaffold, detect your CLIs, compute your install profile — and then
# hands you the exact next step. Safe to re-run; it writes nothing by default.
#
#   bash install.sh            # preflight + guided profile + next steps
#   bash install.sh --check    # preflight only (no questions)
# ---------------------------------------------------------------------------
set -u

ROOT="$(cd "$(dirname "$0")" && pwd)"

# ---- colours (degrade gracefully with no TTY / NO_COLOR) --------------------
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  B=$'\033[1m'; DIM=$'\033[2m'; R=$'\033[0m'
  GRN=$'\033[32m'; RED=$'\033[31m'; YEL=$'\033[33m'; CYN=$'\033[36m'
else
  B=''; DIM=''; R=''; GRN=''; RED=''; YEL=''; CYN=''
fi
ok(){   printf '  %s✓%s %s\n' "$GRN" "$R" "$1"; }
bad(){  printf '  %s✗%s %s\n' "$RED" "$R" "$1"; }
warn(){ printf '  %s○%s %s\n' "$YEL" "$R" "$1"; }
hdr(){  printf '\n%s%s%s\n' "$B$CYN" "$1" "$R"; }

MODE="${1:-guided}"
MISS_REQ=0

banner(){
  printf '%s\n' "$B$CYN"
  printf '  ┌───────────────────────────────────────────────┐\n'
  printf '  │   NeXgen Vault (Agent-OS)  ·  bootstrap        │\n'
  printf '  └───────────────────────────────────────────────┘%s\n' "$R"
  printf '  %sAgentOps governance for AI agents — Git-backed vault.%s\n' "$DIM" "$R"
}

# ---- OS detection ----------------------------------------------------------
detect_os(){
  case "$(uname -s 2>/dev/null)" in
    Linux*)  OS=linux;  HINT_PKG="sudo apt install" ;;
    Darwin*) OS=macos;  HINT_PKG="brew install" ;;
    MINGW*|MSYS*|CYGWIN*) OS=windows; HINT_PKG="winget install / choco install" ;;
    *)       OS=unknown; HINT_PKG="your package manager" ;;
  esac
}

have(){ command -v "$1" >/dev/null 2>&1; }

check_prereqs(){
  hdr "1 · Prerequisites"
  if have git; then ok "git — $(git --version 2>/dev/null | head -1)"; else bad "git MISSING (required) → $HINT_PKG git"; MISS_REQ=1; fi
  if have python3; then ok "python3 — $(python3 --version 2>&1)"; else bad "python3 MISSING (required) → $HINT_PKG python3"; MISS_REQ=1; fi
  if have python3 && python3 -c 'import yaml' 2>/dev/null; then
    ok "PyYAML (python module 'yaml')"
  else
    bad "PyYAML MISSING (required for the engine) → pip install pyyaml"; MISS_REQ=1
  fi
  if have npx; then ok "node/npx — needed to mount MCP servers & external skills"; else warn "node/npx not found — needed only if you mount MCP servers or npx skills"; fi
  if have jq; then ok "jq"; else warn "jq not found — needed only for the MULTI sync/health scripts"; fi
  if have curl; then ok "curl"; else warn "curl not found — needed only for the MULTI health scripts"; fi
  if have gpg; then ok "gpg — for the encrypted 99-SECRETS archive"; else warn "gpg not found — needed only if you store secrets in 99-SECRETS/"; fi
}

check_scaffold(){
  hdr "2 · Vault scaffold"
  for d in 01-NOTES 02-PROJECTS 04-NOW 99-INDEX 99-SECRETS; do
    if [ -d "$ROOT/$d" ]; then ok "$d/"; else warn "$d/ missing — creating"; mkdir -p "$ROOT/$d"; : > "$ROOT/$d/.gitkeep"; fi
  done
  for f in INIT.md 00-START-HERE.md 99-INDEX/USER-PROFILE.md 03-INFRA/agent-universal-layer/instructions/AGENTS.md; do
    if [ -f "$ROOT/$f" ]; then ok "$f"; else bad "$f MISSING — is this a full clone?"; MISS_REQ=1; fi
  done
}

detect_clis(){
  hdr "3 · Agentic CLIs detected on this machine"
  FOUND_CLIS=""
  if have claude; then ok "Claude Code (claude)"; FOUND_CLIS="$FOUND_CLIS claude"; fi
  if have codex;  then ok "Codex (codex)";        FOUND_CLIS="$FOUND_CLIS codex";  fi
  if have opencode; then ok "OpenCode (opencode)"; FOUND_CLIS="$FOUND_CLIS opencode"; fi
  [ -d "$HOME/.gemini" ] && { ok "Antigravity/Gemini (~/.gemini)"; FOUND_CLIS="$FOUND_CLIS antigravity"; }
  if [ -z "$FOUND_CLIS" ]; then
    warn "No agentic CLI found on PATH."
    printf '     %sThe installer needs a filesystem-capable agent (Claude Code, Codex, OpenCode,\n     Antigravity). A plain web chat (claude.ai / gemini) CANNOT write files.%s\n' "$DIM" "$R"
  fi
}

guided_profile(){
  hdr "4 · Guided profile (no files written — recommendation only)"
  [ ! -t 0 ] && { warn "not an interactive terminal — skipping questions"; return; }
  printf '  %sHow many CLIs will you run?%s [1 / 2+]: ' "$B" "$R"; read -r NCLI
  printf '  %sHow many machines to keep aligned?%s [1 / 2+]: ' "$B" "$R"; read -r NMACH
  printf '  %sArchitecture?%s [L=Local-only / C=Cloud-server (VPS)]: ' "$B" "$R"; read -r ARCH
  PROFILE=MINIMAL
  case "$NCLI$NMACH" in *2*) PROFILE=MULTI ;; esac
  printf '\n  %sProfile:%s %s%s%s\n' "$B" "$R" "$B$CYN" "$PROFILE" "$R"
  if [ "$PROFILE" = MINIMAL ]; then
    printf '     %s→ one CLI, one machine. No agent-sync/doctor/timer. Mount MCP + skills by hand.%s\n' "$DIM" "$R"
  else
    printf '     %s→ multiple CLIs/machines. Use agent-sync (apply/guard) to keep them aligned.%s\n' "$DIM" "$R"
  fi
  case "$ARCH" in
    C|c) printf '     %s→ Cloud-Server: deploy 03-INFRA/deploy/ on a VPS and set the tunnel env vars.%s\n' "$DIM" "$R" ;;
    *)   printf '     %s→ Local-Only: native web search, model vision for OCR, no remote automations.%s\n' "$DIM" "$R" ;;
  esac
}

next_steps(){
  hdr "5 · Next step"
  if [ "$MISS_REQ" = 1 ]; then
    printf '  %s✗ Fix the MISSING required items above, then re-run: bash install.sh%s\n' "$RED" "$R"
    return 1
  fi
  cat <<EOF
  ${GRN}✓ Preflight passed.${R} Finish the install with the AI-guided installer:

    1. Open ${B}INIT.md${R}.
    2. Paste its whole content into a ${B}filesystem-capable agent CLI${R}
       (Claude Code, Codex, OpenCode, Antigravity) opened in this folder —
       ${DIM}NOT a plain web chat, which cannot write files.${R}
    3. Answer its interview; it writes 99-INDEX/USER-PROFILE.md and mounts
       your MCP servers and skills.

  ${DIM}MCP dialects reference: 03-INFRA/agent-universal-layer/mcp/render.py
  Secrets workflow: 99-SECRETS/README.md${R}
EOF
}

# ---- run -------------------------------------------------------------------
banner
detect_os
check_prereqs
check_scaffold
detect_clis
[ "$MODE" != "--check" ] && guided_profile
next_steps
