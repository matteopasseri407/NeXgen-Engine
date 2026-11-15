"""The optional ccusage backend for OpenClaw spend: the recorded answer, and every way it fails.

Both fixtures are written from the shape `ccusage` documents for its OpenClaw
source (`docs/guide/openclaw/index.md`) and from the two functions that build it
(`rust/adapters/openclaw/src/report.rs` and
`rust/crates/ccusage-core/src/agent_report.rs`), because nothing on this machine
runs that binary in a test. `daily` keys its rows on `date` and `session` on
`sessionId`, both carrying `totalTokens`, `totalCost` and `modelsUsed`; `session`
adds the two activity stamps the per-agent line needs.

The third fixture is the shape moving: a document that answers `rows` where the
report expects `daily`. It has to be refused whole, because a reader that printed
the two keys it recognised and guessed at the third is how a wrong number reaches a
person who is trying to cut a bill.

The agent attribution is this engine's half of the job and is tested against a
state directory laid out the way OpenClaw writes one:
`agents/<agentId>/sessions/<sessionId>.jsonl`, with an archived transcript
carrying its stem in a longer name.
"""
from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from nexgen_core import ccusage, i18n
from nexgen_core.cli import build_parser
from nexgen_core.openclaw import spend
from nexgen_core.report import Severity

#: The recorded answers, and the one document with a shape this module refuses.
DAILY_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "ccusage-openclaw-daily.json"
SESSION_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "ccusage-openclaw-session.json"
UNKNOWN_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "ccusage-openclaw-unknown.json"

#: The moment every test reads at: noon local, so "yesterday" is 2026-11-14 in
#: whatever time zone the machine running these tests is in.
NOW = datetime(2026, 11, 15, 12, 0, 0).timestamp()

#: The two session ids the session fixture carries, and the agent folder each
#: belongs to on a machine the engine set up.
CAPO_SESSION = "595239de-69ca-402d-b1d5-431335d07102"
MAIN_SESSION = "1d371317-e3a3-4626-b5e9-e1e158ec5339"


@pytest.fixture(autouse=True)
def _english_notes():
    """Pin the source language: these tests assert note contents."""
    i18n.set_language("en")
    yield
    i18n.set_language(None)


@pytest.fixture(autouse=True)
def _no_inherited_override(monkeypatch):
    """No inherited switch may turn the backend off under these tests."""
    monkeypatch.delenv(spend.ENV_OVERRIDE, raising=False)


def daily() -> str:
    """The recorded `openclaw daily --json` answer, byte for byte."""
    return DAILY_FIXTURE.read_text(encoding="utf-8")


def sessions() -> str:
    """The recorded `openclaw session --json` answer, byte for byte."""
    return SESSION_FIXTURE.read_text(encoding="utf-8")


def unknown() -> str:
    """The document whose rows are under `rows` instead of `daily`."""
    return UNKNOWN_FIXTURE.read_text(encoding="utf-8")


def backend(monkeypatch, *, answers: dict[str, str] | None = None,
            code: int = 0) -> list[tuple[tuple[str, ...], Path]]:
    """A ccusage on `PATH` that answers both views, and the runs it was asked for."""
    runs: list[tuple[tuple[str, ...], Path]] = []
    payloads = answers if answers is not None else {"daily": daily(), "session": sessions()}

    def spawn(argv: list[str], cwd: Path) -> tuple[int, str]:
        runs.append((tuple(argv), cwd))
        return code, payloads[argv[2]]

    monkeypatch.setattr(ccusage, "binary", lambda: "/usr/bin/ccusage")
    monkeypatch.setattr(ccusage, "spawn", spawn)
    return runs


def failing(monkeypatch, error: BaseException) -> None:
    """A ccusage that cannot answer at all: it raises on the first run."""
    def spawn(argv: list[str], cwd: Path) -> tuple[int, str]:
        raise error

    monkeypatch.setattr(ccusage, "binary", lambda: "/usr/bin/ccusage")
    monkeypatch.setattr(ccusage, "spawn", spawn)


def openclaw_home(root: Path) -> Path:
    """A state directory holding the transcripts of the two sessions, for two agents."""
    agents = root / "agents"
    (agents / "capo" / "sessions").mkdir(parents=True, exist_ok=True)
    (agents / "main" / "sessions").mkdir(parents=True, exist_ok=True)
    (agents / "capo" / "sessions" / f"{CAPO_SESSION}.jsonl").write_text("", encoding="utf-8")
    # An archived transcript keeps its session id in a longer name.
    (agents / "main" / "sessions" /
     f"{MAIN_SESSION}.jsonl.deleted.2026-10-23T10-00-17.770Z.4d5d19b341b541209ebb4535c818b468.zst"
     ).write_text("", encoding="utf-8")
    return root


