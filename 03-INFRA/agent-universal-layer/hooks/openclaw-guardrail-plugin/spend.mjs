// NeXgen Engine -- the `/spend` command.
//
// One of the jobs of the `nexgen` plugin, registered by `index.mjs`. It answers with the
// lines the engine already rendered (`nexgen usage --json`): per provider, what this
// machine spent and what is left of each plan. The plugin forwards text and never
// formats a number of its own, so the chat and the terminal cannot disagree.
//
// Why `/spend` and not `/usage`: OpenClaw already owns `/usage` (the per-response footer
// and its own `/usage cost`), and a plugin command is matched before the built-in ones --
// registering that name here would take the surface away from OpenClaw. Its own cost
// summary sums session logs, so an agent on a subscription reports $0 and the flat lane
// that was paid for is invisible; this command says what the engine can really read.
//
// Where the engine is, how long it may take and what a failure reads like are not decided
// here: `engine-run.mjs` answers all three for every job that speaks to the engine.
import { spawn } from "node:child_process";

import { CONFIG_PATH, TIMEOUT_MS, engineLines } from "./engine-run.mjs";

//: What every engine job speaks to: the two paths the deposit writes, and the deadline.
export { CONFIG_PATH, TIMEOUT_MS };

export function spendText(configPath = CONFIG_PATH, { spawnFn = spawn, timeoutMs = TIMEOUT_MS } = {}) {
  return engineLines(["usage", "--json"], { configPath, spawnFn, timeoutMs, label: "usage" });
}

export function registerSpend(api, { configPath = CONFIG_PATH, timeoutMs = TIMEOUT_MS } = {}) {
  // An OpenClaw older than its command surface: nothing to register, nothing to break.
  if (typeof api.registerCommand !== "function") {
    return;
  }
  api.registerCommand({
    name: "spend",
    description: "What this machine spent, and what each plan has left, straight from the engine.",
    handler: async () => ({ text: await spendText(configPath, { timeoutMs }) }),
  });
}
