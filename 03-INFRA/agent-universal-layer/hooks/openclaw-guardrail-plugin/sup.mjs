// NeXgen Engine -- `/sup`: the supervised tasks, read from a chat.
//
// One job: answer with the lines the engine already prints for a person at a terminal
// (`nexgen-local sup status`, `sup costi`), so the phone and the desk cannot disagree about
// which unit is waiting for a review. Read-only on purpose: what changes state
// (`start`, `unit`, `run`, `collect`, `review`, `publish`) is a tool for the supervisor
// agent, allowlisted to it and to nobody else -- see `sup-tools.mjs`.
//
// No argument: every task, its units, and the spend of the sessions. With an id: that one
// task. Both halves ask the engine for `--json`, which is the same text in a shape this
// file can forward without parsing anything.
import { spawn } from "node:child_process";

import { CONFIG_PATH, engineLines } from "./engine-run.mjs";

//: The command that answers the same question by hand, quoted in a failure.
export const SUP_HINT = "nexgen local sup status";

//: Reading a lane with many tasks sums several directories: longer than `/quota`, and still
//: something a person can wait for.
export const SUP_TIMEOUT_MS = 45_000;

function options(configPath, spawnFn, timeoutMs) {
  return { configPath, spawnFn, timeoutMs, label: "sup", hint: SUP_HINT };
}

//: One engine call, as the lines a chat shows.
export function supText(id = "", {
  configPath = CONFIG_PATH,
  spawnFn = spawn,
  timeoutMs = SUP_TIMEOUT_MS,
} = {}) {
  const task = String(id || "").trim();
  if (task) {
    return engineLines(["local", "sup", "status", task, "--json"], options(configPath, spawnFn, timeoutMs));
  }
  return Promise.all([
    engineLines(["local", "sup", "status", "--json"], options(configPath, spawnFn, timeoutMs)),
    engineLines(["local", "sup", "costi", "--json"], options(configPath, spawnFn, timeoutMs)),
  ]).then((parts) => {
    // An engine that cannot answer at all says the same sentence for both verbs: one copy.
    return parts[0] === parts[1] ? parts[0] : parts.join("\n");
  });
}

export function registerSup(api, {
  configPath = CONFIG_PATH,
  timeoutMs = SUP_TIMEOUT_MS,
  spawnFn,
} = {}) {
  // An OpenClaw older than its command surface: nothing to register, nothing to break.
  if (typeof api.registerCommand !== "function") {
    return;
  }
  api.registerCommand({
    name: "sup",
    description: "The supervised tasks, their units and their spend; an id for one task.",
    handler: async (ctx) => ({ text: await supText(ctx?.args ?? "", { configPath, timeoutMs, spawnFn }) }),
  });
}
