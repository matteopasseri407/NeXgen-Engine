// NeXgen Engine -- OpenClaw session-continuity checkpoint job.
//
// Registered by `index.mjs`. Writes a session continuity checkpoint to `openclaw-checkpoint.json`
// in the engine's machine-local state directory whenever OpenClaw compacts or ends a session
// (`after_compaction` and `session_end` hooks).
//
// The checkpoint records the origin (openclaw), agent, session key in the shape accepted by
// `openclaw mcp serve`, event, reason and timestamp.
//
// Rules:
//   - An observer must never break a session: all errors are swallowed.
//   - Writes to machine-local state only, NEVER into the KnowledgeVault.
//   - Writes only when the `checkpoint.enabled` switch in `nexgen.config.json` is true.
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";

import { configSection, readPluginConfig } from "./engine-config.mjs";
import { resolveEngineFile } from "./engine-files.mjs";
import { sessionOf } from "./session.mjs";

//: The engine-owned switches: beside the plugin, or in the deposit behind a build copy.
const CONFIG_PATH = resolveEngineFile("nexgen.config.json");
const CHECKPOINT_FILENAME = "openclaw-checkpoint.json";

export function resolveStateDir(config = {}, env = process.env) {
  if (config && config.checkpoint && typeof config.checkpoint.state_dir === "string" && config.checkpoint.state_dir) {
    return config.checkpoint.state_dir;
  }
  if (config && config.engine && typeof config.engine.state_dir === "string" && config.engine.state_dir) {
    return config.engine.state_dir;
  }
  if (env.AGENT_STATE_DIR) {
    return join(env.AGENT_STATE_DIR, "nexgen");
  }
  if (env.XDG_STATE_HOME) {
    return join(env.XDG_STATE_HOME, "nexgen");
  }
  const home = env.NEXGEN_HOME || homedir();
  return join(home, ".local", "state", "nexgen");
}

export function checkpointPath(stateDir = null, config = {}, env = process.env) {
  const dir = stateDir || resolveStateDir(config, env);
  return join(dir, CHECKPOINT_FILENAME);
}

export function writeCheckpoint({
  origin = "openclaw",
  agent = "default",
  sessionKey = null,
  event = "unknown",
  reason = null,
  timestamp = null,
  stateDir = null,
  config = {},
  env = process.env,
} = {}) {
  try {
    const target = checkpointPath(stateDir, config, env);
    mkdirSync(dirname(target), { recursive: true });
    const record = {
      origin,
      agent: agent || "default",
      session_key: sessionKey ? String(sessionKey) : null,
      event,
      reason: reason || null,
      timestamp: timestamp || new Date().toISOString(),
    };
    writeFileSync(target, JSON.stringify(record, null, 2) + "\n", "utf8");
    return target;
  } catch {
    // Continuity write errors must never bubble up to callers or OpenClaw
    return null;
  }
}

export function readCheckpoint(stateDir = null, config = {}, env = process.env) {
  try {
    const target = checkpointPath(stateDir, config, env);
    const parsed = JSON.parse(readFileSync(target, "utf8"));
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

export function checkpointEnabled(configPath = CONFIG_PATH) {
  return configSection(readPluginConfig(configPath), "checkpoint").enabled === true;
}

export function registerCheckpoint(api, {
  configPath = CONFIG_PATH,
  stateDir = null,
  env = process.env,
} = {}) {
  if (!api || typeof api.on !== "function") {
    return;
  }

  api.on("after_compaction", (event, ctx) => {
    try {
      if (!checkpointEnabled(configPath)) {
        return;
      }
      const config = readPluginConfig(configPath);
      const sessionKey = (ctx && (ctx.sessionKey || ctx.sessionId)) || (event && (event.sessionKey || event.sessionId)) || sessionOf(event, ctx);
      const agent = (ctx && ctx.agentId) || (event && event.agentId) || "default";
      const reason = (event && event.reason) || "compaction";
      writeCheckpoint({
        origin: "openclaw",
        agent,
        sessionKey,
        event: "after_compaction",
        reason,
        stateDir,
        config,
        env,
      });
    } catch {
      // Swallowed: session must not be interrupted
    }
  });

  api.on("session_end", (event, ctx) => {
    try {
      if (!checkpointEnabled(configPath)) {
        return;
      }
      const config = readPluginConfig(configPath);
      const sessionKey = (ctx && (ctx.sessionKey || ctx.sessionId)) || (event && (event.sessionKey || event.sessionId)) || sessionOf(event, ctx);
      const agent = (ctx && ctx.agentId) || (event && event.agentId) || "default";
      const reason = (event && event.reason) || "unknown";
      writeCheckpoint({
        origin: "openclaw",
        agent,
        sessionKey,
        event: "session_end",
        reason,
        stateDir,
        config,
        env,
      });
    } catch {
      // Swallowed: session must not be interrupted
    }
  });
}
