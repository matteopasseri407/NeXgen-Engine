// NeXgen Engine -- OpenClaw lifecycle-events job.
//
// One of the three jobs of the `nexgen` plugin, registered by `index.mjs`. It is the OpenClaw
// half of `nexgen-event-sink.mjs`: `on_step` when a tool call starts, `on_done` when the run
// ends, on the same socket, with the same payload. The IPC logic is NOT reimplemented here:
// the sink is imported and its `send()` is called, so a change to the protocol is a change in
// one file.
//
// Enabled only when a module this machine declared needs it (the guard writes
// `events.enabled` in `nexgen.config.json`); otherwise nothing is registered beyond two
// callbacks that return immediately, and the sink file is not even deposited.
//
// Rules: observation only, never a decision, never a block. The sink itself is silent when
// no consumer listens (no socket) and only emits in a cockpit-marked session; this file adds a
// hard deadline of its own, because a slow socket must never delay a tool call in the gateway.
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { configSection, readPluginConfig } from "./engine-config.mjs";
import { commandOfTool } from "./guardrail.mjs";
import { sessionOf } from "./session.mjs";

const DIR = dirname(fileURLToPath(import.meta.url));
const CONFIG_PATH = join(DIR, "nexgen.config.json");
const SINK_PATH = join(DIR, "nexgen-event-sink.mjs");

//: The sink's own deadline is 100 ms; a gateway is not a short-lived hook, so the cap here is
//: generous but still a cap: an emission that has not finished by then is abandoned.
const EMIT_TIMEOUT_MS = 1000;

export function eventsEnabled(configPath = CONFIG_PATH) {
  return configSection(readPluginConfig(configPath), "events").enabled === true;
}

// Loaded once, on the first emission: a machine where no module asked for events never opens
// the sink at all, and a sink that is missing or broken makes the emission a no-op.
let sinkPromise = null;
function loadSink(sinkPath) {
  if (!sinkPromise) {
    sinkPromise = import(pathToFileURL(sinkPath).href).catch(() => null);
  }
  return sinkPromise;
}

function withDeadline(start) {
  return new Promise((resolve) => {
    let settled = false;
    const finish = () => {
      if (!settled) {
        settled = true;
        resolve();
      }
    };
    const timer = setTimeout(finish, EMIT_TIMEOUT_MS);
    if (typeof timer.unref === "function") {
      timer.unref();
    }
    try {
      start(finish);
    } catch {
      finish();
    }
  });
}

async function emit(kind, text, sessionId, sinkPath) {
  try {
    const sink = await loadSink(sinkPath);
    if (!sink || typeof sink.send !== "function") {
      return;
    }
    if (!sink.isVocal() || !sink.socketReady()) {
      return; // no cockpit, no consumer, no work
    }
    await withDeadline((done) => sink.send(kind, "openclaw", text, sessionId, done));
  } catch {
    // An event that cannot be delivered is not worth a broken turn.
  }
}

// OpenClaw's messages are the same row shapes the sink already understands (a role, a content
// string or an array of text blocks). The extractor is the sink's, never a copy.
function replyText(sink, event) {
  try {
    const rows = Array.isArray(event?.messages) ? event.messages : [];
    for (let i = rows.length - 1; i >= 0; i--) {
      const text = sink.extractFromRow(rows[i]);
      if (text) {
        return text;
      }
    }
    return typeof sink.extractProse === "function" ? sink.extractProse(event, true) : "";
  } catch {
    return "";
  }
}

export function registerEvents(api, { configPath = CONFIG_PATH, sinkPath = SINK_PATH } = {}) {
  api.on("before_tool_call", (event, ctx) => {
    try {
      if (!eventsEnabled(configPath)) {
        return;
      }
      const command = commandOfTool(event?.toolName, event?.params);
      const text = command || String(event?.toolName || "");
      void emit("on_step", text, sessionOf(event, ctx), sinkPath);
    } catch {
      // never let the observer change the call
    }
  });

  api.on("agent_end", (event, ctx) => {
    try {
      if (!eventsEnabled(configPath)) {
        return;
      }
      void emitDone(event, ctx, sinkPath);
    } catch {
      // ignore
    }
  });
}

// `on_done` carries the turn's last prose, or an empty text when the turn ended silently: the
// cockpit must be able to tell "finished with nothing to say" apart from "still working", and
// the sink's own contract already treats an empty `on_done` that way.
async function emitDone(event, ctx, sinkPath) {
  try {
    const sink = await loadSink(sinkPath);
    if (!sink || typeof sink.send !== "function") {
      return;
    }
    const text = replyText(sink, event);
    if (!sink.isVocal() || !sink.socketReady()) {
      return;
    }
    await withDeadline((done) => sink.send("on_done", "openclaw", text, sessionOf(event, ctx), done));
  } catch {
    // ignore
  }
}
