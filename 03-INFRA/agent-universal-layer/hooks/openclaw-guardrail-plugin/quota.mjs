// NeXgen Engine -- the `/quota` command.
//
// One of the jobs of the `nexgen` plugin, registered by `index.mjs`. It answers with the
// lines the engine already rendered: the plugin forwards text and never formats a number of
// its own, so the phone and the terminal cannot disagree about a percentage.
//
// The engine deposits `engine.command` and `engine.entry` in `nexgen.config.json`: the
// interpreter and the CLI module, not the `nexgen` launcher. On Windows that launcher is a
// `.cmd`, which needs `cmd.exe`, and this plugin must never spawn a shell.
//
// A command that cannot answer is a sentence, never an exception: a chat that loses `/quota`
// is a worse chat, not a broken gateway.
import { spawn } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { configSection, readPluginConfig } from "./engine-config.mjs";

const DIR = dirname(fileURLToPath(import.meta.url));
const CONFIG_PATH = join(DIR, "nexgen.config.json");

//: Long enough for a week of transcripts to be summed, short enough that a hung child does
//: not hold the chat hostage.
export const TIMEOUT_MS = 25_000;

//: What a person reads when the answer never came. English on purpose: the engine translates
//: its own sentences, and this file has no way to know the language of the person reading.
const NO_ENGINE = "quota: the engine path is not configured yet. Run `nexgen sync` and try again.";
const NO_START = "quota: the engine could not be started. Run `nexgen quota` in a terminal.";
const TOO_SLOW = "quota: the engine did not answer in time. Run `nexgen quota` in a terminal.";
const NO_ANSWER = "quota: the engine answered with an error. Run `nexgen quota` in a terminal.";

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
  const text = String(answer || "").trim();
  return text || NO_ANSWER;
}

export function quotaText(configPath = CONFIG_PATH, { spawnFn = spawn, timeoutMs = TIMEOUT_MS } = {}) {
  const engine = engineCommand(configPath);
  if (!engine) {
    return Promise.resolve(NO_ENGINE);
  }
  return new Promise((resolve) => {
    let answer = "";
    let settled = false;
    const finish = (text) => {
      if (settled) return;
      settled = true;
      resolve(text);
    };
    let child;
    try {
      child = spawnFn(engine.command, [engine.entry, "quota", "--json"], {
        shell: false,
        windowsHide: true,
        stdio: ["ignore", "pipe", "pipe"],
      });
    } catch {
      finish(NO_START);
      return;
    }
    const timer = setTimeout(() => {
      try {
        child.kill();
      } catch {
        // Already gone: the sentence below is the same either way.
      }
      finish(TOO_SLOW);
    }, timeoutMs);
    child.stdout.on("data", (chunk) => {
      answer += chunk;
    });
    // The exit code decides, and a traceback is not for the chat.
    child.stderr.on("data", () => {});
    child.on("error", () => {
      clearTimeout(timer);
      finish(NO_START);
    });
    child.on("close", (code) => {
      clearTimeout(timer);
      finish(code === 0 ? linesOf(answer) : NO_ANSWER);
    });
  });
}

export function registerQuota(api, { configPath = CONFIG_PATH, timeoutMs = TIMEOUT_MS } = {}) {
  // An OpenClaw older than its command surface: nothing to register, nothing to break.
  if (typeof api.registerCommand !== "function") {
    return;
  }
  api.registerCommand({
    name: "quota",
    description: "What is left of each subscription, and how old the reading is.",
    handler: async () => ({ text: await quotaText(configPath, { timeoutMs }) }),
  });
}
