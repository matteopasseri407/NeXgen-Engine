// NeXgen Engine -- OpenClaw vault-briefing job.
//
// One of the jobs of the `nexgen` plugin, registered by `index.mjs`. It is the OpenClaw
// half of the same idea as Claude Code's `claude-vault-checkpoint.mjs`: when a session lost its
// context (a compaction here, a resume/compact there), the next prompt is re-grounded in the
// vault instead of letting the model guess. The TEXT is not written in this file: it is
// `vault-briefing.txt`, deployed beside the plugin and read by Claude's hook too, so the two
// hosts inject exactly the same bytes.
//
// The seam is one prompt later than Claude's, by design: OpenClaw's `after_compaction` and
// `session_start` are observations with no place to return context, so the plugin marks the
// session there and injects on the first `before_prompt_build` that follows, once.
//
// Rules, and they are the opposite of the guardrail's: a briefing must never block, never
// write, never break a session. Every failure here is swallowed on purpose; a session that
// loses its briefing is a worse session, not a broken one.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { configSection, readPluginConfig } from "./engine-config.mjs";
import { sessionOf } from "./session.mjs";

const DIR = dirname(fileURLToPath(import.meta.url));
const CONFIG_PATH = join(DIR, "nexgen.config.json");
const BRIEFING_PATH = join(DIR, "vault-briefing.txt");

//: Sessions waiting for their briefing. Bounded so a gateway that runs for weeks does not
//: accumulate the ids of sessions that were marked and never prompted again.
const MAX_MARKED = 512;

export function briefingText(briefingPath = BRIEFING_PATH) {
  try {
    return readFileSync(briefingPath, "utf8").trim();
  } catch {
    return "";
  }
}

export function briefingEnabled(configPath = CONFIG_PATH) {
  return configSection(readPluginConfig(configPath), "briefing").enabled === true;
}

export function registerBriefing(api, {
  configPath = CONFIG_PATH,
  briefingPath = BRIEFING_PATH,
} = {}) {
  // Insertion-ordered: the oldest marked session goes first when the bound is reached.
  const marked = new Map();

  const mark = (event, ctx) => {
    try {
      if (!briefingEnabled(configPath)) {
        return;
      }
      const session = sessionOf(event, ctx);
      if (!session) {
        return;
      }
      if (marked.size >= MAX_MARKED) {
        marked.delete(marked.keys().next().value);
      }
      marked.set(session, true);
    } catch {
      // A briefing that cannot be prepared must not stop the session.
    }
  };

  api.on("after_compaction", (event, ctx) => {
    mark(event, ctx);
  });

  api.on("session_start", (event, ctx) => {
    // Only a session that RESUMED lost its context. A fresh start is not a briefing case:
    // it has a full prompt and an empty history to build on.
    try {
      if (event && event.resumedFrom) {
        mark(event, ctx);
      }
    } catch {
      // ignore
    }
  });

  api.on("before_prompt_build", (event, ctx) => {
    try {
      const session = sessionOf(event, ctx);
      if (!session || !marked.has(session)) {
        return undefined;
      }
      // Once, and only once: the mark is spent whether or not the text comes back non-empty,
      // so a missing data file cannot turn into a briefing on every prompt.
      marked.delete(session);
      const text = briefingText(briefingPath);
      return text ? { prependContext: text } : undefined;
    } catch {
      return undefined;
    }
  });
}
