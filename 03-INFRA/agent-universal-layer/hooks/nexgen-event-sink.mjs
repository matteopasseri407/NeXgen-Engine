// NeXgen Engine -- Universal Event Sink Hook & Plugin (IPC emitter for lifecycle events)
//
// Deployed by agent_sync.py and registered with CLI runtimes (Claude, Codex,
// Antigravity, OpenCode). Consumers listen on events.sock: the voice cockpit
// today, notifiers or supervisors tomorrow.
//
// Dual-mode contract:
// 1. Imported as an OpenCode plugin: exports lifecycle hooks, never exits.
// 2. Executed directly as a CLI hook: reads stdin/argv, emits one line, exits.
//
// Fail-safe contract:
// - Always exit 0: a hook must never break the CLI it is attached to.
// - Hard wall-clock deadline (DEADLINE_MS) covering everything.
// - Instant exit if the socket is absent: no consumer, no work.
//
// Isolation: only sessions started with COCKPIT_VOCALE=1 emit. Every ordinary
// terminal, background task and IDE process exits immediately and silently.

import { closeSync, existsSync, fstatSync, openSync, readSync } from "node:fs";
import { connect } from "node:net";
import { homedir, platform } from "node:os";
import { join } from "node:path";

process.on("uncaughtException", () => process.exit(0));
process.on("unhandledRejection", () => process.exit(0));

const IS_WIN = platform() === "win32";
const DEFAULT_SOCK = IS_WIN
  ? "\\\\.\\pipe\\nexgen_events"
  : join(homedir(), ".local", "share", "nexgen", "events.sock");
const SOCK_PATH = process.env.NEXGEN_EVENT_IPC_PATH || DEFAULT_SOCK;

// Measured on this machine: reading a 512 KB transcript tail and scanning it
// backwards costs 0.9ms, against 18ms just to start node. The deadline stays
// tight because the read is not what costs.
const DEADLINE_MS = 40;
const TRANSCRIPT_TAIL_BYTES = 512 * 1024;
const MAX_TEXT = 4000;

// Hook payload keys that prove a blob is a hook envelope and not a reply.
const ENVELOPE_KEYS = ["hook_event_name", "tool_input", "tool_response", "tool_use_id", "stop_hook_active"];

function isVocal() {
  return process.env.COCKPIT_VOCALE === "1" && process.env.NEXGEN_DISABLE_EVENT_SINK !== "1";
}

function socketReady() {
  return SOCK_PATH.startsWith("\\\\.\\pipe\\") || existsSync(SOCK_PATH);
}

function payloadLine(eventType, cliName, text, sessionId) {
  return JSON.stringify({
    event: eventType,
    cli: cliName,
    // COCKPIT_SESSION_ID wins: it is the cockpit's own identity for this
    // session, and consumers gate on it. A CLI's internal id lives in a
    // different namespace -- letting it take precedence made every event look
    // like it came from a session nobody had marked vocal, so they were all
    // dropped and the reply was never spoken.
    session_id: process.env.COCKPIT_SESSION_ID || sessionId || "",
    text: text ? String(text).slice(0, MAX_TEXT) : "",
    ts: Date.now() / 1000,
  }) + "\n";
}

/**
 * Last assistant prose in a Claude/Codex JSONL transcript, or "" if none.
 *
 * Scans backwards over a tail rather than parsing the whole file: transcripts
 * reach megabytes and only the end of the current turn matters.
 */
function lastAssistantText(transcriptPath) {
  let fd;
  try {
    fd = openSync(transcriptPath, "r");
    const size = fstatSync(fd).size;
    const start = Math.max(0, size - TRANSCRIPT_TAIL_BYTES);
    const buf = Buffer.alloc(size - start);
    readSync(fd, buf, 0, buf.length, start);
    const lines = buf.toString("utf8").split("\n");
    // Starting mid-file cuts the first line in half; it is never the one we want.
    if (start > 0) lines.shift();

    for (let i = lines.length - 1; i >= 0; i--) {
      const line = lines[i].trim();
      if (!line || line[0] !== "{") continue;
      let row;
      try {
        row = JSON.parse(line);
      } catch {
        continue;
      }
      // Subagent turns share the transcript. Emitting them would hand a
      // consumer the work of every background agent as if it were the reply.
      if (row.isSidechain) continue;
      const msg = row.message;
      if (!msg || msg.role !== "assistant") continue;

      const content = msg.content;
      if (typeof content === "string") {
        if (content.trim()) return content;
        continue;
      }
      if (!Array.isArray(content)) continue;
      const text = content
        .filter((b) => b && b.type === "text" && typeof b.text === "string")
        .map((b) => b.text)
        .join("\n")
        .trim();
      if (text) return text;
    }
  } catch {
    // An unreadable transcript is not worth breaking the CLI over.
  } finally {
    if (fd !== undefined) {
      try {
        closeSync(fd);
      } catch {}
    }
  }
  return "";
}

