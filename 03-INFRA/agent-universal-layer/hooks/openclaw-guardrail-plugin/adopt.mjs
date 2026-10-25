// NeXgen Engine -- `/adopt`: automatic resource adoption and retirement for OpenClaw.
//
// One job: run the automatic adoption pass, bringing SAFE resources into the canonical
// manifests and retiring resources absent for two consecutive passes. Resources needing
// review stay in machine-local state and are reported so the user can inspect them.
//
// What changes state is delegated to `nexgen openclaw adopt --auto`:
// this plugin forwards lines and leaves logic to the engine.
import { spawn } from "node:child_process";

import { CONFIG_PATH, engineLines, runEngine } from "./engine-run.mjs";

export const ADOPT_HINT = "nexgen openclaw adopt";
export const ADOPT_TIMEOUT_MS = 30_000;

function adoptOptions(configPath, spawnFn, timeoutMs) {
  return { configPath, spawnFn, timeoutMs, label: "adopt", hint: ADOPT_HINT };
}

//: Run the auto-adopt pass and return the lines for chat display.
export function adoptText({
  configPath = CONFIG_PATH,
  spawnFn = spawn,
  timeoutMs = ADOPT_TIMEOUT_MS,
} = {}) {
  return engineLines(
    ["openclaw", "adopt", "--auto", "--json"],
    adoptOptions(configPath, spawnFn, timeoutMs),
  );
}

//: Background handler when a skill changes in the workshop or library.
export async function handleSkillChanged(
  event,
  {
    configPath = CONFIG_PATH,
    spawnFn = spawn,
    timeoutMs = ADOPT_TIMEOUT_MS,
    logger = null,
  } = {},
) {
  try {
    const res = await runEngine(
      ["openclaw", "adopt", "--auto"],
      adoptOptions(configPath, spawnFn, timeoutMs),
    );
    if (res.ok && res.text && logger && typeof logger.info === "function") {
      const trimmed = res.text.trim();
      if (trimmed && !trimmed.includes("Everything is aligned")) {
        logger.info(`adopt: ${trimmed}`);
      }
    }
  } catch {
    // an observer never raises or fails the gateway
  }
}

//: Register the /adopt command and the skill_changed lifecycle hook.
export function registerAdopt(
  api,
  {
    configPath = CONFIG_PATH,
    timeoutMs = ADOPT_TIMEOUT_MS,
    spawnFn = spawn,
  } = {},
) {
  if (typeof api.registerCommand === "function") {
    api.registerCommand({
      name: "adopt",
      description: "Adopt safe OpenClaw resources and show pending reviews.",
      handler: async () => ({
        text: await adoptText({ configPath, timeoutMs, spawnFn }),
      }),
    });
  }

  if (typeof api.on === "function") {
    api.on("skill_changed", (event) => {
      handleSkillChanged(event, {
        configPath,
        spawnFn,
        timeoutMs,
        logger: api.logger,
      });
    });
  }
}
