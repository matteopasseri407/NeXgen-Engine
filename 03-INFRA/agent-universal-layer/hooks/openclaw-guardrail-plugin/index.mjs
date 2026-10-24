// NeXgen Engine -- the `nexgen` OpenClaw plugin.
//
// Deployed byte-for-byte by the guard's permissions phase into
// `<state dir>/extensions/nexgen/`. OpenClaw discovers every plugin directory under its state
// `extensions/`; ENABLING it is a `plugins.*` change and happens with the gateway stopped
// (`nexgen openclaw setup`), because editing that block under a running gateway breaks it.
//
// One job per file beside this one, and this file only registers them: a new job costs an import
// and one line in `register`. The jobs know nothing about each other, and documenting them is the
// job file's own business -- nothing here has to be rewritten when one arrives. The guardrail goes
// first: it is the one that can stop a call.
//
// Two engine-owned files travel beside the jobs, both read fresh on every call:
//   nexgen-guardrail.config.json  the guardrail bodies, timeout, strict/autoAllow, audit file
//   nexgen.config.json            the engine's switches: briefing, posture, engine, governor
// `engine-run.mjs` is the one way a job starts the engine: no shell, a deadline, a sentence.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { registerBriefing } from "./briefing.mjs";
import { registerCouncil } from "./council.mjs";
import { registerGovernor } from "./governor.mjs";
import { registerGuardrail } from "./guardrail.mjs";
import { registerQuota } from "./quota.mjs";
import { registerWorkshop } from "./workshop.mjs";

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
  description: "The NeXgen engine for OpenClaw.",
  register(api) {
    registerGuardrail(api);
    registerBriefing(api);
    registerQuota(api);
    registerWorkshop(api);
    registerGovernor(api);
    registerCouncil(api);
  },
};
