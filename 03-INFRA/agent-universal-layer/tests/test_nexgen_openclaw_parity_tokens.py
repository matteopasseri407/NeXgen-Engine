"""What a harness costs natively and inside OpenClaw, and where the record goes.

The numbers in these tests are the owner's measured ones (18,307 input tokens for
`agy` run directly, 25,417 through OpenClaw, so 7,110 of overhead) plus one lane
that is deliberately past the tolerance, and the parsers are driven with the JSON
each CLI really prints: Anthropic's shape, where the cache is kept apart from
`input_tokens`, and OpenAI's, where it is a subset. No test starts `agy`, `claude`,
`codex` or `openclaw`, and no test touches the network.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import i18n  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw import (  # noqa: E402 - sys.path shim for cloned checkout
    parity,
    parity_probe as probe,
    parity_record,
)

AGY_MODEL = "google-antigravity-cli/gemini-3.8-flash"
CLAUDE_MODEL = "anthropic/claude-opus-5-5"
CODEX_MODEL = "openai/gpt-5-2-codex"

#: The owner's measurement, as literals: agy directly, and the same call through OpenClaw.
NATIVE_MEASURED = 18307
OPENCLAW_MEASURED = 25417
OVERHEAD_MEASURED = 7110


@pytest.fixture(autouse=True)
def _english():
    """These tests compare the sentences a person reads, so they read English."""
    i18n.set_language("en")
    yield
    i18n.set_language(None)


# ------------------------------------------------------------------- answers


def openclaw_json(input_tokens: int, *, cache_read: int = 0, cache_write: int = 0) -> str:
    """`openclaw agent --json`, as the pinned release prints it for one turn."""
    return json.dumps({"result": {"payloads": [{"text": "OK"}], "meta": {"agentMeta": {
        "lastCallUsage": {"input": input_tokens, "output": 4,
                          "cacheRead": cache_read, "cacheWrite": cache_write},
        "toolSummary": {"calls": 0},
    }}}})


def agy_json(input_tokens: int, *, cache_read: int = 0, cache_creation: int = 0) -> str:
    """`agy -p ... --output-format json`, whose usage is snake_case."""
    return json.dumps({"usage": {
        "input_tokens": input_tokens, "output_tokens": 3, "thinking_tokens": 0,
        "cache_read_tokens": cache_read, "cache_creation_tokens": cache_creation,
    }})


def claude_json(input_tokens: int, *, cache_read: int = 0, cache_creation: int = 0) -> str:
    """`claude -p ... --output-format json`: one result object with its usage."""
    return json.dumps({"type": "result", "subtype": "success", "result": "OK", "usage": {
        "input_tokens": input_tokens, "output_tokens": 3,
        "cache_creation_input_tokens": cache_creation, "cache_read_input_tokens": cache_read,
    }})


def codex_jsonl(input_tokens: int, *, cached: int = 0) -> str:
    """`codex exec --json`: JSONL, with the turn's usage on its last event."""
    events = [
        {"type": "thread.started", "thread_id": "t-1"},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "OK"}},
        {"type": "turn.completed", "usage": {
            "input_tokens": input_tokens, "cached_input_tokens": cached, "output_tokens": 3,
        }},
    ]
    return "\n".join(json.dumps(event) for event in events)


class Answers:
    """Answers each binary the way that binary does, and remembers every question."""

    def __init__(self, **answers: str) -> None:
        self.answers = answers
        self.calls: list[tuple[list[str], Path, float]] = []

    def __call__(self, argv, cwd, timeout) -> probe.Ran:
        self.calls.append((list(argv), Path(cwd), timeout))
        binary = str(argv[0])
        if list(argv[1:2]) == ["--version"]:
            return probe.Ran(tuple(argv), stdout=f"{self.versions.get(binary, '')}\n")
        answer = self.answers.get(binary)
        if answer is None:
            return probe.Ran(tuple(argv), error=probe.MISSING, detail=binary)
        if isinstance(answer, probe.Ran):
            return answer
        return probe.Ran(tuple(argv), stdout=answer)

    versions = {"openclaw": "OpenClaw 2026.11.2 (b556fdf)", "agy": "1.5.9",
                "claude": "2.1.323 (Claude Code)", "codex": "codex-cli 0.155.1"}


