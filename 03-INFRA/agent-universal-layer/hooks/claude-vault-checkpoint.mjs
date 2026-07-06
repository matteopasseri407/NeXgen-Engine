#!/usr/bin/env node
// Canonical Claude Code hook for the user's KnowledgeVault.
// Universal across machines: lives in the vault, deployed by agent-sync to the Claude runtime
// (~/.claude/) and wired into ~/.claude/settings.json on every OS.
//
// Two events, one script:
//   SessionStart (source resume|compact): inject a short briefing so a reloaded/compacted
//     session re-grounds in the vault instead of guessing.
//   PreCompact: remind the agent to persist durable state BEFORE context is compacted,
//     turning the "vault checkpoint" rule from prose into a mechanical nudge.
//
// The hook only injects context; the actual write still needs the model. It never blocks,
// never writes, never prints secrets. On any error it exits 0 silently (must not break sessions).

const chunks = [];
process.stdin.on("data", (c) => chunks.push(c));
process.stdin.on("end", () => {
  let event = {};
  try {
    event = JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}");
  } catch {
    process.exit(0);
  }

  const name = event.hook_event_name || event.hookEventName || "";
  const source = event.source || event.trigger || "";
  let context = "";

  if (name === "SessionStart") {
    // Brief only when context was actually lost (resume/after-compact), not on fresh manual starts.
    if (source === "resume" || source === "compact") {
      context = [
        "[KnowledgeVault briefing] This session resumed or its context was just compacted.",
        "If the task touches the user's world, re-orient first with ONE targeted read:",
        "vault-library get_start_here, then 04-NOW/current-focus, and recent_activity for what changed recently.",
        "Do not reload the whole vault.",
      ].join(" ");
    }
  } else if (name === "PreCompact") {
    context = [
      "[KnowledgeVault checkpoint] Context is about to be compacted.",
      "If this session produced durable knowledge not yet saved",
      "(a final diagnosis, root cause, canonical command, architecture decision, project state, runbook, verified preference, infra change),",
      "persist it NOW in the vault following knowledge-vault-hygiene: a compressed summary, no debug diary, no secrets, then commit and push.",
    ].join(" ");
  }

  if (context) {
    process.stdout.write(
      JSON.stringify({
        hookSpecificOutput: { hookEventName: name, additionalContext: context },
      })
    );
  }
  process.exit(0);
});
