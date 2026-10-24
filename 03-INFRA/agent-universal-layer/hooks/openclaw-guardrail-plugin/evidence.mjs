// nexgen: one-job reason="the evidence check for a local model: the verdict rules, the turn's receipts, and the hooks that feed them"
// NeXgen Engine -- OpenClaw evidence job: a local model's answer must stand on its own receipts.
//
// One of the jobs of the `nexgen` plugin, registered by `index.mjs`. The old local lane had this
// check and it was the valuable half of the cage: every claim in the answer had to rest on a tool
// receipt from the same turn. The lane is gone and OpenClaw runs the loop, so the check becomes a
// hook that covers every agent on a local model instead of code written per action.
//
// Two seams, both documented by OpenClaw 2026.10.6 and chosen for what they alone can do:
//
//   after_tool_call        every tool result as it lands (event.toolName, event.params,
//                          event.error, event.result), collected per session and run.
//   before_agent_finalize  the finished answer before the harness accepts it
//                          (event.lastAssistantMessage, event.provider, event.stopHookActive,
//                          event.messages with this run's toolCall and toolResult entries).
//
// The transcript in `event.messages` is the authority when the host hands it over; the stream
// collected from `after_tool_call` is the same data for a host that withholds it. `after_tool_call`
// is an observation hook that OpenClaw may dispatch fire-and-forget, so a receipt can arrive after
// finalization: that is why the transcript wins and the stream is the fallback, not the reverse.
//
// What the check does with an answer that does not stand on receipts, in this order:
//
//   1. `{ action: "revise", retry: { maxAttempts: 1 } }`: the model gets one more pass and is told
//      which claims have no receipt. OpenClaw counts that budget per run and per idempotency key.
//   2. The answer is let through with a visible note (`message_sending`), because a revision is not
//      guaranteed: a harness without a finalization integration never asks, a run with a
//      deterministic side effect is not rewound, and the budget above is spent after one pass.
//   3. Never dropped in silence. The note is written on every unsupported answer and cleared only
//      by an answer that stands on its receipts, so the two paths cannot leave a reader guessing.
//
// Only local models are checked. The switch and the providers it covers are NOT in this file: they
// are the `evidence` section of `nexgen.config.json`, written by the engine from the configuration
// in force (`nexgen_core/openclaw/evidence.py`). A cloud model is never touched, and a turn with no
// configured provider list is never checked.
//
// Every rule here is a pure function of text and receipts, so the whole check is tested without a
// gateway, a model or a network.
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { configSection, readPluginConfig } from "./engine-config.mjs";

const DIR = dirname(fileURLToPath(import.meta.url));
const CONFIG_PATH = join(DIR, "nexgen.config.json");

//: How many sessions' worth of state the job keeps. A gateway runs for weeks; a map that grows with
//: it is a leak, and the oldest entry is the one nobody is going to finalize again.
const MAX_ENTRIES = 512;

//: One extra model pass, then the note. OpenClaw spends this budget per run and idempotency key;
//: the key is ours so a second plugin's revisions cannot swallow ours.
const IDEMPOTENCY_KEY = "nexgen-evidence-local-claims";
const MAX_ATTEMPTS = 1;

//: A tool result can be a megabyte and the finalization budget is 15 seconds: only the head of a
//: receipt is used to ground a claim, and the head is what a person could quote anyway.
const RECEIPT_TEXT_CHARS = 20_000;

//: How much of two texts must agree before one is treated as the judged answer. Block streaming
//: delivers an answer in pieces, and only the piece that carries the head is annotated.
const HEAD_CHARS = 40;

//: What the reader sees above the answer. English, like every sentence this plugin renders itself
//: (`quota.mjs` says why): the engine translates its own words, and a file with no locale cannot
//: know the language of the person reading. The claim tables below are a different question, and
//: they carry both languages at once.
const NOTE = "Unverified claims:";

//: Appended to the revision reason OpenClaw sends the model.
const REVISE_INSTRUCTION =
  "Rewrite the answer without the claims listed above: do not add details to compensate for them, " +
  "and say plainly that you did not do them in this turn.";

