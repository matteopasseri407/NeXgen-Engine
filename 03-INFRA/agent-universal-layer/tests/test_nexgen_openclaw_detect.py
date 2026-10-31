"""Detection: what the machine has, asked without opening a credential.

The point of these tests is the boundary, not the happy path: a credential is
seen as a file that exists, or as the top-level key names of OpenCode's auth
file, never as a value; the two local backends are answered by an injected
client so no test opens a socket; and the fake home is a temporary directory
that cannot be the real one.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.openclaw.detect import (  # noqa: E402 - sys.path shim for cloned checkout
    DEFAULT_LLAMACPP_URL,
    DEFAULT_OLLAMA_URL,
    detect,
)

PINNED = "2026.10.6"


def _engine(tmp_path: Path) -> Path:
    """A minimal engine root: the catalog the module API reads."""
    engine = tmp_path / "engine"
    (engine / "agent-universal-layer" / "modules").mkdir(parents=True, exist_ok=True)
    (engine / "agent-universal-layer" / "modules" / "modules.yaml").write_text(
        "schema_version: 2\nmodules:\n"
        "  n8n:\n    label: n8n\n    kind: service\n    states: [absent, local, remote]\n"
        "    env_gates: [N8N_MCP_TOKEN]\n",
        encoding="utf-8",
    )
    return engine


def _vault(tmp_path: Path, state: str | None) -> Path:
    """A vault whose per-machine module state declares (or does not declare) n8n."""
    vault = tmp_path / "vault"
    target = vault / "03-INFRA" / "agent-universal-layer" / "modules.state.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    declared = f"modules:\n  n8n: {state}\n" if state else "modules: {}\n"
    target.write_text(f"schema_version: 2\n{declared}", encoding="utf-8")
    return vault


def _npm_tree(tmp_path: Path, version: str | None, node: str | None = None) -> Path:
    """The folder `npm root -g` will name, with an OpenClaw package inside."""
    root = tmp_path / "npm-root"
    package = root / "openclaw"
    package.mkdir(parents=True, exist_ok=True)
    manifest = {"name": "openclaw", "version": version}
    if node is not None:
        manifest["engines"] = {"node": node}
    (package / "package.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


def _runner(root: Path | None, expected: str = "npm"):
    """`npm root -g`, answered without running npm, and checking what was invoked."""
    class Completed:
        def __init__(self, stdout: str) -> None:
            self.stdout = stdout

    def run(argv, **kwargs):
        assert argv[0] == expected, f"npm must be launched by its resolved path, got {argv[0]!r}"
        assert argv[1:] == ["root", "-g"]
        return Completed(str(root) + "\n" if root else "")

    return run


def _detect(tmp_path: Path, **kwargs):
    kwargs.setdefault("home", tmp_path / "home")
    kwargs.setdefault("env", {})
    kwargs.setdefault("which", lambda name: None)
    kwargs.setdefault("run", _runner(None))
    kwargs.setdefault("http_json", lambda url, timeout, data=None: None)
    kwargs.setdefault("engine_root", _engine(tmp_path))
    kwargs.setdefault("vault_data", _vault(tmp_path, None))
    kwargs.setdefault("platform", "linux")
    return detect(**kwargs)


def test_the_version_comes_from_npm_s_own_tree(tmp_path):
    root = _npm_tree(tmp_path, PINNED)
    found = _detect(tmp_path, which=lambda name: f"/usr/bin/{name}", run=_runner(root, "/usr/bin/npm"))
    assert found.openclaw.installed is True
    assert found.openclaw.version == PINNED
    assert found.openclaw.config_path.endswith("openclaw.json")


def test_on_windows_npm_is_launched_by_its_resolved_cmd_path(tmp_path):
    """A bare `npm.cmd` is not executable by CreateProcess: the path must be used."""
    root = _npm_tree(tmp_path, PINNED)
    resolved = r"C:\Program Files\nodejs\npm.cmd"
    found = _detect(tmp_path, which=lambda name: resolved, run=_runner(root, resolved))
    assert found.openclaw.installed is True
    assert found.openclaw.version == PINNED


def test_the_node_requirement_comes_from_the_installed_package(tmp_path):
    root = _npm_tree(tmp_path, PINNED, node=">=24.22.1 <25 || >=26.12.1")

    found = _detect(tmp_path, which=lambda name: f"/usr/bin/{name}", run=_runner(root, "/usr/bin/npm"))

    assert found.openclaw.node_requirement == ">=24.22.1 <25 || >=26.12.1"


def test_user_prefix_install_found_via_symlinked_openclaw(tmp_path):
    system_root = tmp_path / "system" / "node_modules"
    system_root.mkdir(parents=True)

    user_pkg = tmp_path / "node24" / "lib" / "node_modules" / "openclaw"
    user_pkg.mkdir(parents=True)
    (user_pkg / "package.json").write_text(
        json.dumps({"name": "openclaw", "version": "2026.10.8", "engines": {"node": ">=24.0.0"}}),
        encoding="utf-8",
    )
    entry_file = user_pkg / "openclaw.mjs"
    entry_file.write_text("// entry", encoding="utf-8")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    symlink = bin_dir / "openclaw"
    symlink.symlink_to(entry_file)

    def which_fn(name: str) -> str | None:
        if name == "openclaw":
            return str(symlink)
        if name == "npm":
            return "/usr/bin/npm"
        return None

    found = _detect(tmp_path, which=which_fn, run=_runner(system_root, "/usr/bin/npm"))
    assert found.openclaw.installed is True
    assert found.openclaw.version == "2026.10.8"
    assert found.openclaw.node_requirement == ">=24.0.0"


def test_windows_style_shim_layout_finds_package(tmp_path):
    npm_dir = tmp_path / "roaming" / "npm"
    npm_dir.mkdir(parents=True)
    shim = npm_dir / "openclaw.cmd"
    shim.write_text("@ECHO off\nnode %~dp0\\node_modules\\openclaw\\openclaw.mjs %*", encoding="utf-8")

    pkg = npm_dir / "node_modules" / "openclaw"
    pkg.mkdir(parents=True)
    (pkg / "package.json").write_text(
        json.dumps({"name": "openclaw", "version": "2026.10.7"}),
        encoding="utf-8",
    )

    def which_fn(name: str) -> str | None:
        if name == "openclaw":
            return str(shim)
        if name == "npm":
            return str(npm_dir / "npm.cmd")
        return None

    empty_root = tmp_path / "empty-root"
    found = _detect(tmp_path, which=which_fn, run=_runner(empty_root, str(npm_dir / "npm.cmd")))
    assert found.openclaw.installed is True
    assert found.openclaw.version == "2026.10.7"


def test_fallback_to_npm_root_when_openclaw_not_resolvable(tmp_path):
    root = _npm_tree(tmp_path, "2026.10.6")

    def which_fn(name: str) -> str | None:
        if name == "npm":
            return "/usr/bin/npm"
        return None

    found = _detect(tmp_path, which=which_fn, run=_runner(root, "/usr/bin/npm"))
    assert found.openclaw.installed is True
    assert found.openclaw.version == "2026.10.6"


def test_openclaw_not_installed_when_neither_binary_nor_npm_root_has_it(tmp_path):
    empty_root = tmp_path / "empty-root"
    empty_root.mkdir()
    found = _detect(
        tmp_path,
        which=lambda name: "/usr/bin/npm" if name == "npm" else None,
        run=_runner(empty_root, "/usr/bin/npm"),
    )
    assert found.openclaw.installed is False
    assert str(empty_root) in found.openclaw.detail



def test_without_npm_on_path_openclaw_is_not_installed(tmp_path):
    found = _detect(tmp_path, which=lambda name: None)
    assert found.openclaw.installed is False
    assert "npm" in found.openclaw.detail


def test_an_openclaw_on_path_is_installed_even_when_npm_is_missing(tmp_path):
    """A shim installed by another package manager still means OpenClaw is here."""
    found = _detect(
        tmp_path,
        which=lambda name: "/usr/bin/openclaw" if name == "openclaw" else None,
    )

    assert found.openclaw.installed is True
    assert found.openclaw.version is None
    assert "openclaw" in found.openclaw.detail


def test_a_missing_package_under_npm_root_is_not_installed(tmp_path):
    found = _detect(tmp_path, which=lambda name: name, run=_runner(tmp_path / "empty-root", "npm"))
    assert found.openclaw.installed is False
    assert str(tmp_path / "empty-root") in found.openclaw.detail


def test_the_configuration_path_is_the_engine_s_own_precedence(tmp_path):
    home = tmp_path / "home"
    explicit = tmp_path / "elsewhere" / "custom.json"
    found = _detect(tmp_path, home=home, env={"OPENCLAW_CONFIG_PATH": str(explicit)}, which=lambda name: None)
    assert found.openclaw.config_path == str(explicit)


def test_a_login_file_counts_by_existence_alone(tmp_path):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    # Deliberately not valid JSON: the detection must not parse it.
    (home / ".claude" / ".credentials.json").write_text("not json at all", encoding="utf-8")
    found = _detect(tmp_path, home=home, which=lambda name: "/usr/bin/claude" if name == "claude" else None)
    assert found.claude.binary is True
    assert found.claude.login is True
    assert found.claude.available is True


def test_claude_without_a_login_file_is_installed_but_unusable(tmp_path):
    found = _detect(tmp_path, which=lambda name: "/usr/bin/claude" if name == "claude" else None, platform="linux")
    assert found.claude.binary is True
    assert found.claude.login is False
    assert found.claude.available is False


def test_on_macos_the_login_is_declared_unverifiable(tmp_path):
    found = _detect(tmp_path, which=lambda name: "/usr/local/bin/claude" if name == "claude" else None, platform="darwin")
    assert found.claude.login is None
    assert found.claude.available is True
    assert "Keychain" in found.claude.detail


def test_codex_reads_auth_json_under_codex_home(tmp_path):
    home = tmp_path / "home"
    elsewhere = tmp_path / "codex-home"
    elsewhere.mkdir()
    (elsewhere / "auth.json").write_text("{}", encoding="utf-8")
    found = _detect(tmp_path, home=home, env={"CODEX_HOME": str(elsewhere)}, which=lambda name: "/usr/bin/codex" if name == "codex" else None)
    assert found.codex.login is True
    assert found.codex.available is True

    default_home = _detect(tmp_path, home=home, env={}, which=lambda name: "/usr/bin/codex" if name == "codex" else None)
    assert default_home.codex.login is False


def test_the_go_key_in_the_environment_is_noticed_but_never_returned(tmp_path):
    assert _detect(tmp_path, env={"OPENCODE_API_KEY": "sk-live-value"}).opencode_go is True
    assert _detect(tmp_path, env={"OPENCODE_API_KEY": ""}).opencode_go is False
    assert _detect(tmp_path, env={}).opencode_go is False


def test_a_go_profile_in_openclaw_counts_as_the_key(tmp_path):
    home = tmp_path / "home"
    config = home / ".openclaw" / "openclaw.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({
        "auth": {"profiles": {
            "go": {"provider": "opencode-go", "mode": "api-key", "keyRef": "${OPENCODE_GO_KEY}"},
        }},
    }), encoding="utf-8")
    found = _detect(tmp_path, home=home, env={})
    assert found.opencode_go is True


def test_an_opencode_login_counts_as_the_key(tmp_path):
    home = tmp_path / "home"
    auth = home / ".local" / "share" / "opencode" / "auth.json"
    auth.parent.mkdir(parents=True)
    auth.write_text(json.dumps({"opencode-go": {"type": "api", "key": "sk-secret-never-returned"}}), encoding="utf-8")
    found = _detect(tmp_path, home=home, env={})
    assert found.opencode_go is True
    assert "sk-secret-never-returned" not in json.dumps(found.to_dict())


def test_the_opencode_login_path_follows_xdg_data_home(tmp_path):
    home = tmp_path / "home"
    data_home = tmp_path / "data"
    auth = data_home / "opencode" / "auth.json"
    auth.parent.mkdir(parents=True)
    auth.write_text(json.dumps({"opencode-go": {}}), encoding="utf-8")
    found = _detect(tmp_path, home=home, env={"XDG_DATA_HOME": str(data_home)})
    assert found.opencode_go is True


def _answers(url: str, timeout: float, data=None):
    """One fake backend for both Ollama and llama.cpp."""
    if url.endswith("/api/tags"):
        return {"models": [{"name": "qwen3:8b:latest"}, {"name": "llama3.2:3b"}]}
    if url.endswith("/api/show"):
        assert data == {"model": "qwen3:8b:latest", "name": "qwen3:8b:latest"} or data == {
            "model": "llama3.2:3b", "name": "llama3.2:3b",
        }
        if data["model"].startswith("qwen"):
            return {
                "parameters": "num_ctx 16384\ntemperature 0.6",
                "capabilities": ["completion", "thinking"],
                "model_info": {"qwen3.context_length": 40960},
            }
        return {
            "parameters": "",
            "capabilities": ["completion", "vision"],
            "model_info": {"llama.context_length": 8192},
        }
    if url.endswith("/props"):
        return {"default_generation_settings": {"n_ctx": 4096}}
    return None


def test_ollama_is_asked_at_its_default_and_honours_host(tmp_path):
    asked: list[str] = []

    def probe(url, timeout, data=None):
        asked.append(url)
        return _answers(url, timeout, data)

    found = _detect(tmp_path, http_json=probe)
    assert DEFAULT_OLLAMA_URL + "/api/tags" in asked
    # Exactly as reported: stripping :latest is the profile's job, not detection's.
    assert found.local.ollama_names == ("qwen3:8b:latest", "llama3.2:3b")

    asked.clear()
    _detect(tmp_path, env={"OLLAMA_HOST": "127.0.0.1:9999"}, http_json=probe)
    assert "http://127.0.0.1:9999/api/tags" in asked


def test_each_ollama_model_carries_the_context_and_its_capabilities(tmp_path):
    found = _detect(tmp_path, http_json=_answers)
    qwen, llama = found.local.ollama
    assert qwen.name == "qwen3:8b:latest"
    assert qwen.context_window == 16384          # num_ctx wins over context_length
    assert qwen.reasoning is True
    assert qwen.vision is False
    assert llama.context_window == 8192          # context_length when num_ctx is missing
    assert llama.vision is True


def test_llamacpp_is_asked_on_its_own_url_and_reports_the_context(tmp_path):
    asked: list[str] = []

    def probe(url, timeout, data=None):
        asked.append(url)
        if url.endswith("/v1/models"):
            return {"data": [{"id": "local-model"}]}
        if url.endswith("/props"):
            return {"default_generation_settings": {"n_ctx": 4096}}
        return None

    found = _detect(tmp_path, http_json=probe)
    assert DEFAULT_LLAMACPP_URL + "/v1/models" in asked
    assert DEFAULT_LLAMACPP_URL + "/props" in asked
    assert found.local.llamacpp_names == ("local-model",)
    assert found.local.llamacpp[0].context_window == 4096

    asked.clear()
    _detect(tmp_path, env={"NEXGEN_LLAMACPP_URL": "http://127.0.0.1:1234"}, http_json=probe)
    assert "http://127.0.0.1:1234/v1/models" in asked


def test_a_silent_backend_is_an_empty_list_not_an_error(tmp_path):
    found = _detect(tmp_path, http_json=lambda url, timeout, data=None: None)
    assert found.local.ollama == ()
    assert found.local.llamacpp == ()
    assert found.local.available is False
    assert found.local.detail


def test_n8n_is_active_when_declared_locally_or_remotely(tmp_path):
    engine = _engine(tmp_path)
    local = _vault(tmp_path, "local")
    assert _detect(tmp_path, engine_root=engine, vault_data=local, env={"N8N_MCP_TOKEN": "x"}).n8n is True
    assert _detect(tmp_path, engine_root=engine, vault_data=local, env={}).n8n is False

    remote = _vault(tmp_path / "remote", "remote")
    assert _detect(tmp_path, engine_root=engine, vault_data=remote, env={"N8N_MCP_TOKEN": "x"}).n8n is True

    undeclared = _vault(tmp_path / "other", None)
    assert _detect(tmp_path, engine_root=engine, vault_data=undeclared, env={}).n8n is False


def test_detection_serializes_to_json(tmp_path):
    found = _detect(tmp_path)
    payload = json.dumps(found.to_dict())
    assert payload.startswith("{")
    assert found.to_dict()["claude"]["available"] is False
    assert found.to_dict()["openclaw"]["installed"] is False


def test_on_windows_the_snapshot_carries_the_apps_own_gateway(tmp_path):
    """The app's gateway is a fact about the machine, so it travels in the snapshot."""
    env = {"LOCALAPPDATA": str(tmp_path / "local"), "APPDATA": str(tmp_path / "roaming")}

    found = _detect(tmp_path, env=env, platform="win32", wsl_distributions=lambda: ["OpenClawGateway"])

    assert found.companion.present is True
    assert found.companion.situation(found.openclaw.installed) == "app"
    assert found.to_dict()["companion"]["owns_local_gateway"] is True


def test_off_windows_there_is_no_app_gateway_to_find(tmp_path):
    found = _detect(tmp_path, platform="linux", wsl_distributions=lambda: ["OpenClawGateway"])

    assert found.companion.present is False
    assert found.to_dict()["companion"] == {
        "present": False, "owns_local_gateway": False, "points_remote": False, "points_here": False,
        "port": None, "signals": [],
    }

