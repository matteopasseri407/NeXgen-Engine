"""Observable tests for the per-turn measurement and the two lane doctor lines.

nexgen: one-job reason="prove per-turn calls and tokens are read from a transcript, and the lane/binding checks warn where a turn is paid twice"

The fixture transcripts carry the same shapes a real Claude Code session writes: a
streamed message repeated with one `message.id`, a tool result written as a `user`
record, and usage fields as separate counts. The expected numbers are literals taken
from the measured model call of 2026-11-14 (2 input + 53,392 cache read + 25,323 cache
write + 136 output = 78,853 tokens), so a test that quietly changes the arithmetic
fails here instead of in a bill.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import i18n  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.checks.turn_budget_checks import check_turn_budget  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw import turn_budget  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw.doctor_lanes import (  # noqa: E402 - sys.path shim for cloned checkout
    check_acp_binding,
    check_harness_lane,
    lane_of,
)
from nexgen_core.openclaw.turn_budget import (  # noqa: E402 - sys.path shim for cloned checkout
    PER_TURN_BUDGET,
    read_claude_turns,
    resolved_budget,
    slug_for_cwd,
    worst_turn,
)
from nexgen_core.report import Severity  # noqa: E402 - sys.path shim for cloned checkout

#: The measured single call of 2026-11-14, and the turn of 74 of them that a 150,000
#: first-prompt budget reported green.
ONE_CALL = 78_853
THE_DARU_TURN = 5_835_122

CAPO_CWD = "C:\\Users\\matte\\.openclaw\\workspace-capo"
CAPO_SLUG = "C--Users-matte--openclaw-workspace-capo"


@pytest.fixture(autouse=True)
def _english():
    """Every sentence asserted below is English by construction, whatever the machine reads."""
    i18n.set_language("en")
    yield
    i18n.set_language(None)


def _prompt(text: str = "ciao") -> str:
    """A record a person wrote."""
    return json.dumps({"type": "user", "message": {"role": "user", "content": text}}) + "\n"


def _tool_result() -> str:
    """A record OpenClaw's loopback wrote: a tool's answer, not a new turn."""
    return json.dumps({
        "type": "user",
        "message": {"role": "user", "content": [{"type": "tool_result", "content": "ok"}]},
    }) + "\n"


def _call(
    message_id: str,
    input_tokens: int = 2,
    cache_read: int = 53_392,
    cache_creation: int = 25_323,
    output: int = 136,
) -> str:
    """One assistant record with the three prompt counts kept apart, as Claude Code writes them."""
    return json.dumps({
        "type": "assistant",
        "message": {
            "id": message_id,
            "usage": {
                "input_tokens": input_tokens,
                "cache_read_input_tokens": cache_read,
                "cache_creation_input_tokens": cache_creation,
                "output_tokens": output,
            },
        },
    }) + "\n"


def _session(folder: Path, name: str, *lines: str) -> Path:
    """One transcript file holding those records."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text("".join(lines), encoding="utf-8")
    return path


# ------------------------------------------------------------------ the measurement


def test_a_turn_totals_every_call_and_counts_a_streamed_message_once(tmp_path):
    """The first turn is two calls, not three: the repeated `message.id` is one model answer."""
    transcript = _session(
        tmp_path,
        "session.jsonl",
        _prompt(),
        _call("msg-a"),
        _call("msg-a"),                     # streaming: the same answer written again
        _call("msg-b", 10, 20, 30, 40),     # a second call costing 100 tokens
    )

    turns = read_claude_turns(transcript)

    assert [(turn.calls, turn.tokens) for turn in turns] == [(2, 78_953)]


def test_a_tool_result_is_not_a_new_turn(tmp_path):
    """A tool result is a `user` record too; counting it would report 74 turns of one call."""
    transcript = _session(
        tmp_path,
        "session.jsonl",
        _prompt(),
        _call("msg-a"),
        _tool_result(),
        _call("msg-b"),
    )

    turns = read_claude_turns(transcript)

    assert [(turn.calls, turn.tokens) for turn in turns] == [(2, 2 * ONE_CALL)]


