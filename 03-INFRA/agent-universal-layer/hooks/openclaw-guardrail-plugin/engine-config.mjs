// NeXgen Engine -- the engine's switches, read fresh on every call from the host that owns them.
//
// One job: hand every job of this plugin the switches the engine set, from the host's own copy of
// them and from the file this release still writes.
//
// Why it exists: OpenClaw parses `plugins.entries.<id>.config` itself, validates it against the
// manifest's `configSchema` and hands the result to `register(api)` as `api.pluginConfig`
// (openclaw 2026.11.2, `dist/config-contracts-0_35lKDI.d.ts:51491`), so the engine's switches no
// longer need a JSON file of their own parsed by hand here. `nexgen.config.json` beside the plugin
// is still read, and still wins where it carries a section: `nexgen sync` rewrites that file
// between two setups, while the host only re-reads `openclaw.json` when the gateway comes back, so
// the file is the fresher of the two for this release. With no file (a plain `nexgen openclaw
// setup`, or the release that retires it) the host's config is the only source. A missing,
// unparsable or non-object file is what the host handed over, or an empty object: the caller
// decides what "not set" means -- the guardrail falls back to `bypass`, the briefing to "off", and
// the Workshop notice has no switch at all because it only writes a log line.
import { readFileSync } from "node:fs";

//: The api the host handed to `register(api)`. `pluginConfig` is OpenClaw's own reading of
//: `plugins.entries.<id>.config`, so this is where the switches live natively.
let host = null;

//: Point the reader at the host's config. Called once by `index.mjs`, before any job registers.
export function setPluginConfig(api) {
  host = api && typeof api === "object" ? api : null;
}

//: `plugins.entries.<id>.config` as the host validated it, or an empty object.
function hostedConfig() {
  const handed = host?.pluginConfig;
  return handed && typeof handed === "object" && !Array.isArray(handed) ? handed : {};
}

//: The switches: the file's sections first, then whatever only the host carries.
export function readPluginConfig(configPath) {
  try {
    const parsed = JSON.parse(readFileSync(configPath, "utf8"));
    const fromFile = parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
    return { ...hostedConfig(), ...fromFile };
  } catch {
    return hostedConfig();
  }
}

export function configSection(config, name) {
  const section = config[name];
  return section && typeof section === "object" && !Array.isArray(section) ? section : {};
}
