// NeXgen Engine -- the `nexgen` OpenClaw plugin: one plugin, three separate jobs.
//
// Deployed byte-for-byte by the guard's permissions phase into
// `<state dir>/extensions/nexgen/`. OpenClaw discovers every plugin directory under its state
// `extensions/`; ENABLING it is a `plugins.*` change and happens with the gateway stopped
// (`nexgen openclaw setup`), because editing that block under a running gateway breaks it.
//
// The three jobs live in their own files and know nothing about each other:
//   guardrail.mjs  the gate: the same private body Claude Code runs, on before_tool_call
//   briefing.mjs   the same vault briefing Claude's checkpoint hook injects, one prompt later
//   events.mjs     the same lifecycle events the event sink emits, on OpenClaw's own hooks
// This file only registers them. The guardrail goes first: it is the one that can stop a call.
//
// Two engine-owned files travel beside the jobs, both read fresh on every call:
//   nexgen-guardrail.config.json  the guardrail bodies, timeout, strict/autoAllow, audit file
//   nexgen.config.json            the engine's switches: briefing, events, posture
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { registerGuardrail } from "./guardrail.mjs";
import { registerBriefing } from "./briefing.mjs";
import { registerEvents } from "./events.mjs";

const DIR = dirname(fileURLToPath(import.meta.url));

// The id OpenClaw's `plugins.*` block must name. Read from the manifest that ships beside this
// file, so the two cannot drift: `nexgen_core.runtimes.openclaw.PLUGIN_ID` is the same string,
// and the deposit copies both bytes.
function pluginId() {
  try {
    const id = JSON.parse(readFileSync(join(DIR, "openclaw.plugin.json"), "utf8")).id;
    return typeof id === "string" && id ? id : "nexgen";
  } catch {
    return "nexgen";
  }
}

export default {
  id: pluginId(),
  name: "NeXgen",
  description: "The NeXgen engine for OpenClaw: guardrail, vault briefing and lifecycle events.",
  register(api) {
    registerGuardrail(api);
    registerBriefing(api);
    registerEvents(api);
  },
};