def test_each_prompt_opens_a_turn_of_its_own(tmp_path):
    """Two prompts are two turns, and their calls are not added together."""
    transcript = _session(
        tmp_path,
        "session.jsonl",
        _prompt("primo"),
        _call("msg-a"),
        _prompt("secondo"),
        _call("msg-b"),
        _call("msg-c"),
    )

    turns = read_claude_turns(transcript)

    assert [(turn.calls, turn.tokens) for turn in turns] == [(1, ONE_CALL), (2, 2 * ONE_CALL)]


def test_a_corrupt_line_and_a_zero_usage_record_are_not_calls(tmp_path):
    """Half-written JSON and a record with no usage are skipped, never counted."""
    transcript = _session(
        tmp_path,
        "session.jsonl",
        _prompt(),
        '{"type": "assistant", "message": {"id": "half\n',
        json.dumps({"type": "assistant", "message": {"id": "no-usage"}}) + "\n",
        _call("msg-a"),
    )

    assert [(turn.calls, turn.tokens) for turn in read_claude_turns(transcript)] == [(1, ONE_CALL)]


def test_the_folder_a_harness_writes_is_named_after_its_working_directory():
    """The real folder on this machine, and the POSIX shape the VPS writes."""
    assert slug_for_cwd(CAPO_CWD) == CAPO_SLUG
    assert slug_for_cwd("/home/openclaw/.openclaw/workspace-automazioni") == (
        "-home-openclaw--openclaw-workspace-automazioni"
    )
    # Only ASCII letters and digits survive: an accented folder gets dashes, as Claude Code writes it.
    assert slug_for_cwd("/home/user/Notés") == "-home-user-Not-s"


def test_a_74_call_turn_of_the_measured_call_is_over_the_budget(tmp_path):
    """5,835,122 tokens is the turn the first-prompt budget cannot see."""
    claude_dir = tmp_path / ".claude"
    folder = claude_dir / "projects" / CAPO_SLUG
    _session(
        folder,
        "06dfb942.jsonl",
        _prompt(),
        *(_call(f"msg-{index}") for index in range(74)),
    )

    reading = worst_turn(claude_dir, "capo", CAPO_CWD)

    assert reading is not None
    assert reading.calls == 74
    assert reading.tokens == THE_DARU_TURN
    assert reading.tokens > PER_TURN_BUDGET


def test_a_turn_cut_by_the_read_window_is_dropped_and_never_understated(tmp_path, monkeypatch):
    """A long session is read from its end; a turn the window cuts reports nothing, not a part."""
    first = _prompt("primo") + _call("a") + _call("b")
    second = _prompt("secondo") + _call("c") + _call("d")
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(first + second, encoding="utf-8")
    monkeypatch.setattr(turn_budget, "TAIL_BYTES", len(second.encode()) + 5)

    assert [(turn.calls, turn.tokens) for turn in read_claude_turns(transcript)] == [(2, 2 * ONE_CALL)]


def test_the_newest_session_of_the_agents_folder_is_the_one_measured(tmp_path):
    """An older chat with a huge turn is history; the session the agent wrote last is the reading."""
    claude_dir = tmp_path / ".claude"
    folder = claude_dir / "projects" / CAPO_SLUG
    old = _session(folder, "old.jsonl", _prompt(), *(_call(f"old-{i}") for i in range(74)))
    fresh = _session(folder, "new.jsonl", _prompt(), _call("new-1"))
    os.utime(old, (1_760_000_000, 1_760_000_000))
    os.utime(fresh, (1_760_000_100, 1_760_000_100))

    reading = worst_turn(claude_dir, "capo", CAPO_CWD, now=1_760_000_200)

    assert reading is not None
    assert reading.session == fresh
    assert (reading.calls, reading.tokens) == (1, ONE_CALL)


def test_only_the_newest_turns_are_judged(tmp_path):
    """`limit` keeps the judge on the recent end of a long session."""
    claude_dir = tmp_path / ".claude"
    folder = claude_dir / "projects" / CAPO_SLUG
    _session(
        folder,
        "session.jsonl",
        _prompt("burn"),
        *(_call(f"hot-{index}") for index in range(3)),
        _prompt("quiet"),
        _call("cold-1", 1, 1, 1, 1),
    )

    last = worst_turn(claude_dir, "capo", CAPO_CWD, limit=1)
    last_two = worst_turn(claude_dir, "capo", CAPO_CWD, limit=2)

    assert last is not None and last_two is not None
    assert (last.calls, last.tokens) == (1, 4)
    assert (last_two.calls, last_two.tokens) == (3, 3 * ONE_CALL)


