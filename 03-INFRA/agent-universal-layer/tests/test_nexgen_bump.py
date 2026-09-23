"""Unit tests for the one-yes batch bump: surgical pin edits, backups,
revalidation, and HOLD items never rewritten. Fully offline."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.thirdparty_bump import apply_plan, bump_batch, collect_plan  # noqa: E402
from nexgen_core import i18n  # noqa: E402


@pytest.fixture(autouse=True)
def _english_notes():
    """Pin the source language: these tests assert note contents, and the
    machine locale would otherwise turn them Italian."""
    i18n.set_language("en")
    yield
    i18n.set_language(None)

PIN_A = "a" * 40
HEAD_A = "b" * 40
PIN_D = "d" * 40
HEAD_D = "e" * 40
PIN_F = "f" * 40
HEAD_F = "ab" * 20


def _vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    skills = vault / "03-INFRA" / "agent-universal-layer" / "skills"
    mcp = vault / "03-INFRA" / "agent-universal-layer" / "mcp"
    skills.mkdir(parents=True)
    mcp.mkdir(parents=True)
    (skills / "skills.manifest.yaml").write_text(
        "# commento che deve sopravvivere\n"
        "schema_version: 1\n"
        "skills:\n"
        "  demo-one:\n"
        "    origin: github\n"
        f"    repo: o/r\n    commit: {PIN_A}\n"
        "    targets: [claude]\n"
        "  demo-two:\n"
        "    origin: github\n"
        f"    repo: o/r\n    commit: {PIN_A}\n"
        "    targets: [claude]\n"
        "  demo-three:\n"
        "    origin: github\n"
        f"    repo: o/r2\n    commit: {PIN_D}\n"
        "    targets: [claude]\n",
        encoding="utf-8",
    )
    (mcp / "manifest.yaml").write_text(
        "schema_version: 1\nservers: {}\n", encoding="utf-8"
    )
    return vault


def _payload() -> dict:
    return {
        "checked_at": 9999999999.0,
        "auto": [{
            "what": "skill 'demo-one' (github o/r)", "pinned": PIN_A,
            "upstream": HEAD_A, "reasons": ["identical"], "plain": "piano",
            "target": {"kind": "git-commit", "skill": "demo-one", "repo": "o/r", "field": "commit", "scope": "."},
        }],
        "batch": [],
        "hold": [{
            "what": "skill 'demo-two' (github o/r)", "pinned": PIN_A,
            "upstream": HEAD_A, "reasons": ["script"], "plain": "fermo",
            "target": {"kind": "git-commit", "skill": "demo-two", "repo": "o/r", "field": "commit", "scope": "."},
        }],
    }


def test_collect_plan_splits_raisable_from_held():
    auto, batch, held = collect_plan(_payload())
    assert [i["what"] for i in auto] == ["skill 'demo-one' (github o/r)"]
    assert batch == []
    assert len(held) == 1

    no_target = {"what": "x", "pinned": "1", "upstream": "2", "target": None}
    auto, batch, held = collect_plan({"auto": [no_target], "batch": [], "hold": []})
    assert auto == [] and batch == [] and len(held) == 1


def _demo_one_item() -> dict:
    return {
        "what": "skill 'demo-one' (github o/r)", "pinned": PIN_A,
        "upstream": HEAD_A, "reasons": ["identical"], "plain": "piano",
        "target": {"kind": "git-commit", "skill": "demo-one", "repo": "o/r", "field": "commit", "scope": "."},
    }


def _demo_two_item() -> dict:
    return {
        "what": "skill 'demo-two' (github o/r)", "pinned": PIN_A,
        "upstream": HEAD_A, "reasons": ["script"], "plain": "fermo",
        "target": {"kind": "git-commit", "skill": "demo-two", "repo": "o/r", "field": "commit", "scope": "."},
    }


def test_shared_pin_with_held_twin_stays_put(tmp_path: Path):
    """Sol's case: the held twin shares the commit, so nothing moves."""
    vault = _vault(tmp_path)

    bumps, notes, _moved = apply_plan([_demo_one_item()], vault, sync=False, home=tmp_path / "home")
    assert bumps == 0
    assert any("demo-two" in n for n in notes)
    text = (vault / "03-INFRA" / "agent-universal-layer" / "skills" / "skills.manifest.yaml").read_text(encoding="utf-8")
    assert PIN_A in text and HEAD_A not in text


