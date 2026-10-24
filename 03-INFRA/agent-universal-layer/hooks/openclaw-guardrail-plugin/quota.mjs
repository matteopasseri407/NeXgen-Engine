// NeXgen Engine -- the `/quota` command.
//
// One of the jobs of the `nexgen` plugin, registered by `index.mjs`. It answers with the
// lines the engine already rendered: the plugin forwards text and never formats a number of
// its own, so the phone and the terminal cannot disagree about a percentage.
//
// Where the engine is, how long it may take and what a failure reads like are not decided
// here: `engine-run.mjs` answers all three for every job that speaks to the engine.
//
// A command that cannot answer is a sentence, never an exception: a chat that loses `/quota`
// is a worse chat, not a broken gateway.
import { spawn } from "node:child_process";

import { CONFIG_PATH, TIMEOUT_MS, engineLines } from "./engine-run.mjs";

//: What every engine job speaks to: the two paths the deposit writes, and the deadline.
export { CONFIG_PATH, TIMEOUT_MS };

export function quotaText(configPath = CONFIG_PATH, { spawnFn = spawn, timeoutMs = TIMEOUT_MS } = {}) {
  return engineLines(["quota", "--json"], { configPath, spawnFn, timeoutMs, label: "quota" });
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