def test_an_environment_variable_moves_the_per_turn_ceiling():
    """The documented constant is a default, and an owner can tighten or loosen it."""
    assert resolved_budget(None, {}) == 2_000_000
    assert resolved_budget(None, {"NEXGEN_OPENCLAW_TURN_BUDGET": "500000"}) == 500_000
    assert resolved_budget(None, {"NEXGEN_OPENCLAW_TURN_BUDGET": "nonsense"}) == 2_000_000


# -------------------------------------------------------------------- the doctor line


def _config(cwd: str = CAPO_CWD) -> dict:
    """A configuration with one agent whose harness runs in `cwd`, and no ACP around it."""
    return {"agents": {"entries": {"capo": {"model": "anthropic/claude-opus-5-5", "workspace": cwd}}}}


def test_the_doctor_warns_on_the_worst_turn_and_names_its_call_count(tmp_path):
    """Above the ceiling the line is a warning that shows the turn, not the session's seed."""
    claude_dir = tmp_path / ".claude"
    _session(
        claude_dir / "projects" / CAPO_SLUG,
        "session.jsonl",
        _prompt(),
        *(_call(f"msg-{index}") for index in range(74)),
    )

    outcome = check_turn_budget(tmp_path, config=_config(), claude_dir=claude_dir)

    assert outcome is not None
    assert outcome.id == "openclaw.turn_budget"
    assert outcome.severity == Severity.WARN
    assert "5,835,122" in outcome.message
    assert "74 model calls" in outcome.message
    assert "2,000,000" in outcome.message
    assert "capo" in outcome.message
    assert "daru-burn" in outcome.action


def test_under_the_ceiling_the_doctor_says_what_the_heaviest_turn_cost(tmp_path):
    """A turn of two measured calls is 157,706 tokens: green, and the number is still shown."""
    claude_dir = tmp_path / ".claude"
    _session(
        claude_dir / "projects" / CAPO_SLUG,
        "session.jsonl",
        _prompt(),
        _call("msg-a"),
        _call("msg-b"),
    )

    outcome = check_turn_budget(tmp_path, config=_config(), claude_dir=claude_dir)

    assert outcome is not None
    assert outcome.severity == Severity.OK
    assert "157,706" in outcome.message
    assert "2 model calls" in outcome.message
    assert "capo" in outcome.message


def test_a_machine_with_no_transcript_is_silent(tmp_path):
    """Nothing measured is not a finding: the check says nothing rather than green or red."""
    assert check_turn_budget(
        tmp_path, config=_config(), claude_dir=tmp_path / ".claude",
    ) is None


def test_the_check_reads_the_configuration_the_machine_uses(tmp_path, monkeypatch):
    """Without a handed-in config the file OpenClaw would load is the one that names the folders."""
    claude_dir = tmp_path / ".claude"
    _session(
        claude_dir / "projects" / CAPO_SLUG,
        "session.jsonl",
        _prompt(),
        *(_call(f"msg-{index}") for index in range(74)),
    )
    config_path = tmp_path / "openclaw.json"
    config_path.write_text(json.dumps(_config()), encoding="utf-8")
    monkeypatch.setenv("OPENCLAW_CONFIG_PATH", str(config_path))

    outcome = check_turn_budget(tmp_path, claude_dir=claude_dir)

    assert outcome is not None
    assert outcome.severity == Severity.WARN
    assert "5,835,122" in outcome.message


# ------------------------------------------------------------- the lane doctor lines


def _acp_config(*, bound: bool = True, harness: str = "claude") -> dict:
    """One ACP agent, with or without the binding that makes its lane real."""
    config: dict = {
        "agents": {"entries": {"sviluppo": {
            "model": "anthropic/claude-opus-5-5",
            "workspace": CAPO_CWD,
            "runtime": {"type": "acp", "acp": {"agent": harness, "cwd": CAPO_CWD}},
        }}},
    }
    if bound:
        config["bindings"] = [{
            "type": "acp", "agentId": "sviluppo", "match": {"channel": "telegram"},
            "acp": {"mode": "persistent", "cwd": CAPO_CWD, "label": "sviluppo-claude"},
        }]
    return config


