"""Detection: what the machine has, asked without opening a credential.

The point of these tests is the boundary, not the happy path: a credential is
seen only as a file that exists, the two local backends are answered by an
injected client so no test opens a socket, and the fake home is a temporary
directory that cannot be the real one.
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


def _npm_tree(tmp_path: Path, version: str | None) -> Path:
    """The folder `npm root -g` will name, with an OpenClaw package inside."""
    root = tmp_path / "npm-root"
    package = root / "openclaw"
    package.mkdir(parents=True)
    (package / "package.json").write_text(json.dumps({"name": "openclaw", "version": version}), encoding="utf-8")
    return root


def _runner(root: Path | None):
    """`npm root -g`, answered without running npm."""
    class Completed:
        def __init__(self, stdout: str) -> None:
            self.stdout = stdout

    def run(argv, **kwargs):
        assert argv[:3] == ["npm", "root", "-g"]
        return Completed(str(root) + "\n" if root else "")

    return run


def _detect(tmp_path: Path, **kwargs):
    kwargs.setdefault("home", tmp_path / "home")
    kwargs.setdefault("env", {})
    kwargs.setdefault("which", lambda name: None)
    kwargs.setdefault("run", _runner(None))
    kwargs.setdefault("http_json", lambda url, timeout: None)
    kwargs.setdefault("engine_root", _engine(tmp_path))
    kwargs.setdefault("vault_data", _vault(tmp_path, None))
    kwargs.setdefault("platform", "linux")
    return detect(**kwargs)


def test_the_version_comes_from_npm_s_own_tree(tmp_path):
    root = _npm_tree(tmp_path, PINNED)
    found = _detect(tmp_path, which=lambda name: f"/usr/bin/{name}", run=_runner(root))
    assert found.openclaw.installed is True
    assert found.openclaw.version == PINNED
    assert found.openclaw.config_path.endswith("openclaw.json")


def test_without_npm_on_path_openclaw_is_not_installed(tmp_path):
    found = _detect(tmp_path, which=lambda name: None)
    assert found.openclaw.installed is False
    assert "npm" in found.openclaw.detail


def test_a_missing_package_under_npm_root_is_not_installed(tmp_path):
    found = _detect(tmp_path, which=lambda name: name, run=_runner(tmp_path / "empty-root"))
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
    found = _detect(tmp_path, home=home, which=lambda name: "/usr/bin/claude")
    assert found.claude.binary is True
    assert found.claude.login is True
    assert found.claude.available is True


def test_claude_without_a_login_file_is_installed_but_unusable(tmp_path):
    found = _detect(tmp_path, which=lambda name: "/usr/bin/claude", platform="linux")
    assert found.claude.binary is True
    assert found.claude.login is False
    assert found.claude.available is False


def test_on_macos_the_login_is_declared_unverifiable(tmp_path):
    found = _detect(tmp_path, which=lambda name: "/usr/local/bin/claude", platform="darwin")
    assert found.claude.login is None
    assert found.claude.available is True
    assert "Keychain" in found.claude.detail


def test_codex_reads_auth_json_under_codex_home(tmp_path):
    home = tmp_path / "home"
    elsewhere = tmp_path / "codex-home"
    elsewhere.mkdir()
    (elsewhere / "auth.json").write_text("{}", encoding="utf-8")
    found = _detect(tmp_path, home=home, env={"CODEX_HOME": str(elsewhere)}, which=lambda name: "/usr/bin/codex")
    assert found.codex.login is True
    assert found.codex.available is True

    default_home = _detect(tmp_path, home=home, env={}, which=lambda name: "/usr/bin/codex")
    assert default_home.codex.login is False


def test_the_go_key_is_never_read_only_noticed(tmp_path):
    assert _detect(tmp_path, env={"OPENCODE_API_KEY": "sk-live-value"}).opencode_go is True
    assert _detect(tmp_path, env={"OPENCODE_API_KEY": ""}).opencode_go is False
    assert _detect(tmp_path, env={}).opencode_go is False


def test_ollama_is_asked_at_its_default_and_honours_host(tmp_path):
    asked: list[str] = []

    def probe(url, timeout):
        asked.append(url)
        return {"models": [{"name": "qwen3:8b:latest"}, {"name": "llama3.2:3b"}]}

    found = _detect(tmp_path, http_json=probe)
    assert DEFAULT_OLLAMA_URL + "/api/tags" in asked
    # Exactly as reported: stripping :latest is the profile's job, not detection's.
    assert found.local.ollama == ("qwen3:8b:latest", "llama3.2:3b")

    asked.clear()
    _detect(tmp_path, env={"OLLAMA_HOST": "127.0.0.1:9999"}, http_json=probe)
    assert "http://127.0.0.1:9999/api/tags" in asked


def test_llamacpp_is_asked_on_its_own_url(tmp_path):
    asked: list[str] = []

    def probe(url, timeout):
        asked.append(url)
        return {"data": [{"id": "local-model"}]}

    found = _detect(tmp_path, http_json=probe)
    assert DEFAULT_LLAMACPP_URL + "/v1/models" in asked
    assert found.local.llamacpp == ("local-model",)

    asked.clear()
    _detect(tmp_path, env={"NEXGEN_LLAMACPP_URL": "http://127.0.0.1:1234"}, http_json=probe)
    assert "http://127.0.0.1:1234/v1/models" in asked


def test_a_silent_backend_is_an_empty_list_not_an_error(tmp_path):
    found = _detect(tmp_path, http_json=lambda url, timeout: None)
    assert found.local.ollama == ()
    assert found.local.llamacpp == ()
    assert found.local.available is False
    assert found.local.detail


def test_n8n_is_active_only_when_declared_and_gated(tmp_path):
    engine = _engine(tmp_path)
    declared = _vault(tmp_path, "local")
    assert _detect(
        tmp_path, engine_root=engine, vault_data=declared, env={"N8N_MCP_TOKEN": "x"},
    ).n8n is True
    assert _detect(
        tmp_path, engine_root=engine, vault_data=declared, env={},
    ).n8n is False
    assert _detect(
        tmp_path, engine_root=engine, vault_data=_vault(tmp_path / "other", None), env={},
    ).n8n is False


def test_detection_serializes_to_json(tmp_path):
    found = _detect(tmp_path)
    payload = json.dumps(found.to_dict())
    assert payload.startswith("{")
    assert found.to_dict()["claude"]["available"] is False
    assert found.to_dict()["openclaw"]["installed"] is False
