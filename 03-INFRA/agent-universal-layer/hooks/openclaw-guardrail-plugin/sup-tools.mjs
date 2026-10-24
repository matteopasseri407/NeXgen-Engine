// NeXgen Engine -- the supervision, as tools a supervisor agent can drive.
//
// One job: put the engine's own `sup` verbs in reach of the one agent that supervises work,
// as thin wrappers. Nothing here decides anything: each tool turns its arguments into the
// argv a person would have typed and returns the lines the engine printed, so the tool, the
// chat and the terminal cannot disagree about what happened.
//
// Read-only tools are registered like every other: any agent may look. The tools that change
// state are registered `optional`, which in OpenClaw means "not exposed until an operator
// allowlists this exact name"; `nexgen openclaw setup` writes that allowlist on the
// supervisor agent (`capo`) and on no other, through `agents.entries.<id>.tools.alsoAllow`.
// The engine's guardrail still sees every one of these calls on `before_tool_call`, like any
// other tool call.
//
// The names below are repeated in three places that must agree: this file, the plugin
// manifest (`contracts.tools` / `toolMetadata`), and `nexgen_core/openclaw/profile.py`, which
// writes the allowlist. `test_nexgen_openclaw_sup_tools.py` fails when they drift.
import { CONFIG_PATH, engineLines } from "./engine-run.mjs";
import { SUP_HINT, SUP_TIMEOUT_MS } from "./sup.mjs";

//: The tools that change state. Optional in the manifest, allowlisted to the supervisor only.
export const SUPERVISOR_TOOLS = [
  "nexgen_sup_start",
  "nexgen_sup_unit",
  "nexgen_sup_run",
  "nexgen_sup_collect",
  "nexgen_sup_review",
  "nexgen_sup_publish",
];

//: The read-only ones: an agent may look at what the engine is doing.
export const READ_TOOLS = ["nexgen_sup_status", "nexgen_sup_costs"];

//: Creating a worktree, replaying commits, sending work into a session: a person waits for
//: these, so the deadline is generous but finite.
export const WRITE_TIMEOUT_MS = 120_000;

//: The collection runs the engine's own gates, which run tests: minutes, not seconds.
export const COLLECT_TIMEOUT_MS = 900_000;

const STRING = (description) => ({ type: "string", description });

const TASK_ID = STRING("The task id, as `sup status` prints it.");
const UNIT = STRING("The unit name inside that task.");

function needs(missing) {
  return `sup: I need ${missing} to do that.`;
}

//: A JSON Schema for the tool's arguments: the host validates against it before the call.
function parameters(properties, required = []) {
  return { type: "object", properties, required, additionalProperties: false };
}

function textResult(text, details) {
  return { content: [{ type: "text", text }], details };
}

function missingArgs(params, names) {
  const missing = names.filter((name) => !String(params?.[name] ?? "").trim());
  return missing.length ? needs(missing.join(", ")) : "";
}

//: The engine's answer, and the argv it answered for: one place turns a tool call into it.
async function callEngine(name, argv, { configPath, spawnFn, timeoutMs }) {
  const text = await engineLines(argv, {
    configPath, spawnFn, timeoutMs, label: "sup", hint: SUP_HINT,
  });
  return textResult(text, { tool: name, argv });
}

//: The arguments a `sup` verb takes for one unit, in the engine's own spelling.
function unitArgs(verb, params) {
  const argv = ["local", "sup", verb, String(params.id).trim(), String(params.unit).trim()];
  for (const pattern of params?.allow ?? []) {
    argv.push("--allow", String(pattern));
  }
  for (const flag of ["verify", "repo", "base", "brief", "branch", "remote"]) {
    if (String(params?.[flag] ?? "").trim()) {
      argv.push(`--${flag}`, String(params[flag]).trim());
    }
  }
  if (String(params?.harness ?? "").trim()) {
    argv.push("--harness", String(params.harness).trim());
  }
  return argv;
}

