"""Tests for the session groomer policy: what gets compacted, and what never does."""
from __future__ import annotations

import json
import subprocess

from nexgen_core.openclaw import sessions_groom
from nexgen_core.openclaw.sessions_groom import HARD_TOKENS, IDLE_SECONDS, plan_groom

NOW = 1_800_000_000_000


def session(**overrides):
    base = {
        "key": "agent:main:main",
        "agentId": "main",
        "totalTokens": 40_000,
        "updatedAt": NOW,
        "lastInteractionAt": NOW,
        "status": "done",
    }
    base.update(overrides)
    return base


def test_an_idle_agent_chat_is_compacted():
    entry = session(lastInteractionAt=NOW - (IDLE_SECONDS + 60) * 1000)

    decisions = plan_groom([entry], NOW)

    assert [d.key for d in decisions] == ["agent:main:main"]
    assert decisions[0].reason == "idle"


def test_a_chat_in_use_stays_alone_until_it_grows_too_much():
    assert plan_groom([session()], NOW) == []
    huge = session(totalTokens=HARD_TOKENS + 1)
    assert plan_groom([huge], NOW)[0].reason == "over the limit"


def test_a_session_with_no_tokens_is_not_touched():
    idle = session(totalTokens=0, lastInteractionAt=NOW - 10 * IDLE_SECONDS * 1000)

    assert plan_groom([idle], NOW) == []


def test_a_running_session_is_left_to_finish():
    running = session(status="running", totalTokens=HARD_TOKENS + 1)

    assert plan_groom([running], NOW) == []


def test_subagent_and_automation_sessions_are_not_compacted_here():
    entries = [
        session(key="agent:main:subagent:abc"),
        session(key="agent:main:cron:job:run:1"),
        session(key="agent:main:acp:x"),
    ]

    assert plan_groom(entries, NOW) == []


def test_the_main_chat_is_never_skipped_by_key():
    forked = session(key="agent:main:dashboard:abc", lastInteractionAt=NOW - 2 * IDLE_SECONDS * 1000)

    decisions = plan_groom([forked], NOW)

    assert decisions and decisions[0].key == "agent:main:dashboard:abc"


def test_a_session_on_a_backend_that_cannot_confirm_compaction_is_never_picked():
    """A `claude-cli` agent stays in the plan's blind spot, not in an hourly retry loop."""
    claude_backed = session(
        key="agent:capo:main",
        agentId="capo",
        lastInteractionAt=NOW - 2 * IDLE_SECONDS * 1000,
        agentRuntime={"id": "claude-cli", "source": "config"},
    )
    native = session(key="agent:posta:node-1", lastInteractionAt=NOW - 2 * IDLE_SECONDS * 1000)

    decisions = plan_groom([claude_backed, native], NOW)

    assert [d.key for d in decisions] == ["agent:posta:node-1"]


class FakeGateway:
    """The CLI's two verbs, answered from a table, with every compaction recorded."""

    def __init__(self, sessions, reply=(0, "Compacted session.", ""), listing=None):
        self.sessions = sessions
        self.reply = reply
        self.listing = listing
        self.compactions: list[str] = []

    def __call__(self, argv, **_kwargs):
        if argv[2] == "list":
            if self.listing is not None:
                return subprocess.CompletedProcess(argv, *self.listing)
            return subprocess.CompletedProcess(argv, 0, json.dumps({"sessions": self.sessions}), "")
        self.compactions.append(argv[3])
        code, out, err = self.reply(argv[3]) if callable(self.reply) else self.reply
        return subprocess.CompletedProcess(argv, code, out, err)


def idle_session(**overrides):
    """A chat far past the idle line, changed only where a test says so."""
    fields = {"lastInteractionAt": NOW - 2 * IDLE_SECONDS * 1000}
    fields.update(overrides)
    return session(**fields)


