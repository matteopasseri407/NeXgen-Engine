"""Il deploy di vault-mcp: cosa tocca, cosa prova, cosa rimette a posto.

Nessun test qui avvia Docker né apre una connessione: il runner e la sonda sono
funzioni sostituite, e ogni comando che il deploy avrebbe eseguito resta in una
lista da leggere. Le regole verificate sono tre, e sono quelle che rendono il
comando usabile senza sorvegliarlo: tocca un servizio solo, tiene da parte
immagine e sorgente prima di toccarlo, e se la sonda non passa rimette indietro
entrambi.
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.util import find_spec
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.deploy import after_update, probe, vault_mcp  # noqa: E402 - sys.path shim for cloned checkout


@pytest.fixture(autouse=True)
def _english_messages():
    """Pins the language: these tests compare message text, not translations."""
    from nexgen_core import i18n

    i18n.set_language("en")
    try:
        yield
    finally:
        i18n.set_language(None)

COMMIT = "0123456789abcdef0123" "456789abcdef01234567"
SHORT = "0123" "45678"
IMAGE = "vault-mcp:0.5.0"
TOOLS = ["read_note", "create_note"]


class FakeRunner:
    """A docker and git that answer from a script, and remember what they were asked."""

    def __init__(self, *, head: str | None = COMMIT, image: str | None = IMAGE, fail: str | None = None):
        self.head = head
        self.image = image
        self.fail = fail
        self.calls: list[list[str]] = []
        self.envs: list[dict[str, str] | None] = []

    def __call__(self, args, *, env=None):
        self.calls.append(list(args))
        self.envs.append(dict(env) if env else None)
        text = " ".join(args)
        if args[:2] == ["git", "-C"]:
            if self.head is None:
                return subprocess.CompletedProcess(args, 128, "", "fatal: not a git repository")
            return subprocess.CompletedProcess(args, 0, f"{self.head}\n", "")
        if "inspect" in args:
            if self.image is None:
                return subprocess.CompletedProcess(args, 1, "", "Error: No such object")
            return subprocess.CompletedProcess(args, 0, f"{self.image}\n", "")
        if self.fail and self.fail in text:
            return subprocess.CompletedProcess(args, 1, "", f"{self.fail} refused")
        return subprocess.CompletedProcess(args, 0, "", "")

    def find(self, *needles: str) -> list[list[str]]:
        """The calls whose leading arguments are exactly these."""
        return [call for call in self.calls if call[:len(needles)] == list(needles)]


def _engine(tmp_path: Path, *, token: str = "s3cret") -> Path:
    """An installed engine: the stack folder, its source, and the shared .env."""
    deploy = tmp_path / "engine" / "03-INFRA"
    stack = deploy / "deploy" / "vault-mcp"
    (stack / "src" / "vault_mcp_server").mkdir(parents=True)
    (stack / "src" / "vault_mcp_server" / "__init__.py").write_text('__version__ = "0.5.0"\n', encoding="utf-8")
    (stack / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (stack / "Dockerfile").write_text("FROM python\n", encoding="utf-8")
    (stack / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    (stack / "README.md").write_text("# vault-mcp\n", encoding="utf-8")
    (stack / "LICENSE").write_text("MIT\n", encoding="utf-8")
    (deploy / "deploy" / ".env").write_text(f"VAULT_LIBRARY_TOKEN={token}\n", encoding="utf-8")
    return deploy


def _answers(*_args, **_kwargs) -> list[str]:
    return list(TOOLS)


def _deploy(tmp_path: Path, *, runner=None, probe_fn=None, engine: Path | None = None, source: Path | None = None):
    return vault_mcp.deploy(
        engine or _engine(tmp_path),
        source=source,
        state_dir=tmp_path / "state",
        environ={"VAULT_LIBRARY_URL": "http://127.0.0.1:8081/mcp"},
        out=lambda line: None,
        runner=runner or FakeRunner(),
        probe_fn=probe_fn or _answers,
        attempts=1,
        delay=0,
    )


def test_a_deploy_builds_one_service_with_its_commit_and_probes_it(tmp_path: Path):
    runner = FakeRunner()

    result = _deploy(tmp_path, runner=runner)

    build_index = next(i for i, call in enumerate(runner.calls) if "build" in call)
    assert runner.envs[build_index] == {vault_mcp.COMMIT_ENV: COMMIT}
    assert runner.calls[build_index][0:2] == ["docker", "compose"]
    assert runner.calls[build_index][-1] == "vault-mcp"
    assert result.commit == SHORT
    assert result.tools == tuple(TOOLS)
    assert result.previous_image == IMAGE


def test_the_rollback_tag_is_kept_before_the_image_is_replaced(tmp_path: Path):
    runner = FakeRunner()

    _deploy(tmp_path, runner=runner)

    tags = runner.find("docker", "tag")
    assert tags == [["docker", "tag", IMAGE, f"{IMAGE}{vault_mcp.ROLLBACK_SUFFIX}"]]
    assert runner.calls.index(tags[0]) < next(i for i, call in enumerate(runner.calls) if "build" in call)


def test_nothing_but_vault_mcp_is_named_on_the_compose_commands(tmp_path: Path):
    runner = FakeRunner()

    _deploy(tmp_path, runner=runner)

    compose_calls = [call for call in runner.calls if call[:2] == ["docker", "compose"]]
    assert len(compose_calls) == 2
    for call in compose_calls:
        assert call[-1] == "vault-mcp", call
        assert "n8n" not in " ".join(call) and "ocr" not in " ".join(call)


def test_the_probe_asks_the_published_port_with_the_stack_token(tmp_path: Path):
    seen: list[tuple[str, str | None]] = []

    def recorder(address, *, token, timeout):
        seen.append((address, token))
        return list(TOOLS)

    _deploy(tmp_path, probe_fn=recorder)

    assert seen == [("http://127.0.0.1:8081/mcp", "s3cret")]


def test_the_previous_source_is_saved_before_anything_is_rebuilt(tmp_path: Path):
    engine = _engine(tmp_path)

    result = _deploy(tmp_path, engine=engine)

    saved = result.backup / "src" / "vault_mcp_server" / "__init__.py"
    assert saved.is_file()
    assert "__version__" in saved.read_text(encoding="utf-8")


def test_a_failed_build_puts_the_previous_image_back(tmp_path: Path):
    runner = FakeRunner(fail="build")

    with pytest.raises(vault_mcp.DeployError, match="build failed"):
        _deploy(tmp_path, runner=runner)

    assert runner.find("docker", "tag", f"{IMAGE}{vault_mcp.ROLLBACK_SUFFIX}")
    ups = [call for call in runner.calls if "up" in call]
    assert ups and "--no-build" in ups[-1] and ups[-1][-1] == "vault-mcp"


def test_a_probe_that_never_answers_rolls_the_service_back(tmp_path: Path):
    runner = FakeRunner()

    def unreachable(address, *, token, timeout):
        raise probe.ProbeError("the server answered tools/list without a list of tools")

    with pytest.raises(vault_mcp.DeployError, match="back on the code that worked"):
        _deploy(tmp_path, runner=runner, probe_fn=unreachable)

    assert runner.find("docker", "tag", f"{IMAGE}{vault_mcp.ROLLBACK_SUFFIX}")
    assert [call for call in runner.calls if "up" in call][-1][-1] == "vault-mcp"


def test_the_saved_source_is_put_back_when_the_probe_fails(tmp_path: Path):
    engine = _engine(tmp_path)
    source = engine / "deploy" / "vault-mcp"
    inner = FakeRunner()

    def runner(args, *, env=None):
        result = inner(args, env=env)
        if "build" in args:
            (source / "src" / "vault_mcp_server" / "__init__.py").write_text("broken\n", encoding="utf-8")
        return result

    def unreachable(address, *, token, timeout):
        raise probe.ProbeError("nope")

    with pytest.raises(vault_mcp.DeployError):
        _deploy(tmp_path, runner=runner, probe_fn=unreachable, engine=engine)

    assert "__version__" in (source / "src" / "vault_mcp_server" / "__init__.py").read_text(encoding="utf-8")


def test_a_copy_elsewhere_is_refreshed_from_the_engine_tree(tmp_path: Path):
    engine = _engine(tmp_path)
    copy = tmp_path / "shared-agent-library-mcp"
    (copy / "src").mkdir(parents=True)
    (copy / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (copy / "src" / "stale.py").write_text("old\n", encoding="utf-8")

    result = _deploy(tmp_path, engine=engine, source=copy)

    assert (copy / "src" / "vault_mcp_server" / "__init__.py").is_file()
    assert (copy / "Dockerfile").is_file()
    # what the rollback would restore is the copy as the deploy found it
    assert (result.backup / "src" / "stale.py").read_text(encoding="utf-8") == "old\n"
    assert not (result.backup / "Dockerfile").exists()


def test_a_copy_without_its_compose_file_is_refused(tmp_path: Path):
    copy = tmp_path / "nowhere"
    copy.mkdir()

    with pytest.raises(vault_mcp.DeployError, match="docker-compose.yml"):
        _deploy(tmp_path, engine=_engine(tmp_path), source=copy)


def test_a_stack_without_its_token_is_refused_before_anything_runs(tmp_path: Path):
    runner = FakeRunner()

    with pytest.raises(vault_mcp.DeployError, match="VAULT_LIBRARY_TOKEN"):
        _deploy(tmp_path, runner=runner, engine=_engine(tmp_path, token=""))

    assert runner.calls == []


def test_an_engine_that_is_not_a_checkout_is_refused(tmp_path: Path):
    with pytest.raises(vault_mcp.DeployError, match="not a git checkout"):
        _deploy(tmp_path, runner=FakeRunner(head=None))


def test_a_first_deploy_says_there_is_no_previous_image(tmp_path: Path):
    runner = FakeRunner(image=None)

    result = _deploy(tmp_path, runner=runner)

    assert result.previous_image is None
    assert runner.find("docker", "tag") == []


def test_the_source_is_the_engine_tree_unless_the_env_says_otherwise(tmp_path: Path):
    engine = _engine(tmp_path)

    assert vault_mcp.source_dir(engine, environ={}) == engine / "deploy" / "vault-mcp"
    assert vault_mcp.source_dir(engine, environ={vault_mcp.SOURCE_ENV: str(tmp_path)}) == tmp_path.resolve()


# --- la sonda parla davvero il protocollo, su un server di loopback ---


class _McpHandler(BaseHTTPRequestHandler):
    """Un vault-mcp in miniatura: handshake, lista dei tool, e un token da controllare."""

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length) or b"{}")
        if self.headers.get("Authorization") != "Bearer s3cret":
            self.send_response(401)
            self.end_headers()
            return
        method = payload.get("method")
        if method == "initialize":
            body = {"jsonrpc": "2.0", "id": 1, "result": {
                "protocolVersion": probe.PROTOCOL_VERSION,
                "serverInfo": {"name": "vault", "version": "0.5.0"},
            }}
        elif method == "tools/list":
            body = {"jsonrpc": "2.0", "id": 2, "result": {"tools": [{"name": name} for name in TOOLS]}}
        else:
            body = {}
        data = json.dumps(body).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        """Nessuna riga di log: la sonda si prova, non si ascolta."""


@pytest.fixture
def live_mcp():
    """A vault-mcp that answers the two calls a deploy waits for, on loopback."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _McpHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/mcp"
    finally:
        server.shutdown()
        server.server_close()