def test_shared_pin_moves_when_all_twins_approve(tmp_path: Path):
    vault = _vault(tmp_path)

    bumps, notes, _moved = apply_plan([_demo_one_item(), _demo_two_item()], vault,
                              sync=False, home=tmp_path / "home")
    assert bumps == 2
    assert not any(n.startswith("[ERROR]") for n in notes)
    text = (vault / "03-INFRA" / "agent-universal-layer" / "skills" / "skills.manifest.yaml").read_text(encoding="utf-8")
    assert "# commento che deve sopravvivere" in text
    assert PIN_A not in text
    assert text.count(HEAD_A) == 2
    backups = list((vault / "03-INFRA" / "agent-universal-layer" / "skills").glob("*.bak-*"))
    assert len(backups) == 1
    assert PIN_A in backups[0].read_text(encoding="utf-8")


def test_failed_validation_restores_both_manifests(tmp_path: Path, monkeypatch):
    import nexgen_core.thirdparty_bump as bump_mod

    vault = _vault(tmp_path)
    skills_file = vault / "03-INFRA" / "agent-universal-layer" / "skills" / "skills.manifest.yaml"
    before = skills_file.read_text(encoding="utf-8")
    monkeypatch.setattr(bump_mod, "_revalidate", lambda *a, **k: ["boom"])

    bumps, notes, _moved = apply_plan([_demo_one_item(), _demo_two_item()], vault,
                              sync=False, home=tmp_path / "home")
    assert bumps == 0
    assert any(n.startswith("[ERROR]") for n in notes)
    assert skills_file.read_text(encoding="utf-8") == before


def test_commit_takes_only_the_manifest_paths(tmp_path: Path):
    import subprocess

    from nexgen_core.thirdparty_bump import _commit_manifests

    vault = _vault(tmp_path)
    _git_repo(vault)
    extra = vault / "notes.txt"
    extra.write_text("mine\n", encoding="utf-8")
    subprocess.run(["git", "add", "--", "notes.txt"], cwd=vault, check=True, capture_output=True)
    manifest = vault / "03-INFRA" / "agent-universal-layer" / "skills" / "skills.manifest.yaml"
    manifest.write_text(manifest.read_text(encoding="utf-8") + "# pin bump\n", encoding="utf-8")

    assert _commit_manifests(vault, [{"what": "skill 'demo-one' (github o/r)"}]) is True
    show = subprocess.run(["git", "show", "--name-only", "--format=", "HEAD"],
                          cwd=vault, capture_output=True, text=True, check=True)
    assert "skills.manifest.yaml" in show.stdout
    assert "notes.txt" not in show.stdout
    staged = subprocess.run(["git", "diff", "--cached", "--name-only"],
                            cwd=vault, capture_output=True, text=True, check=True)
    assert "notes.txt" in staged.stdout


def test_shared_npm_token_with_held_twin_stays_put(tmp_path: Path):
    vault = _vault(tmp_path)
    skills_file = vault / "03-INFRA" / "agent-universal-layer" / "skills" / "skills.manifest.yaml"
    skills_file.write_text(
        skills_file.read_text(encoding="utf-8")
        + "  inst-a:\n    origin: installer\n    version: 1.0.0\n"
        '    install: ["npx", "pkg@1.0.0"]\n    targets: [claude]\n'
        + "  inst-b:\n    origin: installer\n    version: 1.0.0\n"
        '    install: ["npx", "pkg@1.0.0"]\n    targets: [claude]\n',
        encoding="utf-8",
    )
    item = {
        "what": "skill 'inst-a' (npm pkg)", "pinned": "1.0.0", "upstream": "1.0.1",
        "reasons": ["patch"], "plain": "piano",
        "target": {"kind": "npm-version", "manifest": "skills", "skill": "inst-a", "package": "pkg"},
    }

    bumps, notes, _moved = apply_plan([item], vault, sync=False, home=tmp_path / "home")
    assert bumps == 0
    assert any("inst-b" in n for n in notes)
    assert "pkg@1.0.1" not in skills_file.read_text(encoding="utf-8")


