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
            while lookahead < len(text) and text[lookahead].isspace():
                lookahead += 1
            if lookahead < len(text) and text[lookahead] in "]}":
                out[i] = " "
        i += 1
    return "".join(out)


def parse_jsonc(text: str) -> Any:
    """Parses the JSON-with-comments dialect accepted by OpenCode."""
    return json.loads(_jsonc_without_trailing_commas(_jsonc_without_comments(text)))


def _skip_jsonc_trivia(text: str, start: int) -> int:
    i = start
    while i < len(text):
        if text[i].isspace():
            i += 1
            continue
        if text.startswith("//", i):
            newline = text.find("\n", i + 2)
            return len(text) if newline < 0 else _skip_jsonc_trivia(text, newline + 1)
        if text.startswith("/*", i):
            close = text.find("*/", i + 2)
            return len(text) if close < 0 else _skip_jsonc_trivia(text, close + 2)
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
    """Returns the value span for a top-level JSONC property."""
    root = _skip_jsonc_trivia(text, 0)
    if root >= len(text) or text[root] != "{":
        raise ValueError("JSONC root is not an object")
    i = root + 1
    while True:
        i = _skip_jsonc_trivia(text, i)
        if i >= len(text):
            raise ValueError("unterminated JSONC root object")
        if text[i] == "}":
            return None
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
            return value_start, value_end
        i = _skip_jsonc_trivia(text, value_end)
        if i < len(text) and text[i] == ",":
            i += 1
            continue
        if i < len(text) and text[i] == "}":
            return None
        raise ValueError("missing comma after top-level JSONC property")


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
    if not isinstance(reparsed, dict) or reparsed.get(key) != value:
        raise ValueError(f"could not set top-level JSONC property {key!r}")
    return result