def test_the_probe_returns_the_tools_a_deploy_waits_for(live_mcp: str):
    assert probe.probe(live_mcp, token="s3cret", timeout=5) == TOOLS


def test_a_wrong_token_is_a_probe_failure_not_a_pass(live_mcp: str):
    with pytest.raises(probe.ProbeError, match="401"):
        probe.probe(live_mcp, token="wrong", timeout=5)


# --- la rotta di salute pubblica il commit, e solo il commit ---


def _buildinfo():
    src = Path(__file__).resolve().parents[2] / "deploy" / "vault-mcp" / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    from vault_mcp_server import buildinfo

    return buildinfo


def test_the_health_route_reports_the_short_commit(monkeypatch: pytest.MonkeyPatch):
    buildinfo = _buildinfo()
    monkeypatch.setenv(buildinfo.ENGINE_COMMIT_ENV, COMMIT)

    assert buildinfo.engine_commit() == SHORT


def test_a_build_that_named_no_commit_publishes_nothing(monkeypatch: pytest.MonkeyPatch):
    buildinfo = _buildinfo()
    for value in ("", buildinfo.UNKNOWN):
        monkeypatch.setenv(buildinfo.ENGINE_COMMIT_ENV, value)
        assert buildinfo.engine_commit() is None


@pytest.mark.skipif(
    find_spec("uvicorn") is None or find_spec("mcp") is None,
    reason="the server module needs its own runtime dependencies",
)
def test_the_health_route_publishes_the_commit_and_nothing_else(monkeypatch: pytest.MonkeyPatch):
    buildinfo = _buildinfo()
    from vault_mcp_server import __version__ as vault_mcp_version
    from vault_mcp_server.server import _health_body

    monkeypatch.setenv(buildinfo.ENGINE_COMMIT_ENV, COMMIT)

    assert _health_body() == {
        "status": "ok", "version": vault_mcp_version, "engine_commit": SHORT,
    }