def test_apply_plan_skips_already_moved_pins(tmp_path: Path):
    vault = _vault(tmp_path)
    auto, _, _ = collect_plan(_payload())
    auto[0]["pinned"] = "c" * 40  # no longer in the manifest

    bumps, notes, _moved = apply_plan(auto, vault, sync=False, home=tmp_path / "home")
    assert bumps == 0
    assert any("already moved" in n for n in notes)
    text = (vault / "03-INFRA" / "agent-universal-layer" / "skills" / "skills.manifest.yaml").read_text(encoding="utf-8")
    assert PIN_A in text  # untouched


def _demo_three_item() -> dict:
    return {
        "what": "skill 'demo-three' (github o/r2)", "pinned": PIN_D,
        "upstream": HEAD_D, "reasons": ["prose"], "plain": "piano",
        "target": {"kind": "git-commit", "skill": "demo-three", "repo": "o/r2", "field": "commit", "scope": "."},
    }


def test_bump_batch_auto_needs_no_question(tmp_path: Path, capsys):
    vault = _vault(tmp_path)
    state = tmp_path / "state"
    (state / "nexgen").mkdir(parents=True)
    import json

    (state / "nexgen" / "third-party-guard.json").write_text(
        json.dumps(_payload()), encoding="utf-8")

    def _must_not_ask(_prompt=""):
        raise AssertionError("auto pins must not prompt")

    rc = bump_batch(home=tmp_path / "home", vault_data=vault, state_dir=state,
                    input_fn=_must_not_ask, sync=False)
    assert rc == 0
    out = capsys.readouterr().out
    assert "already moved on their own" in out
    text = (vault / "03-INFRA" / "agent-universal-layer" / "skills" / "skills.manifest.yaml").read_text(encoding="utf-8")
    assert PIN_A in text and HEAD_A not in text


def test_bump_batch_yes_moves_batch_only(tmp_path: Path, capsys):
    vault = _vault(tmp_path)
    state = tmp_path / "state"
    (state / "nexgen").mkdir(parents=True)
    import json

    payload = {"checked_at": 9999999999.0, "auto": [],
               "batch": [_demo_three_item()], "hold": [_payload()["hold"][0]]}
    (state / "nexgen" / "third-party-guard.json").write_text(
        json.dumps(payload), encoding="utf-8")

    rc = bump_batch(home=tmp_path / "home", vault_data=vault, state_dir=state,
                    input_fn=lambda _prompt="": "s", sync=False)
    assert rc == 0
    out = capsys.readouterr().out
    assert "piano" in out and "fermo" in out
    text = (vault / "03-INFRA" / "agent-universal-layer" / "skills" / "skills.manifest.yaml").read_text(encoding="utf-8")
    assert HEAD_D in text and PIN_D not in text
    assert PIN_A in text  # held twin untouched


def test_apply_plan_leaves_upstream_owned_specs_alone(tmp_path: Path):
    vault = _vault(tmp_path)
    skills_file = vault / "03-INFRA" / "agent-universal-layer" / "skills" / "skills.manifest.yaml"
    skills_file.write_text(
        skills_file.read_text(encoding="utf-8")
        + "  uppy:\n    origin: upstream\n    exposure: manual\n"
        "    targets: [claude]\n    deps:\n      kind: npx\n"
        '      spec: "uppy@1.0.0"\n',
        encoding="utf-8",
    )
    raisable = [{
        "what": "skill 'uppy' (npm uppy)", "pinned": "1.0.0", "upstream": "1.0.1",
        "reasons": ["patch"], "plain": "piano",
        "target": {"kind": "npm-version", "manifest": "skills", "skill": "uppy", "package": "uppy"},
    }]

    bumps, notes, _moved = apply_plan(raisable, vault, sync=False, home=tmp_path / "home")
    assert bumps == 0
    assert any("upstream-owned" in n for n in notes)
    assert "uppy@1.0.1" not in skills_file.read_text(encoding="utf-8")


def _git_repo(path: Path) -> None:
    import os
    import subprocess

    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@localhost",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@localhost",
    }
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True, env=env)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@localhost"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=path, check=True, capture_output=True, env=env)
    subprocess.run(["git", "commit", "-m", "base"], cwd=path, check=True, capture_output=True, env=env)


