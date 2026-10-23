// NeXgen Engine -- OpenClaw guardrail plugin (mechanism only, no policy).
//
// Deployed byte-for-byte by the guard's permissions phase into
// `<state dir>/extensions/nexgen-guardrail/`, next to `nexgen-guardrail-core.mjs`
// and the sidecar `nexgen-guardrail.config.json`. OpenClaw discovers every plugin
// directory under its state `extensions/`; ENABLING it is a `plugins.*` change and
// happens with the gateway stopped (`nexgen openclaw setup`), because editing that
// block under a running gateway breaks it. This file never varies between
// installs: what does vary lives in the sidecar, read fresh on every call.
//
// Contract: each configured guardrail body is a Node script speaking the SAME
// stdin/stdout JSON shape Claude Code's own PreToolUse hooks use. One guardrail
// body, several thin CLI adapters, never duplicated dangerous-command logic.
// See the siblings claude-guardrail-adapter.mjs, antigravity-guardrail-adapter.mjs
// and opencode-guardrail-plugin.mjs.
//
// What OpenClaw 2026.10.6 gives this plugin (docs/plugins/hooks, tool-policy):
//   - `api.on("before_tool_call", handler)`; the handler returns
//     `{ block, blockReason }`, `{ params }` or `{ requireApproval }`, and may be
//     async. `block: true` is terminal.
//   - `before_tool_call` is a GATE: a thrown handler or a timeout applies
//     "fail closed", i.e. the tool call is blocked. This file therefore never
//     wraps the decision in a catch that could turn a fault into an allow.
//   - `matcher` accepts only canonical tool ids (empty, wildcard and alias values
//     are invalid), so nothing is registered with one: a matcher the host rejects
//     would leave the guardrail unregistered, and an unregistered guardrail is
//     exactly the silent failure this file exists to prevent. The handler filters
//     by tool name instead, which costs one string comparison per call.
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const CONFIG_PATH = join(dirname(fileURLToPath(import.meta.url)), "nexgen-guardrail.config.json");

// The documented OpenClaw surfaces that execute something: `exec` is the shell,
// `process` owns a running session, `terminal` types into an operator terminal,
// `code_execution` runs provider-backed Python. Only a value that IS a command
// line is judged: Python source is not a shell line, and matching it against the
// body's shell patterns would hit by accident rather than by meaning.
export const SHELL_TOOLS = new Set(["exec", "process", "terminal", "code_execution"]);

export function commandOfTool(toolName, params) {
  if (!SHELL_TOOLS.has(toolName) || !params || typeof params !== "object") {
    return null;
  }
  for (const key of ["command", "cmd"]) {
    const value = params[key];
    if (typeof value === "string" && value.trim() !== "") {
      return value;
    }
  }
  return null;
}

// One consultation of the guardrail body. The sidecar and the core are read per
// call, not at load: a core that goes missing blocks each command with a reason
// instead of leaving the session with no guardrail at all, and a guard that
// rewrites the policy after the gateway started still takes effect immediately.
async function judge(command, directory, sessionID) {
  let core;
  try {
    core = await import("./nexgen-guardrail-core.mjs");
  } catch (err) {
    // Nothing else can be consulted: block, and say why. Never an allow.
    return { decision: "deny", reason: `nexgen-guardrail: the guardrail core could not load (${err && err.message})` };
  }
  const sidecar = core.loadSidecar(CONFIG_PATH);
  if (sidecar.broken) {
    return { ...core.closed(sidecar.broken, sidecar.strict), autoAllow: false };
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
  const worst = core.worstOf(sidecar.hooks, payload, sidecar.strict);
  core.recordAudit(sidecar.auditFile, "openclaw", worst.decision);
  return { ...worst, autoAllow: Boolean(sidecar.autoAllow) };
}

function approvalFor(reason) {
  return {
    requireApproval: {
      title: "Run command",
      description: reason || "The NeXgen guardrail wants this command approved before it runs.",
      severity: "warning",
      timeoutMs: 60_000,
      allowedDecisions: ["allow-once", "deny"],
    },
  };
}

export default {
  id: "nexgen-guardrail",
  name: "NeXgen Guardrail",
  description: "Runs the NeXgen guardrail before a tool call that executes a command.",
  register(api) {
    api.on("before_tool_call", async (event, ctx) => {
      const command = commandOfTool(event?.toolName, event?.params);
      if (command === null) {
        return; // nothing to judge: the tool carries no command line
      }
      const workdir = typeof event?.params?.workdir === "string" ? event.params.workdir : undefined;
      const verdict = await judge(command, workdir, ctx?.sessionId ?? null);
      if (verdict.decision === "deny") {
        return { block: true, blockReason: verdict.reason || "blocked by the NeXgen guardrail" };
      }
      if (verdict.decision === "ask" && !verdict.autoAllow) {
        // "Ask" is a person's decision, not the engine's: under an `ask` posture
        // the person asked to be asked. Without an approval surface OpenClaw
        // denies a pending request rather than running it.
        return approvalFor(verdict.reason);
      }
      return;
    });
  },
};
