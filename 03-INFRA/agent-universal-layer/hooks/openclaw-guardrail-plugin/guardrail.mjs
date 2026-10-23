// NeXgen Engine -- OpenClaw guardrail job (mechanism only, no policy).
//
// One of the three jobs of the `nexgen` plugin, registered by `index.mjs`. Deployed by the
// guard's permissions phase into `<state dir>/extensions/nexgen/`, next to the shared core
// `nexgen-guardrail-core.mjs` and the sidecar `nexgen-guardrail.config.json`. OpenClaw discovers
// every plugin directory under its state `extensions/`; ENABLING it is a `plugins.*` change and
// happens with the gateway stopped (`nexgen openclaw setup`), because editing that block under a
// running gateway breaks it. This file never varies between installs: what does vary lives in
// the sidecar and in the engine-owned `nexgen.config.json`, both read fresh on every call.
//
// Contract: each configured guardrail body is a Node script speaking the SAME stdin/stdout JSON
// shape Claude Code's own PreToolUse hooks use. One guardrail body, several thin CLI adapters,
// never duplicated dangerous-command logic. See the siblings claude-guardrail-adapter.mjs,
// antigravity-guardrail-adapter.mjs and opencode-guardrail-plugin.mjs.
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
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { readPluginConfig } from "./engine-config.mjs";

const DIR = dirname(fileURLToPath(import.meta.url));
const SIDECAR_PATH = join(DIR, "nexgen-guardrail.config.json");
const CONFIG_PATH = join(DIR, "nexgen.config.json");

// The documented OpenClaw surfaces that execute something: `exec` is the shell,
// `process` owns a running session, `terminal` types into an operator terminal,
// `code_execution` runs provider-backed Python. Only a value that IS a command
// line is judged: Python source is not a shell line, and matching it against the
// body's shell patterns would hit by accident rather than by meaning.
export const SHELL_TOOLS = new Set(["exec", "process", "terminal", "code_execution"]);

// The tools that change files. `apply_patch` keeps its targets in a patch envelope;
// the tool name is what decides here, never a path parsed out of a payload.
export const FILE_TOOLS = new Set(["write", "edit", "apply_patch"]);

// The neutral posture vocabulary, exactly as the private permission manifest speaks it.
// The engine translates nothing for OpenClaw: the plugin speaks the neutral words and
// renders them here, so `openclaw.json` (OpenClaw's own file) is never touched.
export const POSTURES = ["bypass", "accept-edits", "ask"];
export const DEFAULT_POSTURE = "bypass";

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

// The posture the engine last wrote, read per call: a changed manifest takes effect on the
// next command, no gateway restart. An unreadable or absent file is the old behaviour
// (`bypass`, the guardrail decides), never a silent looser posture.
export function readPosture(configPath = CONFIG_PATH) {
  const posture = readPluginConfig(configPath).posture;
  return typeof posture === "string" && POSTURES.includes(posture) ? posture : DEFAULT_POSTURE;
}

// One consultation of the guardrail body. The sidecar and the core are read per
// call, not at load: a core that goes missing blocks each command with a reason
// instead of leaving the session with no guardrail at all, and a guard that
// rewrites the policy after the gateway started still takes effect immediately.
async function judge(command, directory, sessionID, sidecarPath) {
  let core;
  try {
    core = await import("./nexgen-guardrail-core.mjs");
  } catch (err) {
    // Nothing else can be consulted: block, and say why. Never an allow.
    return { decision: "deny", reason: `nexgen-guardrail: the guardrail core could not load (${err && err.message})` };
  }
  const sidecar = core.loadSidecar(sidecarPath);
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

export function approvalFor(reason, title = "Run command") {
  return {
    requireApproval: {
      title,
      description: reason || "The NeXgen guardrail wants this approved before it runs.",
      severity: "warning",
      timeoutMs: 60_000,
      allowedDecisions: ["allow-once", "deny"],
    },
  };
}

function postureReason(posture, verdict) {
  if (verdict.reason) {
    return verdict.reason;
  }
  return posture === "ask"
    ? "The 'ask' posture has OpenClaw confirm every command before it runs."
    : "The 'accept-edits' posture still confirms shell commands the guardrail permits.";
}

// The decision table, in one place so the three postures stay readable:
//   bypass       the guardrail decides alone: what the body permits runs, what it denies blocks,
//                and a body that asks still asks (its author wanted a person).
//   accept-edits like bypass for file writes; a shell command the body allows still asks.
//   ask          every shell command asks, and a tool that changes files asks too.
export function decideFor(posture, verdict) {
  if (verdict.decision === "deny") {
    return { block: true, blockReason: verdict.reason || "blocked by the NeXgen guardrail" };
  }
  if (verdict.decision === "ask") {
    // Never loosens a stricter decision: a person's question stays a question.
    return verdict.autoAllow ? undefined : approvalFor(verdict.reason);
  }
  if (posture === "bypass") {
    return undefined;
  }
  return approvalFor(postureReason(posture, verdict));
}

export function registerGuardrail(api, { sidecarPath = SIDECAR_PATH, configPath = CONFIG_PATH } = {}) {
  api.on("before_tool_call", async (event, ctx) => {
    const posture = readPosture(configPath);
    const command = commandOfTool(event?.toolName, event?.params);
    if (command === null) {
      // No command line to judge. Under `ask` a tool that changes files asks as well: the
      // posture is the CLI's own prompt, which OpenClaw has no other way to express.
      return posture === "ask" && FILE_TOOLS.has(event?.toolName)
        ? approvalFor("The 'ask' posture asks before a tool changes files.", "Change files")
        : undefined;
    }
    const workdir = typeof event?.params?.workdir === "string" ? event.params.workdir : undefined;
    const verdict = await judge(command, workdir, ctx?.sessionId ?? null, sidecarPath);
    return decideFor(posture, verdict);
  });
}
