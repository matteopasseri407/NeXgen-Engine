from __future__ import annotations

import importlib.util

import pytest

from conftest import REAL_SCRIPTS


def _load_schema():
    path = REAL_SCRIPTS / "config_schema.py"
    spec = importlib.util.spec_from_file_location("jsonc_schema_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_parse_jsonc_accepts_comments_and_trailing_commas():
    schema = _load_schema()
    parsed = schema.parse_jsonc(
        '{\n'
        '  // line comment with a fake brace }\n'
        '  "url": "https://example.invalid/a//b",\n'
        '  /* block comment with [brackets] */\n'
        '  "items": [1, 2,],\n'
        '}\n'
    )

    assert parsed == {
        "url": "https://example.invalid/a//b",
        "items": [1, 2],
    }


def test_parse_jsonc_does_not_hide_invalid_documents():
    schema = _load_schema()

    with pytest.raises(ValueError):
        schema.parse_jsonc('{"missing": ]')
    with pytest.raises(ValueError, match="unterminated JSONC block comment"):
        schema.parse_jsonc('{"otherwise": "valid"} /* never closed')


def test_set_jsonc_top_level_value_preserves_inline_comments():
    schema = _load_schema()
    original = (
        '{\n'
        '  "model": "fake-provider/fake-model" // keep this host-local choice\n'
        '}\n'
    )

    updated = schema.set_jsonc_top_level_value(
        original,
        "instructions",
        ["~/KnowledgeVault/03-INFRA/agent-universal-layer/instructions/AGENTS.md"],
    )

    assert "// keep this host-local choice" in updated
    assert schema.parse_jsonc(updated)["model"] == "fake-provider/fake-model"
    assert len(schema.parse_jsonc(updated)["instructions"]) == 1
