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
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const CONFIG_PATH = join(dirname(fileURLToPath(import.meta.url)), "nexgen-guardrail.config.json");

// Two outcomes that must never collapse into one: "no guardrail is
// configured here" (legitimate, allow) and "a guardrail IS configured and
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

function readStdin() {
  try {
    return readFileSync(0, "utf8");
  } catch {
    return "";
  }
}

// Same worst-case precedence as the OpenCode adapter: deny beats ask beats
// allow, and anything unparseable is treated as "ask", never a silent allow.
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
    // Silence IS the answer, and it is the common one. A Claude PreToolUse
    // hook that permits the tool exits 0 and writes nothing -- it only speaks
    // up to deny or to ask. Treating an empty stdout as unparseable made
    // every ordinary command fall back to "ask", which quietly cancelled the
    // very posture this adapter exists to make safe.
    if (result.stdout.trim() === "") {
      return { decision: "allow", reason: "" };
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

function main() {
  const { hooks, broken } = loadConfiguredHooks();
  if (broken) {
    // Fall back to asking, which is exactly the confirmation the bypass
    // posture removed -- never to allowing, which would look identical to a
    // machine that was never meant to have a guardrail.
    process.stdout.write(JSON.stringify({
      decision: "ask",
      reason: `nexgen-guardrail: ${broken}; refusing to run unchecked`,
    }));
    return;
  }
  if (hooks.length === 0) {
    process.stdout.write(JSON.stringify({ decision: "allow" }));
    return;
  }

  let raw;
  try {
    raw = JSON.parse(readStdin());
  } catch {
    process.stdout.write(JSON.stringify({
      decision: "ask",
      reason: "nexgen-guardrail: could not parse Antigravity's own PreToolUse input",
    }));
    return;
  }

  const command = raw && raw.toolCall && raw.toolCall.args && raw.toolCall.args.CommandLine;
  if (typeof command !== "string") {
    process.stdout.write(JSON.stringify({ decision: "allow" }));
    return;
  }

  const payload = JSON.stringify({
    hook_event_name: "PreToolUse",
    tool_name: "Bash",
    tool_input: { command },
    cwd: Array.isArray(raw.workspacePaths) ? raw.workspacePaths[0] : undefined,
    session_id: raw.conversationId || null,
  });

  let worst = { decision: "allow", reason: "" };
  for (const hook of hooks) {
    const result = consultGuardrailBody(hook, payload);
    if (RANK[result.decision] > RANK[worst.decision]) {
      worst = result;
    }
  }
  process.stdout.write(JSON.stringify(worst.decision === "allow" ? { decision: "allow" } : worst));
}

main();
