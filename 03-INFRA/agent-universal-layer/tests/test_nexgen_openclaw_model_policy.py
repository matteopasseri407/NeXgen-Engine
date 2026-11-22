# nexgen: one-job reason="tests for the override allowlist narrowed to the declared flat providers"
"""The picker's allowlist when the owner declares which providers run on a flat plan.

`model_policy` sends one list to `agents.defaults.modelPolicy.allow`: the providers a
person may switch an agent to. A declared flat list replaces "every provider that
answers" with that list; without one, the list is exactly what it was, because a
machine that never declared a plan must not be narrowed by an upgrade.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import i18n  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw import model_policy  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw.detect import Detection, Installation, LocalModels, Login  # noqa: E402 - sys.path shim
from nexgen_core.openclaw.profile import build_config  # noqa: E402 - sys.path shim for cloned checkout


@pytest.fixture(autouse=True)
def _english_notes():
    i18n.set_language("en")
    yield
    i18n.set_language(None)


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


def quota_file(home: Path, text: str) -> Path:
    """The `quota.yaml` the engine reads, at the path it reads it from."""
    path = home / "KnowledgeVault" / "03-INFRA" / "agent-universal-layer" / "quota.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_a_declared_flat_list_narrows_the_allowlist(tmp_path: Path) -> None:
    proposed = model_policy.with_reachable({"agents": {"defaults": {}}}, machine(tmp_path), flat=["opencode-go"])

    assert proposed["agents"]["defaults"]["modelPolicy"]["allow"] == ["opencode-go/*"]


def test_a_provider_outside_the_list_loses_its_runtime_pin(tmp_path: Path) -> None:
    """The pin is metadata for a provider the list no longer names: it does not belong there."""
    proposed = model_policy.with_reachable({"agents": {"defaults": {}}}, machine(tmp_path), flat=["opencode-go"])

    assert "models" not in proposed["agents"]["defaults"]


def test_a_declared_flat_provider_keeps_its_runtime_pin(tmp_path: Path) -> None:
    proposed = model_policy.with_reachable({"agents": {"defaults": {}}}, machine(tmp_path), flat=["anthropic"])

    defaults = proposed["agents"]["defaults"]
    assert defaults["modelPolicy"]["allow"] == ["anthropic/*"]
    assert defaults["models"]["anthropic/*"] == {"agentRuntime": {"id": "claude-cli"}}


def test_without_a_list_every_reachable_provider_stays(tmp_path: Path) -> None:
    defaults = model_policy.with_reachable({"agents": {"defaults": {}}}, machine(tmp_path))["agents"]["defaults"]

    assert defaults["modelPolicy"]["allow"] == ["anthropic/*", "openai/*", "opencode-go/*"]


def test_an_empty_list_is_no_restriction(tmp_path: Path) -> None:
    defaults = model_policy.with_reachable(
        {"agents": {"defaults": {}}}, machine(tmp_path), flat=[],
    )["agents"]["defaults"]

    assert defaults["modelPolicy"]["allow"] == ["anthropic/*", "openai/*", "opencode-go/*"]


def test_the_engine_writes_the_narrowed_allowlist(tmp_path: Path) -> None:
    """What the proposal carries when the Vault declares the list: the whole point of the key."""
    quota_file(tmp_path, "flat_providers:\n  - opencode-go\n")

    defaults = build_config(machine(tmp_path), home=tmp_path)["agents"]["defaults"]

    assert defaults["modelPolicy"]["allow"] == ["opencode-go/*"]


def test_the_plan_that_writes_the_file_carries_the_narrowed_allowlist(tmp_path: Path) -> None:
    """`build_plan` is the command's path to the file: the list has to survive it."""
    from nexgen_core.openclaw.plan import build_plan

    quota_file(tmp_path, "flat_providers:\n  - opencode-go\n")

    plan = build_plan(machine(tmp_path), home=tmp_path, env={}, vault_data=tmp_path / "KnowledgeVault")

    assert plan.config["agents"]["defaults"]["modelPolicy"]["allow"] == ["opencode-go/*"]


def test_a_proposal_built_with_no_home_reads_no_declaration(tmp_path: Path) -> None:
    """`home=None` proposes nothing, and reading the host's Vault there would make a test lie."""
    quota_file(tmp_path, "flat_providers:\n  - opencode-go\n")

    defaults = build_config(machine(tmp_path))["agents"]["defaults"]

    assert "anthropic/*" in defaults["modelPolicy"]["allow"]


def test_a_current_file_that_names_a_metered_provider_does_not_survive_the_narrowing(tmp_path: Path) -> None:
    """The owner asked for the narrowing by declaring the list: the file in force is not an exception."""
    current = {"agents": {"defaults": {"modelPolicy": {"allow": ["anthropic/*", "moonshot/*"]}}}}

    defaults = model_policy.with_reachable(
        {"agents": {"defaults": {}}}, machine(tmp_path), current, flat=["opencode-go"],
    )["agents"]["defaults"]

    assert defaults["modelPolicy"]["allow"] == ["opencode-go/*"]


def test_a_reachable_provider_that_is_not_declared_is_not_selectable(tmp_path: Path) -> None:
    """This is the guard: a child spawned on `anthropic/*` must not be an option any more."""
    detected = SimpleNamespace(
        claude=SimpleNamespace(available=True), codex=SimpleNamespace(available=False),
        opencode_go=False, local=SimpleNamespace(ollama=(), llamacpp=()),
    )

    allow = model_policy.with_reachable({"agents": {"defaults": {}}}, detected, flat=["opencode-go"])[
        "agents"]["defaults"]["modelPolicy"]["allow"]

    assert "anthropic/*" not in allow
