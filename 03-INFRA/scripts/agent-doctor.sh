#!/usr/bin/env bash
# shellcheck disable=SC2015
# agent-doctor — verifies that ALL agents are genuinely aligned and operational.
# Read-only with respect to configuration and service state. The strict
# Firecrawl probe may refresh a small local success cache. Exit 0 if no FAIL.
# Usage:
#   agent-doctor.sh             readable report (colors, sections)
#   agent-doctor.sh --summary   one-line summary (for digests/alerts)
set -u

VAULT="${KNOWLEDGE_VAULT_PATH:-$HOME/KnowledgeVault}"
VAULT_DATA="${AGENT_VAULT_DATA:-$VAULT}"
# Where THIS script itself lives: render.py, skills-sync.py and the OCR MCP
# wrapper always ship in the same engine tree as agent-doctor.sh, so its own
# resolved location is a more reliable source of truth than re-deriving a
# path from KNOWLEDGE_VAULT_PATH -- which silently breaks the moment engine
# and vault data live in two different places (confirmed live: this is why
# the MCP-drift check below was silently skipping after the S3 cutover).
SELF_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
ENGINE_ROOT="${AGENT_ENGINE_ROOT:-$(dirname "$SELF_DIR")}"
ENGINE_UL="$ENGINE_ROOT/agent-universal-layer"
UL="$VAULT_DATA/03-INFRA/agent-universal-layer"
CANON="$UL/instructions/AGENTS.md"
BRANCH="${KNOWLEDGE_VAULT_BRANCH:-main}"
OCJSON=""
for candidate in \
  "$HOME/.config/opencode/opencode.jsonc" \
  "$HOME/.config/opencode/opencode.json" \
  "$HOME/.config/opencode/config.json"; do
  if [ -f "$candidate" ]; then
    OCJSON="$candidate"
    break
  fi
done
[ -n "$OCJSON" ] || OCJSON="$HOME/.config/opencode/opencode.jsonc"
PASS=0; WARN=0; FAILN=0; FAILS=""

REMOTE_CONFIG_ERROR=""
if [ -n "${KNOWLEDGE_VAULT_REMOTE:-}" ]; then
  REMOTE="$KNOWLEDGE_VAULT_REMOTE"
  MIRRORS="$(printf '%s' "${KNOWLEDGE_VAULT_MIRRORS:-}" | tr ',' '\n')"
else
  REMOTE="$(python3 "$SELF_DIR/agent_sync.py" config authoritative_remote 2>/dev/null)" || REMOTE_CONFIG_ERROR=1
  MIRRORS="$(python3 "$SELF_DIR/agent_sync.py" config mirrors 2>/dev/null)" || REMOTE_CONFIG_ERROR=1
  [ -n "$REMOTE" ] || { REMOTE="origin"; REMOTE_CONFIG_ERROR=1; }
fi

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
          Antigravity global MCP path, vault-ocr stdio framing, and one
          cached end-to-end Firecrawl search.
EOF
      exit 0 ;;
  esac
done

ok()   { PASS=$((PASS+1)); [ "$QUIET" = 1 ] || printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { WARN=$((WARN+1)); [ "$QUIET" = 1 ] || printf '  \033[33m⚠\033[0m %s\n' "$*"; }
fail() { FAILN=$((FAILN+1)); FAILS="${FAILS}${FAILS:+, }$*"; [ "$QUIET" = 1 ] || printf '  \033[31m✗\033[0m %s\n' "$*"; }
sec()  { [ "$QUIET" = 1 ] || printf '\n\033[1m%s\033[0m\n' "$*"; }
code() { curl -s -o /dev/null -m 6 -w '%{http_code}' "$@" 2>/dev/null || echo 000; }

# Temp files created by bearer_cfg() below; always cleaned up on exit so a
# bearer token never lingers on disk after this script exits.
_BEARER_CFG_FILES=""
cleanup_bearer_cfgs() {
  # shellcheck disable=SC2086
  [ -n "$_BEARER_CFG_FILES" ] && rm -f $_BEARER_CFG_FILES
}
trap cleanup_bearer_cfgs EXIT

# Writes a curl config file (mode 600) with the given bearer token as an
# Authorization header, instead of passing "Authorization: Bearer <token>"
# as a curl argv element -- an argv element is visible to any other local
# user via `ps` or /proc/<pid>/cmdline, a curl config file is not.
#
# Sets $_LAST_BEARER_CFG to the file path; pass it to curl via -K/--config
# (combinable with other curl flags on the same invocation). Deliberately
# NOT called as `x="$(bearer_cfg ...)"` -- command substitution runs the
# function in a subshell, so its update to $_BEARER_CFG_FILES would be lost
# on subshell exit and the EXIT trap above would then have nothing to clean
# up. Call it as a plain statement (`bearer_cfg "$token"`), then read
# $_LAST_BEARER_CFG.
_LAST_BEARER_CFG=""
bearer_cfg() {
  local token="$1" f
  f="$(mktemp)"
  chmod 600 "$f"
  printf 'header = "Authorization: Bearer %s"\n' "${token//\"/\\\"}" > "$f"
  _BEARER_CFG_FILES="$_BEARER_CFG_FILES $f"
  _LAST_BEARER_CFG="$f"
}

[ "$QUIET" = 1 ] || printf '\033[1m=== agent-doctor: agent alignment check ===\033[0m\n'

sec "Host"
case "$(uname -s)" in Linux) HOST=linux;; Darwin) HOST=mac;; *) HOST=other;; esac
ok "detected: $HOST ($(hostname), $(uname -s))"

sec "Vault (memory) — authoritative remote and mirrors"
[ -z "$REMOTE_CONFIG_ERROR" ] && ok "sync remote config resolved ($REMOTE)" || fail "invalid sync remote config — run: agent-sync config authoritative_remote"
if git -C "$VAULT" rev-parse >/dev/null 2>&1; then
  # Mid-rebase/merge guard (checked before the Local-Only vs remote-configured
  # split below, so it covers both): a conflicted `git pull --rebase` -- the
  # exact recovery agent_sync.py's own vault-push publish path tells the user
  # to run by hand on a conflicting divergence -- left main/HEAD in a state
  # the ahead/behind/dirty checks below cannot tell apart from ordinary
  # unpushed commits or an ordinary dirty tree: `git status --porcelain`
  # reports a conflicted file (e.g. "UU file.txt") as just one more line, and
  # rev-list --count on branch refs is unaffected by a detached HEAD mid-
  # rebase. Left alone, this doctor would print "commits behind" / "not
  # committed" -- wording that invites a naive `git add -A && git commit`
  # that bakes conflict markers straight into the vault instead of resolving
  # them. Detected via the same on-disk state `git status` itself reads, not
  # a subprocess parse of its text.
  VAULT_GIT_DIR="$(git -C "$VAULT" rev-parse --absolute-git-dir 2>/dev/null)"
  if [ -n "$VAULT_GIT_DIR" ] && { [ -d "$VAULT_GIT_DIR/rebase-merge" ] || [ -d "$VAULT_GIT_DIR/rebase-apply" ]; }; then
    fail "vault is mid-rebase with a conflict to resolve — run: git -C \"$VAULT\" rebase --abort (or resolve and rebase --continue), THEN re-run this check"
  elif [ -n "$VAULT_GIT_DIR" ] && [ -f "$VAULT_GIT_DIR/MERGE_HEAD" ]; then
    fail "vault is mid-merge with a conflict to resolve — run: git -C \"$VAULT\" merge --abort (or resolve and commit), THEN re-run this check"
  elif [ "$REMOTE" = "local" ] || [ "$REMOTE" = "none" ]; then
    # Local-Only sentinel (matches agent_sync.py's pull()/publish(): env.remote
    # in ("local", "none")): there is no authoritative remote to compare
    # against, so a fetch/ahead/behind/mirror check would otherwise try
    # `git fetch` from a remote literally named "local"/"none" -- which never
    # exists -- and hard-FAIL a correctly configured Local-Only install.
    ok "Local-Only mode ($REMOTE): no authoritative remote to compare against, skipping fetch/ahead/behind/mirror checks"
    d=$(git -C "$VAULT" status --porcelain --untracked-files=no 2>/dev/null | wc -l | tr -d ' ')
    [ "$d" = 0 ] && ok "working tree clean (tracked files)" || warn "$d tracked files not committed"
  else
    # Capture the real stderr instead of discarding it: a bare `|| warn
    # "...(offline?)"` used to guess "offline" even for e.g. a permissions
    # error on the remote, and rev-list's own '?' fallback (below) used to
    # leak straight into "fail" as a literal, unexplained question mark.
    fetch_err="$(git -C "$VAULT" fetch --prune "$REMOTE" "$BRANCH" 2>&1 >/dev/null)"
    fetch_rc=$?
    if [ "$fetch_rc" -ne 0 ]; then
      fetch_detail="$(printf '%s' "$fetch_err" | tr '\n' ' ' | sed -E 's/ +/ /g; s/ $//')"
      warn "fetch $REMOTE failed: ${fetch_detail:-no error output from git} (offline, or the remote is unreachable)"
    fi
    b=$(git -C "$VAULT" rev-list --count "$BRANCH..$REMOTE/$BRANCH" 2>/dev/null || echo '?')
    a=$(git -C "$VAULT" rev-list --count "$REMOTE/$BRANCH..$BRANCH" 2>/dev/null || echo '?')
    d=$(git -C "$VAULT" status --porcelain --untracked-files=no 2>/dev/null | wc -l | tr -d ' ')
    if [ "$b" = "?" ]; then
      fail "cannot tell how many commits behind the cloud: rev-list failed (see the fetch warning above)"
    elif [ "$b" = 0 ]; then
      ok "aligned with $REMOTE/$BRANCH (0 behind)"
    else
      fail "$b commits behind the cloud"
    fi
    if [ "$a" = 0 ] || [ "$a" = "?" ]; then
      ok "no unpublished local commits"
    else
      oldest_ts=$(git -C "$VAULT" log --reverse --format=%ct "$REMOTE/$BRANCH..$BRANCH" 2>/dev/null | head -n 1)
      if [ -n "$oldest_ts" ]; then
        now=$(date +%s)
        age=$((now - oldest_ts))
        if [ "$age" -gt 7200 ]; then
          fail "$a dangling commit(s) in Vault (oldest is $((age / 3600))h old) — RUN vault-push!"
        else
          warn "$a unpublished local commits (recent, < 2h old)"
        fi
      else
        warn "$a unpublished local commits"
      fi
    fi
    [ "$d" = 0 ] && ok "working tree clean (tracked files)" || warn "$d tracked files not committed (blocks the pull)"
    while IFS= read -r mirror; do
      [ -n "$mirror" ] || continue
      if ! git -C "$VAULT" fetch --prune "$mirror" "$BRANCH" >/dev/null 2>&1; then
        warn "mirror $mirror fetch failed"
        continue
      fi
      auth_head=$(git -C "$VAULT" rev-parse "$REMOTE/$BRANCH" 2>/dev/null || echo "")
      mirror_head=$(git -C "$VAULT" rev-parse "$mirror/$BRANCH" 2>/dev/null || echo "")
      if [ -n "$auth_head" ] && [ "$auth_head" = "$mirror_head" ]; then
        ok "mirror $mirror aligned with authoritative $REMOTE"
      else
        warn "mirror $mirror differs from authoritative $REMOTE (canonical Vault remains $REMOTE)"
      fi
    done <<EOF
