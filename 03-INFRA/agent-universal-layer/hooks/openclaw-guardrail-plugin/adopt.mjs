// NeXgen Engine -- `/adopt`: automatic resource adoption and retirement for OpenClaw.
//
// One job: keep the canonical library in step with OpenClaw without asking. Resources the
// vetting calls SAFE enter; a resource adopted from OpenClaw and absent for two consecutive
// passes leaves; everything a person still has to decide stays in machine-local state and is
// reported, never adopted in the dark.
//
// What changes state is delegated to `nexgen openclaw adopt --auto`: this plugin forwards
// lines and leaves the logic to the engine. Three triggers, one pass:
//   `/adopt`         the person asks, in chat;
//   `skill_changed`  OpenClaw announces a committed skill, the only lifecycle event it has
//                    for what the Workshop and ClawHub create;
//   `gateway_start`  the gateway's own clock, one bounded pass per interval, cleared by
//                    `gateway_stop`. OpenClaw's hook catalog (docs/plugins/hooks/reference.md
//                    in the installed release) announces skills and offers nothing for
//                    `mcp.servers`, so a removed MCP server is noticed by the next tick: the
//                    engine's guard cycle runs every 30 minutes and this keeps that cadence
//                    instead of inventing a second one.
import { spawn } from "node:child_process";

import { CONFIG_PATH, engineLines, runEngine } from "./engine-run.mjs";

export const ADOPT_HINT = "nexgen openclaw adopt";
export const ADOPT_TIMEOUT_MS = 30_000;
//: The cadence of the engine's own guard cycle (scheduler.py, `OnUnitActiveSec=30min`):
//: two consecutive absences of a resource are about an hour apart.
export const ADOPT_INTERVAL_MS = 30 * 60 * 1000;

//: One pass at a time: a tick that lands while the previous pass still runs is dropped,
//: because the next one is a full scan anyway and children must not pile up.
let inFlight = false;

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

//: One background pass, whatever asked for it: a skill change or the gateway's clock.
//: Nothing is said when there is nothing to say. A failure is a log line only when the
//: caller asks for one (`reportFailures`): the scheduled tick does, because nobody typed a
//: command that would have answered with the sentence, while a skill change keeps the
//: Workshop's log to itself. A pass never raises.
export async function handleSkillChanged(
  event,
  {
    configPath = CONFIG_PATH,
    spawnFn = spawn,
    timeoutMs = ADOPT_TIMEOUT_MS,
    logger = null,
    reportFailures = false,
  } = {},
) {
  if (inFlight) {
    return;
  }
  inFlight = true;
  try {
    const res = await runEngine(["openclaw", "adopt", "--auto"], adoptOptions(configPath, spawnFn, timeoutMs));
    const text = res.text ? res.text.trim() : "";
    // A pass that found nothing says nothing (it runs every half hour).
    const worthSaying = res.ok ? Boolean(text) && !text.includes("Everything is aligned") : reportFailures;
    if (worthSaying && logger && typeof logger.info === "function") {
      logger.info(text.startsWith("adopt:") ? text : `adopt: ${text}`);
    }
  } catch {
    // an observer never raises or fails the gateway
  } finally {
    inFlight = false;
  }
}

//: The gateway's clock: one timer, started with the gateway and cleared with it.
//: `setIntervalFn`/`clearIntervalFn` are injected so a test can drive the tick without
//: waiting half an hour, and `unref` keeps the timer from holding the process open alone.
export function registerGatewayClock(
  api,
  {
    configPath = CONFIG_PATH,
    timeoutMs = ADOPT_TIMEOUT_MS,
    spawnFn = spawn,
    intervalMs = ADOPT_INTERVAL_MS,
    setIntervalFn = setInterval,
    clearIntervalFn = clearInterval,
    logger = null,
  } = {},
) {
  let timer = null;
  api.on("gateway_start", () => {
    if (timer !== null) {
      return;
    }
    timer = setIntervalFn(
      () => handleSkillChanged(
        { action: "scheduled" },
        { configPath, timeoutMs, spawnFn, logger, reportFailures: true },
      ),
      intervalMs,
    );
    if (timer && typeof timer.unref === "function") {
      timer.unref();
    }
  });
  api.on("gateway_stop", () => {
    if (timer !== null) {
      clearIntervalFn(timer);
      timer = null;
    }
  });
}

//: Register the /adopt command, the skill observer and the gateway clock.
export function registerAdopt(
  api,
  {
    configPath = CONFIG_PATH,
    timeoutMs = ADOPT_TIMEOUT_MS,
    spawnFn = spawn,
    intervalMs = ADOPT_INTERVAL_MS,
    setIntervalFn = setInterval,
    clearIntervalFn = clearInterval,
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

  if (typeof api.on !== "function") {
    return;
  }
  api.on("skill_changed", (event) => {
    handleSkillChanged(event, { configPath, spawnFn, timeoutMs, logger: api.logger });
  });
  registerGatewayClock(api, {
    configPath, timeoutMs, spawnFn, intervalMs, setIntervalFn, clearIntervalFn, logger: api.logger,
  });
}
