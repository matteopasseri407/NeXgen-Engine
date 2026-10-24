"""Firecrawl is the cloud connector, with a key the person brings.

The engine no longer deploys a Firecrawl stack and no longer speaks to one:
the connector is the vendor's own MCP server against the official API, mounted
only on a machine whose secret store has the key. These tests pin the
declaration and what each CLI is handed, with no network call and a fake home.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

UL = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = UL.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.checks import reachability_checks  # noqa: E402
from nexgen_core.config import load_mcp_manifest  # noqa: E402
from nexgen_core.mcp_render import openclaw as openclaw_mod  # noqa: E402
from nexgen_core.paths import (  # noqa: E402
    antigravity_configs,
    claude_config,
    codex_config,
    openclaw_config,
    opencode_config_path,
)
from nexgen_core.renderer import McpRenderer  # noqa: E402
from nexgen_core.report import Severity  # noqa: E402

MANIFEST = UL / "mcp" / "manifest.yaml"

#: The five CLIs the manifest targets with the connector.
CLIS = ("claude", "codex", "antigravity", "opencode", "openclaw")

#: The key of a machine that never existed: a test that sees this string in a
#: rendered value has found the real key copied out of the environment.
FAKE_KEY = "finta-chiave-firecrawl"


def _manifest() -> dict:
    return load_mcp_manifest(MANIFEST)


def _firecrawl() -> dict:
    return _manifest()["servers"]["firecrawl"]


@pytest.fixture
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """A vault that carries the real manifest, and a home that is nobody's."""
    vault, home = tmp_path / "vault", tmp_path / "home"
    target = vault / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml"
    target.parent.mkdir(parents=True)
    target.write_text(MANIFEST.read_text(encoding="utf-8"), encoding="utf-8")
    home.mkdir()
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    monkeypatch.delenv("FIRECRAWL_API_URL", raising=False)
    monkeypatch.delenv("FIRECRAWL_TUNNEL_PORT", raising=False)
    return vault, home


def test_the_manifest_declares_the_cloud_connector() -> None:
    server = _firecrawl()
    assert server["transport"] == "stdio"
    assert server["command"] == "npx"
    assert server["args"] == ["-y", "firecrawl-mcp@3.25.3"]
    assert server["env"] == {"FIRECRAWL_API_KEY": "${FIRECRAWL_API_KEY}"}
    assert server["require_env"] == "FIRECRAWL_API_KEY"
    assert set(server["targets"]) == set(CLIS)


def test_no_declared_value_points_at_a_self_hosted_stack() -> None:
    text = MANIFEST.read_text(encoding="utf-8")
    assert "FIRECRAWL_API_URL" not in text
    assert "FIRECRAWL_TUNNEL_PORT" not in text
    assert "3002" not in text


def test_the_connector_is_the_reviewed_package_at_the_pinned_version() -> None:
    (package,) = [arg for arg in _firecrawl()["args"] if "firecrawl-mcp" in arg]
    assert package == "firecrawl-mcp@3.25.3"


def test_the_declaration_keys_the_secret_store_and_nothing_else() -> None:
    """The one secret the connector needs is referenced by name, never written."""
    env = _firecrawl()["env"]
    assert list(env) == ["FIRECRAWL_API_KEY"]
    assert env["FIRECRAWL_API_KEY"].startswith("${") and env["FIRECRAWL_API_KEY"].endswith("}")


@pytest.mark.parametrize("cli", CLIS)
def test_a_machine_with_the_key_mounts_the_connector(machine, monkeypatch, cli: str) -> None:
    """Mounted with a reference, never with the key.

    A credential is not written into a harness's config: the value stays
    `${FIRECRAWL_API_KEY}` and the process that starts the server resolves it
    from the environment, or the engine's own launcher does.
    """
    vault, home = machine
    monkeypatch.setenv("FIRECRAWL_API_KEY", FAKE_KEY)
    servers = McpRenderer(vault_data=vault, home=home).load_resolved_servers(cli)
    assert servers["firecrawl"]["command"]
    assert servers["firecrawl"]["env"] == {"FIRECRAWL_API_KEY": "${FIRECRAWL_API_KEY}"}
    assert FAKE_KEY not in json.dumps(servers["firecrawl"])