$MIRRORS
EOF
  fi
else
  fail "the vault is not a git repo: $VAULT"
fi

sec "Canonical instructions (single AGENTS.md, Claude anti-duplication pointer)"
[ -f "$CANON" ] && ok "canonical file present" || fail "missing canonical file $CANON"
CLAUDE_FILE="$HOME/CLAUDE.md"
if [ -f "$CLAUDE_FILE" ] && [ ! -L "$CLAUDE_FILE" ] && grep -Fq "$CANON" "$CLAUDE_FILE" && grep -Fq "compatibility pointer" "$CLAUDE_FILE"; then
  ok "Claude pointer -> canonical AGENTS.md"
else
  fail "Claude.md must be a lightweight pointer, not a copy/symlink of the canonical file ($CLAUDE_FILE)"
fi
# NOTE (found 2026-07-01): Antigravity actually reads ~/.gemini/config/AGENTS.md,
# NOT ~/ANTIGRAVITY.md — that symlink was a dead pattern copied from Codex,
# never read by the app, and this check gave a false "ok" for days while it
# still existed. agent_sync.py's instructions() now actively removes ~/ANTIGRAVITY.md
# as dead wiring on every run, so it should no longer be present at all.
# Verified only with a real behavioral probe (agy -p), not with the symlink's
# mere existence: this loop only proves the WIRING, not that the CLI honors
# the file. If a phantom "aligned" ever shows up again, this is suspect
# number one.
for pair in "Codex:$HOME/.codex/AGENTS.md" "Antigravity:$HOME/.gemini/config/AGENTS.md"; do
  name="${pair%%:*}"; f="${pair#*:}"
  if [ "$(readlink -f "$f" 2>/dev/null)" = "$(readlink -f "$CANON" 2>/dev/null)" ]; then
    ok "$name → canonical AGENTS.md"
  elif [ ! -e "$f" ] && ! command -v "$( [ "$name" = "Codex" ] && printf codex || printf agy )" >/dev/null 2>&1; then
    # Same asymmetry fix as OpenCode's branch below: a CLI that was never
    # installed has no code path that will ever create this file, so a hard
    # FAIL here is permanent and unfixable on a fresh consumer install.
    # The `[ ! -e ]` guard keeps the CLI-absent leniency from masking a
    # PRESENT-but-divergent file: when the file exists and does not match,
    # that is a real drift worth a FAIL regardless of PATH (2026-08-15
    # council, Opus 5).
    warn "$name not installed here? (missing $f)"
  else
    fail "$name does NOT point to the canonical file ($f)"
  fi
done
if [ -f "$OCJSON" ]; then
  oc_canon_count="$(python3 - "$SELF_DIR" "$OCJSON" <<'PY' 2>/dev/null
import sys
sys.path.insert(0, sys.argv[1])
from config_schema import parse_jsonc
with open(sys.argv[2], encoding="utf-8") as handle:
    entries = parse_jsonc(handle.read()).get("instructions", [])
suffix = "/agent-universal-layer/instructions/agents.md"
print(sum(isinstance(item, str) and item.replace("\\", "/").rstrip("/").lower().endswith(suffix) for item in entries))
PY
)"
  case "$oc_canon_count" in
    1) ok "OpenCode instructions → one canonical AGENTS.md" ;;
    0) fail "OpenCode instructions do NOT point to AGENTS.md" ;;
    ''|*[!0-9]*) fail "OpenCode instructions cannot be inspected because $(basename "$OCJSON") is invalid" ;;
    *) warn "OpenCode loads the canonical AGENTS.md $oc_canon_count times; run agent-sync apply to deduplicate slash variants" ;;
  esac
elif command -v opencode >/dev/null 2>&1 || [ -x "$HOME/.opencode/bin/opencode" ]; then
  fail "missing $OCJSON"
else
  # OpenCode genuinely not installed here: unlike a missing config on an
  # installed CLI (a real problem render.py already bootstraps on its own),
  # there is no code path that will ever create this file for a CLI that was
  # never installed. Codex/Antigravity absent is a silent no-op elsewhere in
  # this script (see the Codex/Antigravity loop above); OpenCode used to be
  # the only one of the four CLIs that turned "genuinely never installed"
  # into a permanent, unfixable FAIL.
  warn "OpenCode not installed here? (missing $OCJSON)"
fi

