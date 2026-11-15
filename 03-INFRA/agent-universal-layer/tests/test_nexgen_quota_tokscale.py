"""The optional tokscale backend: the recorded answer, and every way it can fail.

The fixture is one real `tokscale usage --json` run from 2026-11-14 with the
personal fields removed, so the shape is the binary's and not this test's idea of
it. One float is rounded: the binary printed nineteen digits for Copilot's chat
window, and the repository's leak scanner reads a run that long as an identifier.
Nothing here starts that binary either: `_binary` and `_spawn` are the two seams,
and the tests that matter most are the ones about failure, because a wrong number
is worse than the line that says there is none.

The second fixture is the same machine with Claude throttled. No run on this
machine produced it: `tokscale usage --json` omits a provider it has no sample
for, and the run that hit the limit had none, so the fixture carries the shape
`claude.rs` builds when it does, and `throttled()` adds the `freshness` block
that dates the sample.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from nexgen_core import i18n, processes, quota, quota_tokscale

#: The recorded answer, the throttled one, and the moment every test reads at.
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "tokscale-usage.json"
STALE_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "tokscale-usage-claude-stale.json"
NOW = datetime(2026, 11, 14, 12, 0, 0, tzinfo=timezone.utc).timestamp()

#: The moment inside the throttled answer's `freshness`, forty minutes before
#: `NOW`: the reading Claude gave when it still answered.
FETCHED_AT = datetime(2026, 11, 14, 11, 20, 0, tzinfo=timezone.utc).timestamp()

#: The resets the fixture carries, as the instants those stamps name: two with an
#: offset or a `Z`, and one date alone, which a window with no time means at the
#: start of that local day.
CODEX_RESET = datetime(2026, 11, 17, 11, 29, 57, tzinfo=timezone.utc).timestamp()
COPILOT_RESET = datetime(2026, 12, 1).timestamp()
ROLLING_RESET = datetime(2026, 11, 15, 1, 4, 52, tzinfo=timezone.utc).timestamp()
MONTHLY_RESET = datetime(2026, 12, 14, 19, 15, 41, tzinfo=timezone.utc).timestamp()


@pytest.fixture(autouse=True)
def _english_notes():
    """Pin the source language: these tests assert note contents, and the
    machine locale would otherwise turn them Italian."""
    i18n.set_language("en")
    yield
    i18n.set_language(None)


@pytest.fixture(autouse=True)
def _the_switch_is_this_file(monkeypatch):
    """No inherited override may turn the backend off, or move its state, under these tests."""
    monkeypatch.delenv(quota_tokscale.ENV_OVERRIDE, raising=False)
    monkeypatch.delenv(quota_tokscale.CONFIG_DIR_ENV, raising=False)


@pytest.fixture(autouse=True)
def _no_real_claude(monkeypatch):
    """No test may run the `claude` this machine has on PATH.

    A rejected login makes the module ask Claude Code to renew its own token, and
    on the machine that runs these tests that program exists: leaving it reachable
    would make a test rewrite the credentials of whoever is logged in, for a
    backend this suite is supposed to fake. The tests that are about the refresh
    replace it themselves.
    """
    monkeypatch.setattr(quota_tokscale, "_login_binary", lambda: None)


def recorded() -> str:
    """The recorded answer, byte for byte."""
    return FIXTURE.read_text(encoding="utf-8")


def throttled(fetched_at: float = FETCHED_AT) -> str:
    """The recorded answer of the same machine, with Claude on its last sample.

    `freshness` is added here rather than stored in the fixture: it holds an
    epoch, and the repository's leak scanner reads ten digits in a row as an
    identifier, so a fixture that spelled it out could not be committed. The
    shape is still the binary's, from `claude.rs`: the metrics of the last good
    sample, and the moment it was taken beside them.
    """
    entries = json.loads(STALE_FIXTURE.read_text(encoding="utf-8"))
    for entry in entries:
        if entry["provider"] == "Claude":
            entry["freshness"] = {"fetched_at": int(fetched_at), "stale": True}
    return json.dumps(entries)


def cooldown_record(home: Path, *, issued: float, expires: float, version: int = 1,
                    name: str = "account") -> Path:
    """One cooldown tokscale recorded, where it records it: two stamps and no credential."""
    folder = quota_tokscale.config_dir(home).joinpath(*quota_tokscale.COOLDOWN_SUBPATH)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.json"
    path.write_text(json.dumps({"version": version, "issued_at": int(issued),
                                "expires_at": int(expires)}), encoding="utf-8")
    return path


def backend(monkeypatch, answer: str = "", *, code: int = 0) -> list[str]:
    """A tokscale on `PATH` that answers `answer`, and the runs it was asked for."""
    runs: list[str] = []

    def spawn(executable: str, cwd: Path) -> tuple[int, str]:
        runs.append(executable)
        return code, answer

    monkeypatch.setattr(quota_tokscale, "_binary", lambda: "/usr/bin/tokscale")
    monkeypatch.setattr(quota_tokscale, "_spawn", spawn)
    return runs


def backend_answers(monkeypatch, *answers: str) -> list[str]:
    """A tokscale on `PATH` that answers these in turn, and the runs it was asked for."""
    runs: list[str] = []

    def spawn(executable: str, cwd: Path) -> tuple[int, str]:
        runs.append(executable)
        return 0, answers[(len(runs) - 1) % len(answers)]

    monkeypatch.setattr(quota_tokscale, "_binary", lambda: "/usr/bin/tokscale")
    monkeypatch.setattr(quota_tokscale, "_spawn", spawn)
    return runs


def claude_answer(used: float = 34.0) -> str:
    """One answer that carries the plan a refreshed login brings back."""
    return json.dumps([{"provider": "Claude", "plan": "Max 5x", "metrics": [
        {"label": "Session", "used_percent": used, "resets_at": None}]}])


def login(monkeypatch, *, code: int = 0) -> list[dict]:
    """A `claude auth status` that answers `code`, and every launch it was asked for.

    Each launch is recorded with the whole keyword set `Popen` received, so a test
    can see where the command's output was allowed to go.
    """
    launched: list[dict] = []

    class _Login:
        pid = 5252

        def __init__(self, argv, **kwargs):
            self.argv = argv
            self.returncode = code
            launched.append({"argv": list(argv), **kwargs})

        def communicate(self, timeout=None):
            return None, None

    monkeypatch.setattr(quota_tokscale, "_login_binary", lambda: "/usr/bin/claude")
    monkeypatch.setattr(quota_tokscale.subprocess, "Popen", _Login)
    return launched


def read(home: Path, config: Path | None = None, now: float = NOW) -> quota_tokscale.TokscaleUsage:
    """One reading, from the home the cache lives in and the config it obeys."""
    return quota_tokscale.read_usage(home, config=config, now=now)


def quota_file(home: Path, text: str) -> Path:
    """The `quota.yaml` the report reads, at the path it reads it from."""
    path = home / "KnowledgeVault" / "03-INFRA" / "agent-universal-layer" / "quota.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def codex_reading(home: Path, *, used: float = 59.0, window: int = 43200) -> Path:
    """A Codex session carrying one rate-limit line, where Codex writes it."""
    folder = home / ".codex" / "sessions" / "2026" / "11" / "14"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "rollout-2026-11-14.jsonl"
    path.write_text(json.dumps({
        "timestamp": "2026-11-14T11:56:00.000Z",
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "rate_limits": {
                "limit_id": "codex",
                "primary": {"used_percent": used, "window_minutes": window, "resets_at": int(NOW) + 3600},
                "secondary": None,
                "credits": {"has_credits": False, "balance": None},
                "plan_type": "free",
            },
        },
    }) + "\n", encoding="utf-8")
    return path


# ------------------------------------------------------------------ the answer


def test_the_recorded_answer_becomes_one_reading_per_window(tmp_path, monkeypatch):
    backend(monkeypatch, recorded())

    usage = read(tmp_path)

    assert usage.known
    assert usage.note == ""
    assert [(metric.provider, metric.window) for metric in usage.metrics] == [
        ("Codex", "30d"),
        ("Copilot", "Chat"),
        ("Copilot", "Completions"),
        ("Copilot", "Premium"),
        ("OpenCode Go", "Rolling"),
        ("OpenCode Go", "Weekly"),
        ("OpenCode Go", "Monthly"),
    ]
    assert usage.metrics[0].used_percent == 72.0
    assert usage.metrics[0].provider_id == "openai"
    assert usage.metrics[1].used_percent == 0.7
    assert usage.metrics[2].used_percent == 0.0
    assert usage.metrics[3].used_percent == 100.0
    assert usage.metrics[4].provider_id == "opencode-go"
    assert usage.metrics[4].used_percent == 1.0


def test_the_reset_of_each_window_is_read_from_the_stamp_it_carries(tmp_path, monkeypatch):
    backend(monkeypatch, recorded())

    metrics = read(tmp_path).metrics

    assert metrics[0].resets_at == pytest.approx(CODEX_RESET)    # an offset
    assert metrics[1].resets_at == pytest.approx(COPILOT_RESET)  # a date alone
    assert metrics[4].resets_at == pytest.approx(ROLLING_RESET)  # a Z
    assert metrics[6].resets_at == pytest.approx(MONTHLY_RESET)


def test_a_window_with_no_reset_is_a_window_with_no_reset(tmp_path, monkeypatch):
    backend(monkeypatch, json.dumps([{"provider": "Codex", "metrics": [
        {"label": "5h", "used_percent": 3.0, "resets_at": None}]}]))

    metrics = read(tmp_path).metrics

    assert metrics[0].resets_at is None
    assert metrics[0].used_percent == 3.0


def test_a_provider_this_engine_never_read_keeps_an_id_of_its_own(tmp_path, monkeypatch):
    backend(monkeypatch, json.dumps([{"provider": "Z.ai", "metrics": [
        {"label": "Weekly", "used_percent": 12.0, "resets_at": None}]}]))

    metric = read(tmp_path).metrics[0]

    assert metric.provider == "Z.ai"
    assert metric.provider_id == "z-ai"


def test_the_output_carries_no_schema_version_and_a_renamed_field_is_not_guessed(tmp_path, monkeypatch):
    backend(monkeypatch, json.dumps([{"provider": "Codex", "windows": [
        {"label": "5h", "used_percent": 3.0, "resets_at": None}]}]))

    usage = read(tmp_path)

    assert not usage.known
    assert "output format not understood" in usage.note
    assert "metrics list" in usage.note


@pytest.mark.parametrize(
    ("answer", "why"),
    [
        ("{not json", "not JSON"),
        ('{"provider": "Codex"}', "the top level is not a list"),
        ('["Codex"]', "a provider entry is not an object"),
        ('[{"metrics": []}]', "a provider entry has no name"),
        ('[{"provider": "Codex", "metrics": [{"used_percent": 1.0, "resets_at": null}]}]',
         "a window with no label"),
        ('[{"provider": "Codex", "metrics": [{"label": "5h", "used_percent": 1.0}]}]',
         "carries no resets_at"),
        ('[{"provider": "Codex", "metrics": [{"label": "5h", "used_percent": "1", "resets_at": null}]}]',
         "carries no numeric used_percent"),
        ('[{"provider": "Codex", "metrics": [{"label": "5h", "used_percent": true, "resets_at": null}]}]',
         "carries no numeric used_percent"),
        ('[{"provider": "Codex", "metrics": [{"label": "5h", "used_percent": 1.0, "resets_at": 12}]}]',
         "not a date"),
        ('[{"provider": "Codex", "metrics": [{"label": "5h", "used_percent": 1.0, "resets_at": "domani"}]}]',
         "this engine cannot read"),
    ],
)
def test_a_shape_this_module_cannot_read_is_refused_whole(tmp_path, monkeypatch, answer, why):
    backend(monkeypatch, answer)

    usage = read(tmp_path)

    assert usage.metrics == ()
    assert "output format not understood" in usage.note
    assert why in usage.note


# ------------------------------------------------------------------- the fails


def test_a_machine_without_the_binary_says_so_and_nothing_else(tmp_path, monkeypatch):
    monkeypatch.setattr(quota_tokscale, "_binary", lambda: None)

    usage = read(tmp_path)

    assert not usage.known
    assert "not installed on this machine" in usage.note
    assert "skipped" in usage.note


def test_an_exit_code_that_is_not_zero_is_one_line(tmp_path, monkeypatch):
    backend(monkeypatch, code=2)

    usage = read(tmp_path)

    assert not usage.known
    assert "exited with code 2" in usage.note


def test_a_binary_that_never_answers_is_one_line(tmp_path, monkeypatch):
    def slow(executable: str, cwd: Path) -> tuple[int, str]:
        raise quota_tokscale.subprocess.TimeoutExpired(executable, quota_tokscale.RUN_TIMEOUT_SECONDS)

    monkeypatch.setattr(quota_tokscale, "_binary", lambda: "/usr/bin/tokscale")
    monkeypatch.setattr(quota_tokscale, "_spawn", slow)

    usage = read(tmp_path)

    assert not usage.known
    assert "did not answer within 20 seconds" in usage.note


def test_a_binary_that_cannot_be_started_reads_as_missing(tmp_path, monkeypatch):
    def gone(executable: str, cwd: Path) -> tuple[int, str]:
        raise FileNotFoundError(executable)

    monkeypatch.setattr(quota_tokscale, "_binary", lambda: "/usr/bin/tokscale")
    monkeypatch.setattr(quota_tokscale, "_spawn", gone)

    assert "not installed" in read(tmp_path).note


def test_an_answer_with_no_metrics_is_not_a_failure(tmp_path, monkeypatch):
    backend(monkeypatch, "[]")

    usage = read(tmp_path)

    assert not usage.known
    assert usage.note == ""


def test_a_shim_that_cannot_be_started_reads_as_missing(tmp_path, monkeypatch):
    """An npm shim whose node entry is gone cannot start at all: that is the "not installed" line.

    `windows_command_argv` refuses it with `ShimEntryError`, and a refusal to
    start is the same answer as an absent binary, not a traceback out of `nexgen
    quota`.
    """
    def broken(argv: list[str]) -> list[str]:
        raise processes.ShimEntryError("no node entry beside the shim")

    monkeypatch.setattr(quota_tokscale, "_binary", lambda: "/usr/bin/tokscale.cmd")
    monkeypatch.setattr(quota_tokscale.processes, "windows_command_argv", broken)

    usage = read(tmp_path)

    assert not usage.known
    assert usage.note == (
        "tokscale is not installed on this machine: the optional quota backend is skipped."
    )


class _HungChild:
    """A child that never answers: `communicate` runs out of time every time."""

    pid = 4242

    def __init__(self, argv, **kwargs):
        self.argv = argv
        self.returncode = None

    def communicate(self, timeout=None):
        raise quota_tokscale.subprocess.TimeoutExpired(self.argv, timeout)

    def poll(self):
        return self.returncode


def test_a_run_that_hangs_is_stopped_with_its_whole_tree(tmp_path, monkeypatch):
    """Killing the direct child is not the cleanup: the shim's `node` grandchild holds the pipe.

    The engine owns one primitive for this (`processes.force_stop_process_tree`),
    and a run that hangs must cost the timeout rather than the command that asked.
    """
    stopped: list = []
    monkeypatch.setattr(quota_tokscale, "_binary", lambda: "/usr/bin/tokscale")
    monkeypatch.setattr(quota_tokscale.subprocess, "Popen", _HungChild)
    monkeypatch.setattr(quota_tokscale.processes, "force_stop_process_tree",
                        lambda proc, **kwargs: stopped.append(proc) or True)

    usage = read(tmp_path)

    assert [proc.pid for proc in stopped] == [4242]
    assert "did not answer within 20 seconds" in usage.note


# ------------------------------------------------------------------- the cache


def test_one_answer_is_bought_once_and_reused_inside_its_lifetime(tmp_path, monkeypatch):
    runs = backend(monkeypatch, recorded())

    first = read(tmp_path, now=NOW)
    second = read(tmp_path, now=NOW + 60)

    assert len(runs) == 1
    assert second.metrics == first.metrics
    assert second.read_at == NOW


def test_the_answer_is_bought_again_once_it_is_older_than_the_cache(tmp_path, monkeypatch):
    runs = backend(monkeypatch, recorded())

    read(tmp_path, now=NOW)
    read(tmp_path, now=NOW + quota_tokscale.CACHE_TTL_SECONDS)

    assert len(runs) == 2


def test_a_caller_with_a_deadline_reads_the_last_answer_and_buys_nothing(tmp_path, monkeypatch):
    """The line a session starts with may not spend a run: it gets the stored answer."""
    runs = backend(monkeypatch, recorded())

    bought = read(tmp_path, now=NOW)
    asked = quota_tokscale.read_usage(tmp_path, now=NOW + 60, max_age=3600)

    assert len(runs) == 1
    assert asked.metrics == bought.metrics
    assert asked.read_at == NOW


def test_a_caller_with_a_deadline_gets_nothing_when_the_answer_is_older_than_it(tmp_path, monkeypatch):
    runs = backend(monkeypatch, recorded())

    read(tmp_path, now=NOW)
    asked = quota_tokscale.read_usage(tmp_path, now=NOW + 3600, max_age=1800)

    assert len(runs) == 1, "nessuna corsa comprata per una riga con un limite di tempo"
    assert asked.metrics == ()


def test_a_failure_is_kept_too_so_a_hanging_binary_is_not_run_every_hour(tmp_path, monkeypatch):
    runs: list[str] = []

    def counted(executable: str, cwd: Path) -> tuple[int, str]:
        runs.append(executable)
        raise quota_tokscale.subprocess.TimeoutExpired(executable, quota_tokscale.RUN_TIMEOUT_SECONDS)

    monkeypatch.setattr(quota_tokscale, "_binary", lambda: "/usr/bin/tokscale")
    monkeypatch.setattr(quota_tokscale, "_spawn", counted)

    read(tmp_path, now=NOW)
    second = read(tmp_path, now=NOW + 60)

    assert len(runs) == 1
    assert "did not answer within 20 seconds" in second.note


def test_a_failure_after_a_good_answer_keeps_the_numbers_it_had(tmp_path, monkeypatch):
    """One timeout must not blank the plan the run before it really read.

    The readings are written back into the cache on a hard failure (`_carried`),
    so they have to travel with the sentence that says why nothing was refreshed:
    otherwise every caller loses a live row for the ten minutes the failure is kept.
    """
    backend(monkeypatch, json.dumps([{"provider": "Codex", "metrics": [
        {"label": "30d", "used_percent": 72.0, "resets_at": None}]}]))
    read(tmp_path, now=NOW)

    def slow(executable: str, cwd: Path) -> tuple[int, str]:
        raise quota_tokscale.subprocess.TimeoutExpired(executable, quota_tokscale.RUN_TIMEOUT_SECONDS)

    monkeypatch.setattr(quota_tokscale, "_spawn", slow)
    usage = read(tmp_path, now=NOW + 700)

    assert [(metric.provider, metric.used_percent, metric.stale) for metric in usage.metrics] == [
        ("Codex", 72.0, True)
    ]
    assert "did not answer within 20 seconds" in usage.note


def test_a_kept_number_after_a_failure_never_reaches_a_handover(tmp_path, monkeypatch):
    backend(monkeypatch, json.dumps([{"provider": "Codex", "metrics": [
        {"label": "30d", "used_percent": 97.0, "resets_at": None}]}]))
    read(tmp_path, now=NOW)

    def slow(executable: str, cwd: Path) -> tuple[int, str]:
        raise quota_tokscale.subprocess.TimeoutExpired(executable, quota_tokscale.RUN_TIMEOUT_SECONDS)

    monkeypatch.setattr(quota_tokscale, "_spawn", slow)
    rows, _notes = quota.percentage_rows(home=tmp_path, now=NOW + 700)

    codex = [row for row in rows if row.provider == "Codex"][0]
    assert (codex.used, codex.percent, codex.state) == (97.0, None, "cached")


def test_the_cache_keeps_the_numbers_and_never_the_person(tmp_path, monkeypatch):
    answer = recorded().replace('"plan": "Free"', '"plan": "Free", "email": "someone@example.com"')
    backend(monkeypatch, answer)

    read(tmp_path)

    stored = quota_tokscale.cache_path(tmp_path).read_text(encoding="utf-8")
    assert "someone@example.com" not in stored
    assert '"provider": "OpenCode Go"' in stored


def test_a_cache_this_module_cannot_read_is_a_fresh_run(tmp_path, monkeypatch):
    runs = backend(monkeypatch, recorded())
    path = quota_tokscale.cache_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ troncato", encoding="utf-8")

    usage = read(tmp_path)

    assert len(runs) == 1
    assert usage.known


def test_a_cache_from_another_program_is_a_fresh_run(tmp_path, monkeypatch):
    runs = backend(monkeypatch, recorded())
    path = quota_tokscale.cache_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"providers": {"codex": 99}}), encoding="utf-8")

    assert read(tmp_path).known
    assert len(runs) == 1


def test_the_binary_runs_in_the_state_directory_not_in_the_caller_directory(tmp_path, monkeypatch):
    """tokscale drops a `.tokscale/` folder in its own working directory.

    The engine calls it from wherever a command or the hourly beat happens to
    run, so a run left to inherit that directory writes that folder into the
    repository a person is working in. It starts in the machine's state
    directory instead, which this test pins because nothing else would notice.
    """
    seen: list[Path] = []

    def spawn(executable: str, cwd: Path) -> tuple[int, str]:
        seen.append(cwd)
        return 0, recorded()

    monkeypatch.setattr(quota_tokscale, "_binary", lambda: "/usr/bin/tokscale")
    monkeypatch.setattr(quota_tokscale, "_spawn", spawn)

    read(tmp_path)

    assert seen == [tmp_path / ".local" / "state" / "nexgen"]
    assert seen[0].is_dir()


# ---------------------------------------------------------------- the switch


def test_the_backend_is_on_when_the_file_declares_nothing(tmp_path, monkeypatch):
    backend(monkeypatch, recorded())
    config = quota_file(tmp_path, "limits: {}\n")

    assert quota_tokscale.mode(config) == "auto"
    assert read(tmp_path, config=config).known


def test_the_backend_is_off_when_the_file_says_off(tmp_path, monkeypatch):
    runs = backend(monkeypatch, recorded())
    config = quota_file(tmp_path, "backends:\n  tokscale: off\n")

    usage = read(tmp_path, config=config)

    assert quota_tokscale.mode(config) == "off"
    assert not usage.known
    assert usage.note == ""
    assert runs == []


def test_the_switch_is_read_without_caring_about_case_or_spaces(tmp_path):
    assert quota_tokscale.mode(quota_file(tmp_path, "backends:\n  tokscale: ' OFF '\n")) == "off"


def test_a_value_that_is_not_off_leaves_the_shipped_behaviour(tmp_path):
    assert quota_tokscale.mode(quota_file(tmp_path, "backends:\n  tokscale: disabled\n")) == "auto"
    assert quota_tokscale.mode(quota_file(tmp_path, "backends: [tokscale]\n")) == "auto"
    assert quota_tokscale.mode(None) == "auto"


def test_one_run_can_silence_the_backend_without_editing_the_file(tmp_path, monkeypatch):
    runs = backend(monkeypatch, recorded())
    monkeypatch.setenv(quota_tokscale.ENV_OVERRIDE, "off")

    usage = read(tmp_path, config=quota_file(tmp_path, "limits: {}\n"))

    assert not usage.known
    assert usage.note == ""
    assert runs == []


# ----------------------------------------------------------------- the report


def rows_for(report: quota.QuotaReport, provider: str) -> list[quota.QuotaRow]:
    return [row for row in report.rows if row.provider == provider]


def test_the_report_shows_the_live_number_instead_of_the_local_one(tmp_path, monkeypatch):
    codex_reading(tmp_path)
    backend(monkeypatch, recorded())

    report = quota.build_report(home=tmp_path, now=NOW)

    codex = rows_for(report, "Codex")
    assert len(codex) == 1
    assert codex[0].provider_id == "openai"
    assert codex[0].window == "30d"
    assert codex[0].percent == 72.0
    assert codex[0].state == "ok"
    assert codex[0].read_at == NOW
    assert codex[0].resets_at == pytest.approx(CODEX_RESET)


def test_a_provider_the_backend_does_not_answer_keeps_its_local_reading(tmp_path, monkeypatch):
    codex_reading(tmp_path, used=59.0)
    backend(monkeypatch, json.dumps([{"provider": "Copilot", "metrics": [
        {"label": "Premium", "used_percent": 100.0, "resets_at": None}]}]))

    report = quota.build_report(home=tmp_path, now=NOW)

    codex = rows_for(report, "Codex (free)")[0]
    assert codex.percent == 59.0
    assert rows_for(report, "Copilot")[0].percent == 100.0


def archive(home: Path, rows: list[tuple[str, str, float, float]]) -> Path:
    """The harness store: `(session, provider, cost, hours ago)` in `session_v2`."""
    import sqlite3

    path = home / ".local" / "share" / "opencode" / "opencode.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute(
        "create table session_v2 (id text primary key, parent_id text, cost real, "
        "time_updated integer, model text)"
    )
    con.executemany(
        "insert into session_v2 values (?, null, ?, ?, ?)",
        [
            (session, cost, int((NOW - hours * 3600) * 1000), json.dumps({"providerID": provider}))
            for session, provider, cost, hours in rows
        ],
    )
    con.commit()
    con.close()
    return path


def test_the_local_spend_of_a_plan_the_backend_answered_is_not_shown_twice(tmp_path, monkeypatch):
    archive(tmp_path, [("s1", "opencode-go", 1.0, 1.0)])
    backend(monkeypatch, recorded())

    rows, _notes = quota.percentage_rows(home=tmp_path, now=NOW)

    go = [row for row in rows if row.provider == "OpenCode Go"]
    assert [row.window for row in go] == ["Rolling", "Weekly", "Monthly"]
    assert [row.unit for row in go] == ["percent", "percent", "percent"]
    assert [row.used for row in go] == [1.0, 0.0, 0.0]
    assert quota_state_payload(rows)["providers"]["opencode-go"] == {"percent": 1.0, "window": "Rolling"}


def quota_state_payload(rows: list[quota.QuotaRow]) -> dict:
    """The state the plugin reads before a turn: the worst window of each provider."""
    from nexgen_core import quota_state

    return quota_state.payload(rows, now=NOW)


def test_without_the_backend_the_local_spend_is_still_shown(tmp_path, monkeypatch):
    archive(tmp_path, [("s1", "opencode-go", 1.0, 1.0)])
    backend(monkeypatch, recorded())
    quota_file(tmp_path, "backends:\n  tokscale: off\n")

    rows, _notes = quota.percentage_rows(home=tmp_path, now=NOW)

    go = [row for row in rows if row.provider == "OpenCode Go"]
    assert [row.unit for row in go] == ["usd", "usd", "usd"]
    assert [row.window for row in go] == ["5h", "7d", "30d"]
    assert go[0].used == 1.0


def test_a_percentage_the_backend_reports_reaches_the_handover(tmp_path, monkeypatch):
    backend(monkeypatch, json.dumps([{"provider": "Codex", "metrics": [
        {"label": "30d", "used_percent": 96.0, "resets_at": None}]}]))

    rows, _notes = quota.percentage_rows(home=tmp_path, now=NOW)

    assert rows[0].state == "warn"
    assert quota_state_payload(rows)["providers"]["openai"]["percent"] == 96.0


def test_the_off_switch_leaves_the_report_exactly_as_it_was(tmp_path, monkeypatch):
    codex_reading(tmp_path, used=59.0)
    runs = backend(monkeypatch, recorded())
    quota_file(tmp_path, "backends:\n  tokscale: off\n")

    report = quota.build_report(home=tmp_path, now=NOW)

    assert runs == []
    assert rows_for(report, "Codex (free)")[0].percent == 59.0
    assert not any("tokscale" in note for note in report.notes)


def test_a_missing_backend_is_one_line_and_never_a_row(tmp_path, monkeypatch):
    codex_reading(tmp_path, used=59.0)
    monkeypatch.setattr(quota_tokscale, "_binary", lambda: None)

    report = quota.build_report(home=tmp_path, now=NOW)

    assert rows_for(report, "Codex (free)")[0].percent == 59.0
    assert [note for note in report.notes if "tokscale" in note] == [
        "tokscale is not installed on this machine: the optional quota backend is skipped."
    ]


# ------------------------------------------------- the reading nobody refreshed


def claude_row(home: Path, *, sent: int = 1000) -> Path:
    """One assistant message in a Claude Code transcript, where Claude Code writes it."""
    folder = home / ".claude" / "projects" / "progetto"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.fromtimestamp(NOW - 600, timezone.utc).isoformat().replace("+00:00", "Z")
    path = folder / "session.jsonl"
    path.write_text(json.dumps({
        "type": "assistant",
        "timestamp": stamp,
        "message": {
            "id": "msg_1",
            "role": "assistant",
            "content": [{"type": "text", "text": "ok"}],
            "usage": {"input_tokens": sent, "output_tokens": 0,
                      "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
        },
    }) + "\n", encoding="utf-8")
    return path


def test_a_throttled_plan_keeps_its_number_its_own_moment_and_loses_the_percentage(tmp_path, monkeypatch):
    backend(monkeypatch, throttled())

    rows, _notes = quota.percentage_rows(home=tmp_path, now=NOW)

    claude = [row for row in rows if row.provider == "Claude"]
    assert [(row.window, row.used, row.percent, row.state) for row in claude] == [
        ("Session", 34.0, None, "cached"),
        ("Weekly", 61.0, None, "cached"),
    ]
    # The moment tokscale says the sample was taken, not the moment of this run.
    assert [row.read_at for row in claude] == [FETCHED_AT, FETCHED_AT]


def test_a_throttled_row_reads_as_the_last_reading_with_its_age(tmp_path, monkeypatch):
    backend(monkeypatch, throttled())

    rows, _notes = quota.percentage_rows(home=tmp_path, now=NOW)

    claude = [row for row in rows if row.provider == "Claude"][0]
    assert quota.render_row(claude, NOW) == (
        "Claude: 34% of the Session window in the last reading (read 40m ago), and it was not refreshed"
    )


def test_the_throttled_reading_says_why_it_was_not_refreshed(tmp_path, monkeypatch):
    backend(monkeypatch, throttled())

    usage = read(tmp_path)

    assert usage.note == (
        "tokscale could not refresh Claude: showing the reading from 40m ago for it."
    )


def test_each_kept_provider_is_dated_by_its_own_sample(tmp_path, monkeypatch):
    """Two kept readings taken far apart: one sentence would print one as the other's age."""
    backend(monkeypatch, json.dumps([
        {"provider": "Copilot", "metrics": [{"label": "Chat", "used_percent": 7.0, "resets_at": None}],
         "freshness": {"fetched_at": int(NOW) - 2400, "stale": True}},
        {"provider": "Claude", "metrics": [{"label": "Session", "used_percent": 34.0, "resets_at": None}],
         "freshness": {"fetched_at": int(NOW) - 14400, "stale": True}},
    ]))

    usage = read(tmp_path)

    assert usage.note == (
        "tokscale could not refresh Copilot: showing the reading from 40m ago for it. "
        "tokscale could not refresh Claude: showing the reading from 4h 00m ago for it."
    )


