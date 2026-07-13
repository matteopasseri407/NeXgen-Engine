#!/usr/bin/env bash
# vault-groom.sh — the gardener's hand.
#
# Feeds the canonical playbook (03-INFRA/vault-grooming-playbook.md) to an
# LLM runner and lets it do one grooming pass. The judgement lives in the
# playbook, NOT here.
#
# Modes:
#   plan   read-only dry pass: propose a tranche, cannot edit/commit (safe)
#   run    operative: compress/merge/archive + commit (+push unless GROOM_NOPUSH=1)
#
# Env: VAULT, GROOM_MODEL, GROOM_RUNNER (claude|codex|agy, default claude),
#      GROOM_NOPUSH=1 (run without push, for observed runs)
#
# Runner support is real, not cosmetic: each runner below uses that CLI's
# OWN verified read-only/write-scoping mechanism for `plan` vs `run`, not a
# shared flag set. `opencode` has no per-invocation permission-scoping CLI
# flag today (its permission model lives in opencode.json's own config, not
# something this script can safely toggle per run without risking either a
# silent full-access run or a broken invocation) -- selecting it fails
# loudly with that explanation instead of guessing.
set -euo pipefail

VAULT="${AGENT_VAULT_DATA:-${VAULT:-$HOME/KnowledgeVault}}"
PLAYBOOK="03-INFRA/vault-grooming-playbook.md"
MODEL="${GROOM_MODEL:-claude-sonnet-5}"
RUNNER="${GROOM_RUNNER:-claude}"
# Default to the read-only lane. A first-time caller running `./vault-groom.sh`
# with no argument must never land in commit+push mode driven by unreviewed
# LLM judgement -- `run` (and its push) stays an explicit, deliberate choice.
MODE="${1:-plan}"
# mktemp, not a predictable timestamp name: a plain "tee > $LOG" onto a
# guessable /tmp path is a symlink race (CWE-59) -- anything running as this
# same user could pre-create a symlink at that name pointing at, say,
# ~/.bashrc, and tee would clobber it with permission-inherited overwrite.
LOG="${GROOM_LOG:-$(mktemp --suffix=.log "/tmp/vault-groom-$(date +%Y%m%d-%H%M%S)-XXXXXX")}"

cd "$VAULT"

if [ "$MODE" != "plan" ] && [ "$MODE" != "run" ]; then
  echo "usage: $0 {plan|run}   (GROOM_RUNNER=claude|codex|agy, GROOM_NOPUSH=1 run without push)" >&2
  exit 2
fi

# Read-only lane: no Edit/Write/git → the plan pass physically cannot mutate.
READ_TOOLS=(Read Grep Glob "Bash(python3:*)" \
  mcp__vault-library__semantic_search mcp__vault-library__search_notes \
  mcp__vault-library__read_note mcp__vault-library__recent_activity \
  mcp__vault-library__list_related mcp__vault-library__get_start_here)

# Write lane: adds file mutation + git; push is gated separately below.
WRITE_TOOLS=(Read Edit Write Grep Glob \
  "Bash(python3:*)" "Bash(git:*)" "Bash(mkdir:*)" "Bash(mv:*)" \
  mcp__vault-library__semantic_search mcp__vault-library__search_notes \
  mcp__vault-library__read_note mcp__vault-library__list_related \
  mcp__vault-library__update_note mcp__vault-library__create_note \
  mcp__vault-library__append_note)

if [ "$MODE" = plan ]; then
  PROMPT="Read $PLAYBOOK and execute ONLY steps 1-3 (orient, run the audit heat-map, find candidates with semantic_search). Then OUTPUT a proposed grooming tranche: the notes, the action for each (compress / merge / archive / fix-frontmatter), and one line of why. DO NOT edit, write, move, or commit anything — this is a read-only planning pass."
else
  if [ "${GROOM_NOPUSH:-0}" = 1 ]; then
    PROMPT="Read $PLAYBOOK and execute exactly ONE grooming run following it end to end. Commit atomically per tranche with clear messages. Do NOT push — commits stay local for review."
  else
    PROMPT="Read $PLAYBOOK and execute exactly ONE grooming run following it end to end. Commit atomically per tranche with clear messages, then push."
  fi
fi

case "$RUNNER" in
  claude)
    if [ "$MODE" = plan ]; then
      claude -p "$PROMPT" --model "$MODEL" \
        --allowedTools "${READ_TOOLS[@]}" 2>&1 | tee "$LOG"
    elif [ "${GROOM_NOPUSH:-0}" = 1 ]; then
      # The one runner where NOPUSH is a hard runtime block, not just a
      # prompt instruction: --disallowedTools makes `git push` uncallable,
      # not merely discouraged.
      claude -p "$PROMPT" --model "$MODEL" \
        --allowedTools "${WRITE_TOOLS[@]}" \
        --disallowedTools "Bash(git push:*)" 2>&1 | tee "$LOG"
    else
      claude -p "$PROMPT" --model "$MODEL" \
        --allowedTools "${WRITE_TOOLS[@]}" 2>&1 | tee "$LOG"
    fi
    ;;
  codex)
    # -s read-only / workspace-write are real Codex sandbox policies (verified
    # via `codex exec --help`), not a guess: read-only makes plan's "no
    # mutation" promise a runtime guarantee, same strength as Claude's empty
    # tool list. -C scopes the writable root to the vault itself. NOPUSH on
    # this runner is prompt-level only (Codex has no per-command block like
    # Claude's --disallowedTools) -- weaker than the claude runner, said
    # plainly rather than implied.
    if [ "$MODE" = plan ]; then
      codex exec -s read-only -m "$MODEL" -C "$VAULT" - <<<"$PROMPT" 2>&1 | tee "$LOG"
    else
      codex exec -s workspace-write -m "$MODEL" -C "$VAULT" - <<<"$PROMPT" 2>&1 | tee "$LOG"
    fi
    ;;
  agy)
    # --mode plan/accept-edits are real Antigravity session modes (verified
    # via `agy --help`), the same concept as Claude's --permission-mode plan.
    # --sandbox on top of --mode plan for the read-only lane is defense in
    # depth (terminal restrictions + no-edit mode together). NOPUSH here is
    # prompt-level only, same caveat as codex above.
    if [ "$MODE" = plan ]; then
      agy --print --model "$MODEL" --mode plan --sandbox --prompt "$PROMPT" 2>&1 | tee "$LOG"
    else
      agy --print --model "$MODEL" --mode accept-edits --prompt "$PROMPT" 2>&1 | tee "$LOG"
    fi
    ;;
  opencode)
    echo "vault-groom: GROOM_RUNNER=opencode is not supported today." >&2
    echo "  opencode has no per-invocation permission-scoping flag (its permission" >&2
    echo "  model lives in opencode.json's own config, checked once per project," >&2
    echo "  not something this script can safely toggle per run): there is no way" >&2
    echo "  to guarantee plan mode is actually read-only, or that run mode doesn't" >&2
    echo "  silently inherit broader access than intended. Use claude, codex, or" >&2
    echo "  agy, or define a dedicated restricted opencode agent profile yourself" >&2
    echo "  and extend this script's opencode branch to use it explicitly." >&2
    exit 2
    ;;
  *)
    echo "vault-groom: unknown GROOM_RUNNER '$RUNNER' (supported: claude, codex, agy)" >&2
    exit 2
    ;;
esac

echo "log: $LOG"