def harness_config(workspace: Path, *, agy: bool = True, claude: bool = False, codex: bool = False) -> dict:
    """A configuration whose agents sit on the lanes asked for, and on nothing else."""
    entries: dict[str, dict] = {}
    models: dict[str, dict] = {}
    if agy:
        entries["main"] = {"model": AGY_MODEL, "workspace": str(workspace)}
    if claude:
        entries["capo"] = {"model": CLAUDE_MODEL, "workspace": str(workspace)}
        models["anthropic/*"] = {"agentRuntime": {"id": "claude-cli"}}
    if codex:
        entries["sviluppo"] = {"model": CODEX_MODEL, "workspace": str(workspace)}
        models["openai/*"] = {"agentRuntime": {"id": "codex"}}
    return {
        "models": {"providers": {
            "anthropic": {"models": [{"id": "claude-opus-5-5"}]},
            "openai": {"models": [{"id": "gpt-5-2-codex"}]},
        }},
        "agents": {"defaults": {"models": models}, "entries": entries},
    }


def home_of(tmp_path: Path) -> Path:
    """A home this machine's state resolution can use without touching the real one."""
    home = tmp_path / "home"
    (home / ".openclaw").mkdir(parents=True, exist_ok=True)
    return home


def write_config(tmp_path: Path, config: dict) -> Path:
    """The configuration file, where `paths.openclaw_config` looks for it."""
    path = home_of(tmp_path) / ".openclaw" / "openclaw.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


def record_at(moment: float, *, lanes: tuple[parity_record.LaneResult, ...] = ()) -> parity_record.Record:
    """One record, dated where a test needs it."""
    return parity_record.Record(
        engine_commit="0123456789abcdef0123" "456789abcdef01234567",
        openclaw_version="2026.11.2",
        harness_versions={"google-antigravity-cli": "1.5.9"},
        measured_at=parity_record.stamp(moment),
        lanes=lanes,
    )


# --------------------------------------------------------------- the readers


def test_the_parity_limit_is_the_owners_ten_thousand_input_tokens():
    assert parity_record.PARITY_OVERHEAD_TOKENS == 10000


def test_the_probe_message_is_the_one_the_owner_measured_with():
    assert parity.PROBE_MESSAGE == "Rispondi solo con la parola OK."


def test_the_measured_agy_answer_reads_its_input_tokens():
    assert probe.native_input(probe.AGY, agy_json(NATIVE_MEASURED)) == 18307


def test_the_measured_openclaw_answer_reads_its_last_call_input():
    assert probe.openclaw_input(openclaw_json(OPENCLAW_MEASURED)) == 25417


def test_a_claude_answer_adds_the_cache_it_keeps_apart_from_the_input():
    # 1000 + 400 written + 600 read: Anthropic reports the three separately.
    assert probe.native_input(probe.CLAUDE, claude_json(1000, cache_creation=400, cache_read=600)) == 2000


def test_an_agy_answer_adds_its_cache_read_and_creation():
    assert probe.native_input(probe.AGY, agy_json(1000, cache_read=200, cache_creation=100)) == 1300


def test_a_codex_answer_does_not_count_its_cached_tokens_twice():
    # OpenAI's `cached_input_tokens` is a subset of `input_tokens`, not a second count.
    assert probe.native_input(probe.CODEX, codex_jsonl(2000, cached=1500)) == 2000


def test_the_openclaw_side_adds_the_cache_too():
    assert probe.openclaw_input(openclaw_json(2500, cache_read=500, cache_write=100)) == 3100


def test_an_answer_without_the_usage_it_prints_is_refused_not_read_as_zero():
    with pytest.raises(probe.FormatError):
        probe.native_input(probe.AGY, json.dumps({"result": "OK"}))