def test_the_dockerfile_bakes_the_commit_from_a_build_argument():
    root = Path(__file__).resolve().parents[2] / "deploy" / "vault-mcp"
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")

    assert "ARG NEXGEN_ENGINE_COMMIT=unknown" in dockerfile
    assert "ENV NEXGEN_ENGINE_COMMIT=${NEXGEN_ENGINE_COMMIT}" in dockerfile
    assert "org.opencontainers.image.revision" in dockerfile


def test_the_compose_passes_the_build_argument():
    import yaml

    root = Path(__file__).resolve().parents[2] / "deploy" / "vault-mcp"
    compose = yaml.safe_load((root / "docker-compose.yml").read_text(encoding="utf-8"))

    build = compose["services"]["vault-mcp"]["build"]
    assert build["context"] == "."
    assert build["args"]["NEXGEN_ENGINE_COMMIT"] == "${NEXGEN_ENGINE_COMMIT:-unknown}"


# --- il gancio nell'aggiornamento ---


def _listing(paths: list[str]):
    def run(repo: Path, args):
        assert args[:2] == ["diff", "--name-only"]
        return subprocess.CompletedProcess(args, 0, "\n".join(paths) + "\n", "")

    return run


def test_an_update_that_moved_another_folder_rebuilds_nothing(tmp_path: Path):
    moved = ["03-INFRA/scripts/nexgen_core/updater.py"]

    assert after_update.moved_stacks(tmp_path, "a", "b", run=_listing(moved)) == []


