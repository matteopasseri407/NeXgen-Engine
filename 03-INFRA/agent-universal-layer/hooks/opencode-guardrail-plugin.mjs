// NeXgen Engine -- OpenCode guardrail adapter (mechanism only, no policy).
//
// Deployed byte-for-byte by the guard's permissions phase into the OpenCode config
// directory, next to `nexgen-guardrail-core.mjs`, and registered in that CLI's own
// `plugins` array. This file never varies between installs; everything that DOES vary
// lives in the sidecar `nexgen-guardrail.config.json` written next to it, read fresh on
// every `permission.ask` call (so a changed manifest takes effect on the next permission
// check, no OpenCode restart required).
//
// Contract: each configured guardrail body is a Node script speaking the SAME
// stdin/stdout JSON shape Claude Code's own PreToolUse hooks use. One guardrail body,
// several thin CLI adapters, never duplicated dangerous-command logic. See
// claude-guardrail-adapter.mjs and antigravity-guardrail-adapter.mjs for the siblings.
//
// Scope: OpenCode's `permission.ask` hook, which is called when OpenCode is about to ASK
// about an action, for a shell command. That has a consequence the engine's posture
// rendering is built around: an action whose rule says `allow` is never asked about, so
// this hook never sees it. A posture that allows shell outright would therefore leave this
// adapter registered and unreachable. So under the bypass posture the engine writes the
// shell rule as `ask` and sets `autoAllow` in the sidecar: this adapter then answers `allow`
// for what the guardrail body permits, which keeps the no-prompt behaviour, and answers
// `ask` or `deny` for the rest. If OpenCode stopped calling the hook, the failure is
// visible (every command prompts) instead of silent.
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const CONFIG_PATH = join(dirname(fileURLToPath(import.meta.url)), "nexgen-guardrail.config.json");

// The shell action was called "bash" in V1 and is "shell" in V2 (see runtimes/opencode.py).
// What makes a request a command is that it carries one, so that is what is read; the type
// name is not trusted to stay put across versions.
export function commandOf(input) {
  const command = input && input.metadata && input.metadata.command;
  return typeof command === "string" ? command : null;
}

// The handler is registered UNCONDITIONALLY, and the sidecar is read inside it rather
// than at load. OpenCode calls this factory once, when the plugin loads: reading the
// configuration at that moment and returning {} when it was not there yet meant a session
// started before the guard installed the guardrail had no permission handler for its whole
// life, while the no-prompt posture was already on disk.
export default async ({ directory }) => {
  return {
    "permission.ask": async (input, output) => {
      const command = commandOf(input);
      if (command === null) {
        return;
      }
      let strict = false;
      try {
        // Per call, not at load: a core file that is missing makes the plugin fail closed
        // on each command instead of failing to load and leaving none.
        const { closed, loadSidecar, recordAudit, worstOf } = await import("./nexgen-guardrail-core.mjs");
        const sidecar = loadSidecar(CONFIG_PATH);
        strict = sidecar.strict;
        if (sidecar.broken) {
          output.status = closed(sidecar.broken, strict).decision;
          return;
        }
        if (sidecar.hooks.length === 0) {
          return;
        }
        const payload = JSON.stringify({
          hook_event_name: "PreToolUse",
          tool_name: "Bash",
          tool_input: { command },
          cwd: directory,
          session_id: (input && input.sessionID) || null,
        });
        const worst = worstOf(sidecar.hooks, payload, strict);
        recordAudit(sidecar.auditFile, "opencode", worst.decision);
        if (worst.decision === "allow") {
          // Only the engine's own mediated-bypass rendering opts into answering for
          // OpenCode: under an `ask` posture the person asked to be asked.
          if (sidecar.autoAllow) {
            output.status = "allow";
          }
          return;
        }
        output.status = worst.decision;
      } catch (err) {
        void err; // the core itself may be what failed: answer without it
        output.status = strict ? "deny" : "ask";
      }
    },
  };
};
