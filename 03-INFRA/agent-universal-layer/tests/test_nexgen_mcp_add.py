"""`nexgen mcp add`: il comando che toglie la scrittura YAML a mano.

Regole verificate qui: la validazione precede qualunque scrittura (un add
sbagliato finisce dove è iniziato), il file scritto è sempre un manifest
valido (backup + rilettura + rollback), i segreti non entrano in chiaro, e
il risultato è vivo: il renderer lo vede, solo sui target dichiarati.
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.config import load_mcp_manifest  # noqa: E402
from nexgen_core.mcp_add import add_server  # noqa: E402
from nexgen_core.renderer import McpRenderer  # noqa: E402


def _manifest_path(sandbox) -> Path:
    return sandbox.mcp_dir / "manifest.yaml"


def _backup_names(sandbox) -> set[str]:
    return {p.name for p in sandbox.mcp_dir.glob("manifest.yaml.bak-*")}


def test_add_writes_a_valid_entry_with_backup(sandbox):
    before = _backup_names(sandbox)

    code, message = add_server(
        "filesystem", "codex",
        command="npx", args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp/ws"],
        home=sandbox.home, vault_data=sandbox.vault,
    )

    assert code == 0, message
    data = load_mcp_manifest(_manifest_path(sandbox))
    entry = data["servers"]["filesystem"]
    assert entry["command"] == "npx"
    assert entry["args"] == ["-y", "@modelcontextprotocol/server-filesystem", "/tmp/ws"]
    assert entry["targets"] == ["codex"]
    assert len(_backup_names(sandbox)) == len(before) + 1, "il .bak è la rete di sicurezza"


def test_add_renders_only_on_the_declared_targets(sandbox):
    code, message = add_server(
        "filesystem", "codex,opencode",
        command="npx", args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp/ws"],
        home=sandbox.home, vault_data=sandbox.vault,
    )
    assert code == 0, message

    renderer = McpRenderer(vault_data=sandbox.vault, home=sandbox.home)
    assert "filesystem" in renderer.load_resolved_servers("codex")
    assert "filesystem" in renderer.load_resolved_servers("opencode")
    assert "filesystem" not in renderer.load_resolved_servers("claude")


def test_dry_run_writes_nothing(sandbox):
    before = sandbox.tree_snapshot()

    code, message = add_server(
        "filesystem", "claude",
        command="npx", args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp/ws"],
        dry_run=True, home=sandbox.home, vault_data=sandbox.vault,
    )

    assert code == 0, message
    assert "filesystem" in message, "il dry-run mostra lo stub"
    assert sandbox.tree_snapshot() == before


def test_a_second_add_of_the_same_name_is_refused(sandbox):
    code, _ = add_server("filesystem", "claude", command="npx",
                         home=sandbox.home, vault_data=sandbox.vault)
    assert code == 0
    code, message = add_server("filesystem", "claude", command="npx",
                               home=sandbox.home, vault_data=sandbox.vault)
    assert code == 2
    assert "already declared" in message


def test_a_retired_name_stays_retired(sandbox):
    manifest = _manifest_path(sandbox)
    manifest.write_text(
        manifest.read_text(encoding="utf-8") + "retired_servers: [ghost]\n",
        encoding="utf-8",
    )

    code, message = add_server("ghost", "claude", command="npx",
                               home=sandbox.home, vault_data=sandbox.vault)

    assert code == 2
    assert "retired" in message


def test_unknown_targets_are_refused_before_any_write(sandbox):
    before = sandbox.tree_snapshot()

    code, message = add_server("filesystem", "cursor,warp", command="npx",
                               home=sandbox.home, vault_data=sandbox.vault)

    assert code == 2
    assert "cursor" in message
    assert sandbox.tree_snapshot() == before


def test_a_secret_shaped_env_value_is_refused_with_the_reference_form(sandbox):
    before = sandbox.tree_snapshot()

    code, message = add_server(
        "thing", "claude", command="npx",
        env_pairs=["API_KEY=sk-live-abcdefghij0123456789"],
        home=sandbox.home, vault_data=sandbox.vault,
    )

    assert code == 2
    assert "${" in message, "il messaggio deve dire la forma giusta"
    assert sandbox.tree_snapshot() == before


def test_an_env_reference_is_accepted(sandbox):
    code, message = add_server(
        "thing", "claude", command="npx",
        env_pairs=["API_KEY=${MY_API_KEY}"],
        home=sandbox.home, vault_data=sandbox.vault,
    )
    assert code == 0, message
    data = load_mcp_manifest(_manifest_path(sandbox))
    assert data["servers"]["thing"]["env"]["API_KEY"] == "${MY_API_KEY}"


def test_http_server_with_auth_env_and_readonly_gates(sandbox):
    code, message = add_server(
        "remote", "claude,codex", url="https://vault.example.com/mcp",
        auth_env="VAULT_TOKEN", lazy=True, readonly=True,
        home=sandbox.home, vault_data=sandbox.vault,
    )
    assert code == 0, message
    data = load_mcp_manifest(_manifest_path(sandbox))
    entry = data["servers"]["remote"]
    assert entry["transport"] == "http"
    assert entry["auth"] == {"env": "VAULT_TOKEN"}
    assert entry["lazy"] is True and entry["readonly"] is True


def test_readonly_without_lazy_is_meaningless_and_refused(sandbox):
    code, message = add_server("remote", "claude", url="https://x.example.com/mcp",
                               readonly=True, home=sandbox.home, vault_data=sandbox.vault)
    assert code == 2
    assert "lazy" in message


def test_a_broken_write_is_rolled_back_to_the_original(sandbox, monkeypatch):
    """La rete di sicurezza deve reggere: se la rilettura fallisce, il file
    torna esattamente com'era."""
    import nexgen_core.renderer_cli as renderer_cli

    original = _manifest_path(sandbox).read_text(encoding="utf-8")
    real_load = renderer_cli.load_mcp_manifest

    def load_then_break(path):
        real_load(path)
        raise ValueError("simulated post-write failure")

    monkeypatch.setattr(renderer_cli, "load_mcp_manifest", load_then_break)

    from nexgen_core.renderer_cli import insert_server_stubs

    ok, message, bak = insert_server_stubs(_manifest_path(sandbox), ["  ghost:"])
    monkeypatch.setattr(renderer_cli, "load_mcp_manifest", real_load)

    assert not ok
    assert _manifest_path(sandbox).read_text(encoding="utf-8") == original
