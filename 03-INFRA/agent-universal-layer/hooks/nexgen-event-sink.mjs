// NeXgen Engine -- Universal Event Sink Hook & Plugin (IPC emitter for lifecycle events)
//
// Deployed by agent_sync.py and registered with CLI runtimes (Claude, Codex,
// Antigravity, OpenCode). Consumers listen on events.sock: the voice cockpit
// today, notifiers or supervisors tomorrow.
//
// Dual-mode contract:
// 1. Imported as an OpenCode plugin: exports lifecycle hooks, never exits.
// 2. Executed directly as a CLI hook (Claude/Codex/Antigravity): reads stdin/argv, emits one line, exits.
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

// Wall-clock deadline: reading a tail and sending to socket costs <5ms.
const DEADLINE_MS = 100;
const TRANSCRIPT_TAIL_BYTES = 512 * 1024;
const MAX_TEXT = 4000;

// Hook payload keys that prove a blob is a hook envelope and not a reply.
const ENVELOPE_KEYS = [
  "hook_event_name",
  "tool_input",
  "tool_response",
  "tool_use_id",
  "stop_hook_active",
  "executionNum",
  "terminationReason",
  "toolCall",
  "workspacePaths",
];

function isVocal() {
  return process.env.COCKPIT_VOCALE === "1" && process.env.NEXGEN_DISABLE_EVENT_SINK !== "1";
}

function socketReady() {
  return SOCK_PATH.startsWith("\\\\.\\pipe\\") || existsSync(SOCK_PATH);
}

function payloadLine(eventType, cliName, text, sessionId) {
  return (
    JSON.stringify({
      event: eventType,
      cli: cliName,
      // COCKPIT_SESSION_ID wins: it is the cockpit's own identity for this session.
      session_id: process.env.COCKPIT_SESSION_ID || sessionId || "",
      text: text ? String(text).slice(0, MAX_TEXT) : "",
      ts: Date.now() / 1000,
    }) + "\n"
  );
}

function stripThinking(str) {
  if (!str || typeof str !== "string") return "";
  return str
    .replace(/<think>[\s\S]*?<\/think>/gi, "")
    .replace(/<thought>[\s\S]*?<\/thought>/gi, "")
    .replace(/\[thought\][\s\S]*?\[\/thought\]/gi, "")
    .replace(/<reasoning>[\s\S]*?<\/reasoning>/gi, "")
    .replace(/<\/?(?:think|thought|reasoning)>/gi, "")
    .trim();
}

/**
 * Normalizes one row of a JSONL transcript into a reply string, or "" if not a reply.
 *
 * Supports multi-dialect JSONL transcripts:
 * - Claude: row.message.role === "assistant" (string or array of text blocks)
 * - Antigravity: row.type === "PLANNER_RESPONSE" / row.source === "MODEL" (string or array)
 * - Codex / generic: row.role === "assistant"
 */
function extractFromRow(row) {
  if (!row || typeof row !== "object") return "";
  if (row.isSidechain) return "";

  // Ignore tool results, checkpoints, and user inputs
  if (
    row.type === "GENERIC" ||
    row.type === "TOOL_RESULT" ||
    row.type === "CORTEX_STEP_TYPE_TOOL_RESULT" ||
    row.type === "CHECKPOINT" ||
    row.type === "USER_INPUT"
  ) {
    return "";
  }

  const isAssistant =
    row.type === "PLANNER_RESPONSE" ||
    row.type === "CORTEX_STEP_TYPE_PLANNER_RESPONSE" ||
    (row.source === "MODEL" && row.type !== "GENERIC") ||
    (row.role === "assistant" && row.type !== "GENERIC") ||
    (row.message && row.message.role === "assistant");

  if (!isAssistant) return "";

  const content = row.content ?? row.message?.content ?? row.text;
  if (typeof content === "string") {
    const t = stripThinking(content);
    if (t) return t;
  } else if (Array.isArray(content)) {
    const t = content
      .filter((b) => b && (b.type === "text" || typeof b === "string") && !b.thought && !b.reasoning)
      .map((b) => (typeof b === "string" ? b : b.text || ""))
      .join("\n");
    const cleaned = stripThinking(t);
    if (cleaned) return cleaned;
  }
  return "";
}

/**
 * Last assistant prose in a Claude/Antigravity/Codex JSONL transcript, or "" if none.
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
      const prose = extractFromRow(row);
      if (prose) return prose;
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
 */