def read(tmp_path: Path, *, now: float = NOW, since: str | None = None,
         config: Path | None = None, state_dir: Path | None = None) -> spend.Spend:
    """One reading, from the home the cache lives in and the config it obeys."""
    return spend.read_spend(tmp_path, config=config, state_dir=state_dir, now=now, since=since)


# ------------------------------------------------------------------ the answer


def test_the_recorded_daily_answer_becomes_one_row_per_day():
    days = spend.parse_daily(daily())

    assert [(day.date, day.tokens, day.cost_usd) for day in days] == [
        ("2026-11-13", 112383, 0.03),
        ("2026-11-14", 255410, 0.42),
    ]
    assert days[0].models == ("[openclaw] gpt-5.2",)


def test_the_recorded_session_answer_carries_the_activity_the_agent_line_needs():
    found = spend.parse_sessions(sessions())

    assert [session.session_id for session in found] == [CAPO_SESSION, MAIN_SESSION]
    assert [(session.tokens, session.cost_usd) for session in found] == [
        (255410, 0.42),
        (112383, 0.03),
    ]
    assert found[0].last_activity == "2026-11-14T21:10:00.000Z"
    assert found[1].last_activity == "2026-11-13T09:41:00.000Z"


def test_a_reading_is_the_sum_of_its_days(tmp_path, monkeypatch):
    backend(monkeypatch)

    reading = read(tmp_path)

    assert reading.known
    assert reading.note == ""
    assert reading.tokens == 367793
    assert reading.cost_usd == pytest.approx(0.45)
    assert reading.read_at == NOW


def test_every_session_is_attributed_to_the_agent_folder_that_owns_it(tmp_path, monkeypatch):
    backend(monkeypatch)
    state = openclaw_home(tmp_path / ".openclaw")

    reading = read(tmp_path, state_dir=state)

    assert [(session.session_id, session.agent) for session in reading.sessions] == [
        (CAPO_SESSION, "capo"),
        (MAIN_SESSION, "main"),
    ]
    assert reading.by_agent() == (("capo", 255410, 0.42), ("main", 112383, 0.03))


def test_the_session_ids_are_read_from_the_names_openclaw_writes(tmp_path):
    state = openclaw_home(tmp_path / ".openclaw")

    assert spend.session_agents(state) == {CAPO_SESSION: "capo", MAIN_SESSION: "main"}


def test_a_session_with_no_transcript_left_is_unattributed_and_named_so():
    attributed = spend.attribute(spend.parse_sessions(sessions()), {})

    assert [session.agent for session in attributed] == ["", ""]


def test_the_agents_are_ordered_by_what_they_cost(tmp_path, monkeypatch):
    backend(monkeypatch)
    state = openclaw_home(tmp_path / ".openclaw")

    agents = read(tmp_path, state_dir=state).by_agent()

    assert agents[0][0] == "capo"
    assert agents[0][2] == pytest.approx(0.42)


# ------------------------------------------------------------------ the failures


def test_the_unknown_shape_is_refused_whole_with_the_key_it_expected():
    with pytest.raises(spend.FormatError) as refused:
        spend.parse_daily(unknown())

    assert "there is no daily list" in str(refused.value)


def test_an_unknown_shape_costs_the_numbers_and_leaves_one_note(tmp_path, monkeypatch):
    backend(monkeypatch, answers={"daily": unknown(), "session": sessions()})

    reading = read(tmp_path)

    assert not reading.known
    assert reading.days == ()
    assert reading.note == (
        "ccusage output format not understood (there is no daily list): its numbers are not shown."
    )


def test_a_row_with_no_total_cost_is_refused(tmp_path, monkeypatch):
    answer = json.loads(daily())
    del answer["daily"][0]["totalCost"]
    backend(monkeypatch, answers={"daily": json.dumps(answer), "session": sessions()})

    with pytest.raises(spend.FormatError) as refused:
        spend.parse_daily(json.dumps(answer))

    assert "2026-11-13 carries no numeric totalCost" in str(refused.value)


def test_a_missing_binary_is_one_note_and_never_an_exception(tmp_path, monkeypatch):
    monkeypatch.setattr(ccusage, "binary", lambda: None)

    reading = read(tmp_path)

    assert not reading.known
    assert reading.note == (
        "ccusage is not installed on this machine: the optional OpenClaw spend backend is skipped."
    )


def test_a_non_zero_exit_is_one_note(tmp_path, monkeypatch):
    backend(monkeypatch, code=1)

    reading = read(tmp_path)

    assert not reading.known
    assert reading.note == "ccusage exited with code 1: its numbers are not shown."


def test_a_timeout_is_one_note_and_never_holds_the_command(tmp_path, monkeypatch):
    failing(monkeypatch, subprocess.TimeoutExpired(cmd="ccusage", timeout=30))

    reading = read(tmp_path)

    assert not reading.known
    assert reading.note == (
        "ccusage did not answer within 30 seconds: its numbers are not shown."
    )


