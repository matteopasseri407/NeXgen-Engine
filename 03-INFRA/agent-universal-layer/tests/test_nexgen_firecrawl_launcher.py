"""The key reaches the server process from the reference, never from a config file.

The owner's rule: a credential is not written into a harness's configuration.
What lands in `~/.claude.json`, `~/.codex/config.toml`, `opencode.json` and the
rest is `${FIRECRAWL_API_KEY}`, and the value has to arrive some other way.

These tests prove the other half, on the road the engine's own launcher takes:
the trim shim (`mcp-trim.py`, which is what the renderer writes for a server
declaring `tools_deny`) asks the gateway for the entry, the gateway resolves the
reference from the credential chain, and the child starts with the value in its
environment. No process is started here and no socket is opened: the gateway is
a recorder.
"""
from __future__ import annotations

import importlib.util
import io
import sys
from pathlib import Path

import pytest

MCP_DIR = Path(__file__).resolve().parents[2] / "agent-universal-layer" / "mcp"
TRIM = MCP_DIR / "mcp-trim.py"

FAKE_KEY = "fc-launcher-key-never-real"

#: A firecrawl entry shaped like the manifest's: the key arrives as a reference.
FIRECRAWL = {
    "exposure": "eager",
    "transport": "stdio",
    "command": "npx",
    "args": ["-y", "firecrawl-mcp@3.25.3"],
    "env": {"FIRECRAWL_API_KEY": "${FIRECRAWL_API_KEY}"},
    "require_env": "FIRECRAWL_API_KEY",
    "tools_deny": ["firecrawl_crawl"],
}


@pytest.fixture
def gateway():
    """`lazy_env` and `lazy_manifest`, loaded from the gateway's own folder, cleaned up after."""
    before_path = sys.path[:]
    sys.path.insert(0, str(MCP_DIR))
    for name in ("lazy_env", "lazy_manifest"):
        sys.modules.pop(name, None)
    import lazy_env  # noqa: PLC0415 - loaded from the folder the fixture just added
    import lazy_manifest  # noqa: PLC0415

    yield lazy_env, lazy_manifest

    sys.path[:] = before_path
    for name in ("lazy_env", "lazy_manifest"):
        sys.modules.pop(name, None)


def _no_deposit(lazy_env, monkeypatch) -> None:
    """A machine whose secret store is empty: only the environment can answer."""
    class Empty:
        @staticmethod
        def read_deposit() -> dict[str, str]:
            return {}

    monkeypatch.setattr(lazy_env, "deposit_env", lambda: Empty)


def _no_templates(text: str) -> str:
    return text


def test_the_gateway_resolves_the_reference_and_hands_the_value_to_the_child(gateway, monkeypatch):
    lazy_env, lazy_manifest = gateway
    _no_deposit(lazy_env, monkeypatch)
    monkeypatch.setenv("FIRECRAWL_API_KEY", FAKE_KEY)

    entry = lazy_manifest.served_entry(
        "firecrawl", {"servers": {"firecrawl": FIRECRAWL}}, {}, lazy_env.Credentials(lambda: {}), _no_templates,
    )

    assert entry is not None
    assert "_unavailable" not in entry, entry.get("_unavailable")
    assert entry["env"] == {"FIRECRAWL_API_KEY": FAKE_KEY}
    child = lazy_env.child_environment(entry["env"])
    assert child["FIRECRAWL_API_KEY"] == FAKE_KEY, "the child starts with the value, not with the reference"


def test_without_the_key_the_entry_says_which_variable_is_missing(gateway, monkeypatch):
    """A machine with no key mounts nothing and says so, instead of spawning a server that answers 401."""
    lazy_env, lazy_manifest = gateway
    _no_deposit(lazy_env, monkeypatch)
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)

    entry = lazy_manifest.served_entry(
        "firecrawl", {"servers": {"firecrawl": FIRECRAWL}}, {}, lazy_env.Credentials(lambda: {}), _no_templates,
    )

    assert entry is not None
    assert "FIRECRAWL_API_KEY" in entry["_unavailable"]
    assert entry["env"]["FIRECRAWL_API_KEY"] == "", "no key, no value: never the literal reference either"


def test_the_trim_shim_asks_the_gateway_for_the_entry(monkeypatch):
    """The shim the renderer writes is the one that resolves: it starts nothing of its own.

    A stub gateway stands in for `lazy-mcp.py`: what is under test is the wiring
    (`served_entry(argv[0])`), not the proxy it loads.
    """
    asked: list[str] = []

    class Handle:
        def stop(self) -> None:
            asked.append("stopped")

    class StubGateway:
        @staticmethod
        def served_entry(name: str):
            asked.append(name)
            return dict(FIRECRAWL)

        _ServerHandle = staticmethod(lambda name, entry: Handle())

    spec = importlib.util.spec_from_file_location("mcp_trim_under_test", TRIM)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "_load_gateway", lambda: StubGateway)
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))

    assert module.main(["firecrawl"]) == 0

    assert asked == ["firecrawl", "stopped"], asked


def test_the_shim_refuses_a_server_the_manifest_does_not_carry(monkeypatch):
    class StubGateway:
        @staticmethod
        def served_entry(name: str):
            return None

        _ServerHandle = staticmethod(lambda name, entry: None)

    spec = importlib.util.spec_from_file_location("mcp_trim_under_test_absent", TRIM)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "_load_gateway", lambda: StubGateway)
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))

    assert module.main(["firecrawl"]) == 2
