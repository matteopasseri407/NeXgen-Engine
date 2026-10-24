// NeXgen Engine -- the Council, asked from a chat.
//
// One job: start a council run nobody is watching, say so at once, and let the outcome be
// read afterwards. A council takes minutes and opens several vendor CLIs, so it cannot be
// awaited inside a chat turn: the person gets one line with an identifier, the engine runs
// detached, and when it ends the engine tells the person itself.
//
// Why the engine's megaphone and not an OpenClaw API: OpenClaw has documented deferred
// delivery (`api.runtime.channel.reply.dispatchReplyFromConfig`) and `gateway.request
// ("chat.send")`, but both belong to the turn that started the work -- the route and the
// capability expire with it, and a gateway restart loses the pending reply. This process
// does not keep the child either: it is detached on purpose. The megaphone is the engine's
// one alert surface (contract invariant 7), it already speaks through OpenClaw's own CLI,
// and it survives the turn, the chat and the gateway.
//
// What the log is for: the engine prints what it is doing, and this file is the only place
// that survives, so the companion tool `nexgen_council_result` reads it back.
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { CONFIG_PATH, LOG_DIR, startEngine } from "./engine-run.mjs";

//: Where a detached council writes: its own folder, so a `/quota` log and a council log
//: never mix.
export const COUNCIL_LOG_DIR = join(LOG_DIR, "council");

//: How much of the log an answer quotes. The whole run is in the file; a chat message that
//: tried to be the whole file would be unreadable.
export const RESULT_LINES = 40;

//: What the engine prints last, whatever the mode and whatever the ending: `--notify` runs
//: in a `finally`, so this line is the run's own "I am done".
const END_MARKS = ["[council] notified:", "[council] not delivered:"];

//: The engine's answers, in the language this file speaks. English on purpose: the engine
//: translates its own sentences, and this file cannot know who is reading.
const NO_QUESTION = "council: ask a question after the command, for example `/council should we cache at the edge?`";
const NO_RUN = "council: no run with that identifier on this machine.";

//: The engine arguments for a free question. `brainstorm` because a free question is one
//: seat and one round; `--auto` because there is nobody at the terminal to choose a seat;
//: `--keep-session` so the full outcome outlives the run; `--notify` so the person is told.
export function launchArgs(question, { tag = "", keepSession = true, mode = "brainstorm" } = {}) {
  const args = ["council", mode, question.trim(), "--auto"];
  if (keepSession) {
    args.push("--keep-session");
  }
  args.push("--notify");
  if (tag) {
    args.push("--tag", tag);
  }
  return args;
}

//: A short, sortable identifier: the one the acknowledgement shows and the notification
//: quotes back, so two runs started in the same minute are still two runs.
export function newTag(now = new Date()) {
  return `council-${now.toISOString().replace(/[-:]/g, "").replace(/\.\d+Z$/, "Z")}`;
}

export function acknowledgement(id, log) {
  return `council ${id} started. I will write to you when it has finished.\nlog: ${log}`;
}

//: Start one council. Answers `{ok, id, log}` or `{ok: false, text}`.
export function startCouncil(question, {
  configPath = CONFIG_PATH,
  spawnFn,
  logDir = COUNCIL_LOG_DIR,
  tag = "",
  keepSession = true,
  now,
} = {}) {
  const text = String(question || "").trim();
  if (!text) {
    return { ok: false, text: NO_QUESTION };
  }
  const id = tag || newTag(now);
  const started = startEngine(launchArgs(text, { tag: id, keepSession }), {
    configPath,
    spawnFn,
    logDir,
    logName: `${id}.log`,
    header: `# council ${id} started ${(now || new Date()).toISOString()}\n`,
    label: "council",
  });
  if (!started.ok) {
    return started;
  }
  return { ok: true, id, log: started.log };
}

function logText(id, logDir) {
  try {
    return readFileSync(join(logDir, `${id}.log`), "utf8");
  } catch {
    return null;
  }
}

//: The run as the log has it: still working, or the outcome it ended with.
export function resultOf(id, { logDir = COUNCIL_LOG_DIR } = {}) {
  const text = logText(id, logDir);
  if (text === null) {
    return NO_RUN;
  }
  const lines = text.split(/\r?\n/).filter((line) => line.trim() && !line.startsWith("# "));
  const ended = lines.some((line) => END_MARKS.some((mark) => line.startsWith(mark)));
  const shown = lines.slice(-RESULT_LINES);
  const omitted = lines.length > shown.length;
  if (!ended) {
    return [`council ${id}: still running.`, ...shown].join("\n");
  }
  const head = [`council ${id}: finished.`];
  if (omitted) {
    head.push(`(the last ${RESULT_LINES} lines of ${lines.length})`);
  }
  return [...head, ...shown].join("\n");
}

//: A JSON Schema for the tool's arguments: the host validates against it before the call.
function parameters(properties, required) {
  return { type: "object", properties, required, additionalProperties: false };
}

const QUESTION = {
  type: "string",
  description: "The question to put to the council. One line or a paragraph; no files.",
};

const COUNCIL_ID = {
  type: "string",
  description: "The identifier the council tool returned when the run started.",
};

function textResult(text, details) {
  return { content: [{ type: "text", text }], details };
}

export function councilTool({ configPath = CONFIG_PATH, logDir = COUNCIL_LOG_DIR, spawnFn } = {}) {
  return {
    name: "nexgen_council",
    label: "NeXgen council",
    description:
      "Ask the AI Council a question and get an identifier back at once. The council runs for " +
      "minutes; read the outcome later with the companion tool, or wait for the notification.",
    parameters: parameters({ question: QUESTION }, ["question"]),
    async execute(_callId, params) {
      const started = startCouncil(params?.question ?? "", { configPath, logDir, spawnFn });
      if (!started.ok) {
        return textResult(started.text, { error: started.text });
      }
      return textResult(acknowledgement(started.id, started.log), {
        council: started.id,
        log: started.log,
      });
    },
  };
}

export function councilResultTool({ logDir = COUNCIL_LOG_DIR } = {}) {
  return {
    name: "nexgen_council_result",
    label: "NeXgen council outcome",
    description:
      "Read what a council run has produced so far. Says it is still running, or quotes the " +
      "end of the run: verdict, the session directory, and the answer.",
    parameters: parameters({ council_id: COUNCIL_ID }, ["council_id"]),
    async execute(_callId, params) {
      const id = String(params?.council_id ?? "").trim();
      const text = resultOf(id, { logDir });
      return textResult(text, { council: id });
    },
  };
}

export function registerCouncil(api, { configPath = CONFIG_PATH, logDir = COUNCIL_LOG_DIR, spawnFn } = {}) {
  // An OpenClaw older than its command surface: nothing to register, nothing to break.
  if (typeof api.registerCommand === "function") {
    api.registerCommand({
      name: "council",
      description: "Ask the AI Council a question; it answers here when the run is over.",
      handler: async (ctx) => {
        const started = startCouncil(ctx?.args ?? "", { configPath, logDir, spawnFn });
        return { text: started.ok ? acknowledgement(started.id, started.log) : started.text };
      },
    });
  }
  if (typeof api.registerTool === "function") {
    api.registerTool(councilTool({ configPath, logDir, spawnFn }));
    api.registerTool(councilResultTool({ logDir }));
  }
}
