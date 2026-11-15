// NeXgen Engine -- the governor job: a turn starts on the next model when a plan is spent.
//
// One of the jobs of the `nexgen` plugin, registered by `index.mjs` and deployed byte for byte
// into `<state dir>/extensions/nexgen/`. OpenClaw already moves a turn to
// `agents.entries.<id>.model.fallbacks` when a provider answers with a spent-plan error. This
// job does the same thing one step earlier, because a plan at 95% still answers: the turn
// would run, fail, and only then fall back.
//
// Two jobs live here, on two hooks, and they must not be confused:
//   `before_model_resolve`  `decision`   move the turn to a plan that still has room
//   `before_agent_reply`    `admission`  refuse a turn bound to an ACP lane whose plan is spent
// The second exists because an ACP session is the harness running as itself: no fallback chain
// of OpenClaw's is consulted there, so without it the one lane with the largest context per
// turn is also the one lane with no gate at all.
//
// Two files, both read fresh on every turn, and this file invents neither:
//   nexgen.config.json         the `governor` section: the line, the age limit, the chains, the lanes
//   nexgen-quota-state.json    the percentages the beat last read, per provider
//
// The plugin computes no percentage of its own: it compares the reading the engine wrote with
// the line the engine wrote. Nothing happens when the policy names no chain, when the state is
// missing, old or unreadable, when the provider of the turn has no reading, or when every
// chain entry runs on that same spent plan. A turn that cannot be improved is left alone.
import { readFileSync } from "node:fs";

import { configSection, readPluginConfig } from "./engine-config.mjs";
import { resolveEngineFile } from "./engine-files.mjs";

//: The engine's own files: beside the plugin, or in the deposit behind a build copy.
const CONFIG_PATH = resolveEngineFile("nexgen.config.json");
const STATE_PATH = resolveEngineFile("nexgen-quota-state.json");

//: The engine writes both numbers on every setup; these are what a sidecar written by an
//: older engine (or edited by hand) falls back to, so the arithmetic never sees a hole.
export const DEFAULT_BLOCK_AT = 95;
export const DEFAULT_MAX_AGE_SECONDS = 3 * 60 * 60;

//: A model as OpenClaw names it: `provider/model`, split on the first slash the same way the
//: engine does, so the two cannot disagree about which provider a ref names.
const SEPARATOR = "/";

