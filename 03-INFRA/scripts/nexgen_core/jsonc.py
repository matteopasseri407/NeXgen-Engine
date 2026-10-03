"""JSONC (JSON with comments) support for CLI configs.

Port of the JSONC parser/surgeon from the release (config_schema.py): OpenCode
uses ``opencode.jsonc`` (comments and trailing commas), so writing plain JSON
would break it. Comments must be PRESERVED, not lost.
"""
from __future__ import annotations

import json
import re
from typing import Any


def _jsonc_without_comments(text: str) -> str:
    """Replaces JSONC comments with spaces, preserving byte offsets.

    Keeping newlines and character positions means parse errors and surgical
    edits stay pointed at the original document. Comment markers inside JSON
    strings are left untouched.
    """
    out = list(text)
    i = 0
    in_string = False
    escaped = False
    while i < len(text):
        char = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            i += 1
            continue
        if char == '"':
            in_string = True
            i += 1
            continue
        if char == "/" and i + 1 < len(text) and text[i + 1] == "/":
            out[i] = out[i + 1] = " "
            i += 2
            while i < len(text) and text[i] not in "\r\n":
                out[i] = " "
                i += 1
            continue
        if char == "/" and i + 1 < len(text) and text[i + 1] == "*":
            out[i] = out[i + 1] = " "
            i += 2
            closed = False
            while i < len(text):
                if text[i] == "*" and i + 1 < len(text) and text[i + 1] == "/":
                    out[i] = out[i + 1] = " "
                    i += 2
                    closed = True
                    break
                if text[i] not in "\r\n":
                    out[i] = " "
                i += 1
            if not closed:
                raise ValueError("unterminated JSONC block comment")
            continue
        i += 1
    return "".join(out)


def _jsonc_without_trailing_commas(text: str) -> str:
    out = list(text)
    i = 0
    in_string = False
    escaped = False
    while i < len(text):
        char = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            i += 1
            continue
        if char == '"':
            in_string = True
            i += 1
            continue
        if char == ",":
            lookahead = i + 1
            while True:
                while lookahead < len(text) and text[lookahead].isspace():
                    lookahead += 1
                # A comment between the comma and the bracket is still a
                # trailing comma (`{"a": 1, // keep\n}`): skip it too.
                if text.startswith("//", lookahead):
                    newline = text.find("\n", lookahead + 2)
                    lookahead = len(text) if newline < 0 else newline + 1
                    continue
                if text.startswith("/*", lookahead):
                    close = text.find("*/", lookahead + 2)
                    if close < 0:
                        break
                    lookahead = close + 2
                    continue
                break
            if lookahead < len(text) and text[lookahead] in "]}":
                out[i] = " "
        i += 1
    return "".join(out)


def parse_jsonc(text: str) -> Any:
    """Parses the JSON-with-comments dialect accepted by OpenCode."""
    return json.loads(_jsonc_without_trailing_commas(_jsonc_without_comments(text)))


def _skip_jsonc_trivia(text: str, start: int) -> int:
    """Skips whitespace and comments iteratively (never recursive: thousands
    of consecutive comment lines must not cost a stack frame each)."""
    i = start
    while i < len(text):
        if text[i].isspace():
            i += 1
            continue
        if text.startswith("//", i):
            newline = text.find("\n", i + 2)
            if newline < 0:
                return len(text)
            i = newline + 1
            continue
        if text.startswith("/*", i):
            close = text.find("*/", i + 2)
            if close < 0:
                raise ValueError("unterminated JSONC block comment")
            i = close + 2
            continue
        break
    return i


def _jsonc_string_end(text: str, start: int) -> int:
    i = start + 1
    escaped = False
    while i < len(text):
        if escaped:
            escaped = False
        elif text[i] == "\\":
            escaped = True
        elif text[i] == '"':
            return i + 1
        i += 1
    raise ValueError("unterminated JSON string")


def _jsonc_value_end(text: str, start: int) -> int:
    start = _skip_jsonc_trivia(text, start)
    if start >= len(text):
        raise ValueError("missing JSON value")
    if text[start] == '"':
        return _jsonc_string_end(text, start)
    if text[start] not in "[{":
        i = start
        while i < len(text) and text[i] not in ",]}":
            # A comment starts where the scalar ends: without this, a span
            # would swallow `// keep this` and surgery would delete it, and
            # a comma *inside* the comment would truncate the span early.
            if text.startswith("//", i) or text.startswith("/*", i):
                break
            i += 1
        return i

    stack = [text[start]]
    i = start + 1
    while i < len(text):
        if text[i] == '"':
            i = _jsonc_string_end(text, i)
            continue
        if text.startswith("//", i):
            newline = text.find("\n", i + 2)
            i = len(text) if newline < 0 else newline + 1
            continue
        if text.startswith("/*", i):
            close = text.find("*/", i + 2)
            if close < 0:
                raise ValueError("unterminated JSONC block comment")
            i = close + 2
            continue
        if text[i] in "[{":
            stack.append(text[i])
        elif text[i] in "]}":
            expected = "[" if text[i] == "]" else "{"
            if not stack or stack[-1] != expected:
                raise ValueError("mismatched JSON delimiters")
            stack.pop()
            if not stack:
                return i + 1
        i += 1
    raise ValueError("unterminated JSON value")