def test_a_throttled_plan_never_reaches_the_state_a_turn_acts_on(tmp_path, monkeypatch):
    backend(monkeypatch, throttled())

    rows, _notes = quota.percentage_rows(home=tmp_path, now=NOW)

    assert "anthropic" not in quota_state_payload(rows)["providers"]
    assert quota_state_payload(rows)["providers"]["openai"] == {"percent": 72.0, "window": "30d"}


def test_the_local_tokens_are_not_printed_beside_the_throttled_reading(tmp_path, monkeypatch):
    claude_row(tmp_path)
    backend(monkeypatch, throttled())

    report = quota.build_report(home=tmp_path, now=NOW)

    assert [row.provider for row in report.rows].count("Claude") == 2
    assert rows_for(report, "Claude Code") == []


def test_a_fresh_plan_is_a_percentage_like_any_other(tmp_path, monkeypatch):
    backend(monkeypatch, json.dumps([{
        "provider": "Claude", "plan": "Max 5x",
        "metrics": [{"label": "Session", "used_percent": 34.0, "resets_at": None}],
        "freshness": {"fetched_at": int(NOW) - 120, "stale": False},
    }]))

    row = [row for row in quota.percentage_rows(home=tmp_path, now=NOW)[0]
           if row.provider == "Claude"][0]

    assert (row.percent, row.state, row.read_at) == (34.0, "ok", NOW - 120)


