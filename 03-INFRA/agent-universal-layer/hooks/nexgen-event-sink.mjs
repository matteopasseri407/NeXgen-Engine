// NeXgen Engine -- Universal Event Sink Hook & Plugin (IPC emitter for lifecycle events)
//
// Deployed by agent_sync.py and registered with CLI runtimes (Claude, Codex, Antigravity, OpenCode).
// Dual-mode contract:
// 1. When imported as an OpenCode plugin: exports lifecycle hooks without terminating process.
// 2. When executed directly as CLI hook (Claude/Codex/Antigravity): reads stdin/argv, emits to events.sock, exits in <=40ms.
//
// Isolation Guarantee:
// Only sessions launched with COCKPIT_VOCALE=1 (e.g. from Cockpit GUI Tray or 'nexgen-voice tui') emit audio events.
// All regular terminal windows, background tasks, and IDE processes are 100% silent and exit immediately.

import { existsSync } from "node:fs";
import { connect } from "node:net";
import { homedir, platform } from "node:os";
import { join } from "node:path";

process.on("uncaughtException", () => {});
process.on("unhandledRejection", () => {});

const IS_WIN = platform() === "win32";
const DEFAULT_SOCK = IS_WIN
  ? "\\\\.\\pipe\\nexgen_events"
  : join(homedir(), ".local", "share", "nexgen", "events.sock");

const SOCK_PATH = process.env.NEXGEN_EVENT_IPC_PATH || DEFAULT_SOCK;

function sendEventAsync(eventType, cliName, text, sessionId = "") {
  if (process.env.COCKPIT_VOCALE !== "1") {
    return;
  }
  if (!SOCK_PATH.startsWith("\\\\.\\pipe\\") && !existsSync(SOCK_PATH)) {
    return;
  }
  const payload = JSON.stringify({
    event: eventType,
    cli: cliName,
    session_id: sessionId || process.env.COCKPIT_SESSION_ID || "",
    text: text ? String(text).slice(0, 4000) : "",
    ts: Date.now() / 1000,
  }) + "\n";

  try {
    const client = connect(SOCK_PATH, () => {
      client.write(payload, () => {
        client.end();
      });
    });
    client.on("error", () => {});
  } catch {}
}

// 1. OpenCode Plugin Export
export default async function ({ directory }) {
  return {
    "session.idle": async (event) => {
      if (process.env.COCKPIT_VOCALE !== "1") return;
      sendEventAsync("on_done", "opencode", event?.lastAssistantMessage || "", event?.sessionID || "");
    },
    "step.finish": async (event) => {
      if (process.env.COCKPIT_VOCALE !== "1") return;
      sendEventAsync("on_step", "opencode", event?.stepOutput || "", event?.sessionID || "");
    },
  };
}

// 2. Direct CLI invocation entry point (Claude / Codex / Antigravity hooks)
function main() {
  // Strict isolation: if not explicitly marked as vocal, exit instantly
  if (process.env.COCKPIT_VOCALE !== "1" || process.env.NEXGEN_DISABLE_EVENT_SINK === "1") {
    process.exit(0);
  }

  const deadline = setTimeout(() => {
    process.exit(0);
  }, 40);

  if (!SOCK_PATH.startsWith("\\\\.\\pipe\\") && !existsSync(SOCK_PATH)) {
    process.exit(0);
  }

  let eventType = process.argv[2] || "on_done";
  let cliName = process.argv[3] || process.env.COCKPIT_CLI || "unknown";
  let rawText = process.argv[4] || "";

  function emitPayload(text) {
    const payload = JSON.stringify({
      event: eventType,
      cli: cliName,
      session_id: process.env.COCKPIT_SESSION_ID || "",
      text: text ? text.slice(0, 4000) : "",
      ts: Date.now() / 1000,
    }) + "\n";

    try {
      const client = connect(SOCK_PATH, () => {
        client.write(payload, () => {
          client.end();
          clearTimeout(deadline);
          process.exit(0);
        });
      });

      client.on("error", () => {
        process.exit(0);
      });
    } catch {
      process.exit(0);
    }
  }

  if (rawText || process.stdin.isTTY) {
    emitPayload(rawText);
  } else {
    let stdinBuf = "";
    process.stdin.setEncoding("utf8");
    process.stdin.on("data", (chunk) => {
      stdinBuf += chunk;
      if (stdinBuf.length > 8192) {
        process.stdin.pause();
      }
    });
    process.stdin.on("end", () => {
      let parsedText = stdinBuf;
      try {
        const parsed = JSON.parse(stdinBuf);
        parsedText = parsed.last_assistant_message || parsed.text || parsed.response || stdinBuf;
        if (parsed.hook_event_name === "Stop") {
          eventType = "on_done";
        }
      } catch {}
      emitPayload(parsedText);
    });
    process.stdin.on("error", () => {
      emitPayload("");
    });
    process.stdin.resume();
  }
}

if (process.argv[1] && (process.argv[1].endsWith("nexgen-event-sink.mjs") || process.argv[1].endsWith("nexgen-event-sink.js"))) {
  main();
}
