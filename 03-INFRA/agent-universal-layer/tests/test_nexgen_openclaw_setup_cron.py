"""Tests for OpenClaw cron job registration and convergence (D1).

nexgen: one-job reason="verify that OpenClaw context-guard cron job declares no-deliver and converges existing jobs"
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import i18n  # noqa: E402
from nexgen_core.openclaw import setup_cron, setup_steps  # noqa: E402
from nexgen_core.openclaw.setup_steps import Setup  # noqa: E402
from nexgen_core.openclaw.setup_cron import groom_job_step, heartbeat_job_step  # noqa: E402
from nexgen_core.openclaw_service import RunResult  # noqa: E402


@pytest.fixture(autouse=True)
def _english():
    """The lines below are English by construction, whatever the machine's locale is."""
    i18n.set_language("en")
    yield
    i18n.set_language(None)

PROACTIVE = {"id": "job-1", "name": "Proactive check", "enabled": True, "schedule": {"cron": "*/30 * * * *"}}


def a_setup(tmp_path: Path, jobs, calls: list[list[str]], squad: str | None = None) -> Setup:
    """A setup whose scheduler answers with `jobs`, recording every command it runs."""
    import json

    if squad is not None:
        (tmp_path / "squad.yaml").write_text(squad, encoding="utf-8")

    def fake_run(argv, env=None):
        calls.append(list(argv))
        if argv[1:4] == ["automations", "list", "--all"]:
            return RunResult(0, json.dumps({"ok": True, "jobs": jobs}), "")
        return RunResult(0, "", "")

    return Setup(
        home=tmp_path,
        env={},
        config_path=tmp_path / "openclaw.json",
        engine_root=tmp_path,
        vault_data=tmp_path,
        pinned="2026.11.2",
        dry_run=False,
        out=lambda msg: None,
        which=lambda name: f"/usr/bin/{name}",
        run=fake_run,
        service=MagicMock(),
    )


def test_groom_job_step_declares_no_deliver(tmp_path: Path) -> None:
    """The declared job must carry --no-deliver so OpenClaw refuses implicit isolated delivery."""
    calls: list[list[str]] = []

    def fake_run(argv, env=None):
        calls.append(list(argv))
        return RunResult(0, "", "")

    setup = Setup(
        home=tmp_path,
        env={},
        config_path=tmp_path / "openclaw.json",
        engine_root=tmp_path,
        vault_data=tmp_path,
        pinned="2026.11.2",
        dry_run=False,
        out=lambda msg: None,
        which=lambda name: f"/usr/bin/{name}",
        run=fake_run,
        service=MagicMock(),
    )

    groom_job_step(setup)

    assert len(calls) == 1
    argv = calls[0]
    assert "cron" in argv and "add" in argv
    assert "nexgen-context-guard" in argv
    assert "--declaration-key" in argv
    assert argv[argv.index("--declaration-key") + 1] == "nexgen:context-guard"
    # D1 requirement: the job must carry an explicit no-delivery flag
    assert "--no-deliver" in argv
    assert "--channel" not in argv or argv[argv.index("--channel") + 1] != "last"


def test_groom_job_step_declares_the_key_that_converges_the_job(tmp_path: Path) -> None:
    """OpenClaw patches the job a repeated `cron add` names by `--declaration-key`.

    The convergence itself belongs to OpenClaw; what the engine can prove here is the
    declaration it resends: the key that identifies the job and the `--no-deliver`
    that keeps the hourly run out of a chat.
    """
    calls: list[list[str]] = []

    def fake_gateway_run(argv, env=None):
        calls.append(list(argv))
        return RunResult(0, "", "")

    setup = Setup(
        home=tmp_path,
        env={},
        config_path=tmp_path / "openclaw.json",
        engine_root=tmp_path,
        vault_data=tmp_path,
        pinned="2026.11.2",
        dry_run=False,
        out=lambda msg: None,
        which=lambda name: f"/usr/bin/{name}",
        run=fake_gateway_run,
        service=MagicMock(),
    )

    groom_job_step(setup)

    assert len(calls) == 1
    argv = calls[0]
    assert argv[1:4] == ["cron", "add", "nexgen-context-guard"]
    assert argv[argv.index("--declaration-key") + 1] == "nexgen:context-guard"
    assert "--no-deliver" in argv
    assert "--channel" not in argv