def test_an_openclaw_answer_without_last_call_usage_is_refused():
    with pytest.raises(probe.FormatError):
        probe.openclaw_input(json.dumps({"result": {"meta": {"agentMeta": {"usage": {"input": 5}}}}}))


def test_an_answer_that_is_not_json_is_refused():
    with pytest.raises(probe.FormatError):
        probe.native_input(probe.CLAUDE, "OK\n")


def test_a_version_line_is_read_as_the_number_every_comparison_uses():
    assert probe.version_of("OpenClaw 2026.11.2 (b556fdf)") == "2026.11.2"
    assert probe.version_of("2.1.323 (Claude Code)") == "2.1.323"
    assert probe.version_of("1.5.9") == "1.5.9"


def test_the_native_commands_are_the_ones_each_help_page_documents():
    assert probe.native_argv(probe.AGY, "gemini-3.8-flash", "OK?") == (
        "agy", "-p", "OK?", "--output-format", "json", "--model", "gemini-3.8-flash")
    assert probe.native_argv(probe.CLAUDE, "claude-opus-5-5", "OK?") == (
        "claude", "-p", "OK?", "--output-format", "json", "--model", "claude-opus-5-5")
    assert probe.native_argv(probe.CODEX, "gpt-5-2-codex", "OK?") == (
        "codex", "exec", "--json", "--model", "gpt-5-2-codex", "OK?")


def test_the_openclaw_command_sends_the_message_in_its_own_fresh_session():
    assert probe.openclaw_argv("main", "parity-codex-cli-100", "OK?") == (
        "openclaw", "agent", "--agent", "main", "--session-key", "parity-codex-cli-100",
        "--message", "OK?", "--json")


# ------------------------------------------------------------- the measurement


def test_the_measured_lane_passes_with_the_owners_numbers(tmp_path):
    answers = Answers(agy=agy_json(NATIVE_MEASURED), openclaw=openclaw_json(OPENCLAW_MEASURED))

    record = parity.measure(harness_config(tmp_path), home=home_of(tmp_path), run=answers, now=1_700_000_000)

    lane = record.lanes[0]
    assert (lane.lane, lane.agent, lane.model) == (probe.AGY, "main", AGY_MODEL)
    assert (lane.native_input, lane.openclaw_input, lane.overhead) == (18307, 25417, OVERHEAD_MEASURED)
    assert lane.verdict == parity_record.PASS
    assert record.failed == ()
    assert record.worst is not None and record.worst.overhead == 7110


def test_a_lane_thirty_thousand_tokens_heavier_fails(tmp_path):
    answers = Answers(agy=agy_json(20000), openclaw=openclaw_json(50000))

    lane = parity.measure(harness_config(tmp_path), home=home_of(tmp_path), run=answers).lanes[0]

    assert lane.overhead == 30000
    assert lane.verdict == parity_record.FAIL
    assert "30,000 input tokens" in lane.reason
    assert "10,000 the owner allows" in lane.reason


def test_a_lane_exactly_on_the_limit_passes(tmp_path):
    answers = Answers(agy=agy_json(1000), openclaw=openclaw_json(11000))

    lane = parity.measure(harness_config(tmp_path), home=home_of(tmp_path), run=answers).lanes[0]

    assert lane.overhead == 10000
    assert lane.verdict == parity_record.PASS


def test_the_native_call_runs_in_the_agents_workspace(tmp_path):
    workspace = tmp_path / "workspace-main"
    workspace.mkdir()
    answers = Answers(agy=agy_json(NATIVE_MEASURED), openclaw=openclaw_json(OPENCLAW_MEASURED))

    parity.measure(harness_config(workspace), home=home_of(tmp_path), run=answers)

    native = next(call for call in answers.calls if call[0][0] == "agy")
    assert native[1] == workspace