def test_a_plan_that_stops_answering_keeps_the_last_reading_it_gave(tmp_path, monkeypatch):
    backend(monkeypatch, throttled())
    read(tmp_path, now=NOW)
    backend(monkeypatch, json.dumps([{"provider": "Codex", "metrics": [
        {"label": "30d", "used_percent": 72.0, "resets_at": None}]}]))

    usage = read(tmp_path, now=NOW + 900)

    claude = [metric for metric in usage.metrics if metric.provider == "Claude"]
    assert [(metric.window, metric.used_percent, metric.stale) for metric in claude] == [
        ("Session", 34.0, True),
        ("Weekly", 61.0, True),
    ]
    assert [metric.read_at for metric in claude] == [FETCHED_AT, FETCHED_AT]


def test_a_reading_too_old_to_describe_anything_is_not_carried_any_further(tmp_path, monkeypatch):
    backend(monkeypatch, throttled())
    read(tmp_path, now=NOW)
    backend(monkeypatch, json.dumps([{"provider": "Codex", "metrics": [
        {"label": "30d", "used_percent": 72.0, "resets_at": None}]}]))

    usage = read(tmp_path, now=NOW + quota_tokscale.REMEMBER_SECONDS + 1)

    assert [metric.provider for metric in usage.metrics] == ["Codex"]