def test_an_update_that_moved_the_stack_names_it(tmp_path: Path):
    moved = ["CHANGELOG.md", "03-INFRA/deploy/vault-mcp/src/vault_mcp_server/server.py"]

    assert after_update.moved_stacks(tmp_path, "a", "b", run=_listing(moved)) == ["vault-mcp"]


def test_a_workstation_never_redeploys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from nexgen_core import roles

    monkeypatch.setattr(roles, "declared_role", lambda **_: roles.STANDALONE_ROLE)
    deployed: list[str] = []
    monkeypatch.setattr(vault_mcp, "deploy", lambda *a, **k: deployed.append("called"))

    stacks = after_update.deploy_after_update(
        engine_repo=tmp_path, vault_data=tmp_path, state_dir=tmp_path, previous_head="a", target_rev="b",
        out=lambda line: None, run_git=_listing(["03-INFRA/deploy/vault-mcp/Dockerfile"]),
    )

    assert stacks == []
    assert deployed == []


def test_a_server_redeploys_the_stack_the_update_moved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from nexgen_core import roles

    monkeypatch.setattr(roles, "declared_role", lambda **_: roles.SERVER_ROLE)
    seen: list[Path] = []
    monkeypatch.setattr(vault_mcp, "deploy", lambda engine_root, **_: seen.append(engine_root))

    stacks = after_update.deploy_after_update(
        engine_repo=tmp_path, vault_data=tmp_path, state_dir=tmp_path, previous_head="a", target_rev="b",
        out=lambda line: None, run_git=_listing(["03-INFRA/deploy/vault-mcp/Dockerfile"]),
    )

    assert stacks == ["vault-mcp"]
    assert seen == [tmp_path / "03-INFRA"]