//: Where a turn's receipts come from. OpenClaw names an MCP tool `server__tool`, so a name is
//: reduced to its own part before the family is decided, and the families below are the ids
//: OpenClaw and the engine's own MCP servers actually use.
export const READ_TOOLS = new Set([
  "read", "read_file", "fs_read", "pdf", "view_image", "memory_get", "list_dir",
  "read_vault", "read_repo", "read_pdf", "read_mail", "read_drive", "read_calendar", "read_outlook",
]);
export const SEARCH_TOOLS = new Set([
  "search", "grep", "find", "glob", "memory_search", "web_search", "search_vault", "memory_find",
]);
export const EXEC_TOOLS = new Set([
  "exec", "process", "terminal", "bash", "sh", "powershell", "cmd", "run_command", "system",
  "code_execution",
]);
export const WRITE_TOOLS = new Set(["write", "edit", "apply_patch", "file_write", "memory_save"]);

//: The write verbs the MCP servers use as a prefix (`create_note`, `update_section`) or a suffix
//: (`drive_propose_upload` needs no help: it starts with neither, and a proposal is not a write).
const WRITE_PREFIXES = ["write_", "create_", "update_", "append_", "delete_", "remove_", "save_"];
const WRITE_SUFFIXES = ["_write", "_create", "_update", "_append", "_delete", "_remove", "_save"];

//: Claim families, ported from `nexgen_local/evidence.py`. Each family needs its own receipt: a
//: read claim is not evidence for a search, and a search that found nothing is not evidence that
//: something was read.
//:
//: Both languages sit in the same table, and nothing here reads a configured language. The engine
//: is public and answers in English by default; the owners' agents often answer in Italian. A
//: switch would decide which half of the answers is checked, and an English claim without evidence
//: would pass for the precise reason the check exists to prevent. Every phrase stays a plain
//: string: `hasClaim` decides the word edges.
export const READ_CLAIM_PHRASES = [
  "ho letto", "ho consultato", "ho aperto", "ho trovato il file", "ho trovato", "ho recuperato",
  "i read", "i've read", "i have read", "i opened", "i've opened", "i consulted", "i looked at",
  "i retrieved", "i found the file", "i found", "i've found",
];
export const SEARCH_CLAIM_PHRASES = [
  "ho cercato", "ho ricercato",
  "i searched", "i've searched", "i looked up", "i ran a search", "i did a search", "i googled",
];
export const EXEC_CLAIM_PHRASES = [
  "ho eseguito", "ho effettuato", "ho lanciato", "ho avviato", "ho usato",
  "i ran", "i've run", "i have run", "i executed", "i launched", "i started", "i used",
];
export const VERIFY_CLAIM_PHRASES = [
  "ho verificato", "ho testato", "ho controllato i test", "ho superato i test",
  "i verified", "i tested", "i checked the tests", "i ran the tests", "i made sure",
  "the tests pass", "tests pass", "tests passed",
];
export const WRITE_CLAIM_PHRASES = [
  "ho salvato", "ho scritto", "ho creato", "ho aggiunto", "ho modificato", "ho eliminato",
  "ho cancellato", "ho applicato", "ho inviato", "ho mandato", "ho spedito",
  "i saved", "i wrote", "i've written", "i have written", "i created", "i added", "i modified",
  "i updated", "i edited", "i patched", "i deleted", "i removed", "i applied", "i sent", "i emailed",
];

//: Words that turn a generic "ho eseguito" or "I ran" into a test or verification claim.
const TEST_WORDS = ["test", "verific", "collaud", "pytest", "coverage", "check", "suite"];