def test_a_freshness_this_module_cannot_read_is_refused_whole(tmp_path, monkeypatch):
    backend(monkeypatch, json.dumps([{
        "provider": "Claude",
        "metrics": [{"label": "Session", "used_percent": 34.0, "resets_at": None}],
        "freshness": {"stale": True},
    }]))

    usage = read(tmp_path)

    assert not usage.known
    assert "carries a freshness this engine cannot read" in usage.note


def test_the_cache_keeps_the_moment_the_number_was_read_and_that_it_is_a_fallback(tmp_path, monkeypatch):
    backend(monkeypatch, throttled())

    read(tmp_path)

    stored = json.loads(quota_tokscale.cache_path(tmp_path).read_text(encoding="utf-8"))
    claude = [metric for metric in stored["metrics"] if metric["provider"] == "Claude"]
    assert [(metric["read_at"], metric["stale"]) for metric in claude] == [(FETCHED_AT, True), (FETCHED_AT, True)]


# ------------------------------------------------------- the cooldown it recorded


def test_a_throttled_plan_with_no_sample_left_says_why_it_is_missing(tmp_path, monkeypatch):
    backend(monkeypatch, recorded())
    cooldown_record(tmp_path, issued=NOW, expires=NOW + 1500)

    usage = read(tmp_path)

    assert "Claude" not in {metric.provider for metric in usage.metrics}
    assert usage.note == (
        f"Claude: tokscale is rate-limited until {datetime.fromtimestamp(NOW + 1500).strftime('%H:%M')} "
        "(25m from now), so the plan's own windows are not shown."
    )