def test_groom_job_step_says_so_when_the_gateway_refuses(tmp_path: Path) -> None:
    """A refused registration is one line in a long log, so the line has to be honest."""
    said: list[str] = []

    setup = Setup(
        home=tmp_path,
        env={},
        config_path=tmp_path / "openclaw.json",
        engine_root=tmp_path,
        vault_data=tmp_path,
        pinned="2026.11.2",
        dry_run=False,
        out=said.append,
        which=lambda name: f"/usr/bin/{name}",
        run=lambda argv, env=None: RunResult(1, "", "Error: the gateway is not reachable"),
        service=MagicMock(),
    )

    groom_job_step(setup)

    assert said == ["  Context guard: the job was not registered, the gateway refused it."]

def test_the_proactive_check_is_made_light_by_default(tmp_path: Path) -> None:
    """The job OpenClaw provisions is 48 model turns a day over the owner's own chat."""
    calls: list[list[str]] = []
    heartbeat_job_step(a_setup(tmp_path, [PROACTIVE], calls))

    edit = calls[1]
    assert edit[1:4] == ["automations", "edit", "job-1"]
    assert edit[edit.index("--every") + 1] == "6h"
    assert edit[edit.index("--session") + 1] == "isolated"
    assert "--light-context" in edit
    assert edit[edit.index("--tools") + 1] == ""
    # Nothing cheaper is declared on this machine, so the job keeps the agent's model.
    assert "--model" not in edit


def test_the_light_check_carries_a_prompt_that_needs_no_tools(tmp_path: Path) -> None:
    """A tool-free run cannot read the checklist the provisioned prompt asks for.

    Without this the model calls `exec`, the policy blocks it, and every run ends with
    "Exec blocked" instead of a liveness answer.
    """
    calls: list[list[str]] = []
    heartbeat_job_step(a_setup(tmp_path, [PROACTIVE], calls))

    edit = calls[1]
    assert "--message" in edit
    message = edit[edit.index("--message") + 1]
    assert message == setup_cron.LIGHT_HEARTBEAT_MESSAGE
    assert "no tools" in message
    assert "do not try to read" in message


def test_a_squad_key_can_set_the_light_check_prompt(tmp_path: Path) -> None:
    """A person who wants a different question answered writes it in `squad.yaml`."""
    calls: list[list[str]] = []
    heartbeat_job_step(a_setup(tmp_path, [PROACTIVE], calls, "heartbeat:\n  message: custom text\n"))

    edit = calls[1]
    assert edit[edit.index("--message") + 1] == "custom text"


def test_a_full_check_keeps_the_prompt_openclaw_provisioned(tmp_path: Path) -> None:
    """Full mode keeps the conversation and the tools, so its prompt has to stay too."""
    calls: list[list[str]] = []
    squad = "heartbeat:\n  mode: full\n  every: 30m\n"
    heartbeat_job_step(a_setup(tmp_path, [PROACTIVE], calls, squad))

    assert "--message" not in calls[1]


def test_the_shipped_example_squad_takes_the_light_check(tmp_path: Path) -> None:
    """A person copies that file: an uncommented `heartbeat:` block would move every install.

    The example documents the three modes in comments and declares none of them, so the
    setup converges the provisioned check to the cheap default: every 6h, isolated.
    """
    example = Path(__file__).resolve().parents[1] / "squad.example.yaml"
    calls: list[list[str]] = []

    heartbeat_job_step(a_setup(tmp_path, [PROACTIVE], calls, example.read_text(encoding="utf-8")))

    edit = calls[1]
    assert edit[edit.index("--every") + 1] == "6h"
    assert edit[edit.index("--session") + 1] == "isolated"


def test_the_light_check_runs_on_the_utility_model_the_file_declares(tmp_path: Path) -> None:
    """One cheap model answers both a title and a check: the key the setup just wrote."""
    calls: list[list[str]] = []
    setup = a_setup(tmp_path, [PROACTIVE], calls)
    setup.config_path.write_text(
        json.dumps({"agents": {"defaults": {"utilityModel": "ollama/qwen3:8b"}}}), encoding="utf-8",
    )

    heartbeat_job_step(setup)

    edit = calls[1]
    assert edit[edit.index("--model") + 1] == "ollama/qwen3:8b"


def test_a_squad_key_can_switch_the_check_off(tmp_path: Path) -> None:
    """The capability is kept by default, so removing it is a decision somebody writes down."""
    calls: list[list[str]] = []
    heartbeat_job_step(a_setup(tmp_path, [PROACTIVE], calls, "heartbeat:\n  mode: off\n"))

    assert calls[1] == ["/usr/bin/openclaw", "automations", "disable", "job-1"]


