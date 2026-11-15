"""The gateway will not forward a call its own schema already refuses, and says what to send instead.

Eight refused calls in fourteen days read `workflowId: Required`, `nodeIds: Required`, `queries:
Required`: the model called `lazy_call` without `lazy_load`, guessed the argument names, and the
upstream spent a round trip to answer with a one-field validation error. The proxy knew the schema all
along — its index is exactly what `lazy_load` hands over — and forwarded the call anyway.

These tests pin the reading of a tool's `inputSchema`. The end-to-end behaviour (a refused call is
audited as refused and never reaches the server) lives beside the fake servers, in
`test_nexgen_lazy_mcp_serving.py`.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

MCP_DIR = Path(__file__).resolve().parents[2] / "agent-universal-layer" / "mcp"
if str(MCP_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_DIR))

import lazy_schema  # noqa: E402

#: The shape every n8n tool has, and the one the failures came from.
SCHEMA = {
    "type": "object",
    "properties": {
        "workflowId": {"type": "string", "description": "The workflow, from n8n_list_workflows"},
        "nodeIds": {"type": "array", "items": {"type": "string"}},
        "limit": {"type": "integer"},
    },
    "required": ["workflowId"],
    "additionalProperties": False,
}


def test_a_missing_required_argument_is_named():
    assert lazy_schema.arguments_problem(SCHEMA, {}) == "missing required argument: workflowId"
    assert lazy_schema.arguments_problem(SCHEMA, {"workflowId": "42"}) is None


def test_two_missing_arguments_are_named_together():
    schema = {**SCHEMA, "required": ["workflowId", "nodeIds"]}
    assert lazy_schema.arguments_problem(schema, {}) == "missing required arguments: workflowId, nodeIds"


def test_an_argument_the_schema_forbids_is_named_with_the_ones_it_allows():
    assert lazy_schema.arguments_problem(SCHEMA, {"workflow_id": "42", "workflowId": "42"}) == (
        "unknown argument: workflow_id (the schema allows limit, nodeIds, workflowId)"
    )


def test_a_value_of_the_wrong_type_names_the_type_it_wants():
    assert lazy_schema.arguments_problem(SCHEMA, {"workflowId": 12}) == (
        "argument 'workflowId' must be string, got int"
    )
    # a bool is an int in Python and never a JSON number
    assert lazy_schema.arguments_problem(SCHEMA, {"workflowId": "42", "limit": True}) == (
        "argument 'limit' must be integer, got bool"
    )


@pytest.mark.parametrize(
    ("schema", "arguments"),
    [
        (None, {"x": 1}),
        ({"type": "object"}, {"x": 1}),
        ({"type": "object", "properties": {}, "additionalProperties": False}, {"x": 1}),
        ({"type": "object", "properties": {"x": {"$ref": "#/$defs/x"}}, "required": ["x"]}, {"x": 1}),
        ({"type": "object", "properties": {"x": {"anyOf": [{"type": "string"}, {"type": "integer"}]}}}, {"x": 1}),
        # a nested requirement is not read here: the shallow check must stay silent
        ({"type": "object", "properties": {"x": {"type": "object", "required": ["inner"]}}}, {"x": {}}),
    ],
)
def test_a_schema_it_cannot_fully_read_never_refuses(schema, arguments):
    """A proxy must not refuse a call because it failed to parse a constraint."""
    assert lazy_schema.arguments_problem(schema, arguments) is None


def test_the_compact_schema_drops_what_only_describes_it():
    text = lazy_schema.compact_schema(SCHEMA)

    assert "\n" not in text
    assert "description" not in text
    assert '"workflowId":{"type":"string"}' in text
    assert json.loads(text)["required"] == ["workflowId"]


def test_an_enormous_schema_degrades_to_names_and_declared_types():
    big = {
        "type": "object",
        "properties": {f"field_{index}": {"type": "string", "description": "x" * 40} for index in range(200)},
        "required": ["field_0"],
    }

    text = lazy_schema.compact_schema(big)

    assert len(text) <= lazy_schema.SCHEMA_MAX_CHARS
    reduced = json.loads(text)
    assert reduced["required"] == ["field_0"]
    assert reduced["properties"]["field_0"] == "string"
    assert reduced["more_properties"] == 160  # 200 properties, 40 of them named