def test_a_plan_that_answered_says_nothing_about_a_cooldown_that_just_ended(tmp_path, monkeypatch):
    backend(monkeypatch, throttled())
    cooldown_record(tmp_path, issued=NOW - 3600, expires=NOW - 60)

    usage = read(tmp_path)

    assert "Claude" in {metric.provider for metric in usage.metrics}
    assert "rate-limited" not in usage.note


def test_the_engine_asks_at_most_once_per_its_own_interval():
    # Ten minutes, and one constant: every engine caller reads this one cache.
    assert quota_tokscale.CACHE_TTL_SECONDS == 600


def test_the_recorded_cooldown_is_the_moment_it_ends(tmp_path):
    cooldown_record(tmp_path, issued=NOW - 60, expires=NOW + 1500)

    assert quota_tokscale.cooldown_until(tmp_path, now=NOW) == NOW + 1500


def test_no_record_is_no_deadline(tmp_path):
    assert quota_tokscale.cooldown_until(tmp_path, now=NOW) is None


def test_the_longest_record_is_the_one_that_is_honoured(tmp_path):
    cooldown_record(tmp_path, issued=NOW - 60, expires=NOW + 300, name="a")
    cooldown_record(tmp_path, issued=NOW - 60, expires=NOW + 1800, name="b")

    assert quota_tokscale.cooldown_until(tmp_path, now=NOW) == NOW + 1800