def test_auto_apply_moves_silently_commits_and_records(tmp_path: Path):
    import json
    import subprocess

    from nexgen_core.thirdparty_bump import auto_apply

    vault = _vault(tmp_path)
    _git_repo(vault)
    state = tmp_path / "state"

    result = auto_apply({"auto": [], "batch": [], "hold": []}, vault, tmp_path / "home", state, sync=False)
    assert result == {"ok": True, "applied": 0}

    payload = {"checked_at": 9999999999.0, "auto": [
        {"what": "skill 'demo-one' (github o/r)", "pinned": PIN_A, "upstream": HEAD_A,
         "reasons": ["identical"], "plain": "piano",
         "target": {"kind": "git-commit", "skill": "demo-one", "repo": "o/r", "field": "commit", "scope": "."}},
        {"what": "skill 'demo-two' (github o/r)", "pinned": PIN_A, "upstream": HEAD_A,
         "reasons": ["identical"], "plain": "piano",
         "target": {"kind": "git-commit", "skill": "demo-two", "repo": "o/r", "field": "commit", "scope": "."}},
    ], "batch": [], "hold": []}
    result = auto_apply(payload, vault, tmp_path / "home", state, sync=False)
    assert result["ok"] is True and result["applied"] == 2

    text = (vault / "03-INFRA" / "agent-universal-layer" / "skills" / "skills.manifest.yaml").read_text(encoding="utf-8")
    assert HEAD_A in text
    log = subprocess.run(["git", "log", "--oneline"], cwd=vault, capture_output=True, text=True, check=True)
    assert "guardian auto-bump" in log.stdout
    applied = json.loads((state / "nexgen" / "third-party-applied.json").read_text(encoding="utf-8"))
    assert applied["applied"][0]["new"] == HEAD_A


def test_auto_apply_without_git_repo_still_moves_pins(tmp_path: Path):
    from nexgen_core.thirdparty_bump import auto_apply

    vault = _vault(tmp_path)  # not a git repo: commit fails, pins must still move
    state = tmp_path / "state"
    payload = {"checked_at": 9999999999.0, "auto": [
        {"what": "skill 'demo-one' (github o/r)", "pinned": PIN_A, "upstream": HEAD_A,
         "reasons": ["identical"], "plain": "piano",
         "target": {"kind": "git-commit", "skill": "demo-one", "repo": "o/r", "field": "commit", "scope": "."}},
        {"what": "skill 'demo-two' (github o/r)", "pinned": PIN_A, "upstream": HEAD_A,
         "reasons": ["identical"], "plain": "piano",
         "target": {"kind": "git-commit", "skill": "demo-two", "repo": "o/r", "field": "commit", "scope": "."}},
    ], "batch": [], "hold": []}
    result = auto_apply(payload, vault, tmp_path / "home", state, sync=False)
    assert result["applied"] == 2
    text = (vault / "03-INFRA" / "agent-universal-layer" / "skills" / "skills.manifest.yaml").read_text(encoding="utf-8")
    assert HEAD_A in text


def test_hold_git_dep_rev_blocks_shared_sha(tmp_path: Path):
    """Sol's case: a held git-deps rev with the same SHA blocks the change."""
    from nexgen_core.thirdparty_bump import apply_plan

    vault = _vault(tmp_path)
    skills_file = vault / "03-INFRA" / "agent-universal-layer" / "skills" / "skills.manifest.yaml"
    skills_file.write_text(
        skills_file.read_text(encoding="utf-8")
        + "  withdep:\n    origin: vault\n    exposure: manual\n"
        "    targets: [claude]\n    deps:\n      kind: git\n"
        f"      repo: o/r\n      rev: {PIN_A}\n",
        encoding="utf-8",
    )
    raisable = [_demo_one_item(), _demo_two_item()]

    bumps, notes, _moved = apply_plan(raisable, vault, sync=False, home=tmp_path / "home")
    assert bumps == 0
    assert any("withdep" in n for n in notes)
    assert HEAD_A not in skills_file.read_text(encoding="utf-8")


