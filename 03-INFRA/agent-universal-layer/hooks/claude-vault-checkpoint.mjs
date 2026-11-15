#!/usr/bin/env node
// Canonical Claude Code hook for the user's KnowledgeVault.
// Universal across machines: lives in the vault, deployed by agent-sync to the Claude runtime
// (~/.claude/) and wired into ~/.claude/settings.json on every OS.
//
// One event, one script:
//   SessionStart (any source): inject one compact quota line, so the person sees what is left
//     of each plan before the first turn instead of looking for it.
//   SessionStart (source resume|compact): also inject a short briefing so a reloaded/compacted
//     session re-grounds in the vault instead of guessing.
//
// The briefing TEXT is not written here: it is the data file `vault-briefing.txt` beside
// this script, because OpenClaw's plugin injects the same briefing after a compaction.
// One text, two hosts, so the two cannot drift apart. Deploy the .txt next to this file;
// without it the hook falls back to the copy the installed engine carries.
//
// The quota line is the ENGINE's sentence, never formatted here: `nexgen quota --line` reads
// the machine's cheap sources and prints it. The call is cut at QUOTA_TIMEOUT_MS, and a missing
// engine, a slow one or a broken one costs this line and nothing else: a session start must
// never depend on it.
//
// The hook only injects context; the actual write still needs the model. It never blocks,
// never writes, never prints secrets. On any error it exits 0 silently (must not break
// sessions). The engine answers `quota --line` from what the machine already recorded: the
// call buys no run of its optional network backend.
import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const BRIEFING_NAME = "vault-briefing.txt";
const CHECKPOINT_FILENAME = "openclaw-checkpoint.json";
const MAX_CHECKPOINT_AGE_MS = 12 * 60 * 60 * 1000; // 12 hours
// Hard cap on the engine call: a session start may wait this long and no longer.
const QUOTA_TIMEOUT_MS = 3000;

// Where the text can be, nearest first. A deployer that copies only this script (the old
// vault agent-sync did) must not silence the briefing: the installed engine ships the text too.
function briefingPaths() {
  const paths = [join(dirname(fileURLToPath(import.meta.url)), BRIEFING_NAME)];
  if (process.env.AGENT_ENGINE_ROOT) {
    paths.push(join(process.env.AGENT_ENGINE_ROOT, "agent-universal-layer", "hooks", BRIEFING_NAME));
  }
  paths.push(join(homedir(), ".nexgen-engine", "03-INFRA", "agent-universal-layer", "hooks", BRIEFING_NAME));
  return paths;
}

// A missing or unreadable briefing everywhere is no briefing: the session goes on, unchanged.
function briefingText() {
  for (const path of briefingPaths()) {
    try {
      const text = readFileSync(path, "utf8").trim();
      if (text) return text;
    } catch {
      // try the next place
    }
  }
  return "";
}

// One compact line from the engine, or nothing at all: no engine, no answer in time, or an
// answer that is not the one line this asks for. The command is a constant, so no shell
// quoting is involved. Requiring exactly one line also keeps an older engine that ignores the
// flag (and prints the whole report) from putting a report row in the session's context.
function quotaLine(timeoutMs = QUOTA_TIMEOUT_MS) {
  try {
    const done = spawnSync("nexgen quota --line", {
      shell: true,
      encoding: "utf8",
      timeout: timeoutMs,
      windowsHide: true,
      stdio: ["ignore", "pipe", "ignore"],
    });
    if (!done || done.status !== 0 || typeof done.stdout !== "string") return "";
    const lines = done.stdout.split("\n").map((line) => line.trim()).filter((line) => line);
    return lines.length === 1 ? lines[0] : "";
  } catch {
    return "";
  }
}

function checkpointPaths() {
  const paths = [];
  if (process.env.AGENT_STATE_DIR) {
    paths.push(join(process.env.AGENT_STATE_DIR, "nexgen", CHECKPOINT_FILENAME));
    paths.push(join(process.env.AGENT_STATE_DIR, CHECKPOINT_FILENAME));
  }
  if (process.env.XDG_STATE_HOME) {
    paths.push(join(process.env.XDG_STATE_HOME, "nexgen", CHECKPOINT_FILENAME));
    paths.push(join(process.env.XDG_STATE_HOME, CHECKPOINT_FILENAME));
  }
  const home = process.env.NEXGEN_HOME || homedir();
  paths.push(join(home, ".local", "state", "nexgen", CHECKPOINT_FILENAME));
  paths.push(join(home, ".local", "state", CHECKPOINT_FILENAME));
  return paths;
}

function recentCheckpointNote(maxAgeMs = MAX_CHECKPOINT_AGE_MS) {
  for (const path of checkpointPaths()) {
    try {
      const parsed = JSON.parse(readFileSync(path, "utf8"));
      if (!parsed || typeof parsed !== "object") continue;
      const ts = parsed.timestamp ? new Date(parsed.timestamp).getTime() : 0;
      if (!ts || isNaN(ts)) continue;
      const age = Date.now() - ts;
      if (age < 0 || age > maxAgeMs) continue;
      const agent = parsed.agent || "default";
      const sessionKey = parsed.session_key || parsed.session || "";
      const timeStr = parsed.timestamp;
      const sessionSuffix = sessionKey ? ` (session_key: ${sessionKey})` : "";
      return `L'ultima sessione OpenClaw è dell'agente ${agent} alle ${timeStr}${sessionSuffix}; se la persona continua da lì, leggila con il server MCP openclaw.`;
    } catch {
      // try next
    }
  }
  return "";
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
    const parts = [];
    // Brief only when context was actually lost (resume/after-compact), not on fresh manual starts.
    if (source === "resume" || source === "compact") {
      const briefing = briefingText();
      if (briefing) parts.push(briefing);
      const note = recentCheckpointNote();
      if (note) parts.push(note);
    }
    // The gauge is for every start: a fresh session is exactly when nobody looks at it.
    const quota = quotaLine();
    if (quota) parts.push(quota);
    context = parts.join("\n\n");
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