@pytest.mark.parametrize(
    ("issued", "expires", "version"),
    [
        (NOW - 3600, NOW - 60, 1),          # already over
        (NOW + 60, NOW + 300, 1),           # issued in a future this clock does not have
        (NOW - 60, NOW + 7200, 1),          # a wait longer than tokscale can impose
        (NOW - 60, NOW + 1500, 2),          # a record this module does not know
    ],
)
def test_a_record_that_cannot_be_trusted_is_no_deadline(tmp_path, issued, expires, version):
    cooldown_record(tmp_path, issued=issued, expires=expires, version=version)

    assert quota_tokscale.cooldown_until(tmp_path, now=NOW) is None


def test_a_record_that_is_not_a_record_is_no_deadline(tmp_path):
    folder = quota_tokscale.config_dir(tmp_path).joinpath(*quota_tokscale.COOLDOWN_SUBPATH)
    folder.mkdir(parents=True)
    (folder / "truncated.json").write_text("{ non finito", encoding="utf-8")

    assert quota_tokscale.cooldown_until(tmp_path, now=NOW) is None


def test_a_recorded_cooldown_is_not_spent_again_before_it_ends(tmp_path, monkeypatch):
    runs = backend(monkeypatch, recorded())
    read(tmp_path, now=NOW)
    cooldown_record(tmp_path, issued=NOW, expires=NOW + 1500)

    usage = read(tmp_path, now=NOW + 900)

    assert len(runs) == 1
    assert usage.known
    # The numbers bought before the cooldown, still carrying the moment they were read.
    assert {metric.stale for metric in usage.metrics} == {False}
    assert {metric.read_at for metric in usage.metrics} == {NOW}


