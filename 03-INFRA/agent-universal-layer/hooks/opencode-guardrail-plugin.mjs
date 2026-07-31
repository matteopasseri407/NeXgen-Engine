// NeXgen Engine -- OpenCode guardrail adapter (mechanism only, no policy).
//
// Deployed byte-for-byte by agent_sync.py's claude_permissions phase into
// the OpenCode config directory and registered in that CLI's own
// `"plugin"` array. This file never varies between installs; everything
// that DOES vary -- which guardrail body file(s) to run, and their
// per-hook timeout -- lives in the sidecar `nexgen-guardrail.config.json`
// written next to it, read fresh on every `permission.ask` call (so a
// changed manifest takes effect on the next permission check, no OpenCode
// restart required).
//
// Contract: each configured guardrail body is a Node script speaking the
// SAME stdin/stdout JSON shape Claude Code's own PreToolUse hooks use --
// stdin: {hook_event_name, tool_name, tool_input, cwd, session_id, ...};
// stdout: {hookSpecificOutput: {permissionDecision, permissionDecisionReason}}.
// One guardrail body, several thin CLI adapters, never duplicated
// dangerous-command logic. See antigravity-guardrail-adapter.mjs for the
// sibling translation.
//
// Scope: only OpenCode's own `permission.ask` hook, only for its "bash"
// permission type (its own shell-command tool) -- the one place OpenCode
// has a decision-bearing hook at all (see the CLI permissions recon;
// `tool.execute.before` has no way to deny).
import { readFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const CONFIG_PATH = join(dirname(fileURLToPath(import.meta.url)), "nexgen-guardrail.config.json");

// Two outcomes that must never collapse into one: "no guardrail is
// configured here" (legitimate, allow, mirroring every other CLI's
// "manifest absent -> no-op" contract) and "a guardrail IS configured and
// this adapter cannot use it" (broken, must not read as absent). Catching
// every error into an empty list made a corrupt sidecar fail OPEN, in a
// posture whose whole point is that this adapter is the only brake left.
function loadConfiguredHooks() {
  let raw;
  try {
    raw = readFileSync(CONFIG_PATH, "utf8");
  } catch (err) {
    if (err && err.code === "ENOENT") {
      return { hooks: [], broken: null };
    }
    return { hooks: [], broken: `sidecar unreadable (${err.message})` };
  }
  try {
    const parsed = JSON.parse(raw);
    if (!parsed || !Array.isArray(parsed.hooks)) {
      return { hooks: [], broken: "sidecar has no hooks array" };
    }
    return { hooks: parsed.hooks, broken: null };
  } catch (err) {
    return { hooks: [], broken: `sidecar is not valid JSON (${err.message})` };
  }
}

// Worst-case precedence, matching the vocabulary a guardrail body itself
// returns (hookSpecificOutput.permissionDecision): deny beats ask beats
// allow. Any body output this adapter cannot parse is treated as "ask",
// never as a silent allow -- a broken guardrail must not look like no
// guardrail at all.
const RANK = { allow: 0, ask: 1, deny: 2 };

function consultGuardrailBody(hook, payload) {
  try {
    const result = spawnSync("node", [hook.file], {
      input: payload,
      encoding: "utf8",
      timeout: Math.max(1, Number(hook.timeout) || 5) * 1000,
    });
    if (result.error || result.status !== 0) {
      const detail = result.error ? result.error.message : `exit status ${result.status}`;
      return { decision: "ask", reason: `nexgen-guardrail: guardrail body exited abnormally (${detail})` };
    }
    const parsed = JSON.parse(result.stdout);
    const decision = parsed && parsed.hookSpecificOutput && parsed.hookSpecificOutput.permissionDecision;
    if (decision === "allow" || decision === "deny" || decision === "ask") {
      const reason = parsed.hookSpecificOutput.permissionDecisionReason || parsed.reason || "";
      return { decision, reason };
    }
    return { decision: "ask", reason: "nexgen-guardrail: guardrail body returned no usable permissionDecision" };
  } catch (err) {
    return { decision: "ask", reason: `nexgen-guardrail: adapter could not read the guardrail body's output (${err.message})` };
  }
}

// The handler is registered UNCONDITIONALLY, and the sidecar is read inside
// it rather than here. OpenCode calls this factory once, when the plugin
// loads: reading the configuration at that moment and returning {} when it
// was not there yet meant a session started before agent-sync installed the
// guardrail had no permission handler at all, for its entire life -- while
// the no-prompt posture was already on disk. The user believed they were
// covered and were not, which is worse than having no guardrail at all.
export default async ({ directory }) => {
  return {
    "permission.ask": async (input, output) => {
      if (!input || input.type !== "bash") {
        return;
      }
      const command = input.metadata && input.metadata.command;
      if (typeof command !== "string") {
        return;
      }

      const { hooks, broken } = loadConfiguredHooks();
      if (broken) {
        // Fall back to asking, which is exactly the confirmation the bypass
        // posture removed -- never to allowing.
        output.status = "ask";
        return;
      }
      if (hooks.length === 0) {
        return;
      }

      const payload = JSON.stringify({
        hook_event_name: "PreToolUse",
        tool_name: "Bash",
        tool_input: { command },
        cwd: directory,
        session_id: input.sessionID || null,
      });

      let worst = { decision: "allow", reason: "" };
      for (const hook of hooks) {
        const result = consultGuardrailBody(hook, payload);
        if (RANK[result.decision] > RANK[worst.decision]) {
          worst = result;
        }
      }
      if (worst.decision !== "allow") {
        output.status = worst.decision;
      }
    },
  };
};