def test_a_bound_agent_on_a_direct_provider_is_named_as_a_warning():
    """`opencode-go` through OpenClaw's own prompt is the lane the proposal wants moved."""
    config = {
        "agents": {
            "entries": {
                "main": {"model": "opencode-go/deepseek-v4.1-flash"},
                "capo": {"model": "anthropic/claude-opus-5-5"},
            },
            "defaults": {"models": {"anthropic/*": {"agentRuntime": {"id": "claude-cli"}}}},
        },
        "bindings": [{"agentId": "main", "match": {"channel": "telegram"}}],
    }

    outcome = check_harness_lane(config)

    assert outcome is not None
    assert outcome.id == "openclaw.harness_lane"
    assert outcome.severity == Severity.WARN
    assert "These agents answer through a provider" in outcome.message
    assert "main: provider" in outcome.message
    assert "capo: cli (claude-cli)" in outcome.message
    assert "docs/squad.md" in outcome.action


def test_the_lane_line_names_an_acp_agent_and_stays_quiet(tmp_path):
    """An ACP harness in its own folder is the cheap lane: the line says so and warns about nobody."""
    outcome = check_harness_lane(_acp_config())

    assert outcome is not None
    assert outcome.severity == Severity.OK
    assert outcome.message == "Agent lanes: sviluppo: acp (claude)."


def test_an_agent_on_a_local_model_is_not_a_paid_provider_turn():
    """A local model costs nothing per token, so the lane is reported and nothing is warned."""
    config = {
        "agents": {"entries": {"locale": {"model": "ollama/qwen3:8b"}}},
        "bindings": [{"agentId": "locale", "match": {"channel": "telegram"}}],
    }

    outcome = check_harness_lane(config)

    assert outcome is not None
    assert outcome.severity == Severity.OK
    assert lane_of(config, "locale").kind == "provider"


def test_an_agent_on_agy_is_a_cli_lane_and_not_a_provider_one():
    """`agy` is the Google client on this machine: its turns are not OpenClaw's own prompt."""
    config = {
        "agents": {"entries": {"capo": {"model": "google-antigravity-cli/gemini-3-pro"}}},
        "bindings": [{"agentId": "capo", "match": {"channel": "telegram"}}],
    }

    reading = lane_of(config, "capo")
    outcome = check_harness_lane(config)

    assert (reading.kind, reading.runtime) == ("cli", "google-antigravity-cli")
    assert outcome is not None
    assert outcome.severity == Severity.OK


def test_an_agent_nobody_talks_to_is_not_a_cost():
    """A provider lane with no binding spends the plan for no one: the line names it, without a warning."""
    config = {"agents": {"entries": {"main": {"model": "opencode-go/deepseek-v4.1-flash"}}}}

    outcome = check_harness_lane(config)

    assert outcome is not None
    assert outcome.severity == Severity.OK
    assert outcome.message == "Agent lanes: main: provider."


def test_an_acp_runtime_without_a_binding_is_a_warning():
    """The 2026-10-23 bug: a direct chat answers with the default model instead of the harness."""
    outcome = check_acp_binding(_acp_config(bound=False))

    assert outcome is not None
    assert outcome.id == "openclaw.acp_binding"
    assert outcome.severity == Severity.WARN
    assert "sviluppo" in outcome.message
    assert "nexgen openclaw setup" in outcome.action


def test_an_acp_runtime_with_its_binding_is_ok():
    """Both halves written: the lane is real, and the line says which agents are on it."""
    outcome = check_acp_binding(_acp_config())

    assert outcome is not None
    assert outcome.severity == Severity.OK
    assert outcome.message == "Every ACP runtime has its binding: sviluppo."


def test_a_configuration_with_no_acp_lane_says_nothing_about_bindings():
    """A machine that never chose ACP has no binding to miss."""
    assert check_acp_binding(_config()) is None
