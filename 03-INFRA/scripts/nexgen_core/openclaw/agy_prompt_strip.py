"""The Antigravity plugin's prompt filter, fixed so it never deletes the user's message.

One job: keep the user's text in the turn the installed `google-antigravity-cli`
plugin hands to `agy`.

The plugin removes OpenClaw's `⟦openclaw:ctx⟧` context blocks before `agy` runs,
because `agy` keeps that history in its own conversation. Release 0.3.4 gets
two things wrong:

- it removes the blank line in front of each block it cuts, so whatever followed
  the block joins the line before it. On a Telegram turn the "Conversation
  context" cut puts the message on the closing fence of "Conversation info",
  and cutting that block then deletes the message;
- it finds the end of a block by counting code fences, so a stray fence makes
  it run to the end of the prompt and delete everything after the block.

OpenClaw draws these blocks with one rule: they are separated by blank lines
(it splits them on `\\n{2,}` itself), every history line is collapsed to one line
with its fences neutralised, and a JSON payload is one line between a ```json
fence and a closing fence. The fixed filter reads them by the same rule: a
block ends at its first blank line, and a JSON block ends at its closing fence,
so text glued right after it survives too.

Like the manual compaction in `agy_compaction`, this is a bridge until the
plugin ships the fix. OpenClaw loads the plugin from a copy it makes at start,
so the fix applies from the next gateway restart.
"""
from __future__ import annotations

from pathlib import Path

from nexgen_core.openclaw import plugin_patch
from nexgen_core.openclaw.agy_prereqs import extension_dir
from nexgen_core.openclaw.terms import LOCAL_AGY_BACKEND

#: Text only the finished fix contains, so a second run is a no-op.
MARKER = "nexgen-prompt-strip-block-end"
#: The tag of the copy kept beside the file the fix replaces.
BACKUP_TAG = "agy-prompt-strip"
#: The compiled filter the plugin's wrapper runs on every `--print` prompt.
FILTER_RELATIVE = Path("dist") / "prompt-strip.js"

#: The cut that drops the separator, and the same cut keeping one paragraph break.
#: A block at the very start needs none; the filter's final trim removes a trailing one.
#: The replacement is byte-identical to the first version of this fix, so a file
#: that already carries it only gains the block-end rule.
SEPARATOR = plugin_patch.Edit(
    "            result = result.slice(0, sliceStart) + result.slice(blockEnd);\n",
    "            /* nexgen-prompt-strip-separator: keep a paragraph break where a context block was cut */\n"
    '            result = result.slice(0, sliceStart) + (sliceStart > 0 ? "\\n\\n" : "") + result.slice(blockEnd);\n',
)

#: The fence-counting block end of release 0.3.4, and OpenClaw's own rule in its place.
BLOCK_END = plugin_patch.Edit(r'''// Locates the end of a context block by finding the first blank line that is
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
''', r'''// Locates the end of a context block and returns the index right after it (so
// the caller can slice `[start, end)`), or the text length for the last block.
function findBlockEnd(text, start) {
    /* nexgen-prompt-strip-block-end: OpenClaw separates its context blocks with blank lines,
       keeps every block line on one line and neutralises fences inside them, so a block
       ends at its first blank line, and a ```json block ends at a closing fence line
       found before that blank line. */
    const skipNewlines = (index) => {
        let end = index;
        while (end < text.length && (text.charCodeAt(end) === 0x0a || text.charCodeAt(end) === 0x0d))
            end += 1;
        return end;
    };
    const paragraphEnd = (from) => {
        const blank = text.slice(from).search(/\n\r?\n/);
        return blank === -1 ? text.length : from + blank + 1;
    };
    const labelEnd = text.indexOf("\n", start);
    if (labelEnd === -1)
        return text.length;
    const end = paragraphEnd(labelEnd);
    if (text.startsWith("```json", labelEnd + 1)) {
        const from = labelEnd + 1 + "```json".length;
        const close = /\n```[ \t]*(?=\r?\n|$)/.exec(text.slice(from, end));
        if (close)
            return skipNewlines(from + close.index + close[0].length);
    }
    return skipNewlines(end);
}
''')


def filter_file(state_dir: Path, backend: str = LOCAL_AGY_BACKEND) -> Path:
    """The compiled prompt filter inside the installed extension."""
    return extension_dir(state_dir, backend) / FILTER_RELATIVE


def apply(state_dir: Path, backend: str = LOCAL_AGY_BACKEND, *, dry_run: bool = False) -> plugin_patch.Patch:
    """Fix the filter when it still needs it, and say what happened."""
    return plugin_patch.apply(filter_file(state_dir, backend), [SEPARATOR, BLOCK_END],
                              marker=MARKER, backup_tag=BACKUP_TAG, dry_run=dry_run)
