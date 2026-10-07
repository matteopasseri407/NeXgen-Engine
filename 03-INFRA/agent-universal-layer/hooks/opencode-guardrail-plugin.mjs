// NeXgen Engine -- OpenCode guardrail plugin (V2 plugin contract; mechanism only, no policy).
//
// Deployed byte-for-byte by the guard's permissions phase as `index.mjs` of a plugin DIRECTORY,
// `<opencode config>/plugins/nexgen-guardrail/`, next to `nexgen-guardrail-core.mjs`. OpenCode
// loads every directory under its global `plugins/` on its own: nothing is registered in
// `opencode.jsonc`, which is the part that moved between OpenCode releases and left an earlier
// version of this plugin registered and never loaded. This file never varies between installs;
// what does vary lives in the sidecar `nexgen-guardrail.config.json` written next to it, read
// fresh on every call (a changed manifest takes effect on the next command, no restart).
//
// Contract: each configured guardrail body is a Node script speaking the SAME stdin/stdout JSON
// shape Claude Code's own PreToolUse hooks use. One guardrail body, several thin CLI adapters,
// never duplicated dangerous-command logic. See claude-guardrail-adapter.mjs and
// antigravity-guardrail-adapter.mjs for the siblings.
//
// What OpenCode V2 gives a plugin (checked against OpenCode 2.0.24, live):
//   - the plugin is the default export `{ id, setup(ctx) }`; a bare function (the V1 shape) is
//     refused at load with "Plugin must export a default definition with an id and a setup";
//   - `ctx.shell.hook("create.before", fn)` runs before a shell command starts, with
//     `{ command, cwd, timeout, shell, env }`; throwing stops the command. It runs whatever the
//     permission rules say, so under a plain `allow` this is the veto;
//   - `ctx.permission.hook("evaluate", fn)` runs after the configured rules, for every action
//     (an allowed one included), with `{ action, resources, effect, ... }`; for a shell command
//     `action` is "shell" and `resources[0]` is the command. Setting `effect` answers it.
//
// Under the bypass posture the engine writes the shell rule as `ask` and sets `autoAllow` in the
// sidecar: this plugin then answers `allow` for what the guardrail body permits, which keeps the
// no-prompt behaviour, and `deny` for the rest. If OpenCode stopped calling the hook, the failure
// is visible (every command prompts) instead of silent.
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const CONFIG_PATH = join(dirname(fileURLToPath(import.meta.url)), "nexgen-guardrail.config.json");

// What makes a request a shell command is that it carries one: the action name and the place
// the text sits are both read, neither is assumed to stay put across versions.
export function commandOfPermission(event) {
  if (!event || event.action !== "shell" || !Array.isArray(event.resources)) {
    return null;
  }
  const command = event.resources[0];
  return typeof command === "string" ? command : null;
}

export function commandOfShell(event) {
  return event && typeof event.command === "string" ? event.command : null;
}

// One consultation of the guardrail body. The sidecar and the core are read per call, not at
// load: a core that is missing makes the plugin fail closed on each command instead of failing
// to load and leaving none, and a guard that installs the guardrail after OpenCode started
// still takes effect for the session that is already running.
async function judge(command, directory, sessionID, { audit }) {
  let strict = false;
  try {
    const { closed, loadSidecar, recordAudit, worstOf } = await import("./nexgen-guardrail-core.mjs");
    const sidecar = loadSidecar(CONFIG_PATH);
    strict = sidecar.strict;
    if (sidecar.broken) {
      return { ...closed(sidecar.broken, strict), autoAllow: false };
    }
    if (sidecar.hooks.length === 0) {
      return { decision: "allow", reason: "", autoAllow: false };
    }
    const payload = JSON.stringify({
      hook_event_name: "PreToolUse",
      tool_name: "Bash",
      tool_input: { command },
      cwd: directory,
      session_id: sessionID || null,
    });
    const worst = worstOf(sidecar.hooks, payload, strict);
    if (audit) {
      recordAudit(sidecar.auditFile, "opencode", worst.decision);
    }
    return { ...worst, autoAllow: Boolean(sidecar.autoAllow) };
  } catch (err) {
    void err; // the core itself may be what failed: answer without it
    return { decision: strict ? "deny" : "ask", reason: "nexgen-guardrail: the guardrail could not run", autoAllow: false };
  }
}

export default {
  id: "nexgen-guardrail",
  async setup(ctx) {
    // The veto: stops a command the body denies, under any permission posture.
    await ctx.shell.hook("create.before", async (event) => {
      const command = commandOfShell(event);
      if (command === null) {
        return;
      }
      const verdict = await judge(command, event.cwd, null, { audit: true });
      if (verdict.decision === "deny") {
        throw new Error(verdict.reason || "blocked by the NeXgen guardrail");
      }
    });

    // The answer: what the person would be asked. Never loosens a stricter decision; only
    // answers `allow` for a request that was going to be asked, and only when the engine
    // opted the posture in (under an `ask` posture the person asked to be asked).
    await ctx.permission.hook("evaluate", async (event) => {
      const command = commandOfPermission(event);
      if (command === null) {
        return;
      }
      const verdict = await judge(command, event.metadata && event.metadata.cwd, event.sessionID, { audit: false });
      if (verdict.decision === "deny") {
        event.effect = "deny";
        event.message = verdict.reason || "blocked by the NeXgen guardrail";
        return;
      }
      if (verdict.decision === "ask") {
        event.effect = "ask";
        if (verdict.reason) {
          event.message = verdict.reason;
        }
        return;
      }
      if (verdict.autoAllow && event.effect === "ask") {
        event.effect = "allow";
      }
    });
  },
};