function readJson(path) {
  try {
    const parsed = JSON.parse(readFileSync(path, "utf8"));
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

//: The chain this agent falls back to. The engine resolves the shared chain onto every agent
//: it creates before writing the section, so a turn never has to decide which policy applies.
export function chainFor(governor, agentId) {
  const agents = governor && typeof governor.agents === "object" && !Array.isArray(governor.agents)
    ? governor.agents
    : {};
  const chain = typeof agentId === "string" ? agents[agentId] : undefined;
  return Array.isArray(chain) ? chain.filter((ref) => typeof ref === "string" && ref.includes(SEPARATOR)) : [];
}

//: The readings, or null when the file is missing, unreadable or older than the policy allows.
//: A clock that moved backwards is read as "just now": a state from the future is still the
//: last thing the engine wrote, and refusing it would silence the handover for a whole beat.
export function freshState(statePath, now, maxAgeSeconds) {
  const state = readJson(statePath);
  if (!state || typeof state.written_at !== "number" || typeof state.providers !== "object" || !state.providers) {
    return null;
  }
  return Math.max(0, now - state.written_at) <= maxAgeSeconds ? state : null;
}

//: The first chain entry that runs on a plan that still has room. A spent plan takes its own
//: sibling models with it, so an entry on the same provider cannot be the answer.
export function pick(chain, provider, state, blockAt) {
  for (const ref of chain) {
    const cut = ref.indexOf(SEPARATOR);
    const candidate = ref.slice(0, cut);
    const model = ref.slice(cut + SEPARATOR.length);
    if (cut <= 0 || !model || candidate === provider) {
      continue;
    }
    const reading = state.providers[candidate];
    if (reading && typeof reading.percent === "number" && reading.percent >= blockAt) {
      continue;
    }
    return { provider: candidate, model };
  }
  return null;
}

//: What this turn should start on, or undefined to leave it exactly as OpenClaw resolved it.
export function decision({
  ctx,
  configPath = CONFIG_PATH,
  statePath = STATE_PATH,
  now = Date.now() / 1000,
  log,
} = {}) {
  const governor = configSection(readPluginConfig(configPath), "governor");
  const chain = chainFor(governor, ctx && ctx.agentId);
  if (!chain.length) {
    return undefined;
  }
  const provider = typeof ctx?.modelProviderId === "string" ? ctx.modelProviderId.trim() : "";
  if (!provider) {
    return undefined;
  }
  const blockAt = typeof governor.block_at === "number" ? governor.block_at : DEFAULT_BLOCK_AT;
  const maxAge = typeof governor.state_max_age_seconds === "number" && governor.state_max_age_seconds > 0
    ? governor.state_max_age_seconds
    : DEFAULT_MAX_AGE_SECONDS;
  const state = freshState(statePath, now, maxAge);
  if (!state) {
    return undefined;
  }
  const spent = state.providers[provider];
  if (!spent || typeof spent.percent !== "number" || spent.percent < blockAt) {
    return undefined;
  }
  const target = pick(chain, provider, state, blockAt);
  if (!target) {
    return undefined;
  }
  log?.info?.(
    `nexgen governor: ${provider} is at ${Math.round(spent.percent)}% of its ` +
      `${typeof spent.window === "string" && spent.window ? spent.window : "plan"} window, ` +
      `so this turn starts on ${target.provider}${SEPARATOR}${target.model}`,
  );
  return { providerOverride: target.provider, modelOverride: target.model };
}

//: The ACP lane a turn is bound to, and the one line a spent plan answers with.
//: `governor.acp.agents` is `<agent id> -> <plan>`, written by the engine only when
//: the squad asks for it (`acp_admission: true`): a lane the state has no reading
//: for, a missing reading, an old one or another provider all leave the turn alone.
export function admission({ ctx, configPath = CONFIG_PATH, statePath = STATE_PATH, now = Date.now() / 1000 } = {}) {
  const governor = configSection(readPluginConfig(configPath), "governor");
  const lanes = governor?.acp && typeof governor.acp.agents === "object" && !Array.isArray(governor.acp.agents)
    ? governor.acp.agents
    : {};
  const agent = typeof ctx?.agentId === "string" ? ctx.agentId.trim() : "";
  const plan = agent ? lanes[agent] : undefined;
  if (typeof plan !== "string" || !plan) {
    return undefined;
  }
  const blockAt = typeof governor.block_at === "number" ? governor.block_at : DEFAULT_BLOCK_AT;
  const maxAge = typeof governor.state_max_age_seconds === "number" && governor.state_max_age_seconds > 0
    ? governor.state_max_age_seconds
    : DEFAULT_MAX_AGE_SECONDS;
  const state = freshState(statePath, now, maxAge);
  if (!state) {
    return undefined;
  }
  const spent = state.providers[plan];
  if (!spent || typeof spent.percent !== "number" || spent.percent < blockAt) {
    return undefined;
  }
  const window = typeof spent.window === "string" && spent.window ? spent.window : "plan";
  const percent = Math.round(spent.percent);
  return {
    handled: true,
    reply: {
      text:
        `The ${plan} plan is at ${percent}% of its ${window} window, so this harness lane ` +
        `(agent ${agent}) did not start. Wait for the window to reset, or send the same request ` +
        `to an agent on another plan.`,
    },
  };
}

export function registerGovernor(api, { configPath = CONFIG_PATH, statePath = STATE_PATH } = {}) {
  // An OpenClaw older than its hook surface: nothing to register, nothing to break.
  if (typeof api.on !== "function") {
    return;
  }
  api.on("before_model_resolve", (_event, ctx) => {
    try {
      return decision({ ctx, configPath, statePath, log: api.logger });
    } catch {
      // A hook that throws is a turn nobody judged: leave the model OpenClaw resolved.
      return undefined;
    }
  });
  api.on("before_agent_reply", (_event, ctx) => {
    try {
      return admission({ ctx, configPath, statePath });
    } catch {
      // A hook that throws is a turn nobody judged: let OpenClaw answer it as usual.
      return undefined;
    }
  });
}
