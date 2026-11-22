# nexgen: one-job reason="tests that the flat provider list reaches the proposal and the scheduled doctor"
"""The declared flat list at the two places it has to arrive.

The list is one key in the Vault's `quota.yaml`, and `flat_only` already reads it. What
is proven here is the wiring: `build_config` hands it to `model_policy` so the written
`modelPolicy.allow` is that list, and the scheduled `nexgen doctor` runs the guard, so a
ref outside the list fails before a turn spends on it.

Every test builds its own home and Vault under `tmp_path`; the owner's `quota.yaml`, the
owner's `openclaw.json` and the Companion app's settings are never read.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import i18n  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.checks import schedule  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.checks.openclaw_checks import check_openclaw_flat  # noqa: E402 - sys.path shim
from nexgen_core.doctor import Doctor  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw.detect import (  # noqa: E402 - sys.path shim for cloned checkout
    Detection,
    Installation,
    LocalModels,
    Login,
)
from nexgen_core.openclaw.profile import build_config  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.report import CheckOutcome, Report, Severity  # noqa: E402 - sys.path shim

#: The one provider this machine allows to spend: the list the owner would write.
FLAT = "flat_providers:\n  - opencode-go\n"

#: Where the ceilings and that list live inside a Vault.
QUOTA_SUBPATH = ("03-INFRA", "agent-universal-layer", "quota.yaml")


@pytest.fixture(autouse=True)
def _english_notes():
    """Pin the source language: the sentences are asserted below."""
    i18n.set_language("en")
    yield
    i18n.set_language(None)


@pytest.fixture(autouse=True)
def _no_machine_paths(monkeypatch, tmp_path: Path):
    """No test reads this machine's OpenClaw file, Vault or Companion settings."""
    for name in ("OPENCLAW_CONFIG_PATH", "OPENCLAW_STATE_DIR", "AGENT_VAULT_DATA", "KNOWLEDGE_VAULT_PATH"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))


def machine(tmp_path: Path) -> Detection:
    """A machine with a Claude login, a Codex login and the flat OpenCode Go key."""
    return Detection(
        openclaw=Installation(installed=True, version="2026.10.6", config_path=str(tmp_path / "openclaw.json")),
        claude=Login(binary=True, login=True),
        codex=Login(binary=True, login=True),
        opencode_go=True,
        local=LocalModels(),
        n8n=False,
    )


def write_quota(vault: Path, text: str) -> Path:
    """The `quota.yaml` the engine reads, at the path it reads it from."""
    path = vault.joinpath(*QUOTA_SUBPATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def write_openclaw(home: Path, config: dict) -> Path:
    """The configuration in force, where OpenClaw keeps it under this home."""
    path = home / ".openclaw" / "openclaw.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


def metered_config() -> dict:
    """One agent on a provider billed per token, which the flat list does not name."""
    return {"agents": {"entries": {"capo": {"model": "openai/gpt-5"}}}}


def allowed(config: dict) -> list[str]:
    """The override allowlist this proposal would write."""
    return config["agents"]["defaults"]["modelPolicy"]["allow"]


def test_the_proposal_narrows_the_allowlist_to_the_declared_flat_providers(tmp_path: Path) -> None:
    """`nexgen openclaw setup` writes the owner's list, not every provider that answers."""
    home = tmp_path / "home"
    write_quota(home / "KnowledgeVault", FLAT)

    proposed = build_config(machine(tmp_path), home=home)

    assert allowed(proposed) == ["opencode-go/*"], "a picker that still offers a metered plan is the spend the list stops"


def test_without_the_key_the_proposal_is_what_it_writes_today(tmp_path: Path) -> None:
    """A machine that never declared a plan is not narrowed by an upgrade."""
    home = tmp_path / "home"
    write_quota(home / "KnowledgeVault", "failover:\n  chain: []\n")

    narrowed = build_config(machine(tmp_path), home=home)
    before = build_config(machine(tmp_path))

    assert allowed(narrowed) == allowed(before)
    assert allowed(narrowed) == ["anthropic/*", "openai/*", "opencode-go/*"]


def test_the_scheduled_doctor_fails_a_ref_outside_the_flat_list(tmp_path: Path) -> None:
    """The line the guard owes: the agent's own model, named with where it was read."""
    home, vault = tmp_path / "home", tmp_path / "vault"
    write_openclaw(home, metered_config())
    write_quota(vault, FLAT)

    outcome = check_openclaw_flat(home, vault)

    assert isinstance(outcome, CheckOutcome)
    assert outcome.id == "openclaw.flat_models"
    assert outcome.severity == Severity.BROKEN
    assert "agents.entries.capo.model -> openai/gpt-5" in outcome.message
    assert outcome.action, "a failure a person cannot act on is a failure they learn to skip"


def test_the_scheduled_doctor_reports_nothing_outside_the_list_without_the_key(tmp_path: Path) -> None:
    """No declared list means no restriction: the same file is neither a failure nor a warning."""
    home, vault = tmp_path / "home", tmp_path / "vault"
    write_openclaw(home, metered_config())
    write_quota(vault, "failover:\n  chain: []\n")

    outcome = check_openclaw_flat(home, vault)

    assert isinstance(outcome, CheckOutcome)
    assert outcome.severity == Severity.OK
    assert "flat_providers" in outcome.message


def test_a_machine_without_an_openclaw_file_gets_no_line(tmp_path: Path) -> None:
    """The family's own rule: a machine that never chose OpenClaw is told nothing about it."""
    write_quota(tmp_path / "vault", FLAT)

    assert check_openclaw_flat(tmp_path / "home", tmp_path / "vault") is None


def test_the_scheduled_doctor_registers_the_flat_guard(tmp_path: Path, monkeypatch) -> None:
    """The wiring itself: the id is a check `nexgen doctor` runs, and it judges the live file."""
    home, vault = tmp_path / "home", tmp_path / "vault"
    write_openclaw(home, metered_config())
    write_quota(vault, FLAT)
    registered: list[schedule.Check] = []

    def record(checks, **_kwargs):
        registered.extend(checks)
        return Report()

    monkeypatch.setattr(schedule, "run", record)
    doctor = Doctor(home=home, vault_data=vault, state_dir=tmp_path / "state", engine_root=tmp_path / "engine")
    doctor.run_diagnostics(apply_remedies=False)

    guard = {check.id: check for check in registered}.get("openclaw.flat_models")
    assert guard is not None, "the scheduled doctor must run the guard, not only the OpenClaw command"
    assert guard.produce().severity == Severity.BROKEN