def test_replace_touches_only_approved_blocks(tmp_path: Path):
    from nexgen_core.thirdparty_bump import _replace_in_entries

    text = (
        "# comment mentioning the pin\n"
        "skills:\n"
        "  demo-one:\n"
        f"    commit: {PIN_A}\n"
        "  demo-two:\n"
        f"    commit: {PIN_A}\n"
    )
    new_text, replaced = _replace_in_entries(text, [("demo-one", "commit")], PIN_A, HEAD_A)
    assert replaced == 1
    assert new_text.count(HEAD_A) == 1
    assert PIN_A in new_text  # comment and twin untouched
    assert _replace_in_entries(text, [("missing", "commit")], PIN_A, HEAD_A) == (text, 0)


def test_busy_lock_skips_silently(tmp_path: Path, monkeypatch):
    from nexgen_core.lock import HostLock
    from nexgen_core.thirdparty_bump import auto_apply

    monkeypatch.setenv("AGENT_SYNC_LOCK_TIMEOUT_SECONDS", "2")
    vault = _vault(tmp_path)
    state = tmp_path / "state"
    payload = {"checked_at": 9999999999.0, "auto": [_demo_one_item(), _demo_two_item()],
               "batch": [], "hold": []}
    with HostLock(lock_path=state / "third-party-bump.lock", timeout=5,
                  command_name="test"):
        result = auto_apply(payload, vault, tmp_path / "home", state, sync=False)
    assert result.get("busy") is True
    text = (vault / "03-INFRA" / "agent-universal-layer" / "skills" / "skills.manifest.yaml").read_text(encoding="utf-8")
    assert PIN_A in text and HEAD_A not in text


def test_second_write_failure_restores_first_manifest(tmp_path: Path, monkeypatch):
    import nexgen_core.thirdparty_bump as bump_mod

    vault = _vault(tmp_path)
    mcp_file = vault / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml"
    mcp_file.write_text(
        "schema_version: 1\nservers:\n  srv:\n    transport: stdio\n"
        '    command: npx\n    args: ["pkg@1.0.0"]\n    targets: [claude]\n',
        encoding="utf-8",
    )
    skills_file = vault / "03-INFRA" / "agent-universal-layer" / "skills" / "skills.manifest.yaml"
    before_skills, before_mcp = skills_file.read_text(encoding="utf-8"), mcp_file.read_text(encoding="utf-8")

    real_atomic = bump_mod._atomic_write

    def _fail_mcp(path: Path, content: str) -> None:
        if path == mcp_file:
            raise OSError("disk full")
        real_atomic(path, content)

    monkeypatch.setattr(bump_mod, "_atomic_write", _fail_mcp)
    raisable = [_demo_one_item(), _demo_two_item(), {
        "what": "MCP server 'srv' (npm pkg)", "pinned": "1.0.0", "upstream": "1.0.1",
        "reasons": ["patch"], "plain": "piano",
        "target": {"kind": "npm-version", "manifest": "mcp", "server": "srv", "package": "pkg"},
    }]

    bumps, notes, _moved = bump_mod.apply_plan(raisable, vault, sync=False, home=tmp_path / "home")
    assert bumps == 0
    assert any(n.startswith("[ERROR]") for n in notes)
    assert skills_file.read_text(encoding="utf-8") == before_skills
    assert mcp_file.read_text(encoding="utf-8") == before_mcp


def test_materialize_failure_rolls_pins_back(tmp_path: Path, monkeypatch):
    import nexgen_core.thirdparty_bump as bump_mod

    vault = _vault(tmp_path)
    skills_file = vault / "03-INFRA" / "agent-universal-layer" / "skills" / "skills.manifest.yaml"
    before = skills_file.read_text(encoding="utf-8")
    monkeypatch.setattr(bump_mod, "_rematerialize", lambda *a, **k: ["[ERROR] offline"])

    bumps, notes, _moved = bump_mod.apply_plan([_demo_one_item(), _demo_two_item()], vault,
                                       sync=True, home=tmp_path / "home")
    assert bumps == 0
    assert any("rolled back" in n or "riprovo" in n for n in notes)
    assert skills_file.read_text(encoding="utf-8") == before


