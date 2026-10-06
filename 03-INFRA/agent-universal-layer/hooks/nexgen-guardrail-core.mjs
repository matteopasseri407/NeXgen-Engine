// NeXgen Engine -- shared core of the guardrail adapters (mechanism only, no policy).
//
// One guardrail body (private policy, a Node script that speaks Claude Code's own
// PreToolUse stdin/stdout JSON) and thin per-CLI adapters. Everything the adapters
// have in common lives here, so "a broken guardrail must never look like no guardrail"
// is written once instead of once per CLI (it used to be copied into two files, and
// Claude, which had no adapter, did not have it at all).
//
// Deployed byte-for-byte next to each adapter by the engine; never varies per install.
// What varies lives in the sidecar `nexgen-guardrail.config.json`, read fresh on every
// call: { hooks: [{file, timeout}], strict: bool, autoAllow: bool, auditFile: string }.
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { dirname } from "node:path";

export const RANK = { allow: 0, ask: 1, deny: 2 };

// Two outcomes that must never collapse into one: "no guardrail is configured here"
// (legitimate: allow) and "a guardrail IS configured and this cannot use it" (broken:
// must not read as absent). A corrupt sidecar used to fail OPEN in a posture whose whole
// point is that the guardrail is the only brake left.
export function loadSidecar(configPath) {
  const none = { hooks: [], broken: null, strict: false, autoAllow: false, auditFile: null };
  let raw;
  try {
    raw = readFileSync(configPath, "utf8");
  } catch (err) {
    if (err && err.code === "ENOENT") {
      return none;
    }
    return { ...none, broken: `sidecar unreadable (${err.message})` };
  }
  try {
    const parsed = JSON.parse(raw);
    if (!parsed || !Array.isArray(parsed.hooks)) {
      return { ...none, broken: "sidecar has no hooks array" };
    }
    return {
      hooks: parsed.hooks,
      broken: null,
      strict: parsed.strict === true,
      autoAllow: parsed.autoAllow === true,
      auditFile: typeof parsed.auditFile === "string" ? parsed.auditFile : null,
    };
  } catch (err) {
    return { ...none, broken: `sidecar is not valid JSON (${err.message})` };
  }
}

// What a broken guardrail answers. Where the guardrail is the only brake (the bypass
// posture, where nothing else would prompt) the answer is "deny": an "ask" there may be
// skipped by the very mode that makes the guardrail necessary. Elsewhere "ask" keeps a
// person in the loop without blocking the machine on a fault they can repair.
export function closed(reason, strict) {
  return { decision: strict ? "deny" : "ask", reason: `nexgen-guardrail: ${reason}` };
}

export function consultGuardrailBody(hook, payloadText, strict) {
  try {
    const result = spawnSync(process.execPath, [hook.file], {
      input: payloadText,
      encoding: "utf8",
      timeout: Math.max(1, Number(hook.timeout) || 5) * 1000,
    });
    if (result.error || result.status !== 0) {
      const detail = result.error ? result.error.message : `exit status ${result.status}`;
      return closed(`guardrail body exited abnormally (${detail})`, strict);
    }
    // Silence IS the answer, and it is the common one: a Claude PreToolUse hook that
    // permits the tool exits 0 and writes nothing, speaking up only to deny or to ask.
    if (result.stdout.trim() === "") {
      return { decision: "allow", reason: "" };
    }
    const parsed = JSON.parse(result.stdout);
    const decision = parsed && parsed.hookSpecificOutput && parsed.hookSpecificOutput.permissionDecision;
    if (decision === "allow" || decision === "deny" || decision === "ask") {
      const reason = parsed.hookSpecificOutput.permissionDecisionReason || parsed.reason || "";
      return { decision, reason };
    }
    return closed("guardrail body returned no usable permissionDecision", strict);
  } catch (err) {
    return closed(`could not read the guardrail body's output (${err.message})`, strict);
  }
}

// Worst case wins: deny beats ask beats allow.
export function worstOf(hooks, payloadText, strict) {
  let worst = { decision: "allow", reason: "" };
  for (const hook of hooks) {
    const result = consultGuardrailBody(hook, payloadText, strict);
    if (RANK[result.decision] > RANK[worst.decision]) {
      worst = result;
    }
  }
  return worst;
}

// One small file per CLI, overwritten each time: how often and when the guardrail was
// last consulted. It is what lets the doctor tell "this CLI really calls the guardrail"
// from "it is registered and nothing ever reaches it", which a registration alone cannot.
// Best effort: recording must never be able to change a decision.
export function recordAudit(auditFile, cli, decision) {
  if (!auditFile) {
    return;
  }
  try {
    let count = 0;
    try {
      count = Number(JSON.parse(readFileSync(auditFile, "utf8")).count) || 0;
    } catch {
      count = 0;
    }
    mkdirSync(dirname(auditFile), { recursive: true });
    writeFileSync(auditFile, JSON.stringify({ cli, at: Date.now(), count: count + 1, last: decision }) + "\n");
  } catch {
    // never let bookkeeping fail a command
  }
}
