// NeXgen Engine -- the one way the plugin starts the engine.
//
// One job: turn the two paths the deposit writes into `nexgen.config.json` (`engine.command`,
// `engine.entry`) into one engine run -- arguments in, text out -- without a shell, with a
// deadline, and with a sentence where an exception would be. `/quota`, `/council` and `/sup`
// all come through here, so none of them can disagree about where the engine is, how long it
// may take, or what a failure reads like.
//
// The engine is named as interpreter plus CLI module on purpose: on Windows the `nexgen`
// launcher is a `.cmd`, Windows starts a `.cmd` only by handing it to `cmd.exe`, and this
// plugin must never spawn a shell. A job that needs to outlive the chat opens the same pair
// detached, writes what the engine said to a log, and says where that log is.
import { spawn } from "node:child_process";
import { closeSync, mkdirSync, openSync, writeSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { configSection, readPluginConfig } from "./engine-config.mjs";

const DIR = dirname(fileURLToPath(import.meta.url));

//: The engine-owned switches beside the plugin: the deposit writes this file.
export const CONFIG_PATH = join(DIR, "nexgen.config.json");

//: Long enough for the engine to read its own state, short enough that a hung child does not
//: hold a chat hostage.
export const TIMEOUT_MS = 25_000;

//: Where a detached run writes what the engine printed. The system temp directory on purpose:
//: it is a diagnostic to hand back, not a record the engine has to keep.
export const LOG_DIR = join(tmpdir(), "nexgen");

//: The interpreter and the CLI module, or null when the engine has not been deposited.
export function engineCommand(configPath = CONFIG_PATH) {
  const engine = configSection(readPluginConfig(configPath), "engine");
  const command = typeof engine.command === "string" ? engine.command.trim() : "";
  const entry = typeof engine.entry === "string" ? engine.entry.trim() : "";
  return command && entry ? { command, entry } : null;
}

//: The engine's JSON `lines`, joined for a chat message; the raw text when it is not JSON.
export function linesOf(answer) {
  try {
    const data = JSON.parse(answer);
    if (data && Array.isArray(data.lines) && data.lines.length) {
      return data.lines.join("\n");
    }
  } catch {
    // Not JSON: the engine printed something else, and that is still an answer.
  }
  return String(answer || "").trim();
}

//: The four sentences a failed run can produce. They name the job the person asked for and
//: the command that answers the same question in a terminal, so the chat never has to explain.
export function sentencesFor(label, hint = `nexgen ${label}`) {
  return {
    noEngine: `${label}: the engine path is not configured yet. Run \`nexgen sync\` and try again.`,
    noStart: `${label}: the engine could not be started. Run \`${hint}\` in a terminal.`,
    tooSlow: `${label}: the engine did not answer in time. Run \`${hint}\` in a terminal.`,
    noAnswer: `${label}: the engine answered with an error. Run \`${hint}\` in a terminal.`,
  };
}

//: One engine run: `{ok, text}`, where `text` is the raw stdout and never a stack trace.
export function runEngine(args, {
  configPath = CONFIG_PATH,
  spawnFn = spawn,
  timeoutMs = TIMEOUT_MS,
  label = "nexgen",
  hint,
} = {}) {
  const say = sentencesFor(label, hint);
  const engine = engineCommand(configPath);
  if (!engine) {
    return Promise.resolve({ ok: false, text: say.noEngine });
  }
  return new Promise((resolve) => {
    let answer = "";
    let settled = false;
    const finish = (result) => {
      if (settled) return;
      settled = true;
      resolve(result);
    };
    let child;
    try {
      child = spawnFn(engine.command, [engine.entry, ...args], {
        shell: false,
        windowsHide: true,
        stdio: ["ignore", "pipe", "pipe"],
      });
    } catch {
      finish({ ok: false, text: say.noStart });
      return;
    }
    const timer = setTimeout(() => {
      try {
        child.kill();
      } catch {
        // Already gone: the sentence below is the same either way.
      }
      finish({ ok: false, text: say.tooSlow });
    }, timeoutMs);
    child.stdout.on("data", (chunk) => {
      answer += chunk;
    });
    // The exit code decides, and a traceback is not for the chat.
    child.stderr.on("data", () => {});
    child.on("error", () => {
      clearTimeout(timer);
      finish({ ok: false, text: say.noStart });
    });
    child.on("close", (code) => {
      clearTimeout(timer);
      finish(code === 0 ? { ok: true, text: answer } : { ok: false, text: say.noAnswer });
    });
  });
}

//: The engine's answer as the lines a chat message shows: its own lines when it succeeded,
//: the sentence that says what went wrong when it did not.
export async function engineLines(args, options = {}) {
  const run = await runEngine(args, options);
  if (!run.ok) {
    return run.text;
  }
  const lines = linesOf(run.text);
  return lines || sentencesFor(options.label || "nexgen", options.hint).noAnswer;
}

//: One engine run that outlives the turn: detached from this process, output to a log.
//: Returns `{ok, pid, log}` or `{ok: false, text}` with the same sentences as above.
//: `header` is written before the child starts, so it is always the log's first line.
export function startEngine(args, {
  configPath = CONFIG_PATH,
  spawnFn = spawn,
  logDir = LOG_DIR,
  logName = "engine.log",
  header = "",
  label = "nexgen",
  hint,
} = {}) {
  const say = sentencesFor(label, hint);
  const engine = engineCommand(configPath);
  if (!engine) {
    return { ok: false, text: say.noEngine };
  }
  const log = join(logDir, logName);
  let fd;
  try {
    mkdirSync(logDir, { recursive: true });
    fd = openSync(log, "a");
    if (header) {
      writeSync(fd, header);
    }
  } catch {
    return { ok: false, text: say.noStart };
  }
  try {
    const child = spawnFn(engine.command, [engine.entry, ...args], {
      shell: false,
      windowsHide: true,
      detached: true,
      stdio: ["ignore", fd, fd],
    });
    // The chat is not the parent of this run: the gateway may restart while it works.
    child.unref();
    return { ok: true, pid: child.pid, log };
  } catch {
    return { ok: false, text: say.noStart };
  } finally {
    closeSync(fd);
  }
}