def test_same_entry_commit_moves_while_dep_rev_holds(tmp_path: Path):
    """Sol's case: commit AUTO and held deps.rev share one SHA in one entry."""
    from nexgen_core.thirdparty_bump import apply_plan

    vault = _vault(tmp_path)
    skills_file = vault / "03-INFRA" / "agent-universal-layer" / "skills" / "skills.manifest.yaml"
    skills_file.write_text(
        skills_file.read_text(encoding="utf-8")
        + "  mixed:\n    origin: github\n    repo: o/r\n"
        f"    commit: {PIN_F}\n    targets: [claude]\n    deps:\n      kind: git\n"
        f"      repo: o/other\n      rev: {PIN_F}\n",
        encoding="utf-8",
    )
    raisable = [{
        "what": "skill 'mixed' (github o/r)", "pinned": PIN_F, "upstream": HEAD_F,
        "reasons": ["identical"], "plain": "piano",
        "target": {"kind": "git-commit", "skill": "mixed", "repo": "o/r",
                   "field": "commit", "scope": "."},
    }]

    bumps, notes, _moved = apply_plan(raisable, vault, sync=False, home=tmp_path / "home")
    assert bumps == 1
    text = skills_file.read_text(encoding="utf-8")
    assert f"commit: {HEAD_F}" in text
    assert f"rev: {PIN_F}" in text  # held field untouched


def test_null_verdict_items_are_skipped(tmp_path: Path):
    from nexgen_core.thirdparty_bump import bump_batch

    vault = _vault(tmp_path)
    state = tmp_path / "state"
    (state / "nexgen").mkdir(parents=True)
    import json

    (state / "nexgen" / "third-party-guard.json").write_text(
        json.dumps({"checked_at": 9999999999.0, "auto": [None], "batch": "oops",
                    "hold": [None]}), encoding="utf-8")

    rc = bump_batch(home=tmp_path / "home", vault_data=vault, state_dir=state,
                    input_fn=lambda _prompt="": "s", sync=False)
    assert rc == 0


def test_auto_apply_records_only_what_moved(tmp_path: Path):
    import json

    from nexgen_core.thirdparty_bump import auto_apply

    vault = _vault(tmp_path)
    state = tmp_path / "state"
    payload = {"checked_at": 9999999999.0, "auto": [_demo_one_item(), _demo_three_item()],
               "batch": [], "hold": [_demo_two_item()]}
    result = auto_apply(payload, vault, tmp_path / "home", state, sync=False)
    assert result["applied"] == 1
    applied = json.loads((state / "nexgen" / "third-party-applied.json").read_text(encoding="utf-8"))
    whats = [e["what"] for e in applied["applied"]]
    assert whats == ["skill 'demo-three' (github o/r2)"]


def test_string_target_never_crashes(tmp_path: Path):
    from nexgen_core.thirdparty_bump import apply_plan, collect_plan

    vault = _vault(tmp_path)
    payload = {"checked_at": 9999999999.0,
               "auto": [{"what": "x", "pinned": "1", "upstream": "2", "target": "oops"}],
               "batch": [], "hold": [None, "junk"]}
    auto, batch, held = collect_plan(payload)
    assert auto == [] and batch == [] and len(held) == 1
    bumps, notes, _moved = apply_plan(
        [{"what": "x", "pinned": "1", "upstream": "2", "target": "oops"}],
        vault, sync=False, home=tmp_path / "home")
    assert bumps == 0


def test_token_replace_ignores_longer_package_names(tmp_path: Path):
    from nexgen_core.thirdparty_bump import _replace_in_entries

    text = ("skills:\n  demo:\n"
            '    install: ["npx", "otherpkg@1.0.0", "pkg@1.0.0"]\n')
    new_text, replaced = _replace_in_entries(text, [("demo", None)], "pkg@1.0.0", "pkg@1.0.1")
    assert replaced == 1
    assert "otherpkg@1.0.0" in new_text
    assert '"pkg@1.0.1"' in new_text


def test_restore_failure_is_reported_loudly(tmp_path: Path, monkeypatch):
    import nexgen_core.thirdparty_bump as bump_mod

    vault = _vault(tmp_path)
    monkeypatch.setattr(bump_mod, "_revalidate", lambda *a, **k: ["boom"])
    monkeypatch.setattr(bump_mod, "_restore", lambda *a, **k: False)

    bumps, notes, _moved = bump_mod.apply_plan(
        [_demo_one_item(), _demo_two_item()], vault, sync=False, home=tmp_path / "home")
    assert bumps == 0
    assert any("could not restore" in n for n in notes)
