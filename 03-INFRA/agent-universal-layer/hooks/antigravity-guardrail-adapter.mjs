// NeXgen Engine -- Antigravity guardrail adapter (mechanism only, no policy).
//
// Deployed byte-for-byte by agent_sync.py's claude_permissions phase and
// registered as the `command` of a `PreToolUse` hook (matcher "run_command")
// in Antigravity's own global `~/.gemini/config/hooks.json`. This file never
// varies between installs; everything that DOES vary -- which guardrail
// body file(s) to run, and their per-hook timeout -- lives in the sidecar
// `nexgen-guardrail.config.json` deployed next to it, read fresh on every
// invocation (so a changed manifest takes effect on the next command, no
// Antigravity restart required).
//
// Contract: each configured guardrail body is a Node script speaking the
// SAME stdin/stdout JSON shape Claude Code's own PreToolUse hooks use --
// stdin: {hook_event_name, tool_name, tool_input, cwd, session_id, ...};
// stdout: {hookSpecificOutput: {permissionDecision, permissionDecisionReason}}.
// One guardrail body, several thin CLI adapters, never duplicated
// dangerous-command logic. See opencode-guardrail-plugin.mjs for the
// sibling translation.
//
// Antigravity's own documented PreToolUse contract (the product's own
// bundled reference, not reverse-engineered): stdin is a JSON object
// including {toolCall: {name, args: {CommandLine}}, workspacePaths,
// conversationId, ...}; stdout is {decision: "allow"|"deny"|"ask", reason}.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const CONFIG_PATH = join(dirname(fileURLToPath(import.meta.url)), "nexgen-guardrail.config.json");

function readStdin() {
  try {
    return readFileSync(0, "utf8");
  } catch {
    return "";
  }
}

// Antigravity's own contract answers with {decision, reason}, not Claude's JSON.
function emit(result) {
  process.stdout.write(JSON.stringify(result.decision === "allow" ? { decision: "allow" } : result));
}

function main(core) {
  const { closed, loadSidecar, recordAudit, worstOf } = core;
  const sidecar = loadSidecar(CONFIG_PATH);
  const { hooks, strict } = sidecar;
  if (sidecar.broken) {
    // Back to asking (or denying, where the guardrail is the only brake), never to
    // allowing, which would look identical to a machine that was never meant to have one.
    emit(closed(`${sidecar.broken}; refusing to run unchecked`, strict));
    return;
  }
  if (hooks.length === 0) {
    emit({ decision: "allow" });
    return;
  }

  let raw;
  try {
    raw = JSON.parse(readStdin());
  } catch {
    emit(closed("could not parse Antigravity's own PreToolUse input", strict));
    return;
  }

  const command = raw && raw.toolCall && raw.toolCall.args && raw.toolCall.args.CommandLine;
  if (typeof command !== "string") {
    // Fail closed: an unexpected PreToolUse shape (different tool, version drift) must
    // not silently allow what no guardrail body ever saw.
    emit(closed("PreToolUse input has no CommandLine string; not allowing it unchecked", strict));
    return;
  }

  const payload = JSON.stringify({
    hook_event_name: "PreToolUse",
    tool_name: "Bash",
    tool_input: { command },
    cwd: Array.isArray(raw.workspacePaths) ? raw.workspacePaths[0] : undefined,
    session_id: raw.conversationId || null,
  });

  const worst = worstOf(hooks, payload, strict);
  recordAudit(sidecar.auditFile, "antigravity", worst.decision);
  emit(worst);
}

try {
  main(await import("./nexgen-guardrail-core.mjs")); // inside the guard: a missing core must answer too
} catch (err) {
  // An adapter that throws must not read as "no answer": answer "deny" in the contract's own words.
  process.stdout.write(JSON.stringify({ decision: "deny", reason: `nexgen-guardrail: adapter failed (${err && err.message})` }));
}