def test_a_stack_this_engine_does_not_hold_is_named_and_left_alone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from nexgen_core import roles

    monkeypatch.setattr(roles, "declared_role", lambda **_: roles.SERVER_ROLE)
    lines: list[str] = []
    # The real preflight on a tree with no compose file and no .env: what a VPS
    # whose container another user started from its own copy looks like.
    stacks = after_update.deploy_after_update(
        engine_repo=tmp_path, vault_data=tmp_path, state_dir=tmp_path, previous_head="a", target_rev="b",
        out=lines.append, run_git=_listing(["03-INFRA/deploy/vault-mcp/Dockerfile"]),
    )

    assert stacks == []  # nothing raised, so the update is not undone for it
    assert any("not deployed from this engine" in line for line in lines)
    assert any("--source" in line for line in lines)


# --- il verbo, e chi può chiederlo ---


def test_the_deploy_verb_refuses_a_machine_that_does_not_run_the_stack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from nexgen_core import roles
    from nexgen_core.cli import deploy_cmds

    monkeypatch.setattr(roles, "declared_role", lambda **_: roles.STANDALONE_ROLE)
    monkeypatch.setattr(deploy_cmds, "resolve_engine_root", lambda: tmp_path)

    assert deploy_cmds.cmd_vault_mcp(type("Args", (), {"source": None})()) == 2


def test_the_deploy_verb_reports_the_commit_it_built(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    from nexgen_core import roles
    from nexgen_core.cli import deploy_cmds

    monkeypatch.setattr(roles, "declared_role", lambda **_: roles.SERVER_ROLE)
    monkeypatch.setattr(deploy_cmds, "resolve_engine_root", lambda: tmp_path)
    monkeypatch.setattr(deploy_cmds, "resolve_state_dir", lambda: tmp_path / "state")
    monkeypatch.setattr(
        vault_mcp, "deploy",
        lambda *_a, **_k: vault_mcp.DeployResult(
            commit=SHORT, source=tmp_path, backup=tmp_path / "backup",
            previous_image=IMAGE, tools=tuple(TOOLS),
        ),
    )

    assert deploy_cmds.cmd_vault_mcp(type("Args", (), {"source": None})()) == 0
    assert SHORT in capsys.readouterr().out


def test_the_deploy_verb_prints_the_failure_and_exits_non_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys,
):
    from nexgen_core import roles
    from nexgen_core.cli import deploy_cmds

    def refuse(*_a, **_k):
        raise vault_mcp.DeployError("the build failed: docker refused")

    monkeypatch.setattr(roles, "declared_role", lambda **_: roles.SERVER_ROLE)
    monkeypatch.setattr(deploy_cmds, "resolve_engine_root", lambda: tmp_path)
    monkeypatch.setattr(deploy_cmds, "resolve_state_dir", lambda: tmp_path / "state")
    monkeypatch.setattr(vault_mcp, "deploy", refuse)

    assert deploy_cmds.cmd_vault_mcp(type("Args", (), {"source": None})()) == 1
    assert "docker refused" in capsys.readouterr().out


# --- il gancio dentro l'aggiornamento ---


def test_the_update_hands_the_merge_range_to_the_deploy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from nexgen_core import updater

    seen: list[dict] = []
    monkeypatch.setattr(
        after_update, "deploy_after_update",
        lambda **kwargs: seen.append(kwargs) or ["vault-mcp"],
    )

    updater._redeploy_stacks(
        engine_repo=tmp_path, data_repo=tmp_path, state_dir=tmp_path, previous_head="a" * 40,
        target=type("Plan", (), {"rev": "b" * 40})(),
    )

    assert seen[0]["previous_head"] == "a" * 40
    assert seen[0]["target_rev"] == "b" * 40


def test_a_rebuild_that_fails_is_raised_so_the_update_can_undo_it_whole(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from nexgen_core import updater

    def refuse(**kwargs):
        raise vault_mcp.DeployError("the build failed: docker refused")

    monkeypatch.setattr(after_update, "deploy_after_update", refuse)

    with pytest.raises(vault_mcp.DeployError, match="docker refused"):
        updater._redeploy_stacks(
            engine_repo=tmp_path, data_repo=tmp_path, state_dir=tmp_path, previous_head="a" * 40,
            target=type("Plan", (), {"rev": "b" * 40})(),
        )
