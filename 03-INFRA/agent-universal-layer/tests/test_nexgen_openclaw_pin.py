"""The Node range OpenClaw declares, and the small parser that reads it."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.openclaw.pin import node_satisfies, required_node_version  # noqa: E402 - sys.path shim for cloned checkout

ENGINE_ROOT = Path(__file__).resolve().parents[2]
PINNED_RANGE = ">=24.22.1 <25 || >=26.12.1"


@pytest.mark.parametrize("version", ["24.22.1", "v24.22.1", "24.30.0", "26.12.1", "27.0.0"])
def test_a_version_in_the_declared_range_satisfies(version):
    assert node_satisfies(version, PINNED_RANGE) is True


@pytest.mark.parametrize("version", ["22.11.0", "24.22.0", "25.0.0", "25.99.0", "26.12.0"])
def test_a_version_outside_the_range_does_not(version):
    assert node_satisfies(version, PINNED_RANGE) is False


def test_an_unknown_version_or_range_is_not_a_failure():
    """A guess about the host is worse than a clear note, so unknown never blocks."""
    assert node_satisfies(None, PINNED_RANGE) is True
    assert node_satisfies("24.22.1", None) is True
    assert node_satisfies("24.22.1", "^24.0.0") is True


def test_the_pin_file_declares_the_range_this_release_needs():
    assert required_node_version(ENGINE_ROOT) == PINNED_RANGE