def test_the_cooldown_note_names_when_tokscale_will_try_again(tmp_path, monkeypatch):
    backend(monkeypatch, recorded())
    read(tmp_path, now=NOW)
    cooldown_record(tmp_path, issued=NOW, expires=NOW + 1500)

    usage = read(tmp_path, now=NOW + 900)

    assert usage.note == (
        f"Claude: tokscale is rate-limited until {datetime.fromtimestamp(NOW + 1500).strftime('%H:%M')} "
        "(10m from now), so the plan's own windows are not shown."
    )


def test_the_cooldown_ends_with_the_record_and_the_answer_is_bought_again(tmp_path, monkeypatch):
    runs = backend(monkeypatch, recorded())
    read(tmp_path, now=NOW)
    cooldown_record(tmp_path, issued=NOW, expires=NOW + 1500)
    read(tmp_path, now=NOW + 900)

    usage = read(tmp_path, now=NOW + 1500)

    assert len(runs) == 2
    assert {metric.stale for metric in usage.metrics} == {False}
    assert usage.note == ""


def test_a_held_answer_older_than_the_policy_is_not_shown_as_current(tmp_path, monkeypatch):
    """A cooldown record is not a licence to keep an old number current.

    Any run of the binary writes that record, including one five hours after the
    reading this cache holds. Reusing the answer then would hand the next turn a
    percentage older than the age the plugin's own state policy trusts, so the run
    happens and the reading comes back as the kept one it is.
    """
    backend(monkeypatch, json.dumps([{"provider": "Codex", "metrics": [
        {"label": "30d", "used_percent": 97.0, "resets_at": None}]}]))
    read(tmp_path, now=NOW)
    cooldown_record(tmp_path, issued=NOW + 5 * 3600, expires=NOW + 5 * 3600 + 1500)
    backend(monkeypatch, json.dumps([{"provider": "OpenCode Go", "metrics": [
        {"label": "Rolling", "used_percent": 1.0, "resets_at": None}]}]))

    rows, _notes = quota.percentage_rows(home=tmp_path, now=NOW + 5 * 3600)

    codex = [row for row in rows if row.provider == "Codex"][0]
    assert (codex.used, codex.percent, codex.state) == (97.0, None, "cached")


def test_the_held_answer_may_not_be_older_than_the_age_the_owner_declares(tmp_path, monkeypatch):
    """The number is the policy's, not this module's: thirty minutes of trust is thirty.

    The same answer forty minutes old is kept by the shipped three hours and
    bought again by a machine that declared half an hour, which is what the
    plugin reads from the same file.
    """
    relaxed = quota_file(tmp_path, "limits: {}\n")
    strict = tmp_path / "declared" / "quota.yaml"
    strict.parent.mkdir()
    strict.write_text("failover:\n  state_max_age_minutes: 30\n", encoding="utf-8")
    runs = backend(monkeypatch, json.dumps([{"provider": "Codex", "metrics": [
        {"label": "30d", "used_percent": 97.0, "resets_at": None}]}]))
    read(tmp_path, now=NOW)
    cooldown_record(tmp_path, issued=NOW + 1000, expires=NOW + 2500)

    shipped = read(tmp_path, config=relaxed, now=NOW + 2400)
    declared = read(tmp_path, config=strict, now=NOW + 2400)

    assert {metric.stale for metric in shipped.metrics} == {False}
    assert {metric.read_at for metric in shipped.metrics} == {NOW}
    assert len(runs) == 2, "una risposta di 40 minuti non è quella di cui una politica di 30 si fida"
    assert {metric.read_at for metric in declared.metrics} == {NOW + 2400}


# ------------------------------------------------- the login only Claude can renew


def test_a_missing_claude_is_refreshed_and_the_binary_is_asked_again(tmp_path, monkeypatch):
    """A rejected token makes tokscale omit the plan whole, so its absence is the signal.

    `claude auth status` is the one command that makes Claude Code rewrite its own
    login, and it costs no model call. One refresh, then one more run of the
    binary, and the plan is readable again.
    """
    runs = backend_answers(monkeypatch, recorded(), claude_answer())
    launches = login(monkeypatch)

    usage = read(tmp_path)

    assert [launch["argv"][-2:] for launch in launches] == [["auth", "status"]]
    assert len(runs) == 2
    assert [(metric.provider, metric.window, metric.used_percent)
            for metric in usage.metrics if metric.provider == "Claude"] == [("Claude", "Session", 34.0)]
    assert usage.note == ""


