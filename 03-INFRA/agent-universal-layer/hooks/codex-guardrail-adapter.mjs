// NeXgen Engine -- Codex guardrail adapter (mechanism only, no policy).
//
// Registered as the `command` of a `PreToolUse` hook (matcher "Bash") in Codex's own
// `~/.codex/hooks.json`. This file never varies between installs; what does vary -- which
// guardrail body to run and its timeout -- lives in the sidecar
// `nexgen-guardrail.config.json` deployed next to it, read fresh on every call.
//
// Contract: the guardrail body is a Node script speaking the SAME stdin/stdout JSON shape
// Claude Code's own PreToolUse hooks use. Codex's PreToolUse input is that shape already
// ({hook_event_name, tool_name, tool_input.command, cwd, permission_mode}), so the body is
// handed the payload untouched; only the ANSWER is translated, and two documented differences
// from Claude decide how (developers.openai.com/codex/hooks, "PreToolUse"):
//
//   - Codex has no "ask" at PreToolUse: `permissionDecision: "ask"` is parsed, the hook run is
//     reported as failed, and the tool RUNS anyway. A question the body wants a person to answer
//     is therefore a refusal here, carrying the body's reason: the person can run the thing
//     themselves, and nothing is executed while the question is open.
//   - A PreToolUse hook that errors, times out or answers with something unusable lets the tool
//     run. That is why the body runs in a subprocess with its own timeout (shorter than the one
//     Codex enforces on this adapter), why every abnormal outcome becomes an explicit deny, and
//     why an error in this file itself exits 2 -- the one exit code Codex treats as a block.
//
// The core is always asked for its closed answer (`strict`), because Codex has no softer answer
// to give. Never varies per posture: the posture is Codex's own setting and this adapter never
// reads or writes it.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const CONFIG_PATH = join(dirname(fileURLToPath(import.meta.url)), "nexgen-guardrail.config.json");

const NO_QUESTION = " (Codex cannot ask before a tool call, so this is refused: run it yourself if you really want it)";

function readStdin() {
  try {
    return readFileSync(0, "utf8");
  } catch {
    return "";
  }
}

// Codex reads a deny from `hookSpecificOutput.permissionDecision`; exit 0 with no output is how
// a hook permits the tool. An "ask" cannot be expressed, so it leaves as a deny that says why.
function emit(result) {
  if (result.decision === "allow") {
    return;
  }
  const reason = result.reason || "blocked by the NeXgen guardrail";
  process.stdout.write(JSON.stringify({
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "deny",
      permissionDecisionReason: result.decision === "ask" ? reason + NO_QUESTION : reason,
    },
  }));
}

function main(core) {
  const { closed, loadSidecar, recordAudit, worstOf } = core;
  const sidecar = loadSidecar(CONFIG_PATH);

  if (sidecar.missing) {
    // This adapter only exists where the guard deposited it together with its sidecar: an absent
    // file is a broken install, not "no guardrail here". A sidecar that IS present and lists no
    // hooks stays a legitimate allow.
    emit(closed(`no guardrail sidecar at ${CONFIG_PATH}`, true));
    return;
  }
  if (sidecar.broken) {
    emit(closed(`${sidecar.broken}; refusing to run unchecked`, true));
    return;
  }
  if (sidecar.hooks.length === 0) {
    emit({ decision: "allow" });
    return;
  }

  const raw = readStdin();
  try {
    JSON.parse(raw);
  } catch {
    emit(closed("could not parse Codex's own PreToolUse input", true));
    return;
  }

  const worst = worstOf(sidecar.hooks, raw, true);
  recordAudit(sidecar.auditFile, "codex", worst.decision);
  emit(worst);
}

try {
  // Imported here, not at the top: a core file that is missing or truncated must end in the
  // handler below. A failed static import exits 1 before any of this runs, and 1 lets the
  // tool through.
  main(await import("./nexgen-guardrail-core.mjs"));
} catch (err) {
  // Exit 2 is the one code Codex reads as a block. Any other would let the tool run.
  process.stderr.write(`nexgen-guardrail: adapter failed (${err && err.message}); blocking\n`);
  process.exit(2);
}