//: `sup review`: one decision, and the argument that carries it.
function reviewArgs(params) {
  const decision = String(params?.decision ?? "").trim().toLowerCase();
  if (decision === "approve") {
    if (!String(params?.reason ?? "").trim()) {
      return { text: needs("reason (why the unit is approved)") };
    }
    const argv = ["--approve", String(params.reason).trim()];
    if (params?.close) {
      argv.push("--close");
    }
    return { argv };
  }
  if (decision === "rework") {
    if (!String(params?.corrections_file ?? "").trim()) {
      return { text: needs("corrections_file (the corrections to send)") };
    }
    return { argv: ["--rework", String(params.corrections_file).trim()] };
  }
  if (decision === "resend") {
    const argv = ["--resend"];
    if (String(params?.model ?? "").trim()) {
      argv.push("-m", String(params.model).trim());
    }
    return { argv };
  }
  return { text: needs("decision (approve, rework or resend)") };
}

//: A read tool: one `sup` verb, its `--json`, and the id when there is one.
function readTool(name, label, verb, description, options) {
  return {
    name,
    label,
    description,
    parameters: parameters({ id: TASK_ID }),
    async execute(_callId, params) {
      const id = String(params?.id ?? "").trim();
      const argv = ["local", "sup", verb, ...(id ? [id] : []), "--json"];
      return callEngine(name, argv, options);
    },
  };
}

