// NeXgen Engine -- the engine's switches, read fresh on every call.
//
// One file read, three jobs: `nexgen.config.json` beside the plugin carries the posture and the
// briefing/events switches, and every job reads it through here so no job invents its own
// tolerance. A missing, unparsable or non-object file is an empty object: the caller decides
// what "not set" means (the guardrail falls back to `bypass`, the other two to "off").
import { readFileSync } from "node:fs";

export function readPluginConfig(configPath) {
  try {
    const parsed = JSON.parse(readFileSync(configPath, "utf8"));
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
  } catch {
    return {};
  }
}

export function configSection(config, name) {
  const section = config[name];
  return section && typeof section === "object" && !Array.isArray(section) ? section : {};
}
