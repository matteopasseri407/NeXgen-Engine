// NeXgen Engine -- OpenClaw's Skill Workshop observer job.
//
// One of the four jobs of the `nexgen` plugin, registered by `index.mjs`. OpenClaw is where the
// owner creates: the Skill Workshop learns a skill into one agent's own state folder, which no
// other CLI reads, so until it is adopted the other four runtimes have never heard of it and a
// lost state folder takes it with it.
//
// This job says so, once per committed change, in the gateway log, and stops there. Adoption is
// the person's act: it copies into the Vault, appends one row to the canonical manifest, builds
// the views of every CLI and archives the Workshop copy, so a plugin that adopted by itself would
// rewrite the library in the middle of a turn. Observation only -- the handler returns nothing,
// blocks nothing, writes no file and runs no command, and a failure inside it is forgotten.
//
// The trigger is OpenClaw's `skill_changed` hook, which the SDK documents as observation of a
// committed live-skill create, update or removal; the source field is what says the change came
// from the Workshop rather than from ClawHub or a source install.
export const ADOPT_HINT = "run `nexgen openclaw adopt` to make it canonical";

//: Which Workshop changes name a skill the library does not declare yet. A create and an update
//: both leave a skill that no manifest carries; a removal leaves nothing to adopt, and an event
//: from another source is that installer's business (`clawhub`, `source-install`, `upload`).
const ADOPTABLE_ACTIONS = new Set(["created", "updated"]);

function skillName(event) {
  const artifact = event.after && typeof event.after === "object" ? event.after : null;
  const name = artifact ? artifact.name || artifact.skillKey : "";
  return typeof name === "string" ? name.trim() : "";
}

// The line the gateway log carries, or an empty string when this change is not news. Pure, so
// the wording is decided in one place and the handler stays four lines long.
export function adoptionNote(event) {
  if (!event || typeof event !== "object" || event.source !== "workshop") {
    return "";
  }
  if (!ADOPTABLE_ACTIONS.has(event.action)) {
    return "";
  }
  const name = skillName(event);
  return name ? `new skill in the Workshop: "${name}". ${ADOPT_HINT}` : "";
}

export function registerWorkshop(api) {
  api.on("skill_changed", (event) => {
    try {
      const note = adoptionNote(event);
      if (!note) {
        return;
      }
      const logger = api.logger;
      if (logger && typeof logger.info === "function") {
        logger.info(note);
      }
    } catch {
      // the change is already committed: an observer that cannot speak must not undo it
    }
  });
}
