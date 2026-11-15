"""The harnesses' plugins: read from their own listings, never touched by the engine.

Each reader is exercised against the JSON shape that CLI really prints on this
machine (checked 2026-11-15), plus the two ways one can fail: no answer, and an
answer whose shape this engine does not know.
"""
from __future__ import annotations

import json


from nexgen_core import thirdparty_plugins
from nexgen_core.thirdparty_status import BY_HAND, plugin_items


def _pretend(monkeypatch, answers: dict[str, str], codes: dict[str, int] | None = None):
    """A machine where exactly these CLIs exist and answer these strings."""
    seen: list[tuple[str, ...]] = []

    def run(exe, argv):
        seen.append((exe, *argv))
        import subprocess

        return subprocess.CompletedProcess([exe, *argv], (codes or {}).get(exe, 0),
                                           answers.get(exe, ""), "")

    monkeypatch.setattr(thirdparty_plugins, "_run", run)
    return seen


def _which(present: tuple[str, ...]):
    return lambda name: name if name in present else None


CLAUDE_JSON = json.dumps([{"id": "humanizer@x", "version": "3.1.0", "enabled": True}])
CODEX_JSON = json.dumps({"installed": [
    {"pluginId": "pdf@openai-primary-runtime", "version": "26.1112.11217", "enabled": True},
    {"pluginId": "documents@openai-primary-runtime", "version": "26.1112.11217", "enabled": False},
]})
AGY_JSON = json.dumps([{"name": "firebase", "enabled": True, "path": "C:/p/firebase"}])


def test_each_harness_answers_with_its_own_update_command(monkeypatch):
    seen = _pretend(monkeypatch, {"claude": CLAUDE_JSON, "codex": CODEX_JSON, "agy": AGY_JSON})

    rows, problems = thirdparty_plugins.rows(which=_which(("claude", "codex", "agy")))

    assert problems == []
    assert [(row.harness, row.name, row.version) for row in rows] == [
        ("claude", "humanizer", "3.1.0"),
        ("codex", "pdf", "26.1112.11217"),
        ("codex", "documents", "26.1112.11217"),
        ("antigravity", "firebase", ""),
    ]
    commands = {row.name: row.command for row in rows}
    assert commands["humanizer"] == "claude plugin update humanizer"
    assert commands["pdf"] == "codex plugin marketplace upgrade"
    assert commands["firebase"] == "agy plugin install firebase"
    assert seen == [("claude", "plugin", "list", "--json"),
                    ("codex", "plugin", "list", "--json"),
                    ("agy", "plugin", "list", "--json")]


def test_a_disabled_plugin_is_still_reported(monkeypatch):
    _pretend(monkeypatch, {"codex": CODEX_JSON})

    rows, _problems = thirdparty_plugins.rows(which=_which(("codex",)))

    assert {row.name: row.enabled for row in rows} == {"pdf": True, "documents": False}


def test_a_harness_that_is_not_installed_costs_nothing(monkeypatch):
    seen = _pretend(monkeypatch, {})

    rows, problems = thirdparty_plugins.rows(which=_which(()))

    assert (rows, problems, seen) == ([], [], [])


def test_a_harness_that_does_not_answer_is_one_line_not_an_exception(monkeypatch):
    _pretend(monkeypatch, {"claude": ""}, codes={"claude": 3})

    rows, problems = thirdparty_plugins.rows(which=_which(("claude",)))

    assert rows == []
    assert problems == ["claude plugin list --json did not answer"]


def test_an_answer_that_is_not_json_is_refused_whole(monkeypatch):
    _pretend(monkeypatch, {"agy": "Usage: agy plugin <command>"})

    rows, problems = thirdparty_plugins.rows(which=_which(("agy",)))

    assert rows == []
    assert problems == ["agy plugin list --json answered something that is not JSON"]


def test_an_unknown_shape_is_refused_whole(monkeypatch):
    """A release that renames a field must cost that harness' rows, never invent them."""
    _pretend(monkeypatch, {"codex": json.dumps({"plugins": []})})

    rows, problems = thirdparty_plugins.rows(which=_which(("codex",)))

    assert rows == []
    assert problems == ["codex plugin list --json answered a shape this engine does not know"]


def test_plugin_rows_are_by_hand_and_name_the_harness_command():
    """A plugin is never bumped by this engine: the row says who moves it."""
    items = plugin_items([
        thirdparty_plugins.PluginRow(name="pdf", harness="codex", version="26.1112.11217",
                                     enabled=True, command="codex plugin marketplace upgrade"),
        thirdparty_plugins.PluginRow(name="firebase", harness="antigravity", version="",
                                     enabled=False, command="agy plugin install firebase"),
    ])

    assert [(item.kind, item.policy, item.command) for item in items] == [
        ("plugin", BY_HAND, "codex plugin marketplace upgrade"),
        ("plugin", BY_HAND, "agy plugin install firebase"),
    ]
    assert items[0].note == ""
    assert items[1].note.endswith("disabled")
