"""The loopback deny is decided on the file the write leaves, not on the proposal alone.

`nexgen openclaw setup` writes its proposal merged inside the file in force, so every
key the engine does not propose survives the write -- `agents.defaults.models` above
all, and the agents a squad no longer declares. Those keys are what decides which
runtime an agent really runs, and the doctor reads exactly that merged file. Asked of
the proposal alone, the deny named nobody: the setup wrote zero deny rules for the
agents only the file carries, the doctor reported the loopback as open, and a second
run changed nothing because the same proposal produced the same omission. That is the
state a setup left the server and the desktop in on 2026-10-28.

Three promises, one per test: every agent the written file puts on `claude-cli` keeps
its connectors off the loopback, a second run writes not one byte, and a machine with
no such agent is written exactly as before.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import i18n  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.checks.claude_loopback_checks import check_claude_loopback  # noqa: E402
from nexgen_core.openclaw import claude_loopback, setup_write  # noqa: E402
from nexgen_core.openclaw.claude_cli import claude_cli_agents  # noqa: E402
from nexgen_core.openclaw.claude_loopback import patterns as deny_patterns  # noqa: E402
from nexgen_core.openclaw.claude_native import ensure as ensure_native  # noqa: E402
from nexgen_core.openclaw.detect import Detection, Installation, LocalModels, Login  # noqa: E402
from nexgen_core.openclaw.plan import build_plan  # noqa: E402
from nexgen_core.paths import openclaw_config  # noqa: E402
from nexgen_core.report import Severity  # noqa: E402
from nexgen_core.renderer import McpRenderer  # noqa: E402

#: Two connectors, mounted directly by both CLIs, so the deny has something to name.
MANIFEST = """schema_version: 1
servers:
  vault-library:
    exposure: eager
    transport: stdio
    command: node
    args: ["vault.mjs"]
  n8n-mcp:
    exposure: eager
    transport: stdio
    command: node
    args: ["n8n.mjs"]
"""

#: The connectors the test's manifest carries: what every `claude-cli` agent must deny.
SERVERS = ("vault-library", "n8n-mcp")

CLAUDE_MODEL = "anthropic/claude-opus-5-5"
#: The model the file in force runs by default: not the one the proposal names for any agent.
CARRIED_MODEL = "anthropic/claude-opus-4-5"
GO_REF = "opencode-go/deepseek-v4.1-flash"


@pytest.fixture(autouse=True)
def _english():
    """These lines are English by construction."""
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


def _renderer(tmp_path: Path, home: Path, manifest: str = MANIFEST) -> McpRenderer:
    """A renderer over a synthetic vault, so the manifest is what these tests say it is."""
    vault = tmp_path / "vault"
    path = vault / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(manifest, encoding="utf-8")
    return McpRenderer(vault_data=vault, engine_root=tmp_path / "engine", home=home)


def _detected(config_path: Path, *, claude: bool = True) -> Detection:
    """A machine that has OpenClaw and, when asked, a Claude login."""
    return Detection(
        openclaw=Installation(installed=True, version="2026.10.6", config_path=str(config_path), config_present=True),
        claude=Login(binary=claude, login=claude),
        codex=Login(binary=False),
        opencode_go=True,
        local=LocalModels(),
        n8n=True,
    )


def _put(home: Path, config: dict) -> Path:
    """The file in force, as a write of it would leave it."""
    path = openclaw_config(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(setup_write.render(config), encoding="utf-8")
    return path


def _carried_file() -> dict:
    """A gateway whose defaults put every Anthropic model on `claude-cli`, plus two agents.

    `carriera` and `marketing` name no model of their own: they run the inherited
    default, which the wildcard pins to Claude Code's CLI. Neither agent is in the
    proposal a setup builds from this machine's roster, so the write leaves both in
    place -- and the doctor still judges them, because they are in the file.
    """
    return {
        "agents": {
            "defaults": {
                "model": CARRIED_MODEL,
                "models": {"anthropic/*": {"agentRuntime": {"id": "claude-cli"}}},
            },
            "entries": {
                "carriera": {"name": "carriera", "workspace": "/home/person/.openclaw/workspace-carriera"},
                "marketing": {"name": "marketing", "workspace": "/home/person/.openclaw/workspace-marketing"},
            },
        },
        "mcp": {"servers": {"vault-library": {"command": "node", "args": ["vault.mjs"]}}},
    }


def _go_only_file() -> dict:
    """A machine whose every agent names a Go model: nothing here can reach Claude Code."""
    return {
        "agents": {
            "entries": {
                "main": {"name": "main", "model": GO_REF},
                "automazioni": {"name": "automazioni", "model": GO_REF},
            },
        },
        "mcp": {"servers": {"vault-library": {"command": "node", "args": ["vault.mjs"]}}},
    }


def _plan(home: Path, renderer: McpRenderer, *, claude: bool = True):
    """The plan a setup on this machine would print and apply."""
    return build_plan(
        detected=_detected(openclaw_config(home), claude=claude), home=home, env={},
        vault_data=renderer.vault_data, engine_root=renderer.engine_root,
    )


def _token() -> str:
    """A token only this test knows, so the bytes do not change between runs."""
    return "generated-for-this-test"


def _written(plan, path: Path) -> dict:
    """What `write_step` puts on disk: the merge of the proposal inside the file in force."""
    document, _generated = setup_write.with_token(
        setup_write.merged_document(plan.current, plan.config), _token,
    )
    path.write_text(setup_write.render(document), encoding="utf-8")
    return document


def _deny(config: dict, agent_id: str) -> set[str]:
    """The deny patterns one agent's entry carries, as the doctor reads them."""
    entry = config.get("agents", {}).get("entries", {}).get(agent_id) or {}
    deny = (entry.get("tools") or {}).get("deny")
    return {str(item) for item in deny} if isinstance(deny, list) else set()


def test_every_agent_the_written_file_puts_on_claude_cli_keeps_the_connectors_off_the_loopback(
    tmp_path: Path,
) -> None:
    """The regression: the deny was computed on the proposal, the doctor reads the file."""
    home = _home(tmp_path)
    renderer = _renderer(tmp_path, home)
    path = _put(home, _carried_file())

    document = _written(_plan(home, renderer), path)

    on_cli = set(claude_cli_agents(document))
    assert on_cli == {"capo", "carriera", "marketing"}, (
        "the file decides who runs on claude-cli: capo is proposed, the other two are carried"
    )
    for agent_id in sorted(on_cli):
        missing = set(deny_patterns(SERVERS)) - _deny(document, agent_id)
        assert not missing, f"{agent_id} can still reach {sorted(missing)} through the loopback"

    ensure_native(document, home=home, vault_data=renderer.vault_data,
                  engine_root=renderer.engine_root, out=lambda _line: None)
    outcome = check_claude_loopback(home, renderer.vault_data, renderer.engine_root)
    assert outcome is not None, "the doctor has a claude-cli agent to judge here"
    assert outcome.severity == Severity.OK, outcome.message


def test_a_second_setup_on_the_same_file_writes_the_same_bytes(tmp_path: Path) -> None:
    """Convergence: what the first run wrote is what the second run proposes to write."""
    home = _home(tmp_path)
    renderer = _renderer(tmp_path, home)
    path = _put(home, _carried_file())
    first = setup_write.render(_written(_plan(home, renderer), path)).encode("utf-8")

    second = _plan(home, renderer)
    document, generated = setup_write.with_token(
        setup_write.merged_document(second.current, second.config), _token,
    )

    assert generated is False, "the token the first run wrote is the person's from then on"
    assert document == second.current, "the setup would see the file as already written"
    assert setup_write.render(document).encode("utf-8") == first


def test_a_machine_with_no_claude_cli_agent_is_written_exactly_as_before(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The promise `_with_loopback` makes: nothing such a proposal is even asked of."""
    asked: list[object] = []

    def _never(*args: object, **_kwargs: object) -> dict:
        asked.append(args)
        raise AssertionError("the deny was asked of a machine with no claude-cli agent")

    monkeypatch.setattr(claude_loopback, "apply", _never)
    home = _home(tmp_path)
    renderer = _renderer(tmp_path, home)
    path = _put(home, _go_only_file())

    plan = _plan(home, renderer, claude=False)
    document = _written(plan, path)

    assert asked == [], "the early answer must be given before any deny is computed"
    assert claude_cli_agents(document) == []
    for agent_id, entry in document["agents"]["entries"].items():
        assert "deny" not in (entry.get("tools") or {}), agent_id
    assert "__*" not in setup_write.render(document), "no connector pattern reached the file"
    assert not [item for item in (*plan.added, *plan.changed) if item.endswith(".deny")]
    assert check_claude_loopback(home, renderer.vault_data, renderer.engine_root) is None