def test_no_rendered_harness_file_holds_the_key(machine, monkeypatch) -> None:
    """The same fact on disk: every CLI's own file carries the reference, none the value.

    Rendered for real into a fake home, then read back as text. A file that is
    missing would make this test vacuous, so every one of them must exist.
    """
    vault, home = machine
    monkeypatch.setenv("FIRECRAWL_API_KEY", FAKE_KEY)
    openclaw_json = openclaw_config(home)
    openclaw_json.parent.mkdir(parents=True)
    openclaw_json.write_text('{"mcp": {"servers": {}}}', encoding="utf-8")
    renderer = McpRenderer(vault_data=vault, home=home)

    rendered = renderer.render_all(write=True)

    assert all(rendered.values()), rendered
    files = [
        claude_config(home), codex_config(home), opencode_config_path(home),
        *antigravity_configs(home), openclaw_config(home),
    ]
    missing = [str(path) for path in files if not path.is_file()]
    assert missing == [], missing
    for path in files:
        text = path.read_text(encoding="utf-8")
        assert FAKE_KEY not in text, path
        assert "${FIRECRAWL_API_KEY}" in text, path


@pytest.mark.parametrize("cli", CLIS)
def test_a_machine_without_the_key_mounts_nothing(machine, cli: str) -> None:
    vault, home = machine
    servers = McpRenderer(vault_data=vault, home=home).load_resolved_servers(cli)
    assert "firecrawl" not in servers


def test_openclaw_receives_the_reference_instead_of_the_key(machine, monkeypatch) -> None:
    """OpenClaw substitutes `${VAR}` itself: the file must not hold the secret."""
    vault, home = machine
    monkeypatch.setenv("FIRECRAWL_API_KEY", FAKE_KEY)
    renderer = McpRenderer(vault_data=vault, home=home)
    kept = openclaw_mod._env_references(renderer)
    assert kept["firecrawl"] == {"FIRECRAWL_API_KEY": "${FIRECRAWL_API_KEY}"}
    assert FAKE_KEY not in str(kept)

    config = home / ".openclaw" / "openclaw.json"
    config.parent.mkdir(parents=True)
    config.write_text('{"mcp": {"servers": {}}}', encoding="utf-8")
    openclaw_mod.render(renderer, write=True)
    written = config.read_text(encoding="utf-8")
    assert "${FIRECRAWL_API_KEY}" in written
    assert FAKE_KEY not in written


def test_the_reachability_probe_has_no_address_to_probe(machine, monkeypatch) -> None:
    """A cloud connector has no host on this machine: the TCP probe skips it."""
    vault, home = machine
    monkeypatch.setenv("FIRECRAWL_API_KEY", FAKE_KEY)
    entry = McpRenderer(vault_data=vault, home=home).load_resolved_servers("claude")["firecrawl"]
    assert reachability_checks._extract_probe_target(entry) is None


# ---- the doctor: presence of the key, never its value -----------------------

def test_the_doctor_reports_that_the_connector_can_mount() -> None:
    outcome = reachability_checks.check_cloud_connector_key(MANIFEST, {"FIRECRAWL_API_KEY": FAKE_KEY})
    assert outcome.id == "mcp.cloud_key"
    assert outcome.severity == Severity.OK
    assert "FIRECRAWL_API_KEY" in outcome.message


def test_the_doctor_says_when_the_key_is_missing_and_how_to_store_it() -> None:
    outcome = reachability_checks.check_cloud_connector_key(MANIFEST, {})
    assert outcome.severity == Severity.WARN
    assert "FIRECRAWL_API_KEY" in outcome.message
    assert "nexgen-secrets set FIRECRAWL_API_KEY" in outcome.action


