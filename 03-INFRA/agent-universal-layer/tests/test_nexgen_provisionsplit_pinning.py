"""Pinning dello split di provision prima del refactor.

Fissa il comportamento attuale (non quello desiderato): ensure_deps su env
finto ritorna esito, _provision_git su repo finto stabile, firme pubbliche
invariate. Solo tmp_path, solo repo finti locali, nessuna rete, nessun
provisioning reale fuori tmp.
"""
from __future__ import annotations

import inspect
import json
import subprocess
import sys
from pathlib import Path

_scripts = str(Path(__file__).resolve().parents[2] / "scripts")
if _scripts not in sys.path:
    sys.path.insert(0, _scripts)

from nexgen_core import provision  # noqa: E402 - sys.path shim for cloned checkout


def _git_repo_with_commit(tmp: Path) -> tuple[str, str]:
    repo = tmp / "src"
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "hello.txt").write_text("v1", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
    rev = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    ).stdout.strip()
    return repo.as_uri(), rev


def test_ensure_deps_su_env_finto_ritorna_esito(tmp_path: Path) -> None:
    state = tmp_path / "state"
    ctx, err = provision.ensure_deps({}, state, server="srv-finto")
    assert ctx == {}
    assert err is not None and "empty" in err
    ctx, err = provision.ensure_deps(
        {"kind": "npx", "spec": "pkg-finto"}, state, server="srv-finto",
    )
    assert err is not None and "Pin rule" in err
    ctx, err = provision.ensure_deps(
        {"kind": "pip", "spec": "x"}, state, server="srv-finto",
    )
    assert err is not None and "not one of the supported kinds" in err
    ctx, err = provision.ensure_deps(
        {"kind": "git", "repo": "https://example.invalid/r.git", "rev": "a" * 40},
        state, install=False, server="srv-finto",
    )
    assert ctx == {}
    assert err is not None and "not provisioned" in err


def test_provision_git_su_repo_finto_stabile(tmp_path: Path) -> None:
    repo_uri, rev = _git_repo_with_commit(tmp_path)
    deps = {"kind": "git", "repo": repo_uri, "rev": rev}
    state = tmp_path / "state"
    ctx, err = provision.ensure_deps(deps, state, install=True, server="srv-finto")
    assert err is None, err
    ws = Path(ctx["DEPS_WORKSPACE"])
    assert (ws / "hello.txt").read_text(encoding="utf-8") == "v1"
    marker = json.loads((ws / provision.MARKER).read_text(encoding="utf-8"))
    assert marker["rev"] == rev
    assert marker["repo"] == repo_uri
    ctx2, err2 = provision.ensure_deps(deps, state, install=True, server="srv-finto")
    assert err2 is None
    assert ctx2["DEPS_WORKSPACE"] == str(ws)
    ctx3, err3 = provision.ensure_deps(deps, state, install=False, server="srv-finto")
    assert err3 is None
    assert ctx3["DEPS_WORKSPACE"] == str(ws)


def test_firme_pubbliche_invariate() -> None:
    assert list(inspect.signature(provision.validate_deps).parameters) == ["deps", "server"]
    assert list(inspect.signature(provision.workspace_path).parameters) == ["deps", "state_dir"]
    assert list(inspect.signature(provision.ensure_deps).parameters) == ["deps", "state_dir", "install", "server"]
    assert list(inspect.signature(provision.report_unsatisfied_deps).parameters) == ["vault_data", "state_dir"]


def test_wrapper_pubblici_delegano_a_helper_privati() -> None:
    src = Path(provision.__file__).read_text(encoding="utf-8")
    for name in (
        "def _validate_deps_impl",
        "def _workspace_path_impl",
        "def _ensure_deps_impl",
        "def _report_unsatisfied_deps_impl",
    ):
        assert name in src, f"helper privato mancante: {name}"
    for public, private in (
        ("def validate_deps", "_validate_deps_impl("),
        ("def workspace_path", "_workspace_path_impl("),
        ("def ensure_deps", "_ensure_deps_impl("),
        ("def report_unsatisfied_deps", "_report_unsatisfied_deps_impl("),
    ):
        start = src.index(public)
        nxt = src.find("\ndef ", start + len(public))
        body = src[start:] if nxt == -1 else src[start:nxt]
        assert private in body, f"{public} non delega a {private}"