def test_a_binary_that_cannot_start_is_the_missing_one(tmp_path, monkeypatch):
    failing(monkeypatch, OSError("no such file"))

    assert read(tmp_path).note.startswith("ccusage is not installed")


# ------------------------------------------------------------------ the switch


def quota_file(home: Path, text: str) -> Path:
    """The `quota.yaml` this machine declares, at the path it is read from."""
    path = home / "KnowledgeVault" / "03-INFRA" / "agent-universal-layer" / "quota.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_the_backend_is_off_when_the_file_says_off(tmp_path, monkeypatch):
    runs = backend(monkeypatch)
    config = quota_file(tmp_path, "backends:\n  ccusage: off\n")

    reading = read(tmp_path, config=config)

    assert not reading.known
    assert runs == []


def test_the_switch_is_off_for_one_run_by_environment(tmp_path, monkeypatch):
    runs = backend(monkeypatch)
    monkeypatch.setenv(spend.ENV_OVERRIDE, "off")

    assert not read(tmp_path).known
    assert runs == []


# ------------------------------------------------------------------ the cache


def test_one_answer_is_reused_until_the_interval_ends(tmp_path, monkeypatch):
    runs = backend(monkeypatch)

    first = read(tmp_path)
    second = read(tmp_path, now=NOW + 60)

    assert first.read_at == NOW
    assert second.read_at == NOW
    assert len(runs) == 2, "daily and session once, not twice"


def test_a_different_date_filter_buys_a_run_of_its_own(tmp_path, monkeypatch):
    runs = backend(monkeypatch)

    read(tmp_path)
    read(tmp_path, since="2026-11-14")

    assert len(runs) == 4
    assert [argv[-2:] for argv, _cwd in runs[2:]] == [("--since", "2026-11-14")] * 2


def test_a_date_the_binary_cannot_read_is_refused_before_any_run(tmp_path, monkeypatch):
    runs = backend(monkeypatch)

    with pytest.raises(ValueError) as refused:
        read(tmp_path, since="14/11/2026")

    assert "2026-11-14 or 20261114" in str(refused.value)
    assert runs == []


def test_the_run_starts_in_the_state_directory_and_never_the_callers(tmp_path, monkeypatch):
    runs = backend(monkeypatch)

    read(tmp_path)

    folder = spend.workdir(tmp_path)
    assert [cwd for _argv, cwd in runs] == [folder, folder]
    assert folder == tmp_path / ".local" / "state" / "nexgen"
    assert folder.is_dir()


# ------------------------------------------------------------------ the doctor line


def test_yesterday_is_one_line_per_agent_and_never_red(tmp_path, monkeypatch):
    backend(monkeypatch)
    state = openclaw_home(tmp_path / ".openclaw")

    outcome = spend.check_spend(tmp_path, state_dir=state, now=NOW)

    assert outcome is not None
    assert outcome.id == "openclaw.spend"
    assert outcome.severity == Severity.OK
    assert outcome.action is None
    assert outcome.message == (
        "Yesterday OpenClaw spent $0.42 on 255,410 tokens: capo $0.42."
    )


def test_a_day_with_no_row_prints_no_line(tmp_path, monkeypatch):
    backend(monkeypatch)

    later = datetime(2026, 11, 20, 12, 0, 0).timestamp()

    assert spend.check_spend(tmp_path, now=later) is None


def test_a_missing_backend_prints_no_line(tmp_path, monkeypatch):
    monkeypatch.setattr(ccusage, "binary", lambda: None)

    assert spend.check_spend(tmp_path, now=NOW) is None


# ------------------------------------------------------------------ the two surfaces


def test_the_json_view_carries_the_numbers_and_never_a_formatted_string(tmp_path, monkeypatch):
    backend(monkeypatch)
    state = openclaw_home(tmp_path / ".openclaw")

    data = spend.as_dict(read(tmp_path, state_dir=state))

    assert data["totals"] == {"tokens": 367793, "cost_usd": pytest.approx(0.45)}
    assert data["daily"][1] == {"date": "2026-11-14", "tokens": 255410,
                                "cost_usd": 0.42, "models": ["[openclaw] claude-opus-5-5"]}
    assert data["agents"][0] == {"agent": "capo", "tokens": 255410, "cost_usd": 0.42}


def test_the_report_names_the_days_the_total_and_the_agents(tmp_path, monkeypatch):
    backend(monkeypatch)
    state = openclaw_home(tmp_path / ".openclaw")

    text = spend.render(read(tmp_path, state_dir=state))

    assert "2026-11-14" in text
    assert "$0.45" in text
    assert "367,793 tokens" in text
    assert "capo" in text and "main" in text


def test_the_command_is_registered_next_to_groom():
    parsed = build_parser().parse_args(
        ["openclaw", "cost", "--json", "--since", "2026-11-14"]
    )

    assert parsed.func.__name__ == "cmd_cost"
    assert parsed.json is True
    assert parsed.since == "2026-11-14"
