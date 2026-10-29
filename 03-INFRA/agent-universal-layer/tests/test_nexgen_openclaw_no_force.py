"""The doctor never sends a person to a forced service install.

`openclaw gateway install --force` rebuilds the unit from the invoking shell and
erases the variables the gateway service carried: seven of them, measured on the
owner's Linux gateway. The doctor's answer to a key left in the gateway's
environment is the setup, which rewrites `.env`, plus a restart. The unit's own
copy, `gateway.systemd.env`, is named with its full path because the engine never
writes it: that one line is the person's to remove, and removing it is the way to
clear the copy without rebuilding the service around it.

The second half of this file guards the rule itself: no message and no check may
propose the forced command again, whichever file it is talking about.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import nexgen_core  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core import i18n  # noqa: E402
from nexgen_core.checks.anthropic_catalog_checks import check_anthropic_catalog  # noqa: E402
from nexgen_core.paths import openclaw_config  # noqa: E402
from nexgen_core.report import Severity  # noqa: E402

CLAUDE_REF = "anthropic/claude-opus-5-5"

#: The copy the generated systemd unit loads through `EnvironmentFile=`.
SERVICE_COPY = "gateway.systemd.env"

#: The command a person must never be sent to.
FORCED_INSTALL = "gateway install --force"


@pytest.fixture(autouse=True)
def _english():
    i18n.set_language("en")
    yield
    i18n.set_language(None)


@pytest.fixture(autouse=True)
def _no_openclaw_env(monkeypatch):
    monkeypatch.delenv("OPENCLAW_CONFIG_PATH", raising=False)
    monkeypatch.delenv("OPENCLAW_STATE_DIR", raising=False)


def _home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    return home


def _claude_cli_config() -> dict:
    """One agent on Claude Code's CLI; nothing else names the key."""
    return {
        "agents": {
            "defaults": {"models": {CLAUDE_REF: {"agentRuntime": {"id": "claude-cli"}}}},
            "entries": {"capo": {"model": CLAUDE_REF}},
        },
    }


def _write_config(home: Path) -> Path:
    path = openclaw_config(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_claude_cli_config()), encoding="utf-8")
    return path


def _key_in(home: Path, name: str) -> Path:
    path = openclaw_config(home).parent / name
    path.write_text('ANTHROPIC_API_KEY="sk-ant-workspace-scoped"\n', encoding="utf-8")
    return path


def test_the_stale_service_copy_is_named_and_cleared_by_hand(tmp_path: Path) -> None:
    """The engine cannot rewrite the unit's copy, so the action names the exact file."""
    home = _home(tmp_path)
    _write_config(home)
    copy = _key_in(home, SERVICE_COPY)

    outcome = check_anthropic_catalog(home)

    assert outcome is not None
    assert outcome.severity == Severity.WARN
    assert "--force" not in outcome.action
    assert FORCED_INSTALL not in outcome.action
    assert "nexgen openclaw setup" in outcome.action
    assert "nexgen openclaw restart" in outcome.action
    assert str(copy) in outcome.action


def test_a_key_in_the_env_file_is_cleared_without_a_reinstall(tmp_path: Path) -> None:
    home = _home(tmp_path)
    _write_config(home)
    env_file = _key_in(home, ".env")

    outcome = check_anthropic_catalog(home)

    assert outcome is not None
    assert outcome.severity == Severity.WARN
    assert "--force" not in outcome.action
    assert FORCED_INSTALL not in outcome.action
    assert "nexgen openclaw setup" in outcome.action
    assert "nexgen openclaw restart" in outcome.action
    assert str(env_file) in outcome.action


def test_no_message_or_doctor_advice_proposes_a_forced_service_install() -> None:
    """Every English message and every doctor's advice, scanned once for the rule."""
    root = Path(nexgen_core.__file__).resolve().parent
    files = (
        sorted((root / "locales").glob("*.py"))
        + sorted((root / "checks").rglob("*.py"))
        + sorted((root / "openclaw").rglob("*.py"))
    )
    assert files, "the scan found nothing to read; the paths moved"

    offenders = []
    for file in files:
        text = file.read_text(encoding="utf-8")
        for number, line in enumerate(text.splitlines(), 1):
            if FORCED_INSTALL in line:
                offenders.append(f"{file.relative_to(root)}:{number}")

    assert not offenders, f"a message or a check proposes {FORCED_INSTALL}: {offenders}"
