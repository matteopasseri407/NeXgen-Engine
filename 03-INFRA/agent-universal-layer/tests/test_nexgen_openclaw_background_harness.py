"""Tests for background jobs avoiding CLI harness models (D4).

nexgen: one-job reason="verify that unattended background jobs skip CLI harnesses without isolated completion and fall back to direct cloud models"
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.openclaw import background  # noqa: E402
from nexgen_core.report import Severity  # noqa: E402

AGY_MODEL = "google-antigravity-cli/gemini-3.8-flash"
DIRECT_CLOUD = "opencode-go/deepseek-v4.1-flash"
CLAUDE_REF = "anthropic/claude-opus-5-5"
CODEX_REF = "openai/gpt-6-luna"
LOCAL_MODEL = "ollama/qwen3:8b"


def a_claude_cli_agent(fallbacks: list[str] | None = None) -> dict:
    """The shape `profile.py` writes for a Claude subscription: an ordinary ref, a runtime pin."""
    return {
        "agents": {
            "defaults": {"models": {CLAUDE_REF: {"agentRuntime": {"id": "claude-cli"}}}},
            "entries": {"main": {"model": {"primary": CLAUDE_REF, "fallbacks": list(fallbacks or [])}}},
        },
    }


def test_primary_agy_with_direct_fallback_chooses_fallback() -> None:
    """Primary agy with a direct fallback -> the fallback is chosen."""
    config = {
        "plugins": {"allow": ["memory-core"]},
        "agents": {
            "entries": {
                "main": {
                    "model": {
                        "primary": AGY_MODEL,
                        "fallbacks": [DIRECT_CLOUD],
                    }
                }
            }
        }
    }
    assert background.cloud_model(config) == DIRECT_CLOUD
    b = background.blocks({}, config)
    assert b["plugins"]["entries"]["memory-core"]["config"]["dreaming"]["model"] == DIRECT_CLOUD
    assert b["agents"]["defaults"]["compaction"]["model"] == DIRECT_CLOUD


def test_primary_direct_unchanged() -> None:
    """Primary direct cloud model -> unchanged."""
    config = {
        "agents": {
            "entries": {
                "main": {
                    "model": DIRECT_CLOUD,
                }
            }
        }
    }
    assert background.cloud_model(config) == DIRECT_CLOUD
    assert background.blocks({}, config) == {}


def test_only_cli_harness_models_chooses_nothing_and_doctor_warns() -> None:
    """Only CLI harness models -> nothing chosen, doctor line."""
    config = {
        "agents": {
            "entries": {
                "main": {
                    "model": {
                        "primary": AGY_MODEL,
                        "fallbacks": ["claude-cli/claude-opus-5-5"],
                    }
                }
            }
        }
    }
    assert background.cloud_model(config) is None
    assert background.blocks({}, config) == {}
    line = background.report(config)
    assert line.severity == Severity.WARN
    assert "isolated completion" in line.message or AGY_MODEL in line.message


def test_an_agent_pinned_to_claude_cli_is_a_cli_harness() -> None:
    """`anthropic/claude-opus-5-5` with `agentRuntime.id: claude-cli` runs Claude Code, not the API."""
    config = a_claude_cli_agent()
    assert background.is_cli_harness(CLAUDE_REF, config) is True
    # The provider prefix alone reads the same ref as a plain Anthropic model.
    assert background.is_cli_harness(CLAUDE_REF) is False


def test_an_agent_pinned_to_codex_is_a_cli_harness() -> None:
    """The same shape for the Codex runtime: the pin says it, not the `openai/` prefix."""
    config = {
        "agents": {
            "defaults": {"models": {CODEX_REF: {"agentRuntime": {"id": "codex"}}}},
            "entries": {"main": {"model": CODEX_REF}},
        },
    }
    assert background.is_cli_harness(CODEX_REF, config) is True
    assert background.is_cli_harness(CODEX_REF) is False


def test_the_cloud_model_a_job_moves_to_is_never_a_cli_harness() -> None:
    """A Claude Code fallback is skipped like a local model: the next direct model is chosen."""
    config = a_claude_cli_agent(fallbacks=[DIRECT_CLOUD])
    assert background.cloud_model(config) == DIRECT_CLOUD


def test_the_doctor_names_a_job_that_would_run_on_claude_code() -> None:
    """The line the doctor prints on a machine whose compaction model is a CLI harness."""
    config = a_claude_cli_agent()
    config["agents"]["defaults"]["compaction"] = {"model": CLAUDE_REF}
    line = background.report(config)
    assert line.severity == Severity.WARN
    assert CLAUDE_REF in line.message


def test_the_doctor_names_a_harness_job_and_a_local_job_in_one_line() -> None:
    """Both faults do not depend on each other, so one line carries both."""
    config = a_claude_cli_agent()
    config["agents"]["defaults"]["compaction"] = {"model": CLAUDE_REF}
    config["agents"]["entries"]["main"]["model"] = LOCAL_MODEL
    line = background.report(config)
    assert line.severity == Severity.WARN
    assert CLAUDE_REF in line.message, "metà harness della stessa riga"
    assert LOCAL_MODEL in line.message, "metà locale della stessa riga"
