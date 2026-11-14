"""Tests for OpenClaw cron job registration and convergence (D1).

nexgen: one-job reason="verify that OpenClaw context-guard cron job declares no-deliver and converges existing jobs"
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.openclaw.setup_steps import Setup  # noqa: E402
from nexgen_core.openclaw.setup_cron import groom_job_step  # noqa: E402
from nexgen_core.openclaw_service import RunResult  # noqa: E402


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
        stamp="123",
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


def test_groom_job_step_converges_existing_job(tmp_path: Path) -> None:
    """When a job is already present on OpenClaw with channel: last, running setup converges it."""
    # Simulated OpenClaw gateway cron store
    store: dict[str, dict] = {
        "nexgen:context-guard": {
            "name": "nexgen-context-guard",
            "declarationKey": "nexgen:context-guard",
            "delivery": {"channel": "last"},
        }
    }

    def fake_gateway_run(argv, env=None):
        # OpenClaw's cron add with declarationKey updates the existing job
        if argv[1:3] == ["cron", "add"]:
            dec_key = argv[argv.index("--declaration-key") + 1]
            if dec_key in store:
                # If --no-deliver was passed, delivery mode is none
                if "--no-deliver" in argv:
                    store[dec_key]["delivery"] = {"mode": "none"}
                else:
                    store[dec_key]["delivery"] = {"channel": "last"}
            return RunResult(0, "", "")
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
        stamp="123",
    )

    groom_job_step(setup)

    # Job in store must now be converged to mode: none
    assert store["nexgen:context-guard"]["delivery"] == {"mode": "none"}
