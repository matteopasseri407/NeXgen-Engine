// NeXgen Engine -- Universal Event Sink Hook (IPC emitter for lifecycle events)
//
// Deployed by agent_sync.py and registered with CLI runtimes (Claude, Codex, Antigravity, OpenCode).
// Emits lightweight, non-blocking lifecycle events (on_step, on_heartbeat, on_done)
// to optional local consumers (e.g. nexgen-voice companion, notifications, or supervisors).
//
// Fail-Safe Contract:
// 1. Guaranteed exit code 0 under all conditions (never breaks host CLI execution).
// 2. Strict total wall-clock timeout of 15ms covering connect + write + flush.
// 3. Instant exit (<0.5ms) if IPC socket is not present on disk.

import { existsSync, readFileSync } from "node:fs";
import { connect } from "node:net";
import { homedir, platform } from "node:os";
import { join } from "node:path";

// Unconditional top-level catch-all: NEVER crash or return non-zero
process.on("uncaughtException", () => process.exit(0));
process.on("unhandledRejection", () => process.exit(0));

const IS_WIN = platform() === "win32";
const DEFAULT_SOCK = IS_WIN
  ? "\\\\.\\pipe\\nexgen_events"
  : join(homedir(), ".local", "share", "nexgen", "events.sock");

const SOCK_PATH = process.env.NEXGEN_EVENT_IPC_PATH || DEFAULT_SOCK;

function main() {
  if (process.env.NEXGEN_DISABLE_EVENT_SINK === "1") {
    process.exit(0);
  }

  // Hard deadline: process terminates cleanly in at most 15ms total
  const hardTimer = setTimeout(() => {
    process.exit(0);
  }, 15);
  hardTimer.unref();

  // Fast-path: if Unix socket file doesn't exist on disk, exit immediately (<0.2ms)
  if (!IS_WIN && !existsSync(SOCK_PATH)) {
    process.exit(0);
  }

  // Read event payload from arguments or stdin
  let eventType = process.argv[2] || "on_done";
  let cliName = process.argv[3] || process.env.COCKPIT_CLI || "unknown";
  let rawText = "";

  try {
    if (process.argv[4]) {
      rawText = process.argv[4];
    } else if (!process.stdin.isTTY) {
      const buffer = readFileSync(0, "utf8");
      if (buffer) {
        try {
          const parsed = JSON.parse(buffer);
          rawText = parsed.last_assistant_message || parsed.text || parsed.response || buffer;
          if (parsed.hook_event_name === "Stop") {
            eventType = "on_done";
          }
        } catch {
          rawText = buffer;
        }
      }
    }
  } catch {
    // Ignore read errors
  }

  const payload = JSON.stringify({
    event: eventType,
    cli: cliName,
    session_id: process.env.COCKPIT_SESSION_ID || "",
    text: rawText ? rawText.slice(0, 4000) : "",
    ts: Date.now() / 1000,
  }) + "\n";

  try {
    const client = connect(SOCK_PATH, () => {
      client.write(payload, () => {
        client.end();
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

main();
