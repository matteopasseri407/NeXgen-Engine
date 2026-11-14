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


def test_primary_agy_with_direct_fallback_chooses_fallback() -> None:
    """Primary agy with a direct fallback -> the fallback is chosen."""
    config = {
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