def test_a_plan_already_in_the_answer_costs_no_refresh(tmp_path, monkeypatch):
    runs = backend_answers(monkeypatch, claude_answer())
    launches = login(monkeypatch)

    usage = read(tmp_path)

    assert launches == []
    assert len(runs) == 1
    assert usage.known


def test_the_login_is_refreshed_once_per_cache_period(tmp_path, monkeypatch):
    runs = backend_answers(monkeypatch, recorded(), claude_answer())
    launches = login(monkeypatch)

    read(tmp_path, now=NOW)
    second = read(tmp_path, now=NOW + 60)

    assert len(launches) == 1
    assert len(runs) == 2
    assert "Claude" in {metric.provider for metric in second.metrics}


def test_the_next_cache_period_refreshes_again_and_never_loops(tmp_path, monkeypatch):
    """One refresh per period, never one per run: two periods are two refreshes."""
    runs = backend_answers(monkeypatch, recorded(), claude_answer())
    launches = login(monkeypatch)

    read(tmp_path, now=NOW)
    read(tmp_path, now=NOW + quota_tokscale.CACHE_TTL_SECONDS)

    assert len(launches) == 2
    assert len(runs) == 4


def test_a_login_that_cannot_be_refreshed_leaves_claude_unknown_and_says_why(tmp_path, monkeypatch):
    runs = backend_answers(monkeypatch, recorded())
    login(monkeypatch, code=1)

    usage = read(tmp_path)

    assert len(runs) == 1
    assert "Claude" not in {metric.provider for metric in usage.metrics}
    assert usage.note == (
        "Claude's stored access token was rejected and its login could not be refreshed: "
        "the plan's own windows are not shown."
    )


def test_a_failed_refresh_is_not_attempted_again_inside_the_same_period(tmp_path, monkeypatch):
    runs = backend_answers(monkeypatch, recorded())
    launches = login(monkeypatch, code=1)

    read(tmp_path, now=NOW)
    second = read(tmp_path, now=NOW + 60)

    assert len(launches) == 1
    assert len(runs) == 1
    assert "could not be refreshed" in second.note


def test_an_empty_answer_with_a_failed_refresh_is_kept_too(tmp_path, monkeypatch):
    """Nothing to remember is not true of a refresh that failed: keeping it is what stops the retry."""
    runs = backend_answers(monkeypatch, "[]")
    launches = login(monkeypatch, code=1)

    read(tmp_path, now=NOW)
    second = read(tmp_path, now=NOW + 60)

    assert len(launches) == 1
    assert len(runs) == 1
    assert "could not be refreshed" in second.note


def test_the_refresh_output_has_nowhere_to_go_and_never_reaches_the_cache(tmp_path, monkeypatch, capsys):
    """`claude auth status` names the account's e-mail: no pipe, no file, no log, no cache.

    The command's three streams are the null device, so there is nothing this
    module could remember even if it wanted to, and neither the note nor the cache
    file may carry the address.
    """
    backend_answers(monkeypatch, recorded())
    launches = login(monkeypatch)

    usage = read(tmp_path)

    launch = launches[0]
    assert launch["stdout"] is quota_tokscale.subprocess.DEVNULL
    assert launch["stderr"] is quota_tokscale.subprocess.DEVNULL
    assert launch["stdin"] is quota_tokscale.subprocess.DEVNULL
    stored = quota_tokscale.cache_path(tmp_path).read_text(encoding="utf-8")
    assert "someone@example.com" not in stored
    assert "someone@example.com" not in usage.note
    captured = capsys.readouterr()
    assert (captured.out, captured.err) == ("", "")


def test_the_refresh_runs_where_the_engine_runs_it_without_a_console_window(tmp_path, monkeypatch):
    """The same two promises as a run of tokscale: a machine-local cwd, and no window flashed."""
    backend_answers(monkeypatch, recorded())
    launches = login(monkeypatch)

    read(tmp_path)

    launch = launches[0]
    assert launch["cwd"] == str(quota_tokscale.workdir(tmp_path))
    assert set(processes.no_window_kwargs()) <= set(launch)


def test_a_machine_without_the_claude_command_is_not_refreshed(tmp_path, monkeypatch):
    runs = backend_answers(monkeypatch, recorded())

    usage = read(tmp_path)

    assert len(runs) == 1
    assert usage.note == ""


def test_a_refresh_that_hangs_is_stopped_and_cost_one_sentence(tmp_path, monkeypatch):
    backend_answers(monkeypatch, recorded())
    login(monkeypatch)
    stopped: list = []
    monkeypatch.setattr(quota_tokscale.processes, "force_stop_process_tree",
                        lambda proc, **kwargs: stopped.append(proc) or True)

    class _Hung:
        pid = 5253
        returncode = None

        def __init__(self, argv, **kwargs):
            self.argv = argv

        def communicate(self, timeout=None):
            raise quota_tokscale.subprocess.TimeoutExpired(self.argv, timeout)

    monkeypatch.setattr(quota_tokscale.subprocess, "Popen", _Hung)

    usage = read(tmp_path)

    assert quota_tokscale.LOGIN_TIMEOUT_SECONDS == 20
    assert [proc.pid for proc in stopped] == [5253]
    assert "could not be refreshed" in usage.note


def test_a_tokscale_that_fails_does_not_refresh_a_login(tmp_path, monkeypatch):
    backend(monkeypatch, code=2)
    launches = login(monkeypatch)

    usage = read(tmp_path)

    assert launches == []
    assert "exited with code 2" in usage.note


def test_a_plan_on_a_recorded_cooldown_is_not_refreshed(tmp_path, monkeypatch):
    """A throttled plan is missing for another reason, and its token is not the one to renew."""
    runs = backend_answers(monkeypatch, recorded())
    launches = login(monkeypatch)
    cooldown_record(tmp_path, issued=NOW, expires=NOW + 1500)

    usage = read(tmp_path)

    assert launches == []
    assert len(runs) == 1
    assert "rate-limited" in usage.note


def test_the_refreshed_plan_reaches_the_report_as_a_live_percentage(tmp_path, monkeypatch):
    backend_answers(monkeypatch, recorded(), claude_answer(used=34.0))
    login(monkeypatch)

    report = quota.build_report(home=tmp_path, now=NOW)

    assert [(row.window, row.percent, row.state) for row in rows_for(report, "Claude")] == [
        ("Session", 34.0, "ok")
    ]
