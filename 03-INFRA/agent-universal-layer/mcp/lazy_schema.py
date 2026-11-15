#!/usr/bin/env python3
"""lazy_schema.py — does a forwarded call carry what the tool's own schema asks for?

`lazy_call` and `lazy_mutate` forward arguments to a server the gateway did not write, and the
upstream answers a wrong argument list with a one-line validation error that costs a round trip and
tells the model nothing it can use ("workflowId: Required", eight times in fourteen days). This module
answers the questions the gateway can answer without guessing — a missing required field, a value of
the wrong type, a field the schema forbids — and renders the schema compactly for the refusal, so the
next call is right. Anything it does not understand (a `$ref`, a union, a nested constraint) passes: a
proxy must never refuse a call it merely fails to parse.
"""
from __future__ import annotations

import json
from typing import Any

#: JSON Schema's primitive types against Python's, for the shallow type check. `integer` and `number`
#: are handled apart, because a `bool` is an `int` in Python and never a JSON number.
_TYPE_CHECKS: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "boolean": (bool,),
    "object": (dict,),
    "array": (list,),
    "null": (type(None),),
}

#: Keys that describe a schema to a human and cost tokens on every refusal.
_NOISE_KEYS = frozenset({"description", "title", "$comment", "examples", "default", "$schema", "$id"})

#: How much schema a refusal may carry before it degrades to names and declared types.
SCHEMA_MAX_CHARS = 1500

#: How many properties the degraded form lists before it only counts the rest.
REDUCED_PROPERTY_LIMIT = 40


def _type_matches(spec: dict[str, Any], value: Any) -> bool:
    """Whether `value` is the type the property declares; a union or an absent type always passes."""
    expected = spec.get("type")
    if not isinstance(expected, str):
        return True
    if expected in ("integer", "number"):
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    check = _TYPE_CHECKS.get(expected)
    return check is None or isinstance(value, check)


def _plural(names: list[str], one: str) -> str:
    return f"{one}{'' if len(names) == 1 else 's'}: {', '.join(names)}"


def arguments_problem(schema: Any, arguments: Any) -> str | None:
    """What stops this argument list from satisfying the schema, or None when it can be sent."""
    if not isinstance(schema, dict) or not isinstance(arguments, dict):
        return None
    required = schema.get("required")
    if isinstance(required, list):
        missing = [name for name in required if isinstance(name, str) and name not in arguments]
        if missing:
            return _plural(missing, "missing required argument")
    properties = schema.get("properties")
    if not isinstance(properties, dict) or not properties:
        return None
    if schema.get("additionalProperties") is False:
        unknown = sorted(str(key) for key in arguments if key not in properties)
        if unknown:
            return f"{_plural(unknown, 'unknown argument')} (the schema allows {', '.join(sorted(properties))})"
    for name, value in arguments.items():
        spec = properties.get(name)
        if isinstance(spec, dict) and not _type_matches(spec, value):
            return f"argument {name!r} must be {spec['type']}, got {type(value).__name__}"
    return None


def _slim(value: Any) -> Any:
    """The schema without the keys that only describe it to a human."""
    if isinstance(value, dict):
        return {key: _slim(item) for key, item in value.items() if key not in _NOISE_KEYS}
    if isinstance(value, list):
        return [_slim(item) for item in value]
    return value


def compact_schema(schema: Any) -> str:
    """The schema on one line, small enough to sit inside an error message."""
    slim = _slim(schema)
    text = json.dumps(slim, separators=(",", ":"), ensure_ascii=False)
    if len(text) <= SCHEMA_MAX_CHARS or not isinstance(slim, dict):
        return text
    properties = slim.get("properties")
    names = list(properties) if isinstance(properties, dict) else []
    by_type = {
        name: properties[name].get("type") if isinstance(properties[name].get("type"), str) else "?"
        for name in names[:REDUCED_PROPERTY_LIMIT]
    }
    reduced: dict[str, Any] = {"required": slim.get("required", []), "properties": by_type}
    if len(names) > REDUCED_PROPERTY_LIMIT:
        reduced["more_properties"] = len(names) - REDUCED_PROPERTY_LIMIT
    return json.dumps(reduced, separators=(",", ":"), ensure_ascii=False)
