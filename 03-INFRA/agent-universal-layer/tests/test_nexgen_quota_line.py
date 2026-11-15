"""The one line a session start reads: the worst window of every plan, nothing invented.

Every expected value is the literal the report would print, and the clock is
pinned, because a reset is shown as the local hour a person reads and a reading
older than the shared interval has to carry its age.
"""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

from nexgen_core import i18n, quota, quota_line, quota_policy, quota_tokscale

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"

NOW = datetime(2026, 11, 14, 12, 0, 0).timestamp()
RESET_18 = datetime(2026, 11, 14, 18, 0, 0).timestamp()

EXAMPLE = "Quota: Claude 5h 40% (reset 18:00) · Codex 30d 72% · OpenCode Go weekly 0% · Antigravity stale"


@pytest.fixture(autouse=True)
def _english_notes():
    """Pin the source language: the line is asserted word by word."""
    i18n.set_language("en")
    yield
    i18n.set_language(None)


def row(provider: str, window: str, percent: float | None, *, provider_id: str = "",
        resets: float | None = None, age_s: float = 60, state: str = "ok") -> quota.QuotaRow:
    """One row as the cheap report hands it over, read `age_s` before `NOW`."""
    return quota.QuotaRow(
        provider=provider, provider_id=provider_id, window=window, used=percent,
        unit="percent", percent=percent, resets_at=resets, read_at=NOW - age_s, state=state,
    )


def the_example() -> list[quota.QuotaRow]:
    """The four plans the owner asked to see, one of them with no current reading."""
    return [
        row("Claude", "5h", 40.0, provider_id="anthropic", resets=RESET_18, state="ok"),
        row("Codex", "30d", 72.0, provider_id="openai", state="warn"),
        row("OpenCode Go", "weekly", 0.0, provider_id="opencode-go", state="ok"),
        row("Antigravity", "30d", None, age_s=2 * 86400, state="stale"),
    ]


def test_the_line_is_the_four_plans_in_the_space_of_one_line():
    assert quota_line.session_line(the_example(), now=NOW) == EXAMPLE


def test_only_the_worst_window_of_a_plan_is_shown():
    rows = [
        row("Codex", "5h", 20.0, provider_id="openai"),
        row("Codex", "30d", 72.0, provider_id="openai"),
    ]

    assert quota_line.session_line(rows, now=NOW) == "Quota: Codex 30d 72%"


def test_a_reading_that_could_not_be_refreshed_says_stale_and_no_number():
    rows = [row("Claude", "5h", None, provider_id="anthropic", state=quota.STATE_CACHED)]

    assert quota_line.session_line(rows, now=NOW) == "Quota: Claude stale"


def test_spend_without_a_declared_ceiling_is_not_a_quota_and_is_left_out():
    """The line is where a person reads what can be judged; the report lists the rest."""
    rows = [
        row("OpenCode Zen", "5h", None, provider_id="opencode", state="ok"),
        row("Codex", "30d", 72.0, provider_id="openai"),
    ]

    assert quota_line.session_line(rows, now=NOW) == "Quota: Codex 30d 72%"


def test_a_number_older_than_the_shared_interval_carries_its_age():
    rows = [row("Codex", "30d", 72.0, provider_id="openai", age_s=1200)]

    assert quota_line.session_line(rows, now=NOW) == "Quota: Codex 30d 72% (read 20m ago)"


def test_a_reset_and_an_age_are_both_shown():
    rows = [row("Codex", "30d", 72.0, provider_id="openai", resets=RESET_18, age_s=1200)]

    assert quota_line.session_line(rows, now=NOW) == "Quota: Codex 30d 72% (reset 18:00, read 20m ago)"


def test_the_shared_interval_itself_is_still_fresh():
    """Ten minutes is the interval every caller shares: nothing to date at that age."""
    rows = [row("Codex", "30d", 72.0, provider_id="openai", age_s=quota_tokscale.CACHE_TTL_SECONDS)]

    assert quota_line.session_line(rows, now=NOW) == "Quota: Codex 30d 72%"


def test_a_machine_that_can_read_nothing_prints_no_line():
    assert quota_line.session_line([], now=NOW) == ""


def test_the_command_prints_the_line_and_never_buys_a_run(monkeypatch, capsys):
    asked: dict = {}
    # The command reads the clock itself: pin it, so a reset is a local hour and no age appears.
    monkeypatch.setattr(quota_line.time, "time", lambda: NOW)

    def rows(**kwargs):
        asked.update(kwargs)
        return the_example(), []

    monkeypatch.setattr(quota, "percentage_rows", rows)
    monkeypatch.setattr(quota_line.quota_policy, "load", lambda home=None, *, vault=None: (quota_policy.Failover(), []))

    assert quota_line.main() == 0

    assert capsys.readouterr().out == EXAMPLE + "\n"
    assert asked["backend_max_age"] == 10800, "tre ore: la lettura che la politica del proprietario ancora si fida"


def test_the_command_prints_nothing_when_nothing_can_be_read(monkeypatch, capsys):
    monkeypatch.setattr(quota, "percentage_rows", lambda **kwargs: ([], []))
    monkeypatch.setattr(quota_line.quota_policy, "load", lambda home=None, *, vault=None: (quota_policy.Failover(), []))

    assert quota_line.main() == 0

    assert capsys.readouterr().out == ""


def test_the_command_exits_zero_even_when_a_reader_raises(monkeypatch, capsys):
    """The hook reads a failing exit as "no line at all": a reader that raises costs the line.

    `nexgen`'s own net prints two lines and returns 1, which is the wrong contract
    for the one command a session start depends on.
    """
    def broken(**kwargs):
        raise ValueError("archivio corrotto")

    monkeypatch.setattr(quota, "percentage_rows", broken)

    assert quota_line.main() == 0
    assert capsys.readouterr().out == ""


def test_a_piped_line_is_utf8_the_hook_can_decode(tmp_path):
    """`spawnSync` hands this command a pipe, and the hook decodes it as UTF-8.

    On Windows the console's code page decided those bytes: `·` was written as the
    single byte `0xB7`, which is not UTF-8, so every session's first context read
    `Quota: Antigravity stale <replacement> Codex 30d 72%`. The command is run here
    with its stdout on a pipe, which is exactly the failing case.
    """
    script = tmp_path / "line.py"
    script.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(SCRIPTS_DIR)!r})\n"
        "from nexgen_core import quota, quota_policy\n"
        "from nexgen_core.cli import main\n"
        "quota.percentage_rows = lambda **kw: ([\n"
        "    quota.QuotaRow(provider='Codex', provider_id='openai', window='30d', used=72.0,\n"
        "                   unit='percent', percent=72.0, state='ok'),\n"
        "    quota.QuotaRow(provider='Antigravity', window='30d', used=41.0,\n"
        "                   unit='percent', percent=41.0, state='ok'),\n"
        "], [])\n"
        "quota_policy.load = lambda home=None, *, vault=None: (quota_policy.Failover(), [])\n"
        "sys.exit(main(['quota', '--line']))\n",
        encoding="utf-8",
    )

    done = subprocess.run([sys.executable, str(script)], capture_output=True, timeout=180)

    text = done.stdout.decode("utf-8")
    assert done.returncode == 0
    assert "\u00b7" in text, "il separatore deve arrivare come carattere, non come byte di code page"
    assert "Codex" in text
    assert "Antigravity" in text