def run_groom(gateway, monkeypatch, home, dry_run=False):
    monkeypatch.setattr(sessions_groom.subprocess, "run", gateway)
    return sessions_groom.groom(
        openclaw="openclaw", now_ms=NOW, dry_run=dry_run, emit=lambda _line: None, home=home
    )


def test_an_already_compacted_session_is_not_offered_again(monkeypatch, tmp_path):
    gateway = FakeGateway([idle_session()], reply=(1, "", "Error: Already compacted."))

    first = run_groom(gateway, monkeypatch, tmp_path)
    again = run_groom(gateway, monkeypatch, tmp_path)

    assert gateway.compactions == ["agent:main:main"]
    assert len(first.lines) == 1 and first.failed == 0
    assert again.lines == []


def test_a_session_whose_counters_moved_is_offered_again(monkeypatch, tmp_path):
    gateway = FakeGateway([idle_session()], reply=(1, "", "Error: Already compacted."))
    run_groom(gateway, monkeypatch, tmp_path)

    gateway.sessions = [idle_session(totalTokens=41_000)]
    run_groom(gateway, monkeypatch, tmp_path)
    gateway.sessions = [idle_session(totalTokens=41_000, lastInteractionAt=NOW - 3 * IDLE_SECONDS * 1000)]
    run_groom(gateway, monkeypatch, tmp_path)

    assert gateway.compactions == ["agent:main:main", "agent:main:main", "agent:main:main"]


def test_a_failed_attempt_is_retried_on_the_next_run(monkeypatch, tmp_path):
    gateway = FakeGateway([idle_session()], reply=(1, "", "Error: the gateway refused the command."))

    first = run_groom(gateway, monkeypatch, tmp_path)
    second = run_groom(gateway, monkeypatch, tmp_path)

    assert gateway.compactions == ["agent:main:main", "agent:main:main"]
    assert first.failed == 1 and second.failed == 1


def test_an_unconfirmed_native_compaction_is_success_and_is_not_retried(monkeypatch, tmp_path):
    refused = (1, "", 'CLI backend "claude-cli" failed to compact its native session: '
                     "Claude CLI did not confirm that native compaction ran.")
    gateway = FakeGateway([idle_session()], reply=refused)

    first = run_groom(gateway, monkeypatch, tmp_path)
    run_groom(gateway, monkeypatch, tmp_path)

    assert gateway.compactions == ["agent:main:main"]
    assert first.failed == 0 and len(first.lines) == 1


def test_a_claude_cli_session_is_never_compacted_and_dry_run_says_why(monkeypatch, tmp_path):
    claude_backed = idle_session(key="agent:capo:main", agentId="capo",
                                 agentRuntime={"id": "claude-cli", "source": "config"})
    gateway = FakeGateway([claude_backed])

    quiet = run_groom(gateway, monkeypatch, tmp_path)
    shown = run_groom(gateway, monkeypatch, tmp_path, dry_run=True)

    assert gateway.compactions == []
    assert quiet.lines == []
    assert [line for line in shown.lines if "agent:capo:main" in line and "claude-cli" in line]


def test_a_dry_run_reads_the_memory_but_never_writes_it(monkeypatch, tmp_path):
    from nexgen_core.openclaw.groom_memory import memory_path

    memory = memory_path(home=tmp_path)
    gateway = FakeGateway([idle_session()], reply=(1, "", "Error: Already compacted."))

    run_groom(gateway, monkeypatch, tmp_path, dry_run=True)
    assert gateway.compactions == []
    assert not memory.exists()

    run_groom(gateway, monkeypatch, tmp_path)
    written = memory.read_bytes()
    gateway.compactions.clear()
    shown = run_groom(gateway, monkeypatch, tmp_path, dry_run=True)

    assert shown.lines == []
    assert memory.read_bytes() == written
    assert gateway.compactions == []