export function supTools({ configPath = CONFIG_PATH, spawnFn } = {}) {
  const engine = (name, argv, timeoutMs) =>
    callEngine(name, argv, { configPath, spawnFn, timeoutMs });
  const reading = { configPath, spawnFn, timeoutMs: SUP_TIMEOUT_MS };

  return [
    readTool(
      "nexgen_sup_status", "NeXgen supervision status", "status",
      "What the engine is supervising: every task with its units and their reviews, or one " +
      "task when an id is given. Read-only.",
      reading,
    ),
    readTool(
      "nexgen_sup_costs", "NeXgen supervision costs", "costi",
      "What the supervised sessions have spent: the 5 hour, 7 day and 30 day windows, and one " +
      "task's cost by role and unit when an id is given. Read-only.",
      reading,
    ),
    {
      name: "nexgen_sup_start",
      label: "NeXgen supervision start",
      description: "Create a task folder with its brief and report templates, and return its id.",
      parameters: parameters({ objective: STRING("The task's objective, in one line.") }, ["objective"]),
      async execute(_callId, params) {
        const missing = missingArgs(params, ["objective"]);
        if (missing) return textResult(missing, { tool: "nexgen_sup_start" });
        return engine("nexgen_sup_start", ["local", "sup", "start", String(params.objective).trim()], WRITE_TIMEOUT_MS);
      },
    },
    {
      name: "nexgen_sup_unit",
      label: "NeXgen supervision unit",
      description:
        "Create the unit's working copy, its local branch and its boundary, from a repository " +
        "checkout and a base ref.",
      parameters: parameters({
        id: TASK_ID,
        unit: UNIT,
        repo: STRING("The checkout the working copy is created from."),
        base: STRING("The ref the unit's branch starts from."),
        brief: STRING("The brief file to copy into the task folder."),
        allow: {
          type: "array",
          items: { type: "string" },
          description: "One path glob the unit may change; repeatable.",
        },
        verify: STRING("The verification command the engine will run again."),
      }, ["id", "unit", "repo", "base", "brief"]),
      async execute(_callId, params) {
        const missing = missingArgs(params, ["id", "unit", "repo", "base", "brief"]);
        if (missing) return textResult(missing, { tool: "nexgen_sup_unit" });
        return engine("nexgen_sup_unit", unitArgs("unit", params), WRITE_TIMEOUT_MS);
      },
    },
    {
      name: "nexgen_sup_run",
      label: "NeXgen supervision run",
      description: "Start the unit's executor: in the task's own session, or in a new window.",
      parameters: parameters({
        id: TASK_ID,
        unit: UNIT,
        harness: STRING("The harness to run it in, when it is not the lane's default."),
      }, ["id", "unit"]),
      async execute(_callId, params) {
        const missing = missingArgs(params, ["id", "unit"]);
        if (missing) return textResult(missing, { tool: "nexgen_sup_run" });
        return engine("nexgen_sup_run", unitArgs("run", params), WRITE_TIMEOUT_MS);
      },
    },
    {
      name: "nexgen_sup_collect",
      label: "NeXgen supervision collect",
      description:
        "Run the engine's gates on the unit, write the review package, and say the verdict. " +
        "Slow: it runs the unit's own tests.",
      parameters: parameters({ id: TASK_ID, unit: UNIT }, ["id", "unit"]),
      async execute(_callId, params) {
        const missing = missingArgs(params, ["id", "unit"]);
        if (missing) return textResult(missing, { tool: "nexgen_sup_collect" });
        return engine("nexgen_sup_collect", unitArgs("collect", params), COLLECT_TIMEOUT_MS);
      },
    },
    {
      name: "nexgen_sup_review",
      label: "NeXgen supervision review",
      description:
        "The review round: approve the collected unit with a reason, send corrections back to " +
        "its executor, or resend the last round that was not delivered.",
      parameters: parameters({
        id: TASK_ID,
        unit: UNIT,
        decision: {
          type: "string",
          enum: ["approve", "rework", "resend"],
          description: "What this round decides.",
        },
        reason: STRING("With approve: why the unit is approved."),
        corrections_file: STRING("With rework: the file of corrections to send to the executor."),
        model: STRING("With resend: the model for this delivery, when the provider refused the configured one."),
        close: {
          type: "boolean",
          description: "With approve: retire the copy and branch of a unit that will not be published.",
        },
      }, ["id", "unit", "decision"]),
      async execute(_callId, params) {
        const missing = missingArgs(params, ["id", "unit"]);
        if (missing) return textResult(missing, { tool: "nexgen_sup_review" });
        const decision = reviewArgs(params);
        if (decision.text) return textResult(decision.text, { tool: "nexgen_sup_review" });
        const argv = ["local", "sup", "review", String(params.id).trim(), String(params.unit).trim(), ...decision.argv];
        return engine("nexgen_sup_review", argv, WRITE_TIMEOUT_MS);
      },
    },
    {
      name: "nexgen_sup_publish",
      label: "NeXgen supervision publish",
      description:
        "Replay the approved commits onto the owner's branch, never by force, and optionally " +
        "push once with the command the owner declared.",
      parameters: parameters({
        id: TASK_ID,
        unit: UNIT,
        repo: STRING("The owner's checkout the commits are replayed into."),
        branch: STRING("The branch of that checkout the commits land on."),
        remote: STRING("The remote to ask about and to push to (default: origin)."),
        push: { type: "boolean", description: "Push the branch forward after the replay." },
        push_with: STRING("Push with the command the owner declared, instead of a plain push."),
      }, ["id", "unit", "repo", "branch"]),
      async execute(_callId, params) {
        const missing = missingArgs(params, ["id", "unit", "repo", "branch"]);
        if (missing) return textResult(missing, { tool: "nexgen_sup_publish" });
        const argv = unitArgs("publish", params);
        if (params?.push) {
          argv.push("--push");
        } else if (String(params?.push_with ?? "").trim()) {
          argv.push("--push-with", String(params.push_with).trim());
        }
        return engine("nexgen_sup_publish", argv, WRITE_TIMEOUT_MS);
      },
    },
  ];
}

export function registerSupTools(api, { configPath = CONFIG_PATH, spawnFn } = {}) {
  // An OpenClaw older than its tool surface: nothing to register, nothing to break.
  if (typeof api.registerTool !== "function") {
    return;
  }
  for (const tool of supTools({ configPath, spawnFn })) {
    // A read-only tool is always available; a tool that changes state waits for the
    // operator's allowlist, which setup writes for the supervisor agent alone.
    api.registerTool(tool, READ_TOOLS.includes(tool.name) ? undefined : { name: tool.name, optional: true });
  }
}