def test_the_openclaw_side_is_asked_once_per_lane_with_its_own_session_key(tmp_path):
    answers = Answers(agy=agy_json(1), claude=claude_json(2),
                      openclaw=openclaw_json(3))
    config = harness_config(tmp_path, claude=True)

    parity.measure(config, home=home_of(tmp_path), run=answers, now=1_700_000_000)

    keys = [call[0][call[0].index("--session-key") + 1] for call in answers.calls
            if call[0][:2] == ["openclaw", "agent"]]
    assert keys == ["parity-google-antigravity-cli-17000" "00000", "parity-claude-cli-17000" "00000"]


def test_a_lane_no_agent_runs_on_is_not_applicable(tmp_path):
    config = {"agents": {"entries": {"main": {"model": "openai/gpt-6"}}}}

    record = parity.measure(config, home=home_of(tmp_path), run=Answers())

    assert [lane.verdict for lane in record.lanes] == [parity_record.NOT_APPLICABLE] * 3
    assert record.lanes[0].reason == "No configured agent runs on this lane."


def test_an_acp_agent_is_not_a_cli_backend_lane(tmp_path):
    # An ACP session runs the harness as itself, so there is no second prompt to compare.
    config = {"agents": {"entries": {"sviluppo": {
        "model": CLAUDE_MODEL,
        "runtime": {"type": "acp", "acp": {"agent": "claude", "cwd": str(tmp_path)}},
    }}}}

    record = parity.measure(config, home=home_of(tmp_path), run=Answers())

    assert record.lanes[0].verdict == parity_record.NOT_APPLICABLE


def test_a_codex_agent_is_measured_on_the_codex_lane(tmp_path):
    answers = Answers(codex=codex_jsonl(2000, cached=500), openclaw=openclaw_json(2500))
    config = harness_config(tmp_path, agy=False, codex=True)

    lane = parity.measure(config, home=home_of(tmp_path), run=answers).lanes[2]

    assert (lane.lane, lane.agent, lane.native_input, lane.overhead) == (probe.CODEX, "sviluppo", 2000, 500)


def test_the_first_agent_on_a_lane_is_the_one_measured(tmp_path):
    config = harness_config(tmp_path, agy=False, claude=True)
    config["agents"]["entries"]["automazioni"] = {"model": CLAUDE_MODEL, "workspace": str(tmp_path)}

    lane = parity.measure(config, home=home_of(tmp_path),
                          run=Answers(claude=claude_json(1), openclaw=openclaw_json(2))).lanes[1]

    assert lane.agent == "capo"


def test_a_missing_binary_fails_the_lane_in_one_line(tmp_path):
    answers = Answers(openclaw=openclaw_json(OPENCLAW_MEASURED))

    lane = parity.measure(harness_config(tmp_path), home=home_of(tmp_path), run=answers).lanes[0]

    assert lane.verdict == parity_record.FAIL
    assert lane.reason == "The google-antigravity-cli command is not installed here."


def test_a_timeout_fails_the_lane_with_the_deadline(tmp_path):
    answers = Answers(
        agy=probe.Ran(error=probe.TIMEOUT, detail="300"),
        openclaw=openclaw_json(OPENCLAW_MEASURED),
    )

    lane = parity.measure(harness_config(tmp_path), home=home_of(tmp_path), run=answers).lanes[0]

    assert lane.verdict == parity_record.FAIL
    assert "did not answer within 300 seconds" in lane.reason


def test_a_workspace_that_was_never_created_is_its_own_answer(tmp_path):
    missing = tmp_path / "workspace-that-was-never-created"

    ran = probe.default_runner(("agy", "-p", "OK?"), missing, 5.0)

    assert ran.error == probe.FOLDER
    assert ran.detail == str(missing)


def test_a_missing_workspace_reaches_the_lane_as_its_own_sentence(tmp_path):
    answers = Answers(agy=probe.Ran(error=probe.FOLDER, detail="/nowhere"),
                      openclaw=openclaw_json(OPENCLAW_MEASURED))

    lane = parity.measure(harness_config(tmp_path), home=home_of(tmp_path), run=answers).lanes[0]

    assert lane.verdict == parity.FAIL
    assert lane.reason.startswith("The folder /nowhere this agent works in does not exist.")


