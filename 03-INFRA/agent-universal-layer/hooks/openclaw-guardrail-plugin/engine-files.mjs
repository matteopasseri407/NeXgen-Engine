// NeXgen Engine -- where the engine's own files are, for every job of this plugin.
//
// One job: resolve the path of a file the engine deposited beside the plugin, whether
// the plugin is loaded from its deposit or from a build copy OpenClaw makes.
//
// Why it exists: OpenClaw runs a build copy of the package under
// `<state>/tmp/plugin-captures/.../node_modules/nexgen/`, which carries the package and
// never the engine's files (the switches, the briefing text, the quota state, the
// guardrail sidecar). A job that only looked beside itself found nothing there and
// switched itself off in silence. The state folder is an ancestor of any such copy and
// is the one holding `openclaw.json`, which is how it is found without guessing a home
// directory. A file beside the plugin always wins, and a caller with nothing to find
// gets the beside path back, so the guardrail still fails closed.
import { existsSync } from "node:fs";
import { basename, dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

//: Every file of this plugin lives in one folder: this module's own folder is the default.
const DIR = dirname(fileURLToPath(import.meta.url));

export function resolveEngineFile(name, startDir = DIR) {
  const beside = join(startDir, name);
  if (existsSync(beside)) {
    return beside;
  }
  for (let dir = dirname(startDir); ; dir = dirname(dir)) {
    if (existsSync(join(dir, "openclaw.json"))) {
      const deposited = join(dir, "extensions", basename(startDir), name);
      return existsSync(deposited) ? deposited : beside;
    }
    if (dirname(dir) === dir) {
      return beside;
    }
  }
}
