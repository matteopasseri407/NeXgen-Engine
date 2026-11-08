"""Tests for the session groomer policy: what gets compacted, and what never does."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from nexgen_core.openclaw import agy_compaction, groom_memory, sessions_groom
from nexgen_core.openclaw.sessions_groom import HARD_TOKENS, IDLE_SECONDS, blocked_runtimes, plan_groom

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


def test_an_idle_harness_session_is_picked_for_its_native_compaction():
    """An idle chat must not come back to a huge cold context, whichever harness runs it."""
    claude_backed = session(
        key="agent:capo:main",
        agentId="capo",
        totalTokens=400_000,
        lastInteractionAt=NOW - 2 * IDLE_SECONDS * 1000,
        agentRuntime={"id": "claude-cli", "source": "config"},
    )
    native = session(key="agent:posta:node-1", lastInteractionAt=NOW - 2 * IDLE_SECONDS * 1000)

    decisions = plan_groom([claude_backed, native], NOW)

    assert [(d.key, d.reason) for d in decisions] == [("agent:capo:main", "idle"), ("agent:posta:node-1", "idle")]


def test_a_harness_chat_still_in_use_under_the_limit_is_not_picked():
    in_use = session(key="agent:capo:main", totalTokens=400_000, lastInteractionAt=NOW,
                     agentRuntime={"id": "claude-cli", "source": "config"})

    assert plan_groom([in_use], NOW) == []


def test_a_harness_session_over_the_limit_is_picked_for_its_native_compaction():
    """Over the limit, a claude-cli chat is picked, so the backend's own manual /compact runs."""
    claude_backed = session(
        key="agent:capo:main",
        agentId="capo",
        totalTokens=HARD_TOKENS + 1,
        lastInteractionAt=NOW,
        agentRuntime={"id": "claude-cli", "source": "config"},
    )

    decisions = plan_groom([claude_backed], NOW)

    assert [(d.key, d.runtime) for d in decisions] == [("agent:capo:main", "claude-cli")]


def test_a_stale_harness_over_the_limit_is_picked():
    """A harness counter the gateway calls stale still means the chat is over the limit."""
    stale_harness = session(
        key="agent:posta:node-1", totalTokens=1_652_806, totalTokensFresh=False,
        agentRuntime={"id": "google-antigravity-cli", "source": "config"},
    )

    decisions = plan_groom([stale_harness], NOW, blocked=frozenset())

    assert [(d.key, d.reason) for d in decisions] == [("agent:posta:node-1", "over the limit")]


def test_a_stale_idle_harness_under_the_limit_is_picked():
    """A stale counter cannot prove the chat is small: the harness compacts it and says what happened."""
    stale_harness = session(
        key="agent:capo:main", totalTokens=74_969, totalTokensFresh=False,
        lastInteractionAt=NOW - 2 * IDLE_SECONDS * 1000,
        agentRuntime={"id": "claude-cli", "source": "config"},
    )

    assert [d.reason for d in plan_groom([stale_harness], NOW)] == ["idle"]


def test_a_stale_idle_direct_model_chat_is_picked_like_any_other():
    """A direct API chat (DeepSeek on OpenCode Go, for one) follows the same one-hour rule."""
    direct = session(
        key="agent:main:main", totalTokens=400_000, totalTokensFresh=False,
        lastInteractionAt=NOW - 2 * IDLE_SECONDS * 1000,
        model="deepseek-v4.1-flash", modelProvider="opencode-go",
        agentRuntime={"id": "openclaw", "source": "implicit"},
    )

    assert [d.reason for d in plan_groom([direct], NOW)] == ["idle"]


def test_an_idle_codex_session_is_picked_for_its_native_compaction():
    codex_backed = session(
        key="agent:main:main", totalTokens=74_969, lastInteractionAt=NOW - 2 * IDLE_SECONDS * 1000,
        agentRuntime={"id": "codex", "source": "session-key"},
    )

    assert [(d.runtime, d.reason) for d in plan_groom([codex_backed], NOW)] == [("codex", "idle")]