@pytest.mark.parametrize("present", [True, False])
def test_the_doctor_never_puts_the_key_in_its_report(present: bool) -> None:
    env = {"FIRECRAWL_API_KEY": FAKE_KEY} if present else {}
    outcome = reachability_checks.check_cloud_connector_key(MANIFEST, env)
    reported = " ".join(str(part) for part in (outcome.message, outcome.action, outcome.detail))
    assert FAKE_KEY not in reported


def test_an_unreadable_manifest_does_not_end_the_check(tmp_path: Path) -> None:
    broken = tmp_path / "manifest.yaml"
    broken.write_text("servers: [unclosed", encoding="utf-8")
    outcome = reachability_checks.check_cloud_connector_key(broken, {})
    assert outcome.severity == Severity.UNDETERMINED
    assert outcome.id == "mcp.cloud_key"


def test_a_manifest_without_the_connector_has_nothing_to_check(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text("schema_version: 1\nservers:\n  other:\n    command: npx\n", encoding="utf-8")
    outcome = reachability_checks.check_cloud_connector_key(manifest, {})
    assert outcome.severity == Severity.OK


def test_a_connector_that_declares_no_key_is_not_judged(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        "schema_version: 1\nservers:\n  firecrawl:\n    command: npx\n    args: ['-y', 'firecrawl-mcp@3.25.3']\n",
        encoding="utf-8",
    )
    outcome = reachability_checks.check_cloud_connector_key(manifest, {})
    assert outcome.severity == Severity.UNDETERMINED


def test_the_doctor_registers_the_cloud_key_check(machine, monkeypatch) -> None:
    """The check exists only if the doctor runs it: pin the wiring, not just the function."""
    import types

    from nexgen_core import doctor as doctor_mod

    vault, home = machine
    registered: list[tuple[str, object]] = []
    doc = types.SimpleNamespace(
        vault_data=vault, home=home, state_dir=home / "state", engine_root=home / "engine",
    )
    doctor_mod._mcp_and_module_checks(
        lambda check_id, produce, resource=None: registered.append((check_id, produce)), doc,
    )
    produced = dict(registered)
    assert "mcp.cloud_key" in produced

    monkeypatch.setenv("FIRECRAWL_API_KEY", FAKE_KEY)
    assert produced["mcp.cloud_key"]().severity == Severity.OK
    monkeypatch.delenv("FIRECRAWL_API_KEY")
    assert produced["mcp.cloud_key"]().severity == Severity.WARN


# ---- the deploy tree: no stack, no tunnel, no secrets to fill -----------------

def test_the_deploy_tree_ships_no_firecrawl_stack() -> None:
    deploy = UL.parent / "deploy"
    assert not (deploy / "firecrawl").exists()
    assert sorted(path.name for path in deploy.iterdir() if path.is_dir() and not path.name.startswith(".")) == [
        "n8n", "ocr", "vault-mcp",
    ]


def _active_lines(path: Path) -> list[str]:
    """The lines that do something: comments explain history and may name it."""
    return [line for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")]


def test_the_server_bootstrap_starts_no_firecrawl() -> None:
    active = " ".join(_active_lines(UL.parent / "deploy" / "bootstrap-vps.sh")).lower()
    assert "firecrawl" not in active
    assert "3002" not in active
    assert "brave_search_api_key" not in active


def test_backup_restore_offers_no_firecrawl_stack() -> None:
    active = " ".join(_active_lines(UL.parent / "deploy" / "backup-restore.sh")).lower()
    assert "firecrawl" not in active


def test_the_server_env_example_asks_for_no_firecrawl_secrets() -> None:
    active = " ".join(_active_lines(UL.parent / "deploy" / ".env.example"))
    assert "FIRECRAWL_" not in active
    assert "BRAVE_SEARCH_API_KEY" not in active
    assert "OPENAI_API_KEY" not in active