def test_a_failing_command_names_its_exit_code_and_its_last_line(tmp_path):
    answers = Answers(
        agy=probe.Ran(returncode=2, stdout="", stderr="model not found\n"),
        openclaw=openclaw_json(OPENCLAW_MEASURED),
    )

    lane = parity.measure(harness_config(tmp_path), home=home_of(tmp_path), run=answers).lanes[0]

    assert lane.verdict == parity_record.FAIL
    assert "exited with code 2: model not found" in lane.reason


def test_an_answer_this_module_does_not_understand_fails_instead_of_passing(tmp_path):
    answers = Answers(agy=json.dumps({"answer": "OK"}), openclaw=openclaw_json(OPENCLAW_MEASURED))

    lane = parity.measure(harness_config(tmp_path), home=home_of(tmp_path), run=answers).lanes[0]

    assert lane.verdict == parity_record.FAIL
    assert "was not understood" in lane.reason


def test_the_record_names_the_engine_commit_or_the_pin_the_vault_holds(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    (vault / "99-INDEX").mkdir(parents=True)
    (vault / "99-INDEX" / "ENGINE-PIN.txt").write_text("abcdef1234567890\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_VAULT_DATA", str(vault))

    record = parity.measure(harness_config(tmp_path), home=home_of(tmp_path),
                            engine_root=tmp_path / "no-checkout", run=Answers())

    assert record.engine_commit == "abcdef123"


# ------------------------------------------------------------------ the record


def test_the_record_carries_what_it_ran_on_and_one_row_per_lane(tmp_path):
    answers = Answers(agy=agy_json(NATIVE_MEASURED), openclaw=openclaw_json(OPENCLAW_MEASURED))

    record = parity.measure(harness_config(tmp_path), home=home_of(tmp_path), run=answers,
                            now=1_700_000_000)
    stored = json.loads(json.dumps(record.to_dict()))

    assert stored["measured_at"] == "2023-11-14T22:13:20+00:00"
    assert stored["openclaw_version"] == "2026.11.2"
    assert stored["harness_versions"] == {"google-antigravity-cli": "1.5.9"}
    assert stored["lanes"][0] == {
        "lane": "google-antigravity-cli", "agent": "main", "model": AGY_MODEL,
        "native_input": 18307, "openclaw_input": 25417, "overhead": 7110,
        "verdict": "pass", "reason": "",
    }


def test_a_run_on_a_machine_that_may_publish_writes_and_publishes_the_vault_file(tmp_path, monkeypatch):
    monkeypatch.setattr(parity_record, "readonly_reason", lambda *_args, **_kwargs: None)
    published: list[tuple[Path, str]] = []
    vault = tmp_path / "vault"

    kept = parity_record.keep(
        record_at(1_700_000_000), vault=vault, home=home_of(tmp_path),
        publish=lambda target_vault, target: (published.append(
            (target_vault, target.relative_to(target_vault).as_posix())) or (0, "published")),
    )

    assert kept.destination == parity_record.VAULT
    assert kept.path == vault / "99-INDEX" / "OPENCLAW-PARITY.json"
    assert json.loads(kept.path.read_text(encoding="utf-8"))["openclaw_version"] == "2026.11.2"
    assert published == [(vault, "99-INDEX/OPENCLAW-PARITY.json")]


def test_a_machine_that_reads_the_vault_keeps_the_record_in_its_state_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(parity_record, "readonly_reason",
                        lambda *_args, **_kwargs: "the server role owns this machine")
    monkeypatch.setenv("AGENT_STATE_DIR", str(tmp_path / "state"))
    vault = tmp_path / "vault"

    kept = parity_record.keep(record_at(1_700_000_000), vault=vault, home=home_of(tmp_path))

    assert kept.destination == parity_record.STATE
    assert kept.path == tmp_path / "state" / "nexgen" / "OPENCLAW-PARITY.json"
    assert kept.path.is_file()
    assert not (vault / "99-INDEX" / "OPENCLAW-PARITY.json").exists()
    assert str(vault / "99-INDEX" / "OPENCLAW-PARITY.json") in kept.note
    assert "Markdown notes only" in kept.note


def test_a_record_the_publisher_refuses_is_reported_as_unpublished(tmp_path, monkeypatch):
    monkeypatch.setattr(parity_record, "readonly_reason", lambda *_args, **_kwargs: None)

    kept = parity_record.keep(record_at(1_700_000_000), vault=tmp_path / "vault", home=home_of(tmp_path),
                       publish=lambda *_args: (1, "Refusing to publish from this vault clone"))

    assert kept.destination == parity_record.UNPUBLISHED
    assert kept.note == "Refusing to publish from this vault clone"


def test_a_one_lane_run_keeps_the_lanes_it_did_not_measure():
    previous = record_at(1_700_000_000, lanes=(
        parity_record.LaneResult(probe.AGY, "main", AGY_MODEL, 18307, 25417, parity_record.PASS),
        parity_record.LaneResult(probe.CLAUDE, "capo", CLAUDE_MODEL, 1000, 1005, parity_record.PASS),
    ))
    fresh = record_at(1_700_001_000, lanes=(
        parity_record.LaneResult(probe.AGY, "main", AGY_MODEL, 20000, 25000, parity_record.PASS),
    ))

    record = parity_record.merged(previous, fresh)

    assert [lane.lane for lane in record.lanes] == [probe.AGY, probe.CLAUDE]
    assert record.lanes[0].native_input == 20000
    assert record.lanes[1].native_input == 1000
    assert record.measured_at == fresh.measured_at


def test_the_newest_record_any_path_holds_is_the_one_read(tmp_path):
    vault = tmp_path / "vault"
    state = tmp_path / "state"
    older = record_at(1_700_000_000)
    newer = record_at(1_700_100_000)
    parity_record.write(vault / "OPENCLAW-PARITY.json", older)
    parity_record.write(state / "OPENCLAW-PARITY.json", newer)

    assert parity_record.load((vault / "OPENCLAW-PARITY.json", state / "OPENCLAW-PARITY.json")) == newer
    assert parity_record.load((tmp_path / "nothing.json",)) is None


# ------------------------------------------------------------------- the CLI


def test_the_dry_run_prints_the_commands_and_spends_nothing(tmp_path, capsys):
    config = harness_config(tmp_path)
    write_config(tmp_path, config)

    def spending(*_args):
        pytest.fail("a dry run must not start a model")

    code = parity.run_parity(dry_run=True, home=home_of(tmp_path), run=spending,
                             vault=tmp_path / "vault", now=1_700_000_000)

    printed = capsys.readouterr().out
    assert code == 0
    assert "agy -p Rispondi solo con la parola OK. --output-format json --model gemini-3.8-flash" in printed
    assert "openclaw agent --agent main --session-key parity-google-antigravity-cli-17000" "00000" in printed
    assert "No configured agent runs" in printed
    assert not (tmp_path / "vault").exists()


def test_the_command_prints_one_line_per_lane_and_where_the_record_went(tmp_path, capsys, monkeypatch):
    write_config(tmp_path, harness_config(tmp_path))
    monkeypatch.setattr(parity_record, "readonly_reason", lambda *_args, **_kwargs: None)
    monkeypatch.setenv("AGENT_STATE_DIR", str(tmp_path / "state"))
    answers = Answers(agy=agy_json(NATIVE_MEASURED), openclaw=openclaw_json(OPENCLAW_MEASURED))

    code = parity.run_parity(home=home_of(tmp_path), run=answers, vault=tmp_path / "vault",
                             now=1_700_000_000, publish=lambda *_args: (0, "published"))

    printed = capsys.readouterr().out
    assert code == 0
    assert "PASS  google-antigravity-cli" in printed
    assert "native 18,307  openclaw 25,417  overhead +7,110" in printed
    assert "1 measured, 0 failed, 2 not measurable, of 3 lanes." in printed
    assert "published" in printed


def test_the_command_exits_one_when_a_lane_fails(tmp_path, capsys, monkeypatch):
    write_config(tmp_path, harness_config(tmp_path))
    monkeypatch.setattr(parity_record, "readonly_reason", lambda *_args, **_kwargs: None)
    answers = Answers(agy=agy_json(20000), openclaw=openclaw_json(50000))

    code = parity.run_parity(home=home_of(tmp_path), run=answers, vault=tmp_path / "vault",
                             now=1_700_000_000, publish=lambda *_args: (0, "published"))

    assert code == 1
    assert "FAIL  google-antigravity-cli" in capsys.readouterr().out


def test_an_unreadable_configuration_is_one_sentence_not_a_trace(tmp_path, capsys):
    (home_of(tmp_path) / ".openclaw" / "openclaw.json").write_text("{not json", encoding="utf-8")

    code = parity.run_parity(home=home_of(tmp_path), vault=tmp_path / "vault")

    assert code == 2
    assert "Cannot read the OpenClaw configuration at" in capsys.readouterr().out


def test_an_unknown_lane_is_named_with_the_ones_this_measures(tmp_path, capsys):
    write_config(tmp_path, harness_config(tmp_path))

    code = parity.run_parity(lane="gemini", home=home_of(tmp_path), vault=tmp_path / "vault")

    assert code == 2
    assert "google-antigravity-cli, claude-cli, codex-cli" in capsys.readouterr().out


def test_the_json_output_carries_the_same_rows_and_the_record_path(tmp_path, capsys, monkeypatch):
    write_config(tmp_path, harness_config(tmp_path))
    monkeypatch.setattr(parity_record, "readonly_reason", lambda *_args, **_kwargs: None)
    answers = Answers(agy=agy_json(NATIVE_MEASURED), openclaw=openclaw_json(OPENCLAW_MEASURED))

    parity.run_parity(as_json=True, home=home_of(tmp_path), run=answers, vault=tmp_path / "vault",
                      now=1_700_000_000, publish=lambda *_args: (0, "published"))

    answer = json.loads(capsys.readouterr().out)
    assert answer["lanes"][0]["overhead"] == 7110
    assert answer["record_destination"] == "vault"
    assert answer["record_path"] == str(tmp_path / "vault" / "99-INDEX" / "OPENCLAW-PARITY.json")
    assert answer["overhead_limit"] == 10000


# ------------------------------------------------- what the gateway taught us


def test_claude_runs_natively_on_the_login_openclaw_gives_it_never_on_an_api_key():
    environ = {"PATH": "/usr/bin", "HOME": "/home/user", "ANTHROPIC_API_KEY": "placeholder",
               "ANTHROPIC_BASE_URL": "http://127.0.0.1:1", "CLAUDE_CODE_OAUTH_TOKEN": "placeholder"}

    child = probe.child_env(("claude", "-p", "x"), environ)

    assert child == {"PATH": "/usr/bin", "HOME": "/home/user"}


def test_the_other_commands_keep_the_environment_they_were_given():
    environ = {"PATH": "/usr/bin", "ANTHROPIC_API_KEY": "placeholder"}

    assert probe.child_env(("agy", "-p", "x"), environ) == environ
    assert probe.child_env(("openclaw", "agent"), environ) == environ


def test_a_lane_whose_native_side_failed_prints_without_a_number(tmp_path):
    broken = parity_record.LaneResult(probe.CLAUDE, "capo", CLAUDE_MODEL, None, 66784, parity_record.FAIL,
                                      "The claude-cli command exited with code 1.")
    record = parity_record.Record(measured_at="2026-11-15T22:15:00+00:00", lanes=(broken,))

    printed = parity.render(record, parity_record.Kept(parity_record.STATE, tmp_path / "r.json"))

    assert "FAIL  claude-cli" in printed
    assert "native -" in printed
    assert "exited with code 1" in printed