def test_a_corrupt_memory_file_is_no_memory(monkeypatch, tmp_path):
    from nexgen_core.openclaw.groom_memory import memory_path

    memory = memory_path(home=tmp_path)
    memory.parent.mkdir(parents=True, exist_ok=True)
    memory.write_text("{ this is not json", encoding="utf-8")
    gateway = FakeGateway([idle_session()])

    report = run_groom(gateway, monkeypatch, tmp_path)

    assert gateway.compactions == ["agent:main:main"]
    assert report.failed == 0
    assert json.loads(memory.read_text(encoding="utf-8"))["sessions"]


def test_a_session_the_gateway_no_longer_lists_is_forgotten(monkeypatch, tmp_path):
    """The memory cannot grow forever with chats that were cleared by hand."""
    from nexgen_core.openclaw.groom_memory import load_memory

    gateway = FakeGateway([idle_session()], reply=(1, "", "Error: Already compacted."))
    run_groom(gateway, monkeypatch, tmp_path)

    gateway.sessions = [idle_session(key="agent:other:main", agentId="other")]
    run_groom(gateway, monkeypatch, tmp_path)

    assert "agent:main:main" not in load_memory(home=tmp_path)
    assert "agent:other:main" in load_memory(home=tmp_path)


def test_an_unreadable_listing_does_not_erase_what_is_remembered(monkeypatch, tmp_path):
    """A gateway hiccup must not put every settled session back in the retry loop."""
    from nexgen_core.openclaw.groom_memory import load_memory

    gateway = FakeGateway([idle_session()], reply=(1, "", "Error: Already compacted."))
    run_groom(gateway, monkeypatch, tmp_path)

    gateway.listing = (1, "", "Error: the gateway is not reachable")
    run_groom(gateway, monkeypatch, tmp_path)

    assert "agent:main:main" in load_memory(home=tmp_path)


def test_a_zero_exit_is_the_gateway_saying_it_compacted(monkeypatch, tmp_path):
    """A wording change must not turn every success into an hourly failure."""
    gateway = FakeGateway([idle_session()], reply=(0, "Session trimmed.\n", ""))

    first = run_groom(gateway, monkeypatch, tmp_path)
    run_groom(gateway, monkeypatch, tmp_path)

    assert first.failed == 0
    assert gateway.compactions == ["agent:main:main"]


def test_a_reason_on_stderr_is_read_even_when_stdout_has_chatter(monkeypatch, tmp_path):
    gateway = FakeGateway([idle_session()], reply=(1, "Compacting agent:main:main...\n",
                                                   "Error: Already compacted."))

    first = run_groom(gateway, monkeypatch, tmp_path)
    run_groom(gateway, monkeypatch, tmp_path)

    assert first.failed == 0
    assert gateway.compactions == ["agent:main:main"]


def test_an_attempt_that_times_out_is_a_failure_and_is_retried(monkeypatch, tmp_path):
    def timed_out(_key):
        raise subprocess.TimeoutExpired("openclaw", 300)

    gateway = FakeGateway([idle_session()], reply=timed_out)

    first = run_groom(gateway, monkeypatch, tmp_path)
    second = run_groom(gateway, monkeypatch, tmp_path)

    assert first.failed == 1 and second.failed == 1
    assert gateway.compactions == ["agent:main:main", "agent:main:main"]


def test_the_command_fails_only_when_an_attempt_really_failed(monkeypatch, tmp_path):
    from argparse import Namespace

    from nexgen_core.cli import openclaw_cmds

    def exit_code(sessions, reply, scenario):
        monkeypatch.setenv("AGENT_STATE_DIR", str(tmp_path / scenario))
        monkeypatch.setattr(sessions_groom.subprocess, "run", FakeGateway(sessions, reply=reply))
        return openclaw_cmds.cmd_groom(Namespace(dry_run=False))

    huge = session(totalTokens=HARD_TOKENS + 1)
    assert exit_code([huge], (1, "", "The gateway refused the command."), "failed") == 1
    assert exit_code([huge], (1, "", "Error: Already compacted."), "already") == 0
    assert exit_code([], (0, "", ""), "nothing") == 0