//: A negation right before a claim makes it a true statement of absence ("non ho letto la nota",
//: "I did not read the note").
const NEGATION_RE =
  /\b(non|nessun|nessuna|nessuno|senza|niente|mai|not|never|without|none|nothing|nobody|no|nor|neither|cannot|can't|couldn't|didn't|doesn't|don't|isn't|wasn't|aren't|weren't|haven't|hasn't)\b/i;

//: English also negates the object after the verb: "I found no results" denies the object, not the
//: action, and is the honest outcome of a search that returned nothing. Italian puts its negation
//: before the verb, where `NEGATION_RE` already catches it.
const OBJECT_NEGATION_RE = /^\s*(?:no|not|nothing|none|nobody|never)\b/i;

//: Passive result claims without "ho" or "I": "file aggiornato", "the file was updated". Grounded
//: only when the matched word itself plus a neighbouring content word appear in what the turn
//: actually read.
const WRITE_RESULT_RE =
  /\b(aggiornat[oaie]|modificat[oaie]|salvat[oaie]|scritt[oaie]|creat[oaie]|aggiunt[oaie]|eliminat[oaie]|cancellat[oaie]|applicat[oaie]|updated|modified|saved|written|created|added|deleted|removed|applied|edited|patched)\b/gi;
const TEST_RESULT_RE =
  /\btest\s+(superat\w*|passat\w*|eseguit\w*|ok|verdi)\b|\bverific\w*\s+(superat\w*|completat\w*|ok)\b|\bsuperat\w*\s+(i\s+)?test\b|\bverificat[oaie]\b|\btests?\s+(pass|passed|passing|ok|green|succeeded)\b|\bverified\b|\bchecks?\s+pass(ed)?\b/gi;

//: The honest outcome when retrieval produced nothing. A bare "non" ("non supera 900 euro") or a
//: bare "no" ("no more than 900 euro") is a negated fact about the world, not a declaration that
//: the engine found nothing.
const ABSENCE_RE =
  /\b(non ho trovato|non trovo|non ho recuperato|non ho accesso|non ho informazioni|non esiste|non esistono|non risulta|non risultano|non disponibile|non disponibili|non presente|non accessibile|non recuperabile|non so|nessun\w* (risultato|risultati|nota|note|file|documento|documenti|contenuto|contenuti|informazione|informazioni)|non ha prodotto risultati|i did not find|i didn't find|i could not find|i couldn't find|i cannot find|i can't find|i have not found|i haven't found|not found|found nothing|no results?|no matches?|no matching|nothing found|does not exist|do not exist|doesn't exist|don't exist|not available|not accessible|not present|not retrievable|i do not know|i don't know|no information|no data|unable to (find|read|access|retrieve)|could not retrieve|couldn't retrieve|returned nothing|returned no results)\b/i;

//: `\w` covers the paths a citation uses, `\\:` the Windows absolute ones.
const PATH_RE = /`?([\w./\\:-]+\.(?:md|pdf|txt|ya?ml|json|py|toml|sh|ps1|cfg|ini))`?/gi;

const DIGIT_RE = /\d/;

//: The argument names that make a receipt a read of something specific. `name` is how a Drive file
//: is cited (`[Contratto.txt]`), because its id is opaque to a reader.
const READ_TARGET_KEYS = ["path", "file", "filePath", "file_path", "url", "id"];

function words(text) {
  return String(text || "").toLowerCase().match(/[a-zà-ÿ0-9]+/g) || [];
}

function negated(low, index) {
  return NEGATION_RE.test(low.slice(Math.max(0, index - 40), index));
}

const WORD_CHAR_RE = /[a-zà-ÿ0-9]/i;

//: True when the phrase begins and ends on a word edge. English claim phrases are short ("i ran"),
//: and a substring match would find them inside ordinary words ("multi range").
function onWordEdge(low, start, length) {
  const before = start > 0 ? low[start - 1] : " ";
  const after = start + length < low.length ? low[start + length] : " ";
  return !WORD_CHAR_RE.test(before) && !WORD_CHAR_RE.test(after);
}

//: True when the words straight after a phrase deny its object ("i found no results").
function negatedObject(low, end) {
  return OBJECT_NEGATION_RE.test(low.slice(end, end + 16));
}

//: True when a phrase occurs without a negation in the preceding context. `low` is the answer
//: lowercased: the indices never leave that string.
export function hasClaim(low, phrases) {
  for (const phrase of phrases) {
    let start = 0;
    for (;;) {
      const index = low.indexOf(phrase, start);
      if (index < 0) break;
      if (onWordEdge(low, index, phrase.length)
          && !negated(low, index)
          && !negatedObject(low, index + phrase.length)) return true;
      start = index + phrase.length;
    }
  }
  return false;
}

export function declaresAbsence(text) {
  return ABSENCE_RE.test(String(text || ""));
}

//: ``Non ho trovato la nota. Il budget e' 900 euro`` opens with honesty and closes with invention:
//: digits outside a cited path are specific facts an empty retrieval cannot support.
export function absenceWithFacts(text) {
  const content = String(text || "");
  if (!declaresAbsence(content)) return false;
  const stripped = content.replace(new RegExp(ABSENCE_RE.source, "gi"), " ").replace(PATH_RE, " ");
  return DIGIT_RE.test(stripped);
}

//: True for a genuine absence declaration and nothing else. The engine's own sentences are gone
//: with the lane; what remains is the prose rule, one host wide.
export function honestEmptyOutcome(text) {
  return declaresAbsence(text) && !absenceWithFacts(text);
}

//: `server__tool` is how OpenClaw names an MCP tool; the server is not part of what it does.
function toolBase(toolName) {
  const raw = String(toolName || "").toLowerCase();
  const split = raw.indexOf("__");
  return split >= 0 ? raw.slice(split + 2) : raw;
}

//: read, search, exec, write or other: the family that decides which receipt a claim needs.
export function familyOf(toolName) {
  const name = toolBase(toolName);
  if (!name) return "other";
  if (WRITE_TOOLS.has(name) || WRITE_PREFIXES.some((p) => name.startsWith(p)) || WRITE_SUFFIXES.some((s) => name.endsWith(s))) {
    return "write";
  }
  if (SEARCH_TOOLS.has(name) || name.includes("search") || name.includes("grep")) return "search";
  if (READ_TOOLS.has(name) || name.startsWith("read_")) return "read";
  if (EXEC_TOOLS.has(name)) return "exec";
  return "other";
}

function blockList(message) {
  const content = message && message.content;
  if (Array.isArray(content)) return content;
  if (typeof content === "string") return [{ type: "text", text: content }];
  return [];
}

function textOfMessage(message) {
  return blockList(message)
    .filter((block) => block && block.type === "text" && typeof block.text === "string")
    .map((block) => block.text)
    .join("\n")
    .slice(0, RECEIPT_TEXT_CHARS);
}

function isReceipt(value) {
  return Boolean(value) && typeof value === "object" && typeof value.tool === "string";
}

//: The receipts of the turn being finalized, read from the transcript OpenClaw hands over.
//: Scoped to the last user message: an earlier turn's read is not evidence for this answer, and a
//: session that reads the same note every morning would otherwise pass forever.
export function receiptsOf(messages) {
  if (!Array.isArray(messages) || !messages.length) return [];
  let start = -1;
  messages.forEach((message, index) => {
    if (message && message.role === "user") start = index;
  });
  const turn = messages.slice(start + 1);
  const calls = new Map();
  for (const message of turn) {
    for (const block of blockList(message)) {
      if (block && block.type === "toolCall" && block.id) {
        calls.set(String(block.id), {
          tool: String(block.name || ""),
          args: block.arguments && typeof block.arguments === "object" ? block.arguments : {},
        });
      }
    }
  }
  const receipts = [];
  for (const message of turn) {
    if (!message || message.role !== "toolResult") continue;
    const call = calls.get(String(message.toolCallId || "")) || {};
    receipts.push({
      tool: String(message.toolName || call.tool || ""),
      ok: message.isError !== true,
      args: call.args || {},
      text: textOfMessage(message),
    });
  }
  return receipts;
}

//: One receipt from `after_tool_call`, the same shape `receiptsOf` produces.
export function receiptOf(event) {
  const params = event && event.params && typeof event.params === "object" ? event.params : {};
  const result = event ? event.result : undefined;
  let text = "";
  if (typeof result === "string") {
    text = result;
  } else if (result && typeof result === "object") {
    text = textOfMessage(result);
  }
  return {
    tool: String((event && event.toolName) || ""),
    ok: !(event && typeof event.error === "string" && event.error),
    args: params,
    text: text.slice(0, RECEIPT_TEXT_CHARS),
  };
}

function readTarget(receipt) {
  for (const key of READ_TARGET_KEYS) {
    const value = receipt.args ? receipt.args[key] : undefined;
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return "";
}

//: Separator-insensitive: a receipt carries the system's absolute path (backslashes on Windows),
//: a citation uses the slashes a person types.
export function pathMatches(cited, read) {
  const left = String(cited).replace(/^\.?\//, "").replace(/\\/g, "/").toLowerCase();
  const right = String(read).replace(/^\.?\//, "").replace(/\\/g, "/").toLowerCase();
  return left === right || right.endsWith(`/${left}`) || left.endsWith(`/${right}`);
}

//: Paths the answer presents as sources, ignoring negated mentions.
export function citedPaths(text) {
  const content = String(text || "");
  const low = content.toLowerCase();
  const cited = [];
  for (const match of content.matchAll(PATH_RE)) {
    if (negated(low, match.index)) continue;
    const path = match[1];
    if (!cited.includes(path)) cited.push(path);
  }
  return cited;
}

//: True when the word occurs in the haystack outside a negated span: `Il file non e' stato
//: aggiornato` must not ground a `File aggiornato` claim with the opposite meaning.
function unnegatedIn(word, low) {
  let position = low.indexOf(word);
  while (position >= 0) {
    if (!negated(low, position)) return true;
    position = low.indexOf(word, position + word.length);
  }
  return false;
}

//: True when the matched claim restates retrieved content. Proximity to a citation alone never
//: suffices: the matched word plus one neighbouring content word must appear in what was read.
export function groundedIn(matchStart, matchEnd, text, collected) {
  if (!collected) return false;
  const matched = words(String(text).slice(Math.max(0, matchStart), matchEnd));
  if (!matched.length) return false;
  const low = String(collected).toLowerCase();
  if (!matched.every((word) => unnegatedIn(word, low))) return false;
  const window = words(String(text).slice(Math.max(0, matchStart - 80), matchEnd + 80));
  const windowTokens = window.filter((token) => token.length >= 4 && !matched.includes(token));
  const collectedTokens = new Set(words(low).filter((token) => token.length >= 4));
  return windowTokens.some((token) => collectedTokens.has(token));
}

function hasResultPattern(text, pattern, collected) {
  for (const match of String(text).matchAll(pattern)) {
    if (negated(String(text).toLowerCase(), match.index)) continue;
    if (groundedIn(match.index, match.index + match[0].length, text, collected)) continue;
    return true;
  }
  return false;
}

//: True when a cited filename is one the turn read under another name: a Drive id is opaque, so
//: the model rightly cites the human name and the receipt carries it beside the id.
function citedByName(cited, receipts) {
  const wanted = String(cited).split("/").pop().toLowerCase();
  return receipts.some((receipt) => {
    const name = receipt.args && receipt.args.name;
    return familyOf(receipt.tool) === "read" && receipt.ok && typeof name === "string"
      && name.split("/").pop().toLowerCase() === wanted;
  });
}

function unique(problems) {
  const seen = new Set();
  const kept = [];
  for (const problem of problems) {
    if (seen.has(problem)) continue;
    seen.add(problem);
    kept.push(problem);
  }
  return kept;
}

//: True when retrieval ran and left nothing the answer could use: a read or a search happened, and
//: it produced neither a path nor any text.
function retrievalRanEmpty(receipts, paths, searched, collected) {
  const attempted = receipts.some((receipt) => {
    const family = familyOf(receipt.tool);
    return family === "read" || family === "search";
  });
  return attempted && !collected && !paths.length && !searched;
}

//: The verdict: one reason per family, empty when every claim stands on a receipt.
export function verifyAnswer(answer, receipts = []) {
  const text = String(answer || "");
  const low = text.toLowerCase();
  const list = Array.isArray(receipts) ? receipts.filter(isReceipt) : [];
  const successful = list.filter((receipt) => receipt.ok);
  const families = new Set(successful.map((receipt) => familyOf(receipt.tool)));
  const paths = successful.filter((receipt) => familyOf(receipt.tool) === "read").map(readTarget).filter(Boolean);
  const searched = families.has("search");
  const collected = successful.map((receipt) => receipt.text).filter(Boolean).join("\n");
  const problems = [];

  if (hasClaim(low, READ_CLAIM_PHRASES) && !families.has("read")) {
    problems.push(successful.length
      ? "claims a read without a successful read receipt"
      : "claims work done without a successful receipt");
  }
  if (hasClaim(low, SEARCH_CLAIM_PHRASES) && !searched) {
    problems.push("claims a search without a successful search receipt");
  }
  if (hasClaim(low, EXEC_CLAIM_PHRASES) && !families.has("exec")) {
    problems.push(TEST_WORDS.some((word) => low.includes(word))
      ? "claims tests or verification without an execution receipt"
      : "claims work done without a successful receipt");
  }
  if (hasClaim(low, VERIFY_CLAIM_PHRASES) && !families.has("exec")) {
    problems.push("claims tests or verification without an execution receipt");
  }
  // A family is proven by a receipt of that family: the action claims and the passive result
  // patterns ("file aggiornato", "the file was updated") are two ways of saying the same thing, so
  // either one is enough to hold the family open, and a pattern that restates what the turn read
  // is grounded, not claimed.
  if (!families.has("write")
      && (hasClaim(low, WRITE_CLAIM_PHRASES) || hasResultPattern(text, WRITE_RESULT_RE, collected))) {
    problems.push("claims a write without a successful write receipt");
  }
  if (!families.has("exec")
      && (hasClaim(low, VERIFY_CLAIM_PHRASES) || hasResultPattern(text, TEST_RESULT_RE, collected))) {
    problems.push("claims tests or verification without an execution receipt");
  }
  for (const cited of citedPaths(text)) {
    if (paths.some((read) => pathMatches(cited, read))) continue;
    if (collected && collected.toLowerCase().includes(cited.toLowerCase())) continue;
    if (citedByName(cited, successful)) continue;
    problems.push(`cites a source that was never read: ${cited}`);
  }
  if (retrievalRanEmpty(list, paths, searched, collected) && low.trim() && !honestEmptyOutcome(text)) {
    problems.push("claims facts with nothing retrieved by the turn");
  }
  return unique(problems);
}

//: The `evidence` section the engine deposits, or null when the check is off for this machine.
export function evidenceSection(configPath = CONFIG_PATH) {
  const section = configSection(readPluginConfig(configPath), "evidence");
  if (section.enabled !== true) return null;
  const providers = Array.isArray(section.providers)
    ? section.providers.filter((item) => typeof item === "string" && item.trim()).map((item) => item.trim().toLowerCase())
    : [];
  return providers.length ? { providers } : null;
}

//: The provider that answered this turn, from the event first and the context second.
export function providerOf(event, ctx) {
  const value = (event && event.provider) || (ctx && ctx.modelProviderId) || "";
  return String(value).toLowerCase();
}

export function watched(provider, section) {
  return Boolean(section) && Boolean(provider) && section.providers.includes(provider);
}

//: The key that ties an answer to the session it was delivered on. `message_sending` receives the
//: canonical session key, so a mark keyed by session id would never be found: the key comes first.
export function contextKey(event, ctx) {
  const key = (ctx && (ctx.sessionKey || ctx.sessionId))
    || (event && (event.sessionKey || event.sessionId));
  return key ? String(key) : null;
}

function runKey(event, ctx) {
  const session = contextKey(event, ctx);
  if (!session) return null;
  const run = (event && event.runId) || (ctx && ctx.runId) || "";
  return `${session}\u0000${run}`;
}

//: The whole decision, as a value: null when this turn is not the check's business.
export function judge(event, ctx, { section = null, receipts = null } = {}) {
  if (!watched(providerOf(event, ctx), section)) return null;
  const answer = event && typeof event.lastAssistantMessage === "string" ? event.lastAssistantMessage : "";
  if (!answer.trim()) return null;
  const transcript = receiptsOf(event && event.messages);
  const list = transcript.length ? transcript : Array.isArray(receipts) ? receipts : [];
  return { session: contextKey(event, ctx), answer, problems: verifyAnswer(answer, list) };
}

//: True when `content` is the answer that was judged, or the streamed piece that carries its head.
export function sameAnswer(content, answer) {
  const squash = (value) => String(value || "").replace(/\s+/g, " ").trim();
  const left = squash(content);
  const right = squash(answer);
  if (!left || !right) return false;
  if (left === right) return true;
  return right.startsWith(left.slice(0, HEAD_CHARS)) || left.startsWith(right.slice(0, HEAD_CHARS));
}

export function annotate(content, problems) {
  const text = String(content || "");
  const list = Array.isArray(problems) ? problems.filter(Boolean) : [];
  if (!text.trim() || !list.length) return text;
  const note = `${NOTE} ${list.join("; ")}`;
  if (text.includes(note)) return text;
  return `${text.replace(/\s+$/, "")}\n\n${note}`;
}

function reviseFor(problems) {
  const reason = `${NOTE} ${problems.join("; ")}`;
  return {
    action: "revise",
    reason,
    retry: { instruction: REVISE_INSTRUCTION, idempotencyKey: IDEMPOTENCY_KEY, maxAttempts: MAX_ATTEMPTS },
  };
}

//: Keeps a map bounded, and keeps the oldest entry the one written longest ago.
function remember(map, key, value, bound) {
  if (map.size >= bound) map.delete(map.keys().next().value);
  map.delete(key);
  map.set(key, value);
}

export function registerEvidence(api, { configPath = CONFIG_PATH, maxEntries = MAX_ENTRIES } = {}) {
  //: Receipts of the turn in flight, by session and run: the fallback for a host whose finalize
  //: event carries no transcript.
  const turnReceipts = new Map();
  //: Answers that did not stand on their receipts, by session, waiting for delivery.
  const unsupported = new Map();

  api.on("after_tool_call", (event, ctx) => {
    try {
      const key = runKey(event, ctx);
      if (!key) return;
      const list = (turnReceipts.get(key) || []).concat([receiptOf(event)]);
      remember(turnReceipts, key, list, maxEntries);
    } catch {
      // A receipt that cannot be collected must never fail a tool call that already happened.
    }
  });

  api.on("before_agent_finalize", (event, ctx) => {
    try {
      const key = runKey(event, ctx);
      const verdict = judge(event, ctx, {
        section: evidenceSection(configPath),
        receipts: key ? turnReceipts.get(key) : null,
      });
      // The turn is over either way: a receipt kept past it is memory nobody reads.
      if (key) turnReceipts.delete(key);
      if (!verdict) return undefined;
      if (!verdict.problems.length) {
        // An answer that stands on its receipts clears the note the previous pass earned.
        if (verdict.session) unsupported.delete(verdict.session);
        return undefined;
      }
      if (verdict.session) remember(unsupported, verdict.session, { answer: verdict.answer, problems: verdict.problems }, maxEntries);
      // A stop hook is already asking for a pass; asking again is how revision loops are made.
      if (event && event.stopHookActive === true) return undefined;
      return reviseFor(verdict.problems);
    } catch (err) {
      // The check never blocks a session, but an answer it could not read is worth a line in the
      // gateway log: silence here would be exactly the failure this job exists to prevent.
      console.error(`nexgen evidence: the check could not run (${err && err.message})`);
      return undefined;
    }
  });

  api.on("message_sending", (event, ctx) => {
    try {
      const session = contextKey(event, ctx);
      const mark = session ? unsupported.get(session) : null;
      const content = event && typeof event.content === "string" ? event.content : "";
      if (!mark || !sameAnswer(content, mark.answer)) return undefined;
      // Once: a retry or a second channel does not earn a second note.
      unsupported.delete(session);
      return { content: annotate(content, mark.problems) };
    } catch (err) {
      console.error(`nexgen evidence: the note could not be added (${err && err.message})`);
      return undefined;
    }
  });
}