def jsonc_top_level_value_span(text: str, key: str) -> tuple[int, int] | None:
    """Returns the value span for a top-level JSONC property.

    On duplicate keys the LAST span wins: plain JSON applies the last
    occurrence, so rewriting any earlier one would edit a value the
    runtime never reads.
    """
    root = _skip_jsonc_trivia(text, 0)
    if root >= len(text) or text[root] != "{":
        raise ValueError("JSONC root is not an object")
    found: tuple[int, int] | None = None
    i = root + 1
    while True:
        i = _skip_jsonc_trivia(text, i)
        if i >= len(text):
            raise ValueError("unterminated JSONC root object")
        if text[i] == "}":
            return found
        if text[i] != '"':
            raise ValueError("top-level JSONC property name is not a string")
        name_end = _jsonc_string_end(text, i)
        name = json.loads(text[i:name_end])
        colon = _skip_jsonc_trivia(text, name_end)
        if colon >= len(text) or text[colon] != ":":
            raise ValueError("missing colon after top-level JSONC property")
        value_start = _skip_jsonc_trivia(text, colon + 1)
        value_end = _jsonc_value_end(text, value_start)
        if name == key:
            found = (value_start, value_end)
        i = _skip_jsonc_trivia(text, value_end)
        if i < len(text) and text[i] == ",":
            i += 1
            continue
        if i < len(text) and text[i] == "}":
            return found
        raise ValueError("missing comma after top-level JSONC property")


def remove_jsonc_top_level_value(text: str, key: str) -> str:
    """Surgically removes a top-level property while preserving comments.

    Used for one-time migrations of engine-imposed keys (never for user
    data): the pair and exactly one adjacent comma go away, everything else
    -- comments included -- stays byte-identical. Returns the input
    unchanged when the key is absent.
    """
    parsed = parse_jsonc(text)
    if not isinstance(parsed, dict) or key not in parsed:
        return text
    root = _skip_jsonc_trivia(text, 0)
    if text[root] != "{":
        raise ValueError("JSONC root is not an object")
    # Collect every occurrence: duplicate keys all go, otherwise the
    # surviving twin keeps the value alive and the removal is a lie.
    spans: list[tuple[int, int, int]] = []
    i = root + 1
    while True:
        i = _skip_jsonc_trivia(text, i)
        if i >= len(text) or text[i] == "}":
            break
        if text[i] != '"':
            raise ValueError("top-level JSONC property name is not a string")
        key_start = i
        name_end = _jsonc_string_end(text, i)
        name = json.loads(text[i:name_end])
        colon = _skip_jsonc_trivia(text, name_end)
        value_start = _skip_jsonc_trivia(text, colon + 1)
        value_end = _jsonc_value_end(text, value_start)
        after = _skip_jsonc_trivia(text, value_end)
        if name == key:
            spans.append((key_start, value_end, after))
        i = after
        if i < len(text) and text[i] == ",":
            i += 1
            continue
        break
    if not spans:
        return text
    result = text
    # Back to front so earlier offsets stay valid; each removal takes its
    # own adjacent comma, like the single-pair path below.
    for key_start, value_end, after in reversed(spans):
        if after < len(result) and result[after] == ",":
            result = result[:key_start] + result[after + 1:]
        else:
            # Last (or only) pair: take the preceding comma instead.
            j = key_start - 1
            while j > root and (result[j].isspace() or result[j] == ","):
                if result[j] == ",":
                    result = result[:j] + result[value_end:]
                    break
                j -= 1
            else:
                result = result[:key_start] + result[value_end:]
    reparsed = parse_jsonc(result)
    if not isinstance(reparsed, dict) or key in reparsed:
        raise ValueError(f"could not remove top-level JSONC property {key!r}")
    return result


def set_jsonc_top_level_value(text: str, key: str, value: Any) -> str:
    """Surgically sets a top-level value while preserving comments."""
    parsed = parse_jsonc(text)
    if not isinstance(parsed, dict):
        raise ValueError("JSONC root is not an object")
    serialized = json.dumps(value, ensure_ascii=False, indent=2)
    span = jsonc_top_level_value_span(text, key)
    if span is not None:
        start, end = span
        line_start = text.rfind("\n", 0, start) + 1
        indent_match = re.match(r"[ \t]*", text[line_start:start])
        indent = indent_match.group(0) if indent_match else ""
        if "\n" in serialized:
            lines = serialized.splitlines()
            serialized = lines[0] + "\n" + "\n".join(indent + line for line in lines[1:])
        result = text[:start] + serialized + text[end:]
    else:
        root_start = _skip_jsonc_trivia(text, 0)
        root_end = _jsonc_value_end(text, root_start) - 1
        indent = "  "
        uncommented = _jsonc_without_comments(text)
        match = re.search(r'(?m)^([ \t]+)"', uncommented[root_start + 1:root_end])
        if match:
            indent = match.group(1)
        value_lines = serialized.splitlines()
        rendered = value_lines[0]
        if len(value_lines) > 1:
            rendered += "\n" + "\n".join(indent + line for line in value_lines[1:])
        result = (
            text[:root_start + 1]
            + "\n"
            + indent
            + json.dumps(key, ensure_ascii=False)
            + ": "
            + rendered
            + ("," if parsed else "")
            + text[root_start + 1:]
        )
    reparsed = parse_jsonc(result)
    # Compare against the serialized form, not the caller's object: a tuple
    # or set serializes fine but never `==` its JSON twin.
    if not isinstance(reparsed, dict) or reparsed.get(key) != json.loads(serialized):
        raise ValueError(f"could not set top-level JSONC property {key!r}")
    return result