sec "Canonical bootstrap hygiene (size budget, pointer integrity)"
# Additive, read-only guardrails on the single AGENTS.md bootstrap and its
# load-on-demand detail notes. WARN-only by design: they surface drift (a
# bloated bootstrap, a pointer to a note renamed/removed out from under the
# list) without ever flipping a green doctor red on a pre-existing condition.
# Budgets are overridable via env for installs with different conventions.
BOOTSTRAP_MAX_BYTES="${NEXGEN_BOOTSTRAP_MAX_BYTES:-40000}"
NOTE_MAX_BYTES="${NEXGEN_NOTE_MAX_BYTES:-16000}"
if [ -f "$CANON" ]; then
  canon_bytes=$(wc -c < "$CANON" | tr -d ' ')
  if [ "$canon_bytes" -gt "$BOOTSTRAP_MAX_BYTES" ]; then
    warn "bootstrap AGENTS.md is ${canon_bytes} bytes, over the ${BOOTSTRAP_MAX_BYTES}-byte budget -- move task-specific content into a load-on-demand note (override: NEXGEN_BOOTSTRAP_MAX_BYTES)"
  else
    ok "bootstrap AGENTS.md within budget (${canon_bytes}/${BOOTSTRAP_MAX_BYTES} bytes)"
  fi
  NOTES_DIR="$VAULT_DATA/03-INFRA"
  oversized=""
  if [ -d "$NOTES_DIR" ]; then
    for note in "$NOTES_DIR"/*.md; do
      [ -f "$note" ] || continue
      nb=$(wc -c < "$note" | tr -d ' ')
      [ "$nb" -gt "$NOTE_MAX_BYTES" ] && oversized="${oversized}${oversized:+, }$(basename "$note") (${nb}b)"
    done
  fi
  [ -z "$oversized" ] && ok "detail notes within the ${NOTE_MAX_BYTES}-byte budget" || warn "oversized detail note(s) over ${NOTE_MAX_BYTES} bytes, consider splitting: $oversized (override: NEXGEN_NOTE_MAX_BYTES)"
  # Load-on-demand pointer integrity: every vault-relative note path in
  # backticks must resolve under the vault. The literal placeholder
  # 03-INFRA/<topic>.md in the editing-discipline prose is skipped (angle
  # brackets); ~-rooted paths and URLs never match the vault-prefix set.
  BT='`'
  ptr_list="$(grep -oE "${BT}(03-INFRA|99-INDEX|04-NOW|02-PROJECTS|01-NOTES|00-START-HERE)[^${BT}]*${BT}" "$CANON" 2>/dev/null | tr -d "$BT" | grep -E '\.md$' | sort -u)"
  missing_ptr=""
  checked_ptr=0
  while IFS= read -r ref; do
    [ -n "$ref" ] || continue
    case "$ref" in *"<"*|*">"*) continue ;; esac
    checked_ptr=$((checked_ptr + 1))
    [ -f "$VAULT_DATA/$ref" ] || missing_ptr="${missing_ptr}${missing_ptr:+, }$ref"
  done <<EOF
$ptr_list
EOF
  if [ "$checked_ptr" = 0 ]; then
    ok "no vault-relative bootstrap pointers to verify"
  elif [ -z "$missing_ptr" ]; then
    ok "all $checked_ptr bootstrap load-on-demand pointers resolve"
  else
    warn "bootstrap load-on-demand pointer(s) not found under the vault: $missing_ptr -- a renamed/removed note leaves a dead pointer"
  fi
  # Required invariant rules present in the canonical AGENTS.md (guards the
  # non-negotiable security/behaviour rules from silently vanishing -- the
  # vault<->public drift class). Read-only, WARN-only; skips if the checker or
  # its rules file isn't present in this engine tree.
  RULES_CHECK="$SELF_DIR/check_required_rules.py"
  RULES_FILE="$ENGINE_UL/instructions/required-rules.txt"
  if command -v python3 >/dev/null 2>&1 && [ -f "$RULES_CHECK" ] && [ -f "$RULES_FILE" ]; then
    if rules_out="$(python3 "$RULES_CHECK" "$CANON" "$RULES_FILE" 2>/dev/null)"; then
      ok "canonical AGENTS.md carries all required invariant rules"
    else
      miss="$(printf '%s\n' "$rules_out" | sed -n 's/^    - //p' | tr '\n' ';')"
      warn "canonical AGENTS.md is missing required invariant rule(s): ${miss:-see check} -- run: python3 $RULES_CHECK $CANON $RULES_FILE"
    fi
  fi
else
  warn "canonical AGENTS.md not found, skipping bootstrap hygiene checks"
fi

# Vault-wide wikilink integrity backstop (WARN-only, like every hygiene
# check): the primary anti-rot mechanisms are the write-time advisory and
# the groom preview map; this only catches what slips through. Skips
# silently when python3 or the script is unavailable.
MAP_SCRIPT="$SELF_DIR/vault-map.py"
if [ -f "$MAP_SCRIPT" ] && command -v python3 >/dev/null 2>&1; then
  map_line="$(python3 "$MAP_SCRIPT" --vault "$VAULT_DATA" --check 2>/dev/null | head -n 1)"
  if [ -z "$map_line" ]; then
    warn "vault-map backstop could not analyze the vault"
  else
    case " $map_line " in
      *" broken=0 "*) ok "vault wikilinks all resolve ($map_line)" ;;
      *) warn "broken wikilink(s) in the vault ($map_line) -- a renamed note leaves dead links behind; run: python3 $MAP_SCRIPT --vault $VAULT_DATA" ;;
    esac
  fi
fi

sec "Deterministic agent utilities"
if command -v agent-now >/dev/null 2>&1; then
  now_payload="$(agent-now --json 2>/dev/null || true)"
  if printf '%s\n' "$now_payload" | grep -q '"source": "system_clock"' && printf '%s\n' "$now_payload" | grep -q '"local_time"'; then
    ok "agent-now available and working"
  else
    fail "agent-now present but output invalid"
  fi
else
  fail "agent-now not in PATH (run agent-sync.sh)"
fi

sec "Architecture Mode (99-INDEX/USER-PROFILE.md)"
# Mode is a MINIMUM baseline the user commits to, never a ceiling (see the
# clarifying paragraph in USER-PROFILE.md itself). It only gates whether a
# missing/unreachable connector below counts as a real FAIL: a connector is
# "expected" when Mode declares CLOUD-SERVER, OR when its own env var
# (matching manifest.yaml's require_env) is already set regardless of Mode --
# so an upgrade past the declared Mode, or a single cloud connector added
# while staying Local-Only for the rest, is recognized automatically and
# never punished for going beyond what Mode declares. A missing/unparseable
# Mode is treated as unknown (same gating as Local-Only, never a crash).
USER_PROFILE_MD="$VAULT_DATA/99-INDEX/USER-PROFILE.md"
DECLARED_MODE="unknown"
if [ -f "$USER_PROFILE_MD" ]; then
  mode_line="$(grep -m1 '\*\*Mode\*\*' "$USER_PROFILE_MD" 2>/dev/null || true)"
  mode_value="$(printf '%s' "$mode_line" | sed -E 's/.*\*\*Mode\*\*[[:space:]]*:[[:space:]]*//' | tr -d '`[]' | tr '[:lower:]' '[:upper:]' | sed -E 's/[[:space:]]+$//')"
  case "$mode_value" in
    LOCAL-ONLY) DECLARED_MODE="local-only" ;;
    CLOUD-SERVER) DECLARED_MODE="cloud-server" ;;
    *) DECLARED_MODE="unknown" ;;
  esac
fi
case "$DECLARED_MODE" in
  local-only)   ok "USER-PROFILE.md declares Mode: LOCAL-ONLY" ;;
  cloud-server) ok "USER-PROFILE.md declares Mode: CLOUD-SERVER" ;;
  *) warn "USER-PROFILE.md Mode not found or not parseable ($USER_PROFILE_MD) -- treated as unknown (same gating as Local-Only unless a connector's own env var is already set)" ;;
esac
connector_expected() {
  # $1 = the connector's require_env var name (see manifest.yaml)
  [ "$DECLARED_MODE" = "cloud-server" ] && return 0
  [ -n "${!1:-}" ]
}

# GUARDRAIL: the engine can apply a "bypass" permission posture (no
# confirmation prompts) to a CLI, but the anti-catastrophic-command guardrail
# that is supposed to accompany it only exists as a PreToolUse hook -- see
# claude_permissions()'s own comment in agent_sync.py for why a hook, not a
# permissions.deny list, is the only thing that still runs under bypass. This
# reads the PRIVATE instance permissions manifest directly (never the strict
# schema validator, which today still only recognizes "claude" as a target --
# this check must keep working as that set grows) and calls out, visibly, any
# CLI running in bypass posture with no declared PreToolUse hook for it. A
# missing manifest (the public-engine default: no policy shipped) is a
# complete, silent no-op.
PERM_MANIFEST="$UL/permissions/manifest.yaml"
if [ -f "$PERM_MANIFEST" ]; then
  sec "Permission posture guardrail (instance manifest)"
  if command -v python3 >/dev/null 2>&1; then
    bypass_report="$(python3 - "$PERM_MANIFEST" <<'PY' 2>/dev/null
import sys
from pathlib import Path
import yaml
path = Path(sys.argv[1])
try:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
except Exception:
    sys.exit(0)
if not isinstance(data, dict):
    sys.exit(0)
posture = data.get("posture")
if not isinstance(posture, dict):
    sys.exit(0)
hooks = data.get("hooks")
if not isinstance(hooks, list):
    hooks = []
guarded = set()
for h in hooks:
    if not isinstance(h, dict) or h.get("event") != "PreToolUse":
        continue
    for t in (h.get("targets") or []):
        if isinstance(t, str):
            guarded.add(t)
for cli, value in posture.items():
    if value == "bypass":
        print(f"{cli} {'GUARDED' if cli in guarded else 'UNGUARDED'}")
PY
)"
    if [ -n "$bypass_report" ]; then
      while IFS=' ' read -r cli guard_state; do
        [ -n "$cli" ] || continue
        if [ "$guard_state" = "GUARDED" ]; then
          ok "$cli runs in bypass posture (no confirmation prompts) WITH a declared PreToolUse guardrail hook"
        else
          # warn, never fail: nobody lands here by accident. This state exists
          # only because the instance manifest deliberately declares a bypass
          # posture for a CLI and no guardrail hook targeting it, so it is a
          # standing choice, not a fault. A FAIL that can never be cleared
          # keeps the doctor permanently red and every alert channel firing,
          # which is how a real FAIL stops being read.
          warn "$cli runs in bypass posture (no confirmation prompts) WITHOUT a declared PreToolUse guardrail hook -- catastrophic commands are NOT intercepted"
        fi
      done <<EOF
$bypass_report
EOF
    else
      ok "no CLI declared in bypass posture in the instance permissions manifest"
    fi
  else
    warn "python3 not found -- cannot inspect the instance permissions manifest for bypass posture"
  fi
fi

sec "MCP connectors — reachability"
c=$(code http://127.0.0.1:5678/healthz)
if [ "$c" = 200 ]; then
  ok "n8n-mcp (5678): $c"
elif connector_expected N8N_MCP_TOKEN; then
  fail "n8n-mcp (5678): $c"
else
  ok "n8n-mcp (5678): not reachable ($c) -- not expected in current Mode (Local-Only / N8N_MCP_TOKEN not set)"
fi
c=$(code http://127.0.0.1:33002/)
if [ "$c" = 200 ] || [ "$c" = 302 ]; then
  ok "firecrawl (33002): $c"
  if [ "$STRICT" = 1 ] && connector_expected FIRECRAWL_TUNNEL_PORT; then
    search_health="$SELF_DIR/firecrawl-search-health.py"
    search_cache="${XDG_CACHE_HOME:-$HOME/.cache}/nexgen/firecrawl-search-health.json"
    if command -v python3 >/dev/null 2>&1 && [ -f "$search_health" ]; then
      if search_result="$(python3 "$search_health" --cache "$search_cache" 2>/dev/null)"; then
        case "$search_result" in
          *'"status":"cached"'*) ok "firecrawl search: end-to-end result cached for less than 24h" ;;
          *) ok "firecrawl search: live end-to-end query returned the expected source" ;;
        esac
      else
        fail "firecrawl search: API is reachable but the end-to-end query failed"
      fi
    else
      warn "firecrawl search: strict probe skipped because Python or firecrawl-search-health.py is missing"
    fi
  fi
elif connector_expected FIRECRAWL_TUNNEL_PORT; then
  fail "firecrawl (33002): $c"
else
  ok "firecrawl (33002): not reachable ($c) -- not expected in current Mode (Local-Only / FIRECRAWL_TUNNEL_PORT not set)"
fi
c=$(code http://127.0.0.1:33003/health)
if [ "$c" = 200 ]; then
  ok "vault-ocr (33003): $c"
elif connector_expected OCR_TUNNEL_PORT; then
  fail "vault-ocr (33003): $c"
else
  ok "vault-ocr (33003): not reachable ($c) -- not expected in current Mode (Local-Only / OCR_TUNNEL_PORT not set)"
fi
vault_library_url="${VAULT_LIBRARY_URL:-}"
render_py="$ENGINE_UL/mcp/render.py"
if command -v python3 >/dev/null 2>&1 && [ -f "$render_py" ]; then
  rendered_vault_url="$(python3 "$render_py" --server-url vault-library 2>/dev/null)"
  rendered_vault_rc=$?
  if [ "$rendered_vault_rc" -eq 0 ] && [ -n "$rendered_vault_url" ]; then
    vault_library_url="$rendered_vault_url"
  elif [ "$rendered_vault_rc" -ne 3 ]; then
    fail "vault-library endpoint cannot be derived from the rendered manifest"
  fi
fi
if [ -n "$vault_library_url" ]; then
  # Streamable HTTP MCP rejects a generic GET without the protocol Accept
  # header. OPTIONS gives a bounded, authenticated route probe without
  # opening a response stream; a healthy endpoint answers 405 here.
  # Bearer token goes through a curl config file (bearer_cfg), not -H on
  # curl's own argv -- see bearer_cfg()'s comment for why.
  bearer_cfg "${VAULT_LIBRARY_TOKEN:-}"
  c=$(code -X OPTIONS -K "$_LAST_BEARER_CFG" -H "Accept: application/json, text/event-stream" "$vault_library_url")
  if [ "$c" = 200 ] || [ "$c" = 405 ]; then
    ok "vault-library: $c (up)"
    # A Cloud-Server install is TWO installs. Upgrading this clone also moves
    # 03-INFRA/deploy/, but the VPS keeps running the containers it was last
    # deployed with, and nothing ever said so: the maintainer redeployed by
    # hand and an end user simply would not have known to. The server's root
    # route reports its running version and is unauthenticated by design
    # (McpSecurityMiddleware guards only the MCP path), so no token is sent
    # here. WARN, never FAIL: when to deploy is the user's own call.
    vm_shipped=$(sed -n 's/^__version__ *= *"\([^"]*\)".*/\1/p' \
      "$ENGINE_ROOT/deploy/vault-mcp/src/vault_mcp_server/__init__.py" 2>/dev/null | head -1)
    vm_origin=$(printf '%s' "$vault_library_url" | sed -E 's#^([A-Za-z][A-Za-z0-9+.-]*://[^/]+).*#\1#')
    vm_running=$(curl -fsS -m 6 "$vm_origin/" 2>/dev/null \
      | sed -n 's/.*"version": *"\([^"]*\)".*/\1/p' | head -1)
    if [ -z "$vm_shipped" ] || [ -z "$vm_running" ]; then
      ok "vault-mcp version not compared (server or local source reported none)"
    elif [ "$vm_shipped" = "$vm_running" ]; then
      ok "vault-mcp on the server is $vm_running, matching this engine"
    else
      warn "vault-mcp on the server is $vm_running but this engine ships $vm_shipped — the server half of the upgrade was never deployed; redeploy per 03-INFRA/deploy/README.md"
    fi
  else
    fail "vault-library: $c"
  fi
elif connector_expected VAULT_LIBRARY_URL; then
  # Same Mode gating as the "Tokens in env" check below for this exact
  # variable -- an unconditional warn here used to contradict that check's
  # "ok, not expected in current Mode" a few lines later in the SAME run
  # (G1-contraddizione: one half of the report said "problem", the other half
  # said "fine, expected" for the identical unset variable).
  fail "vault-library: no endpoint resolved (VAULT_LIBRARY_URL not set, manifest render found none)"
else
  ok "vault-library: not configured -- not expected in current Mode (Local-Only / VAULT_LIBRARY_URL not set)"
fi
# Semantic RAG (optional, not part of the bundled deploy/ stack): checked through
# the MCP container's own network (same lane the agents use), so it also catches
# the footgun of "container recreated outside the expected docker network", which
# a plain curl on the exposed port would not see. Container name is overridable
# because it depends on the user's own docker-compose project name.
if [ -n "${VAULT_SEMANTIC_CONTAINER:-}" ] && [ -n "${REMOTE_ALIAS:-}" ]; then
  rag_c="$(ssh -o BatchMode=yes -o ConnectTimeout=6 "$REMOTE_ALIAS" \
    "docker exec $VAULT_SEMANTIC_CONTAINER python3 -c \"import urllib.request as u; print(u.urlopen('http://vault-semantic:8080/health', timeout=5).status)\"" 2>/dev/null || true)"
  [ "$rag_c" = "200" ] && ok "vault-semantic RAG (MCP lane): 200" || fail "vault-semantic RAG (MCP lane): ${rag_c:-KO}"
else
  warn "VAULT_SEMANTIC_CONTAINER or REMOTE_ALIAS not set, skipping semantic RAG check (optional component)"
fi
command -v npx >/dev/null 2>&1 && ok "playwright: npx available" || warn "npx not in PATH (playwright MCP)"

sec "Tokens in env"
if [ -n "${N8N_MCP_TOKEN:-}" ]; then
  ok "N8N_MCP_TOKEN present"
elif connector_expected N8N_MCP_TOKEN; then
  fail "N8N_MCP_TOKEN missing"
else
  ok "N8N_MCP_TOKEN not set -- not expected in current Mode (Local-Only / n8n not configured)"
fi
for v in VAULT_LIBRARY_TOKEN VAULT_LIBRARY_URL; do
  if [ -n "${!v:-}" ]; then
    ok "$v present"
  elif connector_expected VAULT_LIBRARY_URL; then
    fail "$v missing"
  else
    ok "$v not set -- not expected in current Mode (Local-Only / vault-library not configured)"
  fi
done
[ -n "${DEEPSEEK_API_KEY:-}" ] && ok "DEEPSEEK_API_KEY present" || warn "DEEPSEEK_API_KEY missing (only affects direct DeepSeek fallback/batch; OpenCode Go stays fine)"

sec "MCP configured in the runtimes (Vault 2.0 drift detection)"
if command -v python3 >/dev/null 2>&1 && [ -f "$ENGINE_UL/mcp/render.py" ]; then
  render_out="$(python3 "$ENGINE_UL/mcp/render.py" 2>&1)"
  render_rc=$?
  if [ "$render_rc" -ne 0 ]; then
    # A crash here (missing PyYAML, a broken manifest, a permission error...)
    # must never read as "no drift found": empty/error output would otherwise
    # fall through to the "100% aligned" branch below, the worst possible
    # false-green in the one check whose job is to catch misconfiguration.
    # cmd_diff now isolates a broken CLI PER SECTION instead of aborting on
    # the first one (2026-07-13 follow-up): the actual reason is one or more
    # '>>> STOP: ...' lines, not necessarily the last line of output (which
    # can just be the trailing summary) -- surface those specifically when
    # present, fall back to the last line only when there's no STOP marker
    # to show (e.g. a manifest-load crash outside the per-CLI loop).
    stop_lines="$(printf '%s\n' "$render_out" | grep '>>> STOP' || true)"
    if [ -n "$stop_lines" ]; then
      fail "render.py failed to run (exit $render_rc): $(printf '%s' "$stop_lines" | tr '\n' '; ')"
    else
      fail "render.py failed to run (exit $render_rc): $(printf '%s' "$render_out" | tail -1)"
    fi
  else
  drift_scan="$render_out"
  claude_pending=0
  if pgrep -x claude >/dev/null 2>&1; then
    claude_section="$(printf '%s\n' "$render_out" | awk '/^========== CLAUDE ==========/{p=1; next} /^========== /{p=0} p')"
    if printf '%s\n' "$claude_section" | grep -Eq '\[(DIFF|MISSING|ERROR)\]'; then
      claude_pending=1
      drift_scan="$(printf '%s\n' "$render_out" | awk '/^========== CLAUDE ==========/{p=1; next} /^========== /{p=0} !p')"
    fi
  fi
  if printf '%s\n' "$drift_scan" | grep -Eq '\[ERROR\]'; then
    fail "MCP drift detected against the canonical manifest"
    # Pull out the offending lines to show them in the report
    drift_lines="$(printf '%s\n' "$drift_scan" | grep -E '\[DIFF\]|\[MISSING\]|\[ERROR\]')"
    [ "$QUIET" = 1 ] || printf '%s\n' "$drift_lines" | while IFS= read -r line; do warn "drift detail: $line"; done
  elif printf '%s\n' "$drift_scan" | grep -Eq '\[DIFF\]|\[MISSING\]'; then
    warn "MCP drift detected against the canonical manifest (DIFF/MISSING)"
    drift_lines="$(printf '%s\n' "$drift_scan" | grep -E '\[DIFF\]|\[MISSING\]')"
    [ "$QUIET" = 1 ] || printf '%s\n' "$drift_lines" | while IFS= read -r line; do warn "drift detail: $line"; done
  elif [ "$claude_pending" = 1 ]; then
    warn "Claude MCP not aligned but Claude is running: will be written by the next agent-sync once Claude is closed"
  else
    ok "MCP configs 100% aligned with the canonical manifest"
  fi
  # A CLI that was never launched has no config file to patch: render.py just
  # notes "(config not present...)" for it, with no [DIFF]/[MISSING] tag, so
  # the drift scan above reads as clean even though that CLI has zero MCP
  # servers mounted. OpenCode is already caught above (missing $OCJSON is a
  # hard fail there because it holds both instructions and MCP config); check
  # the other three here so this doesn't stay a silent gap.
  for cli in claude codex antigravity; do
    section="$(printf '%s\n' "$render_out" | awk -v s="========== $(printf '%s' "$cli" | tr '[:lower:]' '[:upper:]') ==========" '$0==s{p=1; next} /^========== /{p=0} p')"
    if printf '%s\n' "$section" | grep -q "config not present"; then
      case "$cli" in
        claude) have=0; command -v claude >/dev/null 2>&1 && have=1 ;;
        codex) have=0; command -v codex >/dev/null 2>&1 && have=1 ;;
        antigravity) have=0; [ -d "$HOME/.gemini" ] && have=1 ;;
      esac
      [ "$have" = 1 ] && warn "$cli is installed but has never been launched: its MCP config doesn't exist yet, open it once and re-run agent-sync"
    fi
  done
  fi
else
  warn "python3 or render.py not found, skipping MCP drift check"
fi

# BUG-B (incident 2026-07-30): a manifest server whose require_env var is
# missing is silently dropped by render.py's own ">>> skip [...]: require_env
# not satisfied (Local-Only?)" line above, which never names the variable --
# a real incident where N8N_MCP_TOKEN was simply unset, render.py dropped
# n8n-mcp from all 4 CLIs, and this doctor gave no clue why for hours. Read
# the manifest directly here (not render.py's terse skip line, which carries
# no variable name) to name exactly which require_env var is unsatisfied for
# which server, and only escalate to a WARNING when connector_expected() says
# the current Mode should actually have it -- a legitimate Local-Only skip
# stays the silent no-op it already is everywhere else in this script.
if command -v python3 >/dev/null 2>&1 && [ -f "$UL/mcp/manifest.yaml" ]; then
  skipped_servers="$(python3 - "$SELF_DIR" "$UL/mcp/manifest.yaml" <<'PY' 2>/dev/null
import os, sys
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from config_schema import ConfigValidationError, load_mcp_manifest_document
try:
    servers, _retired = load_mcp_manifest_document(Path(sys.argv[2]))
except ConfigValidationError:
    sys.exit(0)
for name, spec in servers.items():
    req = spec.get("require_env")
    if req and not os.environ.get(req, "").strip():
        print(f"{name} {req}")
PY
)"
  while IFS=' ' read -r srv var; do
    [ -n "$srv" ] || continue
    if connector_expected "$var"; then
      warn "$var missing: manifest server '$srv' is not mounted on any CLI (require_env not satisfied)"
    fi
  done <<EOF
$skipped_servers
EOF
fi

if [ "$STRICT" = 1 ]; then
  sec "CLI consumer conformance (--strict)"

  # Expected MCP server set for the strict consumer checks below: used to be
  # hardcoded here in 4 separate spots ({firecrawl, n8n-mcp, vault-library,
  # vault-ocr}), so a manifest change (server added/removed, a require_env
  # gate flipped) silently went stale. Derived ONCE here instead, straight
  # from the manifest via render.py --expected-servers, which already
  # applies the same require_env filtering agent-sync's --write uses -- what
  # this check expects is exactly what a correctly-configured install
  # actually has. An empty result (e.g. Local-Only, nothing require_env-gated
  # for that CLI) is legitimate and different from "couldn't derive it" --
  # both skip the checks below explicitly (never silently pass on an empty
  # expected set, never fail), the messages just say why.
  RENDER_PY="$ENGINE_UL/mcp/render.py"
  expected_ag=""
  expected_oc=""
  if ! command -v python3 >/dev/null 2>&1; then
    warn "python3 not found -- cannot derive the expected MCP server set, skipping its strict checks"
  elif [ ! -f "$RENDER_PY" ]; then
    warn "render.py not found ($RENDER_PY) -- cannot derive the expected MCP server set, skipping its strict checks"
  else
    expected_ag="$(python3 "$RENDER_PY" --expected-servers antigravity 2>/dev/null)"
    expected_oc="$(python3 "$RENDER_PY" --expected-servers opencode 2>/dev/null)"
  fi

  AG_SRC="$HOME/.gemini/antigravity/mcp_config.json"
  AG_GLOBAL="$HOME/.gemini/config/mcp_config.json"
  if [ "$(readlink -f "$AG_GLOBAL" 2>/dev/null)" = "$(readlink -f "$AG_SRC" 2>/dev/null)" ]; then
    ok "Antigravity global MCP path -> generated source"
  else
    fail "Antigravity global MCP path does NOT point to the generated source ($AG_GLOBAL)"
  fi
  if [ -s "$AG_GLOBAL" ]; then
    ok "Antigravity global mcp_config.json not empty"
  else
    fail "Antigravity global mcp_config.json empty or missing"
  fi
  if [ -z "$expected_ag" ]; then
    warn "no expected Antigravity MCP servers derived from the manifest -- skipping the core-servers content check"
  elif [ -s "$AG_GLOBAL" ]; then
    # Expected server list comes in as $expected_ag (one name per line from
    # render.py); read into an array first so each name reaches python3 as
    # its OWN argv element -- an unquoted `$expected_ag` expansion here
    # would both word-split on IFS AND glob-expand against this directory's
    # actual filenames, which a server name containing a shell glob
    # metacharacter would silently corrupt into whatever files happen to
    # match. mapfile + a quoted "${arr[@]}" expansion carries each element
    # through verbatim, with no reinterpretation.
    mapfile -t expected_ag_arr <<<"$expected_ag"
    ag_missing="$(python3 - "$AG_GLOBAL" "${expected_ag_arr[@]}" <<'PY' 2>/dev/null || true
import json, sys
path = sys.argv[1]
expected = set(sys.argv[2:])
data = json.load(open(path, encoding="utf-8"))
got = set((data.get("mcpServers") or {}).keys())
print(",".join(sorted(expected - got)))
PY
)"
    [ -z "$ag_missing" ] && ok "Antigravity global contains the core MCP servers" || fail "Antigravity global is missing core MCP servers: $ag_missing"
  fi

  # Real behavioral probe (found missing in a cross-vendor audit, 2026-07-09:
  # the checks above only prove the config FILE is well-formed, not that the
  # CLI actually honors it — same false-positive class as the old dead
  # ANTIGRAVITY.md symlink). agy has no deterministic "mcp list" subcommand
  # like opencode, so the only real probe is asking the model itself: slower
  # by design, not a bug.
  if [ -z "$expected_ag" ]; then
    warn "no expected Antigravity MCP servers derived from the manifest -- skipping the Antigravity behavioral probe"
  elif [ "${NEXGEN_SKIP_LIVE_CONSUMER_PROBES:-0}" = "1" ]; then
    warn "Antigravity behavioral probe skipped by the sandbox safety gate"
  elif command -v agy >/dev/null 2>&1; then
    ag_probe_best_out=""
    ag_probe_best_missing="${expected_ag//$'\n'/, }"
    ag_probe_best_count=$(printf '%s\n' "$expected_ag" | grep -c .)
    ag_probe_had_non_timeout=0
    ag_probe_timed_out=0
    ag_probe_quota=0
    for _attempt in 1 2; do
      ag_probe_tmp="$(mktemp)"
      timeout -k 5s 45s agy --print "Elenca SOLO i nomi dei server MCP disponibili in questa sessione, una riga per server, NESSUN dettaglio sui singoli tool e NESSUNA invocazione." --model "Gemini 3.5 Flash (Medium)" --sandbox >"$ag_probe_tmp" 2>&1
      ag_probe_rc=$?
      ag_probe_out="$(cat "$ag_probe_tmp" 2>/dev/null)"
      rm -f "$ag_probe_tmp"
      if [ "$ag_probe_rc" = 124 ] || [ "$ag_probe_rc" = 137 ]; then
        ag_probe_timed_out=1
        continue
      fi
      if printf '%s\n' "$ag_probe_out" | grep -Eqi 'individual[[:space:]]+quota|quota[[:space:]]+(reached|exhausted|exceeded)|rate[[:space:]]+limit|too many requests|(^|[^0-9])429([^0-9]|$)'; then
        ag_probe_quota=1
        continue
      fi
      ag_probe_had_non_timeout=1
      ag_probe_missing=""
      ag_probe_count=0
      # shellcheck disable=SC2086
      for srv in $expected_ag; do
        if ! printf '%s\n' "$ag_probe_out" | grep -Fqi "$srv"; then
          ag_probe_missing="${ag_probe_missing}${ag_probe_missing:+, }$srv"
          ag_probe_count=$((ag_probe_count + 1))
        fi
      done
      if [ "$ag_probe_count" -lt "$ag_probe_best_count" ]; then
        ag_probe_best_count="$ag_probe_count"
        ag_probe_best_missing="$ag_probe_missing"
        ag_probe_best_out="$ag_probe_out"
      fi
      [ "$ag_probe_count" -eq 0 ] && break
    done

    if [ "$ag_probe_had_non_timeout" = 0 ] && [ "$ag_probe_quota" = 1 ]; then
      warn "Antigravity behavioral probe skipped: the selected model quota is unavailable"
    elif [ "$ag_probe_had_non_timeout" = 0 ] && [ "$ag_probe_timed_out" = 1 ]; then
      fail "Antigravity behavioral probe (agy --print) timed out"
    else
      ag_n8n_ok=0
      case ", $ag_probe_best_missing, " in
        *", n8n-mcp, "*)
          ag_probe_tmp="$(mktemp)"
          timeout -k 5s 45s agy --print "Usa un tool MCP n8n-mcp di sola lettura per elencare i workflow o leggere lo schema, senza creare/modificare nulla. Rispondi SOLO OK se una chiamata n8n-mcp riesce, oppure FAIL se non puoi chiamare n8n-mcp." --model "Gemini 3.5 Flash (Medium)" --sandbox >"$ag_probe_tmp" 2>&1
          ag_probe_rc=$?
          ag_probe_out="$(cat "$ag_probe_tmp" 2>/dev/null)"
          rm -f "$ag_probe_tmp"
          if [ "$ag_probe_rc" = 0 ] && printf '%s\n' "$ag_probe_out" | grep -Fq "OK"; then
            ag_n8n_ok=1
          fi
          ;;
      esac

      ag_vault_library_ok=0
      case ", $ag_probe_best_missing, " in
        *", vault-library, "*)
          ag_probe_tmp="$(mktemp)"
          timeout -k 5s 45s agy --print "Usa il tool MCP vault-library/get_start_here, poi rispondi SOLO con OK se la chiamata riesce, oppure FAIL se non puoi chiamarlo. Non includere contenuto della nota." --model "Gemini 3.5 Flash (Medium)" --sandbox >"$ag_probe_tmp" 2>&1
          ag_probe_rc=$?
          ag_probe_out="$(cat "$ag_probe_tmp" 2>/dev/null)"
          rm -f "$ag_probe_tmp"
          if [ "$ag_probe_rc" = 0 ] && printf '%s\n' "$ag_probe_out" | grep -Fq "OK"; then
            ag_vault_library_ok=1
          fi
          ;;
      esac

      ag_probe_missing=""
      # shellcheck disable=SC2086
      for srv in $expected_ag; do
        [ "$srv" = "n8n-mcp" ] && [ "$ag_n8n_ok" = 1 ] && continue
        [ "$srv" = "vault-library" ] && [ "$ag_vault_library_ok" = 1 ] && continue
        printf '%s\n' "$ag_probe_best_out" | grep -Fqi "$srv" || ag_probe_missing="${ag_probe_missing}${ag_probe_missing:+, }$srv"
      done
      if [ -z "$ag_probe_missing" ]; then
        ok "Antigravity behavioral probe confirms the core MCP servers are visible"
      else
        fail "Antigravity behavioral probe does not confirm: $ag_probe_missing"
      fi
    fi
  else
    warn "agy not in PATH, skipping Antigravity behavioral probe"
  fi

  if [ -z "$expected_oc" ]; then
    warn "no expected OpenCode MCP servers derived from the manifest -- skipping the OpenCode consumer test"
  else
    opencode_cmd="$(command -v opencode 2>/dev/null || true)"
    if [ -z "$opencode_cmd" ] && [ -x "$HOME/.opencode/bin/opencode" ]; then
      opencode_cmd="$HOME/.opencode/bin/opencode"
    fi
  fi
  if [ -z "$expected_oc" ]; then
    :
  elif [ "${NEXGEN_SKIP_LIVE_CONSUMER_PROBES:-0}" = "1" ]; then
    warn "OpenCode consumer test skipped by the sandbox safety gate"
  elif [ -n "${opencode_cmd:-}" ]; then
    oc_tmp="$(mktemp)"
    if command -v setsid >/dev/null 2>&1; then
      setsid timeout -k 5s 25s "$opencode_cmd" mcp list >"$oc_tmp" 2>&1
      oc_rc=$?
    else
      timeout -k 5s 25s "$opencode_cmd" mcp list >"$oc_tmp" 2>&1
      oc_rc=$?
    fi
    oc_out="$(cat "$oc_tmp" 2>/dev/null)"
    rm -f "$oc_tmp"
    oc_missing=""
    # shellcheck disable=SC2086
    for srv in $expected_oc; do
      printf '%s\n' "$oc_out" | grep -F "$srv" | grep -Fqi "connected" || oc_missing="${oc_missing}${oc_missing:+, }$srv"
    done
    if [ "$oc_rc" = 124 ] || [ "$oc_rc" = 137 ]; then
      fail "OpenCode mcp list timed out during strict check"
    elif [ -z "$oc_missing" ]; then
      ok "OpenCode mcp list shows the core servers connected"
    else
      fail "OpenCode mcp list does not confirm: $oc_missing"
    fi
  else
    warn "opencode not found in PATH or ~/.opencode/bin, skipping OpenCode consumer test"
  fi

  OCR_MCP="$ENGINE_ROOT/deploy/ocr/mcp/vault_ocr_mcp.py"
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
raw, _ = proc.communicate(timeout=15)
assert proc.returncode == 0
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
      fail "vault-ocr stdio framing test failed"
    fi
  else
    warn "vault-ocr MCP wrapper not found, skipping framing test"
  fi
fi

sec "Shared browser and defaults"
if [ "$HOST" = linux ] && command -v xdg-settings >/dev/null 2>&1; then
  db=$(xdg-settings get default-web-browser 2>/dev/null)
  [ "$db" = "agent-chrome.desktop" ] && ok "system default browser → agent-chrome" || warn "system default browser = ${db:-?} (expected agent-chrome.desktop)"
fi
# Chrome rewrites its generated web-app launchers whenever an app is installed
# or updated, which silently restores a direct call to the Chrome binary. Such
# a launcher can win the first-process race and start the shared profile with
# no CDP port, and nothing else reports it: opening the web app still works,
# only the agents stop being able to attach. `agent-sync guard` repairs these
# every 30 minutes, so a finding here means the repair is not running.
if [ "$HOST" = linux ]; then
  apps="$HOME/.local/share/applications"
  unrouted=0
  for entry in "$apps"/chrome-*.desktop; do
    [ -f "$entry" ] || continue
    if grep -q '^Exec=[^ ]*/\(google-chrome[^ /]*\|chrome\|chromium\|chromium-browser\) ' "$entry"; then
      unrouted=$((unrouted + 1))
    fi
  done
  if [ "$unrouted" -gt 0 ]; then
    warn "$unrouted Chrome web-app launcher(s) call Chrome directly and can start the shared profile without CDP — run 'agent-sync apply'"
  else
    ok "Chrome web-app launchers route through agent-chrome"
  fi
  # The live consequence of a lost race. The launcher only writes it to stderr,
  # which nobody reads when Chrome was started from a desktop icon, so the
  # judge is the surface that reports it.
  chrome_profile="${AGENT_CHROME_PROFILE:-$HOME/.config/chrome-agent-debug}"
  owner_link=$(readlink "$chrome_profile/SingletonLock" 2>/dev/null || true)
  owner_pid=${owner_link##*-}
  if [ -n "$owner_link" ] && [ -n "$owner_pid" ] && kill -0 "$owner_pid" 2>/dev/null; then
    if curl -fsS --max-time 2 http://127.0.0.1:9222/json/version >/dev/null 2>&1; then
      ok "shared Chrome is up with CDP on 127.0.0.1:9222"
    else
      warn "Chrome (PID $owner_pid) holds the shared profile without CDP — agents cannot attach, run 'agent-chrome --heal'"
    fi
  fi
fi

sec "Skills"
# The active root is deliberately small. Bodies live in the non-discovered
# library and are opened via `agent-skill show`, so do not mistake zero core
# folders for a broken fresh install.
SKILL_ACTIVE="$HOME/.agents/skills"
SKILL_LIBRARY="$HOME/.agents/skill-library"
n=0; broken=""
for s in "$SKILL_LIBRARY"/*; do
  [ -L "$s" ] || [ -d "$s" ] || continue
  if [ -L "$s" ] && [ ! -e "$s" ]; then
    broken="$broken $(basename "$s")"
    continue
  fi
  [ -f "$s/SKILL.md" ] || continue
  n=$((n+1))
done
[ "${n:-0}" -gt 0 ] && ok "$n readable managed skills in ~/.agents/skill-library" || warn "no managed skill in ~/.agents/skill-library (fresh install, or none configured in the manifest yet)"
[ -n "$broken" ] && fail "BROKEN skill-library entries (self-loop/dangling symlink):$broken — fix with: python3 $ENGINE_ROOT/scripts/skills-sync.py --apply"
[ -f "$SKILL_ACTIVE/INDEX.md" ] && ok "lazy skill catalog present in ~/.agents/skills/INDEX.md" || warn "lazy skill catalog missing — run: agent-sync guard"
core=0
for s in "$SKILL_ACTIVE"/*; do
  [ -L "$s" ] || [ -d "$s" ] || continue
  [ -f "$s/SKILL.md" ] && core=$((core+1))
done
ok "$core core skills exposed to eager runtimes"
# Onboarding coherence: skills materialized in the library but absent from the
# manifest are strays the onboarding flow should adopt (canonize) or drop. A
# gentle WARN, never a FAIL -- a stray skill still works, it just isn't tracked.
SKILL_MANIFEST="$VAULT_DATA/03-INFRA/agent-universal-layer/skills/skills.manifest.yaml"
oom_skills="$(python3 - "$SKILL_LIBRARY" "$SKILL_MANIFEST" <<'PY' 2>/dev/null
import sys, pathlib
lib, man = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
mat = {p.name for p in lib.iterdir() if p.is_dir()} if lib.is_dir() else set()
names = set()
if man.is_file():
    try:
        import yaml
        d = yaml.safe_load(man.read_text(encoding='utf-8')) or {}
        s = d.get('skills') if isinstance(d, dict) else None
        names = set(s) if isinstance(s, dict) else set()
    except Exception:
        names = set()
print(len(mat - names))
PY
)"
if [ "${oom_skills:-0}" -gt 0 ] 2>/dev/null; then
  warn "$oom_skills skill(s) materialized but not in the manifest; adopt or drop them via onboarding (agent-sync inventory)"
else
  ok "no out-of-manifest skills to reconcile"
fi
# The starter commands README promises actually being there. This used to be
# invisible: with no manifest at all, skills-sync printed "skipping" to stderr
# and exited 0, and the only doctor line was the "no managed skill (fresh
# install)" WARN above -- which reads as normal. Five cold installs shipped
# without /vault-doctor or /nexgen-update before anyone noticed. WARN, never
# FAIL: an emptied manifest is a legitimate deliberate choice.
missing_starters="$(python3 - "$SKILL_LIBRARY" "$SKILL_MANIFEST" <<'PY' 2>/dev/null
import pathlib, sys
STARTERS = ("vault-doctor", "vault-close", "vault-save", "vault-council",
            "vault-groom", "nexgen-update", "vault-map")
lib, man = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
print("NOMANIFEST" if not man.is_file() else
      " ".join("/" + n for n in STARTERS if not (lib / n / "SKILL.md").is_file()))
PY
)"
if [ "$missing_starters" = "NOMANIFEST" ]; then
  warn "no skills manifest yet, so none of the starter commands (/vault-doctor, /nexgen-update, ...) exist — create it: cp $SKILL_MANIFEST.example $SKILL_MANIFEST && agent-sync apply"
elif [ -n "$missing_starters" ]; then
  warn "starter commands not installed: $missing_starters — if you want them, add them to $SKILL_MANIFEST and run: agent-sync apply"
else
  ok "all 7 starter commands installed (/vault-doctor, /vault-close, /vault-save, /vault-council, /vault-groom, /nexgen-update, /vault-map)"
fi

# Third-party CLI compatibility: a short, pruneable list of known-broken
# releases. NOT a general version pin -- only versions confirmed broken here
# (verified live: every tool call, including a no-op, was rejected with
# "unsupported call") get listed. Remove an entry once you've confirmed the
# upstream release fixed it; this list is expected to go stale and shrink.
sec "Third-party CLI compatibility"
if command -v codex >/dev/null 2>&1; then
  codex_ver=$(codex --version 2>/dev/null | grep -o '[0-9]\+\.[0-9]\+\.[0-9]\+' | head -1)
  case "$codex_ver" in
    0.143.0)
      fail "Codex CLI $codex_ver has a known tool-dispatcher regression (every tool call is rejected as 'unsupported call') -- known-bad as of 2026-07-09, upgrade or downgrade past it. Check https://github.com/openai/codex/releases before assuming this is still accurate."
      ;;
    "")
      : # codex present but --version didn't parse a semver -- don't guess
      ;;
    *)
      ok "Codex CLI $codex_ver (not in the known-bad list)"
      ;;
  esac
fi

sec "OpenCode config"
if command -v python3 >/dev/null 2>&1 && [ -f "$OCJSON" ]; then
  if python3 - "$SELF_DIR" "$OCJSON" <<'PY' >/dev/null 2>&1
import sys
sys.path.insert(0, sys.argv[1])
from config_schema import parse_jsonc
with open(sys.argv[2], encoding="utf-8") as handle:
    parse_jsonc(handle.read())
PY
  then
    case "$OCJSON" in
      *.jsonc) ok "$(basename "$OCJSON"): valid JSONC" ;;
      *) ok "$(basename "$OCJSON"): valid JSON" ;;
    esac
    ok "OpenCode model/provider profile is host-local; engine sync owns only instructions and MCP"
  else
    fail "$(basename "$OCJSON"): invalid JSON/JSONC"
  fi
fi
[ -f "$UL/opencode/opencode.json" ] && warn "legacy shared OpenCode model/provider profile is still present at $UL/opencode/opencode.json; engine sync owns only instructions and MCP, host-local models must stay in the runtime config"

sec "Local model (host-aware)"
if [ "$HOST" = linux ]; then
  if ss -ltn 2>/dev/null | grep -q ':11434'; then
    ok "Ollama running (emergency local fallback, not the routing worker)"
  else
    ok "Ollama not listening (fine: on-demand emergency fallback)"
  fi
fi

SETTINGS="$HOME/.claude/settings.json"

sec "Claude authentication"
# Only meaningful when Claude is actually used on this host: the
# layer-managed settings.json is the signal. Without it Claude isn't part of
# this install, so a logged-out CLI is not this host's problem -- stay silent
# instead of raising a FAIL a non-Claude user cannot act on. How the user
# configures Claude's own permissions is a host-local choice, not something
# the engine grades (see CHANGELOG 0.91.3).
if [ -f "$SETTINGS" ]; then
  if command -v claude >/dev/null 2>&1; then
    claude_auth="$(claude auth status 2>/dev/null || true)"
    if printf '%s' "$claude_auth" | grep -Eq '"loggedIn"[[:space:]]*:[[:space:]]*true'; then
      ok "Claude authentication is active"
    elif printf '%s' "$claude_auth" | grep -Eq '"loggedIn"[[:space:]]*:[[:space:]]*false'; then
      warn "Claude is not authenticated; run: claude auth login"
    elif [ -n "$claude_auth" ]; then
      warn "Claude auth status returned unreadable output; run: claude auth status"
    else
      warn "Claude auth status returned no output; run: claude auth status"
    fi
  else
    warn "Claude is configured on this host but 'claude' is not in PATH"
  fi
fi

sec "Claude hooks (vault checkpoint/briefing)"
if [ -f "$SETTINGS" ]; then
  if command -v jq >/dev/null 2>&1; then
    cnt=$(jq -r '[(.hooks.SessionStart[]?.hooks[]?.command), (.hooks.PreCompact[]?.hooks[]?.command)] | map(select(test("claude-vault-checkpoint"))) | length' "$SETTINGS" 2>/dev/null || echo 0)
    if [ "${cnt:-0}" -ge 2 ]; then ok "checkpoint/briefing hook on SessionStart + PreCompact"; else fail "vault-checkpoint hook missing in settings.json (run agent-sync.sh)"; fi
  else
    warn "jq missing: skipping the Claude hook check"
  fi
else
  warn "Claude settings.json missing (Claude not installed here?)"
fi

# Consumer engine clone — version pin (S2). Applies only where a consumer
# clone exists (default ~/.nexgen-engine, or AGENT_ENGINE_ROOT's repo root).
# Before the cutover this machine has none, so the whole section is skipped
# silently — same pattern as the S0 section above.
CONSUMER_ENGINE_ROOT="${AGENT_ENGINE_ROOT:-$HOME/.nexgen-engine/03-INFRA}"
CONSUMER_ENGINE_REPO="$(dirname "$CONSUMER_ENGINE_ROOT")"
if [ -d "$CONSUMER_ENGINE_REPO/.git" ]; then
  sec "Consumer engine clone — version pin (S2)"
  PIN_FILE="$VAULT_DATA/99-INDEX/ENGINE-PIN.txt"
  live_sha=$(git -C "$CONSUMER_ENGINE_REPO" rev-parse HEAD 2>/dev/null || echo "")
  if [ -z "$live_sha" ]; then
    fail "cannot read the consumer engine clone's HEAD ($CONSUMER_ENGINE_REPO)"
  else
    if [ -f "$PIN_FILE" ]; then
      pin_sha=$(head -1 "$PIN_FILE" | tr -d '[:space:]')
      if [ "$pin_sha" = "$live_sha" ]; then
        ok "consumer engine at the pinned version (${live_sha:0:7})"
      else
        fail "consumer engine at ${live_sha:0:7}, pin expects ${pin_sha:0:7} — silent drift: pull was skipped, or the pin wasn't updated after a deliberate upgrade"
      fi
    else
      warn "no engine pin set ($PIN_FILE missing) — consumer engine version isn't tracked yet, run: git -C $CONSUMER_ENGINE_REPO rev-parse HEAD > $PIN_FILE"
    fi
    # A sha match only proves HEAD == pin; it says nothing about the working
    # tree itself. A hand-edited tracked file at an otherwise correctly
    # pinned HEAD used to report "OK" with no signal that the checkout no
    # longer matches the release byte-for-byte. Independent of the sha
    # outcome above, same porcelain check already used for $VAULT.
    consumer_dirty=$(git -C "$CONSUMER_ENGINE_REPO" status --porcelain --untracked-files=no 2>/dev/null | wc -l | tr -d ' ')
    if [ "$consumer_dirty" = 0 ]; then
      ok "consumer engine working tree clean (tracked files)"
    else
      warn "$consumer_dirty tracked file(s) modified in the consumer engine clone — a pinned clone should match the release exactly, not be hand-edited"
    fi
  fi
  # New-version-available check (B3, informational only, never auto-updates).
  # Fetch is read-only (only moves remote-tracking refs/tags), safe to run
  # here even though this machine never auto-upgrades the pinned commit.
  git -C "$CONSUMER_ENGINE_REPO" fetch --quiet --tags origin >/dev/null 2>&1
  latest_tag=$(git -C "$CONSUMER_ENGINE_REPO" tag --merged origin/main --sort=-v:refname 2>/dev/null | head -1)
  if [ -n "$latest_tag" ] && [ -n "$live_sha" ]; then
    current_version=$(git -C "$CONSUMER_ENGINE_REPO" show "$live_sha:VERSION" 2>/dev/null | tr -d '[:space:]')
    if [ -n "$current_version" ]; then
      if [ "v$current_version" != "$latest_tag" ]; then
        warn "NeXgen Engine update available: $latest_tag (this machine runs v$current_version) -- run: nexgen-update"
      else
        ok "consumer engine at the latest released version ($latest_tag)"
      fi
    else
      warn "NeXgen Engine update available: $latest_tag (this machine predates the VERSION file) -- run: nexgen-update"
    fi
  fi
fi

# New-version-available check for the DEFAULT single-clone install — the
# topology INIT.md actually produces (one clone = engine code + data root).
# Same informational-only contract as the consumer-clone check above (B3):
# it never auto-updates anything, upgrading stays a deliberate act
# (docs/upgrade.md). Gated on the consumer clone NOT existing (no double
# warning after the cutover) and on this vault actually tracking the engine
# (a VERSION file at its root — a pure data vault has none and skips).
if [ ! -d "$CONSUMER_ENGINE_REPO/.git" ] && [ -d "$VAULT_DATA/.git" ] && [ -f "$VAULT_DATA/VERSION" ]; then
  sec "Engine version (single-clone install)"
  current_version=$(head -1 "$VAULT_DATA/VERSION" | tr -d '[:space:]')
  # Fetch is read-only (only moves remote-tracking refs/tags); bounded so an
  # unreachable origin can't hang the doctor run.
  if timeout -k 5s 20s git -C "$VAULT_DATA" fetch --quiet --tags origin >/dev/null 2>&1; then
    latest_tag=$(git -C "$VAULT_DATA" tag --merged origin/main --sort=-v:refname 2>/dev/null | head -1)
    if [ -z "$latest_tag" ]; then
      ok "origin has no released engine tags -- nothing to compare (origin is not the engine repo?)"
    else
      # sort -V so a maintainer clone sitting AHEAD of the last tag doesn't
      # get told to "upgrade" backwards.
      newest=$(printf '%s\n%s\n' "v$current_version" "$latest_tag" | sort -V | tail -1)
      if [ "$newest" = "v$current_version" ]; then
        ok "engine at (or ahead of) the latest released version ($latest_tag, running v$current_version)"
      else
        warn "NeXgen Engine update available: $latest_tag (this machine runs v$current_version) -- run: nexgen-update"
      fi
    fi
  else
    warn "cannot fetch origin -- engine version check skipped (offline, or origin unreachable)"
  fi
fi

if [ "$QUIET" = 1 ]; then
  line="agent-doctor [$HOST] PASS=$PASS WARN=$WARN FAIL=$FAILN"
  [ "$FAILN" -gt 0 ] && line="$line | FAIL: $FAILS"
  printf '%s\n' "$line"
else
  sec "Summary"
  printf "  \033[32mPASS=%s\033[0m  \033[33mWARN=%s\033[0m  \033[31mFAIL=%s\033[0m\n" "$PASS" "$WARN" "$FAILN"
  [ "$FAILN" -eq 0 ] && printf "  → \033[32malignment VERIFIED\033[0m\n" || printf "  → \033[31mthere are FAILs to fix\033[0m\n"
fi
[ "$FAILN" -eq 0 ]
