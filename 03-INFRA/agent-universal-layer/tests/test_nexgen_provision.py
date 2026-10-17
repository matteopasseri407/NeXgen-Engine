"""Tests per il provisioning delle dipendenze MCP (nexgen_core.provision).

Il contratto: deps: nel manifest, pinnaggio obbligatorio (mai @latest, mai
branch), provisioning lazy al primo spawn del cameriere, workspace locale
deterministico, verifica offline-safe per il doctor.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_scripts = str(Path(__file__).resolve().parents[2] / "scripts")
if _scripts not in sys.path:
    sys.path.insert(0, _scripts)

from nexgen_core import provision  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.provision import (  # noqa: E402 - sys.path shim for cloned checkout
    ensure_deps,
    report_unsatisfied_deps,
    workspace_path,
)


def _git_repo_with_commit(tmp: Path, filename: str = "hello.txt", content: str = "v1") -> tuple[str, str]:
    repo = tmp / "src"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / filename).write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
    rev = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()
    return repo.as_uri(), rev


def test_npx_pin_mandatory(tmp_path):
    ctx, err = ensure_deps({"kind": "npx", "spec": "firecrawl-mcp"}, tmp_path / "ws", server="srv")
    assert err is not None and "Pin rule" in err


def test_npx_ok_needs_node(tmp_path):
    if not shutil.which("node"):
        pytest.skip("node is not on this machine; the check cannot be exercised")
    ctx, err = ensure_deps({"kind": "npx", "spec": "firecrawl-mcp@3.24.0"}, tmp_path / "ws", server="srv")
    assert err is None
    assert ctx == {}


def test_git_pin_mandatory(tmp_path):
    ctx, err = ensure_deps({"kind": "git", "repo": "https://example.com/r.git"}, tmp_path / "ws", server="srv")
    assert err is not None and "rev" in err


def test_git_rev_must_be_commit(tmp_path):
    ctx, err = ensure_deps(
        {"kind": "git", "repo": "https://example.com/r.git", "rev": "main"}, tmp_path / "ws", server="srv",
    )
    assert err is not None and "not a commit" in err


def test_unknown_kind_refused(tmp_path):
    ctx, err = ensure_deps({"kind": "pip", "spec": "x"}, tmp_path / "ws", server="srv")
    assert err is not None and "not one of the supported kinds" in err


def test_workspace_path_deterministic(tmp_path):
    deps = {"kind": "git", "repo": "https://example.com/r.git", "rev": "a" * 40}
    assert workspace_path(deps, tmp_path) == workspace_path(deps, tmp_path)


def test_git_provision_and_verify(tmp_path):
    repo_uri, rev = _git_repo_with_commit(tmp_path)
    deps = {"kind": "git", "repo": repo_uri, "rev": rev}
    state = tmp_path / "state"

    ctx, err = ensure_deps(deps, state, install=True, server="srv")
    assert err is None, err
    ws = Path(ctx["DEPS_WORKSPACE"])
    assert (ws / "hello.txt").read_text(encoding="utf-8") == "v1"
    marker = json.loads((ws / provision.MARKER).read_text(encoding="utf-8"))
    assert marker["rev"] == rev

    # verify (install=False) is offline-safe and agrees
    ctx2, err2 = ensure_deps(deps, state, install=False, server="srv")
    assert err2 is None
    assert ctx2["DEPS_WORKSPACE"] == str(ws)


def test_git_rev_change_provisions_fresh(tmp_path):
    repo_uri, rev1 = _git_repo_with_commit(tmp_path, content="v1")
    state = tmp_path / "state"
    _, err = ensure_deps({"kind": "git", "repo": repo_uri, "rev": rev1}, state, install=True, server="srv")
    from urllib.parse import urlsplit
    from urllib.request import url2pathname
    src = Path(url2pathname(urlsplit(repo_uri).path))
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=src, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=src, check=True)
    (src / "hello.txt").write_text("v2", encoding="utf-8")
    subprocess.run(["git", "commit", "-qam", "v2"], cwd=src, check=True)
    rev2 = subprocess.run(["git", "rev-parse", "HEAD"], cwd=src, check=True, capture_output=True, text=True).stdout.strip()

    ctx, err = ensure_deps({"kind": "git", "repo": repo_uri, "rev": rev2}, state, install=True, server="srv")
    assert err is None
    assert (Path(ctx["DEPS_WORKSPACE"]) / "hello.txt").read_text(encoding="utf-8") == "v2"
    assert ctx["DEPS_WORKSPACE"] != workspace_path({"kind": "git", "repo": repo_uri, "rev": rev1}, state)


def test_a_workspace_that_cannot_be_removed_is_a_clear_failure(tmp_path, monkeypatch):
    """Su Windows un file tenuto aperto lascia la cartella al suo posto.

    `rmtree(..., ignore_errors=True)` non dice se ha davvero rimosso: senza il
    controllo, `os.replace` sollevava un PermissionError grezzo, la run restava
    a metà e nessuno spiegava cosa tenere chiuso.
    """
    repo_uri, rev = _git_repo_with_commit(tmp_path)
    deps = {"kind": "git", "repo": repo_uri, "rev": rev}
    state = tmp_path / "state"
    ctx, err = ensure_deps(deps, state, install=True, server="srv")
    assert err is None, err
    final = Path(ctx["DEPS_WORKSPACE"])
    # Un run interrotto prima del marker: la cartella è lì, il marker no, quindi
    # il provisioning la ricrea e deve prima toglierla di mezzo.
    (final / provision.MARKER).unlink()

    monkeypatch.setattr(provision.shutil, "rmtree", lambda *a, **k: None)  # cosa fa Windows con un file aperto
    real_replace = provision.os.replace

    def refuse_the_workspace_swap(src_path, dst_path, *args, **kwargs):
        if Path(dst_path) == final:
            raise PermissionError(13, "Access is denied")
        return real_replace(src_path, dst_path, *args, **kwargs)

    monkeypatch.setattr(provision.os, "replace", refuse_the_workspace_swap)

    ctx2, err2 = ensure_deps(deps, state, install=True, server="srv")

    assert ctx2 == {}
    assert err2 is not None and "still holds" in err2, err2


def test_git_subdir_and_build(tmp_path):
    repo = tmp_path / "src"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    pkg = repo / "packages" / "mcp"
    pkg.mkdir(parents=True)
    (pkg / "tool.txt").write_text("base", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
    rev = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()

    deps = {
        "kind": "git", "repo": repo.as_uri(), "rev": rev,
        "subdir": "packages/mcp",
        "build": [["python3", "-c", "open('built.txt','w').write('ok')"]],
    }
    ctx, err = ensure_deps(deps, tmp_path / "state", install=True, server="srv")
    assert err is None, err
    ws = Path(ctx["DEPS_WORKSPACE"])
    assert (ws / "tool.txt").is_file()
    assert (ws / "built.txt").read_text(encoding="utf-8") == "ok"


def test_build_failure_is_provision_failure(tmp_path):
    repo_uri, rev = _git_repo_with_commit(tmp_path)
    deps = {
        "kind": "git", "repo": repo_uri, "rev": rev,
        "build": [["python3", "-c", "import sys; sys.exit(3)"]],
    }
    ctx, err = ensure_deps(deps, tmp_path / "state", install=True, server="srv")
    assert err is not None and "build for server 'srv' failed" in err
    assert "DEPS_WORKSPACE" not in ctx


def test_verify_missing_workspace_no_network(tmp_path):
    deps = {"kind": "git", "repo": "https://example.invalid/r.git", "rev": "a" * 40}
    ctx, err = ensure_deps(deps, tmp_path / "state", install=False, server="srv")
    assert err is not None and "not provisioned" in err


def test_report_unsatisfied_deps(tmp_path):
    vault = tmp_path / "vault"
    mcp = vault / "03-INFRA" / "agent-universal-layer" / "mcp"
    mcp.mkdir(parents=True)
    (mcp / "manifest.yaml").write_text(
        """
