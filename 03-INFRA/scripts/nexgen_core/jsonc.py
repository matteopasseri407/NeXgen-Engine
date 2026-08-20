"""Supporto JSONC (JSON con commenti) per le config delle CLI.

Port dei parser/chirurghi JSONC dalla release (config_schema.py): OpenCode
usa ``opencode.jsonc`` (commenti e virgole finali), quindi scrivere JSON
puro lo spezzerebbe. I commenti vanno PRESERVATI, non persi.
"""
from __future__ import annotations

import json
import re
from typing import Any


def _jsonc_without_comments(text: str) -> str:
    """Sostituisce i commenti JSONC con spazi preservando gli offset byte.

    Mantenere newline e posizioni dei caratteri rende gli errori di parse e
    le modifiche chirurgiche puntati al documento originale. I marcatori di
    commento dentro stringhe JSON non vengono toccati.
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
                raise ValueError("commento JSONC a blocco non terminato")
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
    """Analizza il dialetto JSON con commenti accettato da OpenCode."""
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
    raise ValueError("stringa JSON non terminata")


def _jsonc_value_end(text: str, start: int) -> int:
    start = _skip_jsonc_trivia(text, start)
    if start >= len(text):
        raise ValueError("valore JSON mancante")
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
                raise ValueError("commento JSONC a blocco non terminato")
            i = close + 2
            continue
        if text[i] in "[{":
            stack.append(text[i])
        elif text[i] in "]}":
            expected = "[" if text[i] == "]" else "{"
            if not stack or stack[-1] != expected:
                raise ValueError("delimitatori JSON non corrispondenti")
            stack.pop()
            if not stack:
                return i + 1
        i += 1
    raise ValueError("valore JSON non terminato")


def jsonc_top_level_value_span(text: str, key: str) -> tuple[int, int] | None:
    """Restituisce lo span del valore per una proprietà JSONC top-level."""
    root = _skip_jsonc_trivia(text, 0)
    if root >= len(text) or text[root] != "{":
        raise ValueError("la radice JSONC non è un oggetto")
    i = root + 1
    while True:
        i = _skip_jsonc_trivia(text, i)
        if i >= len(text):
            raise ValueError("oggetto radice JSONC non terminato")
        if text[i] == "}":
            return None
        if text[i] != '"':
            raise ValueError("nome proprietà JSONC top-level non è una stringa")
        name_end = _jsonc_string_end(text, i)
        name = json.loads(text[i:name_end])
        colon = _skip_jsonc_trivia(text, name_end)
        if colon >= len(text) or text[colon] != ":":
            raise ValueError("due punti mancanti dopo proprietà JSONC top-level")
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
        raise ValueError("virgola mancante dopo proprietà JSONC top-level")


def set_jsonc_top_level_value(text: str, key: str, value: Any) -> str:
    """Imposta chirurgicamente un valore top-level preservando i commenti."""
    parsed = parse_jsonc(text)
    if not isinstance(parsed, dict):
        raise ValueError("la radice JSONC non è un oggetto")
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
        raise ValueError(f"impossibile impostare la proprietà JSONC top-level {key!r}")
    return result
