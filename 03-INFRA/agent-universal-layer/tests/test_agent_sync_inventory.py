"""Onboarding inventory slice on agent_sync.py: the pure helpers behind the
`inventory` mode (skill split + manifest name parsing). The MCP part is already
covered by test_render_inventory.py; the bootstrap part is trivial existence."""
from __future__ import annotations

from conftest import load_agent_sync_module


def test_inventory_cli_exits_2_when_mcp_manifest_is_broken(sandbox, monkeypatch, capsys):
    """Regression: _inventory_cli always `return 0`, ignoring render.py
    --inventory's own returncode -- contradicting its own docstring ("Exit 0
    (report) or 2 on an env/config error"). A missing/invalid MCP manifest
    makes render.py print '>>> STOP: invalid MCP manifest' and exit 2; the
    wrapper must propagate that, not silently report success."""
    mod = load_agent_sync_module(sandbox)
    monkeypatch.setenv("HOME", str(sandbox.home))
    monkeypatch.setenv("KNOWLEDGE_VAULT_PATH", str(sandbox.vault))
    (sandbox.mcp_dir / "manifest.yaml").unlink()

    rc = mod._inventory_cli([])

    assert rc == 2
    err = capsys.readouterr().err
    assert "STOP" in err


def test_skill_inventory_split(sandbox):
    mod = load_agent_sync_module(sandbox)
    canonical, extras, missing = mod._skill_inventory({"a", "b"}, ["a", "c"])
    assert canonical == ["a"]        # materialized AND in manifest
    assert extras == ["c"]           # materialized, out-of-manifest
    assert missing == ["b"]          # in manifest, not materialized


def test_skill_manifest_names_reads_mapping_keys(sandbox, tmp_path):
    mod = load_agent_sync_module(sandbox)
    manifest = tmp_path / "skills.manifest.yaml"
    manifest.write_text(
        "skills:\n"
        "  vault-doctor:\n    origin: vault\n"
        "  vault-map:\n    origin: vault\n",
        encoding="utf-8",
    )
    assert mod._skill_manifest_names(manifest) == {"vault-doctor", "vault-map"}
    # absent manifest -> None (caller skips the skill section, no false 'all stray')
    assert mod._skill_manifest_names(tmp_path / "nope.yaml") is None


def test_claude_memory_stats_counts_facts_per_project(sandbox, tmp_path):
    mod = load_agent_sync_module(sandbox)
    projects = tmp_path / "projects"
    (projects / "projA" / "memory").mkdir(parents=True)
    (projects / "projA" / "memory" / "fact1.md").write_text("x", encoding="utf-8")
    (projects / "projA" / "memory" / "fact2.md").write_text("x", encoding="utf-8")
    (projects / "projB" / "memory").mkdir(parents=True)   # memory dir, no facts
    (projects / "projC").mkdir()                          # no memory dir -> excluded

    stats = dict(mod._claude_memory_stats(projects))
    assert stats["projA"] == 2
    assert stats["projB"] == 0
    assert "projC" not in stats
    # absent projects dir -> empty, never crashes
    assert mod._claude_memory_stats(tmp_path / "absent") == []


def test_inventory_defers_transcript_distillation_without_naming_a_stale_version(sandbox, monkeypatch, capsys):
    """Regression: this line used to read 'deferred to v0.93, not imported'
    while the engine was already released well past v0.93 (see VERSION),
    which reads as a broken promise instead of an honest status. Match the
    CHANGELOG's own version-agnostic phrasing ("deferred to a later
    release") so a future release bump can never make this stale again."""
    mod = load_agent_sync_module(sandbox)
    monkeypatch.setenv("HOME", str(sandbox.home))
    monkeypatch.setenv("KNOWLEDGE_VAULT_PATH", str(sandbox.vault))
    (sandbox.home / ".codex" / "sessions").mkdir(parents=True, exist_ok=True)

    rc = mod._inventory_cli([])

    assert rc == 0
    out = capsys.readouterr().out
    assert "codex: session transcripts present -- distillation deferred to a later release, not imported" in out
    assert "v0.9" not in out
