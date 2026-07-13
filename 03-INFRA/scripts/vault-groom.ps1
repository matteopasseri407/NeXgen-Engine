#!/usr/bin/env pwsh
# vault-groom.ps1 — Windows twin of vault-groom.sh (the gardener's hand).
#
# Same contract as the .sh: feed the canonical playbook
# (03-INFRA/vault-grooming-playbook.md) to an LLM runner for ONE grooming pass.
# On-demand only — never scheduled to self-start (see the playbook: two machines
# grooming the shared vault would collide on git).
#
# Modes:
#   plan   read-only dry pass: propose a tranche, cannot edit/commit (safe)
#   run    operative: compress/merge/archive + commit (+push unless GROOM_NOPUSH=1)
#
# Env: VAULT, GROOM_MODEL, GROOM_RUNNER (claude|codex|agy, default claude),
#      GROOM_NOPUSH=1 (run without push, for observed runs)
#
# Runner support mirrors vault-groom.sh: each runner uses ITS OWN verified
# read-only/write-scoping mechanism for plan vs run, not a shared flag set.
# opencode has no per-invocation permission-scoping flag (config-file based,
# not something safe to toggle per run) -- selecting it fails loudly instead
# of guessing.
#
# TODO(windows-verify): confirm on Windows — `claude`/`codex`/`agy` resolve on
# PATH in pwsh, array splat to --allowedTools works, and the audit call in the
# playbook uses `python` not `python3`. The codex/agy branches below are new
# (2026-07-13) and unverified on Windows specifically, same caveat as the
# pre-existing claude branch already had.

param(
  [ValidateSet('plan', 'run')]
  # Default to the read-only lane, matching vault-groom.sh: a first-time
  # caller with no argument must never land in commit+push mode driven by
  # unreviewed LLM judgement -- `run` (and its push) stays an explicit choice.
  [string]$Mode = 'plan'
)
$ErrorActionPreference = 'Stop'

$Vault    = if ($env:VAULT) { $env:VAULT } else { Join-Path $env:USERPROFILE 'KnowledgeVault' }
$Playbook = '03-INFRA/vault-grooming-playbook.md'
$Model    = if ($env:GROOM_MODEL) { $env:GROOM_MODEL } else { 'claude-sonnet-5' }
$Runner   = if ($env:GROOM_RUNNER) { $env:GROOM_RUNNER } else { 'claude' }
$Log      = if ($env:GROOM_LOG) { $env:GROOM_LOG } else { Join-Path $env:TEMP ("vault-groom-{0}.log" -f (Get-Date -Format 'yyyyMMdd-HHmmss')) }

Set-Location $Vault

# Read-only lane: no Edit/Write/git -> the plan pass physically cannot mutate.
$ReadTools = @(
  'Read', 'Grep', 'Glob', 'Bash(python3:*)',
  'mcp__vault-library__semantic_search', 'mcp__vault-library__search_notes',
  'mcp__vault-library__read_note', 'mcp__vault-library__recent_activity',
  'mcp__vault-library__list_related', 'mcp__vault-library__get_start_here'
)

# Write lane: adds file mutation + git; push is gated separately below.
$WriteTools = @(
  'Read', 'Edit', 'Write', 'Grep', 'Glob',
  'Bash(python3:*)', 'Bash(git:*)', 'Bash(mkdir:*)', 'Bash(mv:*)',
  'mcp__vault-library__semantic_search', 'mcp__vault-library__search_notes',
  'mcp__vault-library__read_note', 'mcp__vault-library__list_related',
  'mcp__vault-library__update_note', 'mcp__vault-library__create_note',
  'mcp__vault-library__append_note'
)

if ($Mode -eq 'plan') {
  $prompt = "Read $Playbook and execute ONLY steps 1-3 (orient, run the audit heat-map, find candidates with semantic_search). Then OUTPUT a proposed grooming tranche: the notes, the action for each (compress / merge / archive / fix-frontmatter), and one line of why. DO NOT edit, write, move, or commit anything - this is a read-only planning pass."
}
elseif ($env:GROOM_NOPUSH -eq '1') {
  $prompt = "Read $Playbook and execute exactly ONE grooming run following it end to end. Commit atomically per tranche with clear messages. Do NOT push - commits stay local for review."
}
else {
  $prompt = "Read $Playbook and execute exactly ONE grooming run following it end to end. Commit atomically per tranche with clear messages, then push."
}

switch ($Runner) {
  'claude' {
    if ($Mode -eq 'plan') {
      claude -p $prompt --model $Model --allowedTools $ReadTools 2>&1 | Tee-Object -FilePath $Log
    }
    elseif ($env:GROOM_NOPUSH -eq '1') {
      # The one runner where NOPUSH is a hard runtime block, not just a
      # prompt instruction: --disallowedTools makes `git push` uncallable.
      claude -p $prompt --model $Model --allowedTools $WriteTools --disallowedTools 'Bash(git push:*)' 2>&1 | Tee-Object -FilePath $Log
    }
    else {
      claude -p $prompt --model $Model --allowedTools $WriteTools 2>&1 | Tee-Object -FilePath $Log
    }
  }
  'codex' {
    # -s read-only / workspace-write are real Codex sandbox policies (verified
    # via `codex exec --help` on Linux; same CLI contract expected on Windows,
    # see TODO above). NOPUSH on this runner is prompt-level only -- Codex has
    # no per-command block like Claude's --disallowedTools.
    if ($Mode -eq 'plan') {
      $prompt | codex exec -s read-only -m $Model -C $Vault - 2>&1 | Tee-Object -FilePath $Log
    }
    else {
      $prompt | codex exec -s workspace-write -m $Model -C $Vault - 2>&1 | Tee-Object -FilePath $Log
    }
  }
  'agy' {
    # --mode plan/accept-edits are real Antigravity session modes (verified
    # via `agy --help` on Linux; see TODO above for the Windows caveat).
    if ($Mode -eq 'plan') {
      agy --print --model $Model --mode plan --sandbox --prompt $prompt 2>&1 | Tee-Object -FilePath $Log
    }
    else {
      agy --print --model $Model --mode accept-edits --prompt $prompt 2>&1 | Tee-Object -FilePath $Log
    }
  }
  'opencode' {
    Write-Error @'
vault-groom: GROOM_RUNNER=opencode is not supported today.
  opencode has no per-invocation permission-scoping flag (its permission
  model lives in opencode.json's own config, checked once per project, not
  something this script can safely toggle per run): there is no way to
  guarantee plan mode is actually read-only, or that run mode doesn't
  silently inherit broader access than intended. Use claude, codex, or agy,
  or define a dedicated restricted opencode agent profile yourself and
  extend this script's opencode branch to use it explicitly.
'@
    exit 2
  }
  default {
    Write-Error "vault-groom: unknown GROOM_RUNNER '$Runner' (supported: claude, codex, agy)"
    exit 2
  }
}

Write-Host "log: $Log"