/**
 * The reply text carried by a hook payload, or "" when it carries none.
 *
 * Never falls back to the raw payload. A Claude Stop hook carries
 * {session_id, transcript_path, cwd, hook_event_name} and no reply at all, so
 * returning the buffer handed consumers the hook's own JSON -- which the voice
 * cockpit then read out loud, bash commands included.
 *
 * `allowTranscript` is false on the per-tool-call path: that hook fires
 * constantly and must stay on the fast path.
 */
function extractProse(payload, allowTranscript) {
  if (!payload || typeof payload !== "object") return "";

  const isEnvelope = ENVELOPE_KEYS.some((k) => k in payload);
  // "message" is the reply in a plain payload but the envelope's own field in a
  // hook payload, so it is only trusted when nothing marks this as an envelope.
  const keys = isEnvelope
    ? ["last_assistant_message", "response"]
    : ["last_assistant_message", "response", "text", "message"];

  for (const key of keys) {
    const value = payload[key];
    if (typeof value === "string" && value.trim()) return value;
  }
  if (allowTranscript && typeof payload.transcript_path === "string" && payload.transcript_path) {
    return lastAssistantText(payload.transcript_path);
  }
  return "";
}

function send(eventType, cliName, text, sessionId, onDone) {
  const finish = onDone || (() => {});
  try {
    const client = connect(SOCK_PATH, () => {
      client.write(payloadLine(eventType, cliName, text, sessionId), () => {
        client.end();
        finish();
      });
    });
    client.on("error", finish);
  } catch {
    finish();
  }
}

// --- 1. OpenCode plugin ----------------------------------------------------

export default async function () {
  return {
    "session.idle": async (event) => {
      if (!isVocal() || !socketReady()) return;
      send("on_done", "opencode", event?.lastAssistantMessage || "", event?.sessionID || "");
    },
    "step.finish": async (event) => {
      if (!isVocal() || !socketReady()) return;
      const text = event?.stepOutput || "";
      if (text) send("on_step", "opencode", text, event?.sessionID || "");
    },
  };
}

// --- 2. Direct CLI hook ----------------------------------------------------

function main() {
  if (!isVocal() || !socketReady()) process.exit(0);

  const deadline = setTimeout(() => process.exit(0), DEADLINE_MS);
  const finish = () => {
    clearTimeout(deadline);
    process.exit(0);
  };

  const eventType = process.argv[2] || "on_done";
  const cliName = process.argv[3] || process.env.COCKPIT_CLI || "unknown";
  const argText = process.argv[4] || "";
  // Only the end-of-turn event may go to disk for the reply; the per-tool-call
  // hook fires on every action and stays on the fast path.
  const isTurnEnd = eventType === "on_done";

  if (argText) {
    send(eventType, cliName, argText, "", finish);
    return;
  }
  if (process.stdin.isTTY) finish();

  let raw = "";
  process.stdin.setEncoding("utf8");
  process.stdin.on("data", (chunk) => {
    raw += chunk;
  });
  process.stdin.on("end", () => {
    let payload = null;
    try {
      payload = JSON.parse(raw);
    } catch {
      finish();
      return;
    }
    const text = extractProse(payload, isTurnEnd);
    // End of turn is a fact worth reporting even with nothing to say, so a
    // consumer can tell "finished silently" from "still working". A tool-call
    // step with no text is pure noise.
    if (!text && !isTurnEnd) {
      finish();
      return;
    }
    send(eventType, cliName, text, payload.session_id || "", finish);
  });
  process.stdin.on("error", finish);
  process.stdin.resume();
}

const invoked = process.argv[1] || "";
if (invoked.endsWith("nexgen-event-sink.mjs") || invoked.endsWith("nexgen-event-sink.js")) {
  main();
}