function extractProse(payload, allowTranscript) {
  if (!payload || typeof payload !== "object") return "";

  const isEnvelope = ENVELOPE_KEYS.some((k) => k in payload);
  const keys = isEnvelope
    ? ["last_assistant_message", "lastAssistantMessage", "response"]
    : ["last_assistant_message", "lastAssistantMessage", "response", "text", "message"];

  for (const key of keys) {
    const value = payload[key];
    if (typeof value === "string") {
      const cleaned = stripThinking(value);
      if (cleaned) return cleaned;
    }
  }

  if (allowTranscript) {
    const transcriptPath = payload.transcriptPath || payload.transcript_path;
    if (typeof transcriptPath === "string" && transcriptPath) {
      return lastAssistantText(transcriptPath);
    }
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

function getOpencodeReplyFromDb(sessionID) {
  if (!sessionID) return "";
  try {
    const { DatabaseSync } = require("node:sqlite");
    const dbPath = join(homedir(), ".local", "share", "opencode", "opencode.db");
    if (!existsSync(dbPath)) return "";
    const db = new DatabaseSync(dbPath, { readOnly: true });
    const msgStmt = db.prepare(
      "SELECT id FROM message WHERE session_id = ? ORDER BY time_created DESC LIMIT 5"
    );
    const msgs = msgStmt.all(sessionID);
    let reply = "";
    for (const m of msgs) {
      const partStmt = db.prepare(
        "SELECT data FROM part WHERE message_id = ? ORDER BY time_created ASC"
      );
      const parts = partStmt
        .all(m.id)
        .map((r) => {
          try {
            return JSON.parse(r.data);
          } catch {
            return null;
          }
        })
        .filter(Boolean);
      const text = parts
        .filter((p) => p && p.type === "text" && typeof p.text === "string")
        .map((p) => p.text)
        .join("\n")
        .trim();
      if (text) {
        reply = text;
        break;
      }
    }
    db.close();
    return stripThinking(reply);
  } catch {
    return "";
  }
}

// --- 1. OpenCode plugin ----------------------------------------------------

export default async function (input) {
  const client = input?.client;

  return {
    event: async ({ event }) => {
      if (!isVocal() || !socketReady()) return;
      if (!event || typeof event !== "object") return;

      const type = event.type;
      const props = event.properties || {};

      if (type === "step.finish") {
        const text = props.stepOutput || "";
        const sessionID = props.sessionID || "";
        if (text) {
          send("on_step", "opencode", text, sessionID);
        }
      }

      if (type === "session.idle") {
        const sessionID = props.sessionID || "";
        let replyText = "";

        if (client && sessionID && typeof client.session?.messages === "function") {
          try {
            const res = await client.session.messages({ path: { id: sessionID } });
            const messages = res?.data || res || [];
            if (Array.isArray(messages)) {
              for (let i = messages.length - 1; i >= 0; i--) {
                const m = messages[i];
                if (m?.info?.role === "assistant" || m?.role === "assistant") {
                  const parts = m.parts || [];
                  const text = parts
                    .filter((p) => p && p.type === "text" && typeof p.text === "string")
                    .map((p) => p.text)
                    .join("\n")
                    .trim();
                  if (text) {
                    replyText = text;
                    break;
                  }
                }
              }
            }
          } catch {}
        }

        if (!replyText && sessionID) {
          replyText = getOpencodeReplyFromDb(sessionID);
        }

        send("on_done", "opencode", stripThinking(replyText), sessionID);
      }
    },
    // Compatibility hooks if invoked directly by older/future runners
    "session.idle": async (event) => {
      if (!isVocal() || !socketReady()) return;
      const text = stripThinking(event?.lastAssistantMessage || "");
      send("on_done", "opencode", text, event?.sessionID || "");
    },
    "step.finish": async (event) => {
      if (!isVocal() || !socketReady()) return;
      send("on_step", "opencode", event?.stepOutput || "", event?.sessionID || "");
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

  let eventType = process.argv[2] || "on_done";
  const cliName = process.argv[3] || process.env.COCKPIT_CLI || "unknown";
  const argText = process.argv[4] || "";

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
    const isTurnEnd =
      eventType === "on_done" ||
      payload.hook_event_name === "Stop" ||
      payload.terminationReason !== undefined;

    if (isTurnEnd) {
      eventType = "on_done";
    }

    const text = extractProse(payload, isTurnEnd);
    if (!text && !isTurnEnd) {
      finish();
      return;
    }
    const sessionId =
      payload.conversationId || payload.session_id || payload.sessionID || "";
    send(eventType, cliName, text, sessionId, finish);
  });
  process.stdin.on("error", finish);
  process.stdin.resume();
}

const invoked = process.argv[1] || "";
if (invoked.endsWith("nexgen-event-sink.mjs") || invoked.endsWith("nexgen-event-sink.js")) {
  main();
}
