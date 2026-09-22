"""OpenCode V2 native-contract smoke: runs on every CI job, never skips.

Unlike the binary-acceptance test in ``test_nexgen_opencode_mcp_v2.py``
(which needs the real CLI), every assertion here is pure Python against a
fixture home -- so a missing binary can never turn this gate green by
absence. It pins the whole V2 surface in one place: native MCP nesting,
native plugin/permission keys, no engine-imposed websearch kill-switch,
the scope file the model really loads, and the native skill directory.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.guard import GuardRunner  # noqa: E402
from nexgen_core.paths import (  # noqa: E402
    opencode_agents_file,
    opencode_config_candidates,
    opencode_config_path,
    opencode_skills_dir,
)
from nexgen_core.renderer import McpRenderer  # noqa: E402
from nexgen_core.runtimes.opencode import OpenCodeRuntime  # noqa: E402
from nexgen_core.skills import SkillMaterializer  # noqa: E402


def _sandbox(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    home = tmp_path / "home"
    vault = tmp_path / "vault"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("NEXGEN_HOME", str(home))
    monkeypatch.delenv("APPDATA", raising=False)
    canon = vault / "03-INFRA" / "agent-universal-layer" / "instructions" / "AGENTS.md"
    canon.parent.mkdir(parents=True)
    canon.write_text("# sentinel-rules\n", encoding="utf-8")
    manifest = vault / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        "schema_version: 1\nservers:\n"
        "  demo:\n    tier: core\n    transport: stdio\n"
        "    command: echo\n    targets: [opencode]\n",
        encoding="utf-8",
    )
    return home, vault


def test_smoke_single_config_resolution(tmp_path: Path, monkeypatch) -> None:
    """Renderer, guardrail adapter and inventory resolve the SAME file:
    jsonc wins over json when both exist, the default is the jsonc path."""
    home, _vault = _sandbox(tmp_path, monkeypatch)
    assert opencode_config_path(home).name == "opencode.jsonc"
    assert opencode_config_candidates(home)[0].name == "opencode.jsonc"

    cfg_dir = home / ".config" / "opencode"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "opencode.json").write_text("{}", encoding="utf-8")
    assert opencode_config_path(home).name == "opencode.json"
    rt = OpenCodeRuntime()
    assert rt._config_path(home).name == "opencode.json"
    assert McpRenderer(home=home).opencode_config_path().name == "opencode.json"

    (cfg_dir / "opencode.jsonc").write_text("{}", encoding="utf-8")
    assert opencode_config_path(home).name == "opencode.jsonc"
    assert rt._config_path(home).name == "opencode.jsonc"


def test_smoke_render_writes_native_keys_only(tmp_path: Path, monkeypatch) -> None:
    """A fresh render carries `mcp.servers` and NO legacy `plugin`,
    `permission` or engine-imposed websearch kill-switch."""
    home, vault = _sandbox(tmp_path, monkeypatch)
    renderer = McpRenderer(vault_data=vault, home=home)
    assert renderer.render_opencode(write=True)[0]

    data = json.loads(opencode_config_path(home).read_text(encoding="utf-8"))
    assert set(data["mcp"]) == {"servers"}
    assert "demo" in data["mcp"]["servers"]
    assert "plugin" not in data
    assert "permission" not in data
    assert "websearch" not in data
    assert data.get("tools", {}).get("websearch") is not False


def test_smoke_scope_file_is_the_loaded_instructions(tmp_path: Path, monkeypatch) -> None:
    """The guard materializes the scope file V2 really loads, and the legacy
    array is never the proof: even a config whose array names the canon
    stays BROKEN while the scope file is missing."""
    from nexgen_core.checks import instructions_checks

    home, vault = _sandbox(tmp_path, monkeypatch)
    canon = vault / "03-INFRA" / "agent-universal-layer" / "instructions" / "AGENTS.md"
    runner = GuardRunner(vault_data=vault, home=home)
    actions = runner.align_instructions()
    assert any("opencode" in a for a in actions)

    scope = opencode_agents_file(home)
    assert scope.is_symlink() and scope.resolve() == canon.resolve()
    assert "sentinel-rules" in scope.read_text(encoding="utf-8")

    outcome = instructions_checks.check_opencode_instructions(vault, home)
    assert outcome is not None and outcome.severity.name == "OK"


def test_smoke_posture_and_guardrail_use_native_keys(tmp_path: Path, monkeypatch) -> None:
    """Posture lands in ordered `permissions`, guardrail in `plugins`; the
    legacy keys are migrated, never rewritten."""
    home, _vault = _sandbox(tmp_path, monkeypatch)
    cfg = home / ".config" / "opencode" / "opencode.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(
        json.dumps({
            "permission": {"edit": "allow", "bash": "allow"},
            "plugin": ["some-other-plugin"],
        }),
        encoding="utf-8",
    )
    rt = OpenCodeRuntime()
    assert rt.apply_posture(home, "bypass") is not None
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert "permission" not in data
    effects = {(r["action"], r["resource"]): r["effect"] for r in data["permissions"]}
    assert effects[("edit", "*")] == "allow"
    assert effects[("shell", "*")] == "allow"

    hook_source = tmp_path / "guardrail.mjs"
    hook_source.write_text("// policy\n", encoding="utf-8")
    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()
    (hooks_dir / "opencode-guardrail-plugin.mjs").write_text("// adapter\n", encoding="utf-8")
    assert rt.install_guardrail(home, hook_source, hooks_dir) is not None
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert "plugin" not in data
    assert "some-other-plugin" in data["plugins"]
    assert any("opencode-guardrail-plugin.mjs" in p for p in data["plugins"] if isinstance(p, str))
    # Second guardrail run is silent: idempotence the guard relies on.
    assert rt.install_guardrail(home, hook_source, hooks_dir) is None


def test_smoke_engine_fingerprint_websearch_is_dropped_but_user_choice_stays(
    tmp_path: Path, monkeypatch
) -> None:
    """The old renderer's `tools.websearch=false` + `websearch="parallel"`
    combo is its own fingerprint and goes away; an explicit user `false`
    (or any other value) is never second-guessed."""
    home, vault = _sandbox(tmp_path, monkeypatch)
    renderer = McpRenderer(vault_data=vault, home=home)
    cfg = renderer.opencode_config_path()
    cfg.parent.mkdir(parents=True)

    cfg.write_text(
        json.dumps({"websearch": "parallel", "tools": {"websearch": False}, "mcp": {}}),
        encoding="utf-8",
    )
    assert renderer.render_opencode(write=True)[0]
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert "websearch" not in data
    assert "websearch" not in data.get("tools", {})

    cfg.write_text(
        json.dumps({"tools": {"websearch": False}, "mcp": {}}),
        encoding="utf-8",
    )
    assert renderer.render_opencode(write=True)[0]
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["tools"]["websearch"] is False


def test_smoke_engine_fingerprint_websearch_is_dropped_in_jsonc(
    tmp_path: Path, monkeypatch
) -> None:
    """Same migration through the JSONC path: comments survive, the two
    engine-imposed keys do not."""
    home, vault = _sandbox(tmp_path, monkeypatch)
    renderer = McpRenderer(vault_data=vault, home=home)
    cfg = home / ".config" / "opencode" / "opencode.jsonc"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(
        '{\n  // user comment survives\n  "websearch": "parallel",\n'
        '  "tools": {"websearch": false},\n  "mcp": {}\n}\n',
        encoding="utf-8",
    )
    assert renderer.render_opencode(write=True)[0]
    raw = cfg.read_text(encoding="utf-8")
    assert "// user comment survives" in raw
    data = json.loads(raw.replace("// user comment survives", ""))
    assert "websearch" not in data
    assert "websearch" not in data.get("tools", {})


def test_smoke_native_skill_directory_is_a_view_target(tmp_path: Path, monkeypatch) -> None:
    """Eager skills land in the V2 native directory too, from the single
    manifest -- no second source, no copy of the catalog."""
    home, vault = _sandbox(tmp_path, monkeypatch)
    skills_dir = vault / "03-INFRA" / "agent-universal-layer" / "skills"
    skills_dir.mkdir(parents=True)
    (skills_dir / "demo-skill").mkdir()
    (skills_dir / "demo-skill" / "SKILL.md").write_text(
        "---\ndescription: demo\n---\n\nbody\n", encoding="utf-8"
    )
    manifest = vault / "03-INFRA" / "agent-universal-layer" / "skills" / "skills.manifest.yaml"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        "schema_version: 1\nskills:\n  demo-skill:\n    origin: vault\n"
        "    exposure: eager\n    targets: [opencode]\n",
        encoding="utf-8",
    )
    mat = SkillMaterializer(vault_data=vault, home=home)
    mat.materialize(apply=True)
    native_view = opencode_skills_dir(home) / "demo-skill"
    assert native_view.exists()


def test_smoke_inventory_reads_the_v2_session_store_not_the_dead_path(
    tmp_path: Path, monkeypatch
) -> None:
    """The inventory censused `~/.opencode/storage`, a pre-V2 path that no
    longer exists on migrated machines -- every V2 install reported "no
    transcripts" with a live session store next to it. V2 sessions live
    under XDG data; the legacy path stays as fallback only."""
    from nexgen_core.cli.engine import _native_memory_report

    home = tmp_path / "home"
    home.mkdir()
    store = home / ".local" / "share" / "opencode"
    store.mkdir(parents=True)
    (store / "session.db").write_text("x", encoding="utf-8")

    notes = dict(_native_memory_report(home))
    assert ".local/share/opencode" in notes["opencode"].replace("\\", "/")
    assert "no transcripts" not in notes["opencode"]

    # Legacy-only machine: still censused, from the old root.
    legacy_home = tmp_path / "legacy-home"
    legacy_store = legacy_home / ".opencode" / "storage"
    legacy_store.mkdir(parents=True)
    (legacy_store / "s.json").write_text("x", encoding="utf-8")
    legacy_notes = dict(_native_memory_report(legacy_home))
    assert ".opencode/storage" in legacy_notes["opencode"].replace("\\", "/")

    # Neither: honestly empty (either locale).
    empty_notes = dict(_native_memory_report(tmp_path / "empty-home"))
    assert "no transcripts" in empty_notes["opencode"] or "nessuna trascrizione" in empty_notes["opencode"]
