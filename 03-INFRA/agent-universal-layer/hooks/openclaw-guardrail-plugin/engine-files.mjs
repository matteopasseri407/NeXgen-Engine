// NeXgen Engine -- where the engine's own files are, for every job of this plugin.
//
// One job: resolve the path of a file the engine deposited beside the plugin, whether the plugin
// is loaded from its deposit or from a build copy OpenClaw makes.
//
// Why it exists: the files the engine writes for this plugin (the switches, the briefing text, the
// quota state, the guardrail sidecar) are never part of the package, so a job that looked only
// beside itself found nothing on a machine where OpenClaw runs a copy of the package, and switched
// itself off in silence. The host knows where it loaded the plugin from and says so: `api.rootDir`
// is that folder and `api.resolvePath` resolves a name against it (openclaw 2026.11.2,
// `dist/registry-wjpI9g9x.mjs:740-742`, `rootDir: record.rootDir` and
// `resolvePath: (input) => resolvePluginPath(input, record.rootDir)`), so the host's own answer is
// asked first, then the file beside the plugin. A caller with nothing to find gets the beside path
// back, so the guardrail still fails closed.
import { existsSync } from "node:fs";
import { basename, dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

//: Every file of this plugin lives in one folder: this module's own folder is the default.
const DIR = dirname(fileURLToPath(import.meta.url));

//: The api the host handed to `register(api)`, set once by `index.mjs`. Its `rootDir` and
//: `resolvePath` are the host's answer to "where is this plugin loaded from"; they are absent on
//: the bare object a test registers, and then the deposit beside the module is the answer.
let host = null;

//: Point the resolver at the host. Called once by `index.mjs`, before any job registers.
export function setPluginHost(api) {
  host = api && typeof api === "object" ? api : null;
}

//: The host's path for this name, or "" when it has nothing to say or the file is not there.
function hostedFile(name) {
  if (typeof host?.resolvePath === "function") {
    const resolved = host.resolvePath(name);
    if (typeof resolved === "string" && existsSync(resolved)) {
      return resolved;
    }
  }
  if (typeof host?.rootDir === "string") {
    const rooted = join(host.rootDir, name);
    if (existsSync(rooted)) {
      return rooted;
    }
  }
  return "";
}

export function resolveEngineFile(name, startDir = DIR) {
  const hosted = hostedFile(name);
  if (hosted) {
    return hosted;
  }
  const beside = join(startDir, name);
  if (existsSync(beside)) {
    return beside;
  }
  // The deposit search, kept for one release because the switches only reach
  // `plugins.entries.<id>.config` when `nexgen openclaw setup` writes them: on a machine that has
  // synced but not set up again, this is still the only way to the file the engine deposited. It
  // goes away with the file, when every switch and every path arrives from the host.
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
