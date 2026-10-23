#!/usr/bin/env node
// Canonical Claude Code hook for the user's KnowledgeVault.
// Universal across machines: lives in the vault, deployed by agent-sync to the Claude runtime
// (~/.claude/) and wired into ~/.claude/settings.json on every OS.
//
// One event, one script:
//   SessionStart (source resume|compact): inject a short briefing so a reloaded/compacted
//     session re-grounds in the vault instead of guessing.
//
// The briefing TEXT is not written here: it is the data file `vault-briefing.txt` beside
// this script, because OpenClaw's plugin injects the same briefing after a compaction.
// One text, two hosts, so the two cannot drift apart. Deploy the .txt next to this file.
//
// The hook only injects context; the actual write still needs the model. It never blocks,
// never writes, never prints secrets. On any error it exits 0 silently (must not break sessions).
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const BRIEFING_PATH = join(dirname(fileURLToPath(import.meta.url)), "vault-briefing.txt");

// A missing or unreadable briefing is no briefing: the session goes on, unchanged.
function briefingText() {
  try {
    return readFileSync(BRIEFING_PATH, "utf8").trim();
  } catch {
    return "";
  }
}

process.stdin.on("error", () => process.exit(0));
process.stdout.on("error", () => process.exit(0));

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
      context = briefingText();
    }
  }

  if (context) {
    try {
      process.stdout.write(
        JSON.stringify({
          hookSpecificOutput: { hookEventName: name, additionalContext: context },
        }) + "\n"
      );
    } catch {
      // ignore
    }
  }
});