servers:
  a:
    command: npx
    args: ["-y", "firecrawl-mcp@3.24.0"]
    deps: {kind: npx, spec: "firecrawl-mcp@3.24.0"}
  b:
    command: node
    args: ["${DEPS_WORKSPACE}/dist/index.js"]
    deps: {kind: git, repo: "https://example.invalid/r.git", rev: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}
""",
        encoding="utf-8",
    )
    problems = report_unsatisfied_deps(vault, tmp_path / "state")
    assert any("b:" in p and "not provisioned" in p for p in problems)
    assert not any(p.startswith("a:") for p in problems)


def test_run_build_handles_quoted_arguments(tmp_path: Path):
    from nexgen_core.provision import _run_build
    out_file = tmp_path / "out.txt"
    # Using python executable to write with an argument containing spaces
    cmd = f'"{sys.executable}" -c "import sys, pathlib; pathlib.Path(sys.argv[1]).write_text(sys.argv[2])" "{out_file}" "hello world with spaces"'
    _run_build([cmd], tmp_path, "test-server")
    assert out_file.read_text(encoding="utf-8") == "hello world with spaces"



def test_a_build_step_that_is_a_cmd_shim_goes_through_cmd_exe(tmp_path: Path, monkeypatch):
    """`shutil.which("npm")` trova `npm.cmd`, che CreateProcess non esegue da solo.

    `_run` lo lanciava per nome: su Windows il passo di build moriva con
    FileNotFoundError, quindi i build NPM non partivano mai.
    """
    from nexgen_core.provision import _run_build

    shim = r"C:\nodejs\npm.cmd"
    monkeypatch.setattr(provision.shutil, "which", lambda name: shim if name == "npm" else None)
    monkeypatch.setattr(provision.os, "name", "nt")
    seen: list[list[str]] = []

    def fake_run(argv, **kwargs):
        seen.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(provision.subprocess, "run", fake_run)

    _run_build([["npm", "run", "build"]], tmp_path, "test-server")

    assert seen == [["cmd.exe", "/d", "/s", "/c", shim, "run", "build"]]


def test_a_helper_process_never_inherits_the_callers_stdin():
    """The waiter provisions on a request thread while its main thread blocks reading the MCP stream.
    A child holding that same handle wedged on Windows (git waits behind the pending read: the
    release CI hung 24 minutes in test_git_dep_provisioned_at_spawn) and, anywhere, could swallow
    protocol bytes. The caller here keeps its stdin pipe OPEN, as the MCP client does, and the
    helper's child must see end-of-file at once instead of waiting for it."""
    code = (
        "import sys; sys.path.insert(0, sys.argv[1])\n"
        "from pathlib import Path\n"
        "from nexgen_core.provision import _run\n"
        "out = _run([sys.executable, '-c', 'import sys; print(repr(sys.stdin.read()))'], Path.cwd(), 30)\n"
        "print(out.stdout.strip())\n"
    )
    caller = subprocess.Popen(
        [sys.executable, "-c", code, _scripts],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        caller.wait(timeout=60)  # stdin stays open: an inheriting child would block here
        assert caller.stdout.read().strip() == "''", caller.stderr.read()
    finally:
        if caller.poll() is None:
            caller.kill()
        for stream in (caller.stdin, caller.stdout, caller.stderr):
            stream.close()