def test_a_stale_codex_session_over_the_limit_is_picked():
    codex_backed = session(
        key="agent:main:main", totalTokens=HARD_TOKENS + 1, totalTokensFresh=False, lastInteractionAt=NOW,
        agentRuntime={"id": "codex", "source": "session-key"},
    )

    assert [(d.runtime, d.reason) for d in plan_groom([codex_backed], NOW)] == [("codex", "over the limit")]


def test_a_stale_plain_chat_over_the_limit_is_not_picked():
    stale_plain = session(key="agent:main:main", totalTokens=HARD_TOKENS + 1, totalTokensFresh=False)

    assert plan_groom([stale_plain], NOW) == []


def test_a_stale_harness_with_an_unchanged_fingerprint_is_not_retried():
    """After a settled attempt, a stale harness over the limit waits until its counters move."""
    stale_harness = session(
        key="agent:capo:main", totalTokens=HARD_TOKENS + 1, totalTokensFresh=False, lastInteractionAt=NOW,
        agentRuntime={"id": "claude-cli", "source": "config"},
    )
    first = plan_groom([stale_harness], NOW)
    memory: dict = {}
    groom_memory.remember(memory, first[0].key, first[0].fingerprint, groom_memory.NOT_COMPACTED)

    assert plan_groom([stale_harness], NOW, memory=memory) == []


def test_a_counter_the_gateway_calls_stale_is_not_pressure():
    """After a compaction the reported number is the summariser's own usage, not the next turn."""
    stale = session(totalTokens=2_376_120, totalTokensFresh=False, lastInteractionAt=NOW)
    fresh = session(key="agent:posta:main", totalTokens=HARD_TOKENS + 1, totalTokensFresh=True)

    decisions = plan_groom([stale, fresh], NOW)

    assert [d.key for d in decisions] == ["agent:posta:main"]


def test_a_google_antigravity_cli_session_is_never_picked():
    """While the engine's manual compaction is not in the plugin, asking fails every hour."""
    antigravity = session(
        key="agent:main:main",
        totalTokens=859_000,
        lastInteractionAt=NOW - 2 * IDLE_SECONDS * 1000,
        agentRuntime={"id": "google-antigravity-cli", "source": "config"},
    )
    native = session(key="agent:posta:node-1", totalTokens=HARD_TOKENS + 1)

    decisions = plan_groom([antigravity, native], NOW)

    assert [d.key for d in decisions] == ["agent:posta:node-1"]


def _patched_plugin(home: Path) -> Path:
    """An installed antigravity plugin whose backend carries the engine's marker."""
    path = agy_compaction.backend_file(home)
    path.parent.mkdir(parents=True)
    path.write_text(f"{agy_compaction.ANCHOR}{agy_compaction.BLOCK}", encoding="utf-8")
    return path


def test_a_patched_antigravity_backend_is_picked_again(tmp_path: Path):
    """The same session the plan skipped is compacted once the plugin can answer."""
    antigravity = session(
        key="agent:main:main",
        totalTokens=HARD_TOKENS + 1,
        lastInteractionAt=NOW,
        agentRuntime={"id": "google-antigravity-cli", "source": "config"},
    )

    assert "google-antigravity-cli" in blocked_runtimes(tmp_path)
    _patched_plugin(tmp_path)

    assert "google-antigravity-cli" not in blocked_runtimes(tmp_path)
    decisions = plan_groom([antigravity], NOW, blocked=blocked_runtimes(tmp_path))
    assert [d.key for d in decisions] == ["agent:main:main"]


