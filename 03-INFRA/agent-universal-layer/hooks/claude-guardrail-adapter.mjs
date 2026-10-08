// NeXgen Engine -- Claude Code guardrail adapter (mechanism only, no policy).
//
// Claude speaks the guardrail body's own JSON, so this adapter does not translate
// anything. It exists because a hook registered directly fails OPEN: Claude treats any
// exit code other than 2, and any timeout, as a non-blocking error and runs the command.
// Under bypassPermissions, the one posture where the guardrail is the only brake, a body
// that crashed, was missing, or hung let every command through while the registration
// looked perfectly healthy. Here the body runs in a subprocess with its own timeout
// (shorter than the one Claude enforces on this adapter), every abnormal outcome becomes
// an explicit decision, and an uncaught error in this file itself exits 2, which blocks.
//
// Registered as the `command` of the PreToolUse hook (matcher "Bash") in
// ~/.claude/settings.json. The body and its timeout come from the sidecar
// `nexgen-guardrail.config.json` next to this file, read fresh on every call.
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

function emit(result) {
  if (result.decision === "allow") {
    return; // exit 0 with no output is how a Claude hook permits the tool
  }
  process.stdout.write(JSON.stringify({
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: result.decision,
      permissionDecisionReason: result.reason,
    },
  }));
}

function main(core) {
  const { closed, loadSidecar, recordAudit, worstOf } = core;
  const sidecar = loadSidecar(CONFIG_PATH);
  const raw = readStdin();

  let parsed = null;
  try {
    parsed = JSON.parse(raw);
  } catch {
    parsed = null;
  }
  // The mode Claude is actually running in beats the posture the engine last wrote:
  // `--dangerously-skip-permissions` needs no settings change.
  const strict = sidecar.strict || (parsed && parsed.permission_mode === "bypassPermissions");

  if (sidecar.broken) {
    emit(closed(`${sidecar.broken}; refusing to run unchecked`, strict));
    return;
  }
  if (sidecar.hooks.length === 0) {
    emit({ decision: "allow" });
    return;
  }
  if (parsed === null) {
    emit(closed("could not parse Claude's own PreToolUse input", strict));
    return;
  }

  const worst = worstOf(sidecar.hooks, raw, strict);
  recordAudit(sidecar.auditFile, "claude", worst.decision);
  emit(worst);
}

try {
  // Imported here, not at the top: a core file that is missing or truncated must end in the
  // handler below. A failed static import exits 1 before any of this runs, and 1 lets the
  // command through.
  main(await import("./nexgen-guardrail-core.mjs"));
} catch (err) {
  // Exit 2 is the one code that blocks. Any other would let the command run.
  process.stderr.write(`nexgen-guardrail: adapter failed (${err && err.message}); blocking\n`);
  process.exit(2);
}
