"""How a note from the sync cycle says it is a warning or an error.

Skills, runtimes, shims, the notifier and the pin bump all return plain
strings, and the severity rode in a prefix that each of them typed by hand and
each reader matched by hand. The two sides drifted: `skill_sources` wrote
`[WARNING]`, the guard counted `[WARN]` and `[AVVISO]` (which nothing writes),
so a skill warning was not counted and the cycle was reported as a plain
success.

The text stays what it was, so consumers and the JSON they produce do not
change; what changes is that the prefix is written and read in one place.
Make a note with `WARN + text` or `ERROR + text`, ask it with `is_warning` or
`is_error`. A test refuses the literal prefixes anywhere else.
"""
from __future__ import annotations

WARN = "[WARN] "
ERROR = "[ERROR] "

# Spellings that already existed in the wild: a note carrying any of them is
# read as what it says it is, so a stale emitter still gets counted.
_WARNING_MARKERS = ("[WARN]", "[WARNING]", "[AVVISO]")
_ERROR_MARKERS = ("[ERROR]", "[ERRORE]")


def is_warning(note: str) -> bool:
    return note.startswith(_WARNING_MARKERS)


def is_error(note: str) -> bool:
    return note.startswith(_ERROR_MARKERS)
