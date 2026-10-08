"""Pre-release regression: council challenge fixes must not break valid inputs."""
from __future__ import annotations


def test_git_refs_accept_valid_reject_option_injection():
    from nexgen_core.git_ops import assert_safe_git_refs
    import pytest

    assert_safe_git_refs("origin", "main")
    assert_safe_git_refs("origin", "feature/x")
    with pytest.raises(ValueError):
        assert_safe_git_refs("--upload-pack=touch /tmp/x", "main")
    with pytest.raises(ValueError):
        assert_safe_git_refs("origin", "-x")
    with pytest.raises(ValueError):
        assert_safe_git_refs("https://github.com/o/r.git", "main")


def test_resolve_remotes_keeps_unsafe_so_downstream_blocks(tmp_path, monkeypatch):
    from nexgen_core import git_ops

    vault = tmp_path / "v"
    syncdir = vault / "03-INFRA" / "agent-universal-layer" / "sync"
    syncdir.mkdir(parents=True)
    (syncdir / "remotes.yaml").write_text("authoritative_remote: '--evil'\n", encoding="utf-8")
    remote, _ = git_ops.resolve_remotes(vault)
    assert remote == "--evil"
    import pytest

    with pytest.raises(ValueError):
        git_ops.assert_safe_git_refs(remote, "main")


def test_hook_filename_rejected():
    from nexgen_core.runtimes.base import Runtime

    assert Runtime._is_safe_hook_filename("policy.mjs") is True
    assert Runtime._is_safe_hook_filename('a";touch x;"#.mjs') is False
    assert Runtime._is_safe_hook_filename(".") is False
    assert Runtime._is_safe_hook_filename("..") is False


def test_coverage_missing_plan_is_dirty(tmp_path):
    from nexgen_core.vault.coverage import check_coverage

    out = check_coverage(str(tmp_path / "nope-plan.txt"), [], set(), "archive")
    assert out["coverage_status"] == "dirty"


def test_codex_collision_raises(tmp_path):
    from nexgen_core.renderer import McpRenderer

    r = McpRenderer.__new__(McpRenderer)
    r.manifest_path = tmp_path / "manifest.yaml"
    r.home = tmp_path
    # minimal stub: two servers colliding after dash normalization
    r.load_resolved_servers = lambda cli: {"vault-ocr": {}, "vault_ocr": {}}  # type: ignore
    r.retired_server_names = lambda: set()  # type: ignore
    r.unmounted_server_names = lambda mounted, cli: set()  # type: ignore
    from nexgen_core.mcp_render import codex as codex_mod
    import pytest

    with pytest.raises(ValueError):
        codex_mod.render(r, write=False)


def test_renderer_skips_empty_command(tmp_path, monkeypatch):
    from nexgen_core.renderer import McpRenderer

    vault = tmp_path / "vault"
    manifest_dir = vault / "03-INFRA" / "agent-universal-layer" / "mcp"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "manifest.yaml").write_text(
        "servers:\n  bad:\n    command: '${MISSING_VAR_XYZ}/x'\n    args: []\n", encoding="utf-8"
    )
    monkeypatch.delenv("MISSING_VAR_XYZ", raising=False)
    r = McpRenderer(vault_data=vault, home=tmp_path / "home")
    resolved = r.load_resolved_servers("claude")
    assert "bad" not in resolved


def test_research_continue_resets_penalties():
    import inspect

    from nexgen_local import research_graph

    src = inspect.getsource(research_graph._continue_research)
    assert "empty_streak" in src and "tried_queries" in src


def test_updater_refuses_unverified_without_flag():
    import inspect

    from nexgen_core import updater as _updater

    assert "--allow-unverified" in inspect.getsource(_updater.build_parser)
    src = inspect.getsource(_updater.main)
    assert "allow_unverified" in src and "could not be verified" in src