def test_a_squad_key_can_keep_the_full_check_untouched(tmp_path: Path) -> None:
    """`mode: full` is the older behaviour on purpose: the agent's own conversation, its tools."""
    calls: list[list[str]] = []
    heartbeat_job_step(a_setup(tmp_path, [PROACTIVE], calls, "heartbeat:\n  mode: full\n"))

    # Nothing to change: the scheduler lists the job and the setup leaves it as it is.
    assert calls == [["/usr/bin/openclaw", "automations", "list", "--all", "--json"]]


def test_a_full_check_still_follows_the_cadence_and_model_it_names(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    squad = "heartbeat:\n  mode: full\n  every: 30m\n  model: anthropic/claude-opus-5-5\n"
    heartbeat_job_step(a_setup(tmp_path, [PROACTIVE], calls, squad))

    edit = calls[1]
    assert edit[1:4] == ["automations", "edit", "job-1"]
    assert edit[edit.index("--every") + 1] == "30m"
    assert edit[edit.index("--model") + 1] == "anthropic/claude-opus-5-5"
    # A full check keeps the conversation and the tools it was asked to keep.
    assert "--light-context" not in edit
    assert "--session" not in edit
    assert "--tools" not in edit


def test_a_check_the_owner_asked_for_runs_light_and_in_its_own_context(tmp_path: Path) -> None:
    """The opt-in keeps the capability: no bootstrap files, no tools, its own cadence."""
    calls: list[list[str]] = []
    squad = "heartbeat:\n  enabled: true\n"
    heartbeat_job_step(a_setup(tmp_path, [PROACTIVE], calls, squad))

    edit = calls[1]
    assert edit[1:4] == ["automations", "edit", "job-1"]
    assert edit[edit.index("--every") + 1] == "6h"
    # `--light-context` is documented for isolated jobs only, so the session moves with it.
    assert edit[edit.index("--session") + 1] == "isolated"
    assert "--light-context" in edit
    assert edit[edit.index("--tools") + 1] == ""
    assert "--model" not in edit


def test_the_opt_in_may_name_its_own_cadence_and_model(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    squad = "heartbeat:\n  enabled: true\n  every: 12h\n  model: ollama/qwen3:8b\n"
    heartbeat_job_step(a_setup(tmp_path, [PROACTIVE], calls, squad))

    edit = calls[1]
    assert edit[edit.index("--every") + 1] == "12h"
    assert edit[edit.index("--model") + 1] == "ollama/qwen3:8b"


def test_a_disabled_job_the_owner_asked_for_is_switched_back_on(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    stopped = {**PROACTIVE, "enabled": False}
    heartbeat_job_step(a_setup(tmp_path, [stopped], calls, "heartbeat:\n  enabled: true\n"))

    assert calls[2] == ["/usr/bin/openclaw", "automations", "enable", "job-1"]


def test_a_full_check_the_owner_asked_for_is_switched_back_on_as_it_is(tmp_path: Path) -> None:
    """`mode: full` keeps the job in the conversation, and keeps it running."""
    calls: list[list[str]] = []
    stopped = {**PROACTIVE, "enabled": False}
    heartbeat_job_step(a_setup(tmp_path, [stopped], calls, "heartbeat:\n  mode: full\n"))

    assert calls[1] == ["/usr/bin/openclaw", "automations", "enable", "job-1"]
    assert not [call for call in calls if "--light-context" in call]


def test_a_scheduler_that_does_not_answer_changes_nothing(tmp_path: Path) -> None:
    """A CLI that fails is an unread scheduler, never an empty job list."""
    calls: list[list[str]] = []

    def fake_run(argv, env=None):
        calls.append(list(argv))
        return RunResult(1, "", "gateway down")

    setup = a_setup(tmp_path, [], calls)
    setup.run = fake_run
    heartbeat_job_step(setup)

    assert calls == [["/usr/bin/openclaw", "automations", "list", "--all", "--json"]]


def test_the_cron_step_resolves_the_command_through_the_setups_own_helper(
    tmp_path: Path, monkeypatch,
) -> None:
    """One resolver for the whole setup: a private copy drifts apart on the `.cmd`/`.exe` shim."""
    assert setup_cron.openclaw_bin is setup_steps.openclaw_bin

    calls: list[list[str]] = []
    monkeypatch.setattr(setup_cron, "openclaw_bin", lambda setup: "il-binario-del-setup")
    setup_cron.groom_job_step(a_setup(tmp_path, [], calls))

    assert calls[0][0] == "il-binario-del-setup"
