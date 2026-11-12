// Removes the `⟦openclaw:ctx⟧`-tagged channel-context blocks that OpenClaw
// prepends to every CLI turn.
//
// Why this exists at all: OpenClaw's cli-runner composes each turn's prompt
// after all before_prompt_build hooks have run
// (src/agents/cli-runner/prepare.ts:1856 — `preparedPrompt =
// renderCurrentPrompt(preparedPrompt, preferResumableText)`), and that call
// stitches the channel-provided context onto the front of the user's text.
// For a CLI backend with native session persistence (`sessionMode: "existing"`,
// resuming via `agy --conversation <id>`), that block duplicates history agy
// already keeps in its per-conversation SQLite — the same content is stored
// twice per turn, so the context window explodes (observed 942 % of the 1M
// window in a single Telegram session).
//
// The channel context blocks are recognisable: each one starts with a
// specific label followed by the literal marker `⟦openclaw:ctx⟧`, and the
// Active goal block is appended by the same code path. Strip those three
// sections and hand the trimmed prompt to agy. If the marker isn't present
// (side channels, direct API calls) the input passes through unchanged.
// Every openclaw:ctx block starts with one of these labels — the current
// full set as of openclaw 2026.8.x. Kept sorted by prefix length descending
// so overlapping prefixes don't shadow each other.
const CONTEXT_BLOCK_LABELS = [
    "Conversation context (chronological, selected for current message):",
    "Conversation info:",
];
// The Active goal section is a separate block that arrives with the same
// channel context, no ctx marker of its own. Anchored to the start of a line
// so it doesn't match "Active goal" mentions inside a user message.
const ACTIVE_GOAL_ANCHOR = /(^|\n)Active goal:\s/;
const CTX_MARKER = "⟦openclaw:ctx⟧";
// Locates the end of a context block by finding the first blank line that is
// not itself part of the block's fenced code payload. Returns the index right
// after the terminator (so the caller can slice `[start, end)`), or the
// text length when no terminator exists (last block in the prompt).
function findBlockEnd(text, start) {
    // Scan line-by-line; a paragraph break (double newline) ends the block.
    let cursor = start;
    let inCodeFence = false;
    while (cursor < text.length) {
        const nextNewline = text.indexOf("\n", cursor);
        if (nextNewline === -1)
            return text.length;
        const line = text.slice(cursor, nextNewline);
        if (line.trimStart().startsWith("```")) {
            inCodeFence = !inCodeFence;
        }
        // A paragraph break (empty line) outside a fenced block ends the section.
        if (!inCodeFence) {
            const after = text.charCodeAt(nextNewline + 1);
            // Empty line: next char is \n (LF-LF) or \r (LF-CR-LF).
            if (after === 0x0a || after === 0x0d) {
                // Consume the newline group and return the index right after.
                let end = nextNewline + 1;
                while (end < text.length) {
                    const code = text.charCodeAt(end);
                    if (code === 0x0a || code === 0x0d)
                        end += 1;
                    else
                        break;
                }
                return end;
            }
        }
        cursor = nextNewline + 1;
    }
    return text.length;
}
function stripLabelBlocks(text) {
    let result = text;
    for (const label of CONTEXT_BLOCK_LABELS) {
        // Only strip when the label is followed by the openclaw ctx marker; any
        // other paragraph starting with the same label (from a user pasting a
        // legitimate reply) stays.
        // eslint-disable-next-line no-constant-condition
        while (true) {
            const labelStart = result.indexOf(label);
            if (labelStart === -1)
                break;
            // Confirm the ctx marker is on this section header.
            const headerEnd = result.indexOf("\n", labelStart);
            const header = result.slice(labelStart, headerEnd === -1 ? result.length : headerEnd);
            if (!header.includes(CTX_MARKER))
                break; // no marker → user content, don't touch
            const blockEnd = findBlockEnd(result, labelStart);
            // Trim any leading whitespace before the label so we don't leave a
            // dangling blank line where the block used to be.
            let sliceStart = labelStart;
            while (sliceStart > 0) {
                const code = result.charCodeAt(sliceStart - 1);
                if (code === 0x0a || code === 0x0d || code === 0x20 || code === 0x09) {
                    sliceStart -= 1;
                }
                else {
                    break;
                }
            }
            result = result.slice(0, sliceStart) + result.slice(blockEnd);
        }
    }
    return result;
}
function stripActiveGoal(text) {
    const match = ACTIVE_GOAL_ANCHOR.exec(text);
    if (!match)
        return text;
    const anchor = match.index + (match[1]?.length ?? 0);
    const end = findBlockEnd(text, anchor);
    return (text.slice(0, anchor) + text.slice(end)).replace(/[ \t]+\n/g, "\n").trimEnd();
}
// Strip the OpenClaw-injected channel-context sections from a `--print`
// prompt while leaving the user's own text and any other markdown intact.
// Idempotent — running twice on the same input yields the same output.
export function stripOpenClawChannelContext(prompt) {
    if (typeof prompt !== "string" || prompt.length === 0)
        return prompt;
    if (!prompt.includes(CTX_MARKER) && !ACTIVE_GOAL_ANCHOR.test(prompt))
        return prompt;
    let out = stripLabelBlocks(prompt);
    out = stripActiveGoal(out);
    // Collapse runs of blank lines that might survive removal (up to two).
    out = out.replace(/\n{3,}/g, "\n\n");
    return out.trim();
}
// Returns the index and value of the `--print` (or `-p`, `--prompt`) argument
// in an argv-style list, or undefined when the flag isn't present.
export function findPrintArg(args) {
    for (let i = 0; i < args.length; i += 1) {
        const arg = args[i];
        if (arg === "--print" || arg === "-p" || arg === "--prompt") {
            const value = args[i + 1];
            if (typeof value === "string")
                return { index: i + 1, value };
            return undefined;
        }
    }
    return undefined;
}
// Returns a new argv with the `--print` value replaced by its stripped form.
// If the argv doesn't contain a `--print` value, returns the input unchanged.
export function stripArgvChannelContext(args) {
    const target = findPrintArg(args);
    if (!target)
        return [...args];
    const stripped = stripOpenClawChannelContext(target.value);
    if (stripped === target.value)
        return [...args];
    const out = [...args];
    out[target.index] = stripped;
    return out;
}
//# sourceMappingURL=prompt-strip.js.map