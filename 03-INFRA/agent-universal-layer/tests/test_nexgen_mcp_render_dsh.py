"""DeepSeek Harness as an MCP target: what lands in its profile patch.

Every test runs the real renderer against a temporary `cordis.patch.yml` and
reads the YAML that comes out, because the two rules that make DSH different --
an `mcp-manager` entry replaces the whole server table, and a stdio child
starts with a scrubbed environment -- are exactly the ones a schema accepts
while the connector never starts.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.mcp_state import record_declared_servers  # noqa: E402
from nexgen_core.paths import dsh_profile_patch  # noqa: E402
from nexgen_core.renderer import McpRenderer  # noqa: E402

MANIFEST = """schema_version: 1
retired_servers: [gone-forever]
servers:
  node-tool:
    tier: core
    transport: stdio
    command: node
    args: ["server.mjs"]
    env:
      DEMO_MODE: headless
      DEMO_API_KEY: "${DEMO_API_KEY}"
  python-tool:
    tier: core
    transport: stdio
    command: python3
    args: ["-m", "demo"]
  remote:
    tier: core
    transport: http
    url: "https://demo.invalid/mcp"
    auth: {type: bearer, env: DEMO_TOKEN}
    timeouts: {startup: 12, tool: 90}
  lazy-one:
    exposure: lazy
    transport: stdio
    command: node
    args: ["lazy-one.mjs"]
    env:
      LAZY_SECRET: "${LAZY_SECRET}"
  lazy-mcp:
    exposure: eager
    tier: core
    transport: stdio
    command: python3
    args: ["${AGENT_ENGINE_ROOT}/agent-universal-layer/mcp/lazy-mcp.py"]
    env:
      AGENT_VAULT_DATA: "${AGENT_VAULT_DATA}"
  only-codex:
    tier: core
    transport: stdio
    command: node
    args: ["codex.mjs"]
    targets: [codex]
"""

#: What the app's Settings page and the person left in the file before a sync.
EXISTING = """# Your patch layer for this dsh profile, applied after every bundle layer:
# a top-level YAML array of loader patch entries.
- id: ui-chat
  name: "@deepseek-ai/dsh-client-ui-chat"
  config:
    transcriptView: standard
- id: llm-pi-ai
  name: "@deepseek-ai/dsh-llm-pi-ai"
  config:
    providers:
      opencode-go:
        apiKeyEnv: OPENCODE_GO_API_KEY
- id: agent-default-model
  name: "@deepseek-ai/dsh-agent-default-model"
  config:
    provider: opencode-go
    model: deepseek-v4.1-flash
    reasoningEffort: high
- id: mcp-manager
  config:
    servers:
      user-own:
        transport: stdio
        command: node
        args: ["mine.mjs"]
      old-engine-server:
        transport: stdio
        command: node
        args: ["dust.mjs"]
      gone-forever:
        transport: stdio
        command: node
        args: ["retired.mjs"]
"""

SECRET_VALUES = (
    "fake-key-should-never-be-written",
    "fake-token-should-never-be-written",
    "fake-lazy-secret-should-never-be-written",
)


@pytest.fixture
def renderer(tmp_path: Path, monkeypatch) -> McpRenderer:
    """A renderer over a synthetic vault, with DSH's location override cleared."""
    monkeypatch.delenv("DSH_HOME", raising=False)
    monkeypatch.setenv("DEMO_API_KEY", SECRET_VALUES[0])
    monkeypatch.setenv("DEMO_TOKEN", SECRET_VALUES[1])
    monkeypatch.setenv("LAZY_SECRET", SECRET_VALUES[2])
    vault = tmp_path / "vault"
    manifest = vault / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(MANIFEST, encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    return McpRenderer(vault_data=vault, engine_root=tmp_path / "engine", home=home)


def _install(renderer: McpRenderer, text: str = EXISTING) -> Path:
    """The patch and the first-run id a machine with the harness already has."""
    patch = dsh_profile_patch(renderer.home)
    patch.parent.mkdir(parents=True, exist_ok=True)
    patch.write_text(text, encoding="utf-8")
    (renderer.home / ".dsh" / ".anonymous-user-id").write_text("anonymous\n", encoding="utf-8")
    return patch


def _entries(patch: Path) -> list:
    return yaml.safe_load(patch.read_text(encoding="utf-8"))


def _by_id(patch: Path) -> dict:
    return {entry["id"]: entry for entry in _entries(patch) if isinstance(entry, dict) and "id" in entry}


def _servers(patch: Path) -> dict:
    return _by_id(patch)["mcp-manager"]["config"]["servers"]


def test_the_first_render_keeps_every_foreign_entry_in_place(renderer) -> None:
    patch = _install(renderer)
    before = [entry for entry in yaml.safe_load(EXISTING) if isinstance(entry, dict)][:3]

    assert renderer.render_dsh(write=True)[0]

    entries = _entries(patch)
    ids = [entry["id"] for entry in entries if isinstance(entry, dict)]
    assert ids[:3] == ["ui-chat", "llm-pi-ai", "agent-default-model"]
    assert ids[3:] == [
        "mcp-manager",
        "session-log-deepseek",
        "session-telemetry-otel",
        "plugin-package-inventory-deepseek",
    ]
    assert entries[:3] == before, "the app's and the person's entries are untouched and in order"
    assert patch.read_text(encoding="utf-8").startswith("# Your patch layer for this dsh profile")
    by_id = _by_id(patch)
    assert by_id["session-log-deepseek"]["config"] == {"enabled": False}
    assert by_id["session-telemetry-otel"]["config"] == {"mode": "DISABLED"}
    assert by_id["plugin-package-inventory-deepseek"]["config"] == {"enabled": False}


def test_a_server_the_person_added_survives_two_renders(renderer) -> None:
    patch = _install(renderer)

    assert renderer.render_dsh(write=True)[0]
    first = _servers(patch)["user-own"]
    assert first["args"] == ["mine.mjs"]

    assert renderer.render_dsh(write=True)[0]
    assert _servers(patch)["user-own"] == first


def test_only_the_names_the_engine_owns_disappear(renderer) -> None:
    patch = _install(renderer)
    record_declared_servers({"old-engine-server"}, renderer.home)

    assert renderer.render_dsh(write=True)[0]

    servers = _servers(patch)
    assert "gone-forever" not in servers, "a tombstone in retired_servers wins"
    assert "old-engine-server" not in servers, "the engine declared it once, so it removes it"
    assert "user-own" in servers, "a name the engine never declared is never touched"


def test_a_credential_is_a_template_and_its_value_never_reaches_the_file(renderer) -> None:
    patch = _install(renderer)

    assert renderer.render_dsh(write=True)[0]

    text = patch.read_text(encoding="utf-8")
    servers = _servers(patch)
    assert servers["node-tool"]["env"]["DEMO_API_KEY"] == "${credential:DEMO_API_KEY}"
    assert servers["node-tool"]["env"]["DEMO_MODE"] == "headless"
    assert servers["remote"]["headers"]["Authorization"] == "Bearer ${credential:DEMO_TOKEN}"
    assert servers["remote"]["startupTimeoutMs"] == 12000
    assert servers["remote"]["toolCallTimeoutMs"] == 90000
    assert servers["lazy-mcp"]["env"]["LAZY_SECRET"] == "${credential:LAZY_SECRET}"
    assert servers["lazy-mcp"]["env"]["LAZY_MCP_CLI"] == "dsh"
    assert servers["python-tool"]["env"]["PYTHONUTF8"] == "1"
    for value in SECRET_VALUES:
        assert value not in text


def test_a_second_render_does_not_touch_the_file(renderer) -> None:
    patch = _install(renderer)
    assert renderer.render_dsh(write=True)[0]
    first = patch.read_bytes()
    written_at = patch.stat().st_mtime_ns
    backups = sorted(patch.parent.glob(patch.name + ".bak-*"))

    assert renderer.render_dsh(write=True)[0]

    assert patch.read_bytes() == first, "the same patch, byte for byte"
    assert patch.stat().st_mtime_ns == written_at, "an unchanged patch is not rewritten"
    assert sorted(patch.parent.glob(patch.name + ".bak-*")) == backups, "nor backed up again"


def test_a_machine_without_the_harness_gets_no_file(renderer, monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda _name: None)

    assert renderer.render_dsh(write=True)[0]

    assert not (renderer.home / ".dsh").exists(), "not installed means not even a home"


def test_a_patch_that_is_not_a_list_stops_the_render_without_touching_it(renderer) -> None:
    patch = _install(renderer, "config: not-a-list\n")
    before = patch.read_bytes()

    with pytest.raises(ValueError) as excinfo:
        renderer.render_dsh(write=True)

    assert str(patch) in str(excinfo.value), "the message names the file to fix"
    assert patch.read_bytes() == before