class FakeGateway:
    """The CLI's two verbs, answered from a table, with every compaction recorded."""

    def __init__(self, sessions, reply=(0, '{"ok": true, "compacted": true}', ""), listing=None):
        self.sessions = sessions
        self.reply = reply
        self.listing = listing
        self.compactions: list[str] = []
        self.argvs: list[list[str]] = []

    def __call__(self, argv, **_kwargs):
        if argv[2] == "list":
            if self.listing is not None:
                return subprocess.CompletedProcess(argv, *self.listing)
            return subprocess.CompletedProcess(argv, 0, json.dumps({"sessions": self.sessions}), "")
        self.argvs.append(list(argv))
        self.compactions.append(argv[3])
        code, out, err = self.reply(argv[3]) if callable(self.reply) else self.reply
        return subprocess.CompletedProcess(argv, code, out, err)


def idle_session(**overrides):
    """A chat far past the idle line, changed only where a test says so."""
    fields = {"lastInteractionAt": NOW - 2 * IDLE_SECONDS * 1000}
    fields.update(overrides)
    return session(**fields)


def run_groom(gateway, monkeypatch, home, dry_run=False, now=NOW):
    monkeypatch.setattr(sessions_groom.subprocess, "run", gateway)
    return sessions_groom.groom(
        openclaw="openclaw", now_ms=now, dry_run=dry_run, emit=lambda _line: None, home=home
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


def _chat_that_the_compaction_itself_changes(gateway, chat):
    """The compaction moves the chat's date and shrinks its counter: nobody wrote to it."""
    def compacted(_key):
        gateway.sessions = [{**chat, "totalTokens": 30_000, "updatedAt": chat["updatedAt"] + 5_000}]
        return 0, '{"ok": true, "compacted": true}', ""
    gateway.reply = compacted


def test_a_compacted_harness_chat_waits_until_it_is_written_to(monkeypatch, tmp_path):
    chat = idle_session(lastInteractionAt=None, updatedAt=NOW - 2 * IDLE_SECONDS * 1000, totalTokens=400_000,
                        agentRuntime={"id": "claude-cli", "source": "config"})
    gateway = FakeGateway([chat])
    _chat_that_the_compaction_itself_changes(gateway, chat)

    run_groom(gateway, monkeypatch, tmp_path)
    run_groom(gateway, monkeypatch, tmp_path)
    assert gateway.compactions == ["agent:main:main"]

    gateway.sessions = [{**chat, "totalTokens": 31_000, "updatedAt": NOW - IDLE_SECONDS * 1000 - 1}]
    run_groom(gateway, monkeypatch, tmp_path)
    assert gateway.compactions == ["agent:main:main", "agent:main:main"]


def test_a_compacted_direct_model_chat_waits_until_it_is_written_to(monkeypatch, tmp_path):
    chat = idle_session(lastInteractionAt=None, updatedAt=NOW - 2 * IDLE_SECONDS * 1000, totalTokens=400_000,
                        agentRuntime={"id": "openclaw", "source": "implicit"})
    gateway = FakeGateway([chat])
    _chat_that_the_compaction_itself_changes(gateway, chat)

    run_groom(gateway, monkeypatch, tmp_path)
    run_groom(gateway, monkeypatch, tmp_path)

    assert gateway.compactions == ["agent:main:main"]


def test_a_listing_that_times_out_after_the_attempts_still_keeps_the_memory(monkeypatch, tmp_path):
    gateway = FakeGateway([idle_session()])
    calls = {"list": 0}

    def flaky(argv, **kwargs):
        if argv[2] == "list":
            calls["list"] += 1
            if calls["list"] == 2:
                raise subprocess.TimeoutExpired(argv, 120)
        return gateway(argv, **kwargs)

    run_groom(flaky, monkeypatch, tmp_path)
    run_groom(flaky, monkeypatch, tmp_path)

    assert gateway.compactions == ["agent:main:main"]


SPENT_QUOTA = json.dumps({
    "ok": False,
    "compacted": False,
    "reason": "All models failed (2): opencode-go/deepseek-v4.1-flash: Summarization failed: 429 Go usage limit "
              "exceeded; Retry-After: 519069 seconds (rate_limit) | google-antigravity-cli/gemini-3.8-flash: fetch failed",
})


def test_a_spent_quota_is_not_tried_again_until_it_renews(monkeypatch, tmp_path):
    gateway = FakeGateway([idle_session()], reply=(0, SPENT_QUOTA, ""))
    renewal = 519_069 * 1000

    first = run_groom(gateway, monkeypatch, tmp_path)
    before = run_groom(gateway, monkeypatch, tmp_path, now=NOW + renewal - 1)
    assert gateway.compactions == ["agent:main:main"]
    assert first.failed == 1 and before.failed == 0

    run_groom(gateway, monkeypatch, tmp_path, now=NOW + renewal)
    assert gateway.compactions == ["agent:main:main", "agent:main:main"]


def test_a_spent_quota_keeps_the_other_chats_of_that_provider_waiting_too(monkeypatch, tmp_path):
    spent = idle_session(key="agent:one:main", agentId="one", modelProvider="opencode-go")
    gateway = FakeGateway([spent], reply=(0, SPENT_QUOTA, ""))
    run_groom(gateway, monkeypatch, tmp_path)

    gateway.reply = (0, '{"ok": true, "compacted": true}', "")
    gateway.sessions = [
        spent,
        idle_session(key="agent:two:main", agentId="two", modelProvider="opencode-go"),
        idle_session(key="agent:three:main", agentId="three", modelProvider="openai"),
    ]
    run_groom(gateway, monkeypatch, tmp_path, now=NOW + 1)

    assert gateway.compactions == ["agent:one:main", "agent:three:main"]


def test_a_quota_failure_with_no_renewal_date_waits_six_hours(monkeypatch, tmp_path):
    gateway = FakeGateway([idle_session()], reply=(1, "", "Summarization failed: 429 usage limit exceeded"))
    hour = 60 * 60 * 1000

    run_groom(gateway, monkeypatch, tmp_path)
    run_groom(gateway, monkeypatch, tmp_path, now=NOW + 5 * hour)
    assert gateway.compactions == ["agent:main:main"]

    run_groom(gateway, monkeypatch, tmp_path, now=NOW + 6 * hour)
    assert gateway.compactions == ["agent:main:main", "agent:main:main"]


def test_a_spent_quota_is_recognised_whichever_provider_reports_it():
    for text in (
        "429 Go usage limit exceeded",
        "Claude AI usage limit reached",
        "You've hit your limit",
        "usage_limit_reached",
        "RESOURCE_EXHAUSTED: try again later",
        "Your credit balance is too low",
        "insufficient API credits",
        "You are out of credits",
        "exceeded your current quota",
        "rate_limit_error",
    ):
        renews = sessions_groom._quota_renews_at(sessions_groom.Outcome(groom_memory.FAILED, text), NOW)
        assert renews == NOW + sessions_groom.UNDATED_QUOTA_WAIT_MS, text


def test_a_stated_renewal_is_used_whichever_provider_reports_it():
    outcome = sessions_groom.Outcome(groom_memory.FAILED, "anthropic: 429; Retry-After: 3600 seconds")

    assert sessions_groom._quota_renews_at(outcome, NOW) == NOW + 3_600_000


def test_a_failure_that_is_not_about_quota_is_retried_on_the_next_run(monkeypatch, tmp_path):
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


def test_a_backend_without_manual_compaction_is_unsupported_not_failed():
    """Any backend that owns compaction and refuses it answers the same way every hour."""
    proc = subprocess.CompletedProcess(
        ["openclaw", "sessions", "compact"], 1, "",
        'Error: CLI backend "google-antigravity-cli" owns compaction '
        "but does not support manual compaction.",
    )

    outcome = sessions_groom._classify(proc)

    assert outcome.state == groom_memory.UNSUPPORTED


def test_an_antigravity_refusal_is_settled_and_never_retried(monkeypatch, tmp_path):
    refused = (1, "", 'CLI backend "google-antigravity-cli" owns compaction '
                     "but does not support manual compaction.")
    gateway = FakeGateway([idle_session()], reply=refused)

    first = run_groom(gateway, monkeypatch, tmp_path)
    run_groom(gateway, monkeypatch, tmp_path)

    assert first.failed == 0 and len(first.lines) == 1
    assert gateway.compactions == ["agent:main:main"]


def test_a_claude_cli_session_over_the_limit_is_compacted_with_json_and_read_from_the_answer(monkeypatch, tmp_path):
    claude_backed = session(key="agent:capo:main", agentId="capo", totalTokens=HARD_TOKENS + 1,
                            agentRuntime={"id": "claude-cli", "source": "config"})
    gateway = FakeGateway([claude_backed])

    report = run_groom(gateway, monkeypatch, tmp_path)

    assert gateway.compactions == ["agent:capo:main"]
    assert gateway.argvs[0][-1] == "--json"
    assert report.failed == 0 and len(report.lines) == 1


def test_a_gateway_that_does_not_compact_is_not_a_success_and_is_not_retried(monkeypatch, tmp_path):
    """`compacted: false` is the gateway saying nothing changed: it is reported, not claimed."""
    gateway = FakeGateway([idle_session()], reply=(0, '{"ok": true, "compacted": false, "reason": "no transcript"}', ""))

    first = run_groom(gateway, monkeypatch, tmp_path)
    again = run_groom(gateway, monkeypatch, tmp_path)

    assert first.failed == 0 and "no transcript" in first.lines[0]
    assert again.lines == []
    assert gateway.compactions == ["agent:main:main"]


def test_an_exit_zero_without_the_json_answer_is_never_a_success(monkeypatch, tmp_path):
    """A plain-text answer, however friendly, does not prove a compaction: it is retried and counted."""
    gateway = FakeGateway([idle_session()], reply=(0, "Compacted session agent:main:main.\n", ""))

    first = run_groom(gateway, monkeypatch, tmp_path)
    second = run_groom(gateway, monkeypatch, tmp_path)

    assert first.failed == 1 and second.failed == 1
    assert gateway.compactions == ["agent:main:main", "agent:main:main"]


def test_an_answer_without_the_compacted_field_is_not_a_success():
    proc = subprocess.CompletedProcess(["openclaw"], 0, '{"ok": true, "key": "agent:main:main"}', "")

    assert sessions_groom._classify(proc).state == groom_memory.FAILED


def test_a_json_answer_that_compacted_is_a_success():
    proc = subprocess.CompletedProcess(["openclaw"], 0, '{"ok": true, "compacted": true}', "")

    assert sessions_groom._classify(proc).state == groom_memory.COMPACTED


def test_a_json_failure_is_a_failure_even_with_exit_zero():
    proc = subprocess.CompletedProcess(["openclaw"], 0, '{"ok": false, "compacted": true, "reason": "x"}', "")

    assert sessions_groom._classify(proc).state == groom_memory.FAILED


def test_the_compaction_command_asks_for_the_json_answer(monkeypatch):
    seen: list[list[str]] = []

    def run(argv, **_kwargs):
        seen.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, '{"ok": true, "compacted": true}', "")

    monkeypatch.setattr(sessions_groom.subprocess, "run", run)
    decision = plan_groom([session(totalTokens=HARD_TOKENS + 1)], NOW)[0]

    outcome = sessions_groom.compact("openclaw", decision)

    assert seen == [["openclaw", "sessions", "compact", "agent:main:main", "--agent", "main", "--json"]]
    assert outcome.state == groom_memory.COMPACTED


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
        # The command resolves the real CLI first: a machine without one must not decide the result.
        monkeypatch.setattr(sessions_groom, "_resolve_binary", lambda name="openclaw": "openclaw")
        return openclaw_cmds.cmd_groom(Namespace(dry_run=False))

    huge = session(totalTokens=HARD_TOKENS + 1)
    assert exit_code([huge], (1, "", "The gateway refused the command."), "failed") == 1
    assert exit_code([huge], (1, "", "Error: Already compacted."), "already") == 0
    assert exit_code([], (0, "", ""), "nothing") == 0
