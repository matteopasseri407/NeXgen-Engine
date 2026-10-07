"""The dependency watch also says when a pin is abandoned, and when the engine's n8n image is behind.

Two things it was blind to: a package whose publisher withdrew support (npm marks the version
`deprecated`, and "nothing newer exists" read as "all good"), and n8n, which is a program shipped as
a Docker image rather than an npm package, so no manifest pin ever named it.
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest
import yaml

INFRA = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(INFRA / "scripts"))

from nexgen_core import depwatch, extensions, i18n  # noqa: E402
from nexgen_core.thirdparty_guard import _target_of_pin, judge_finding  # noqa: E402
from nexgen_core.tools import info as info_mod  # noqa: E402

COMPOSE = "services:\n  n8n:\n    image: ${N8N_IMAGE:-n8nio/n8n:2.35.3}\n    container_name: n8n\n"


@pytest.fixture(autouse=True)
def _english():
    i18n.set_language("en")
    yield
    i18n.set_language(None)


@pytest.fixture
def machine(tmp_path, monkeypatch):
    vault, engine, state, home = tmp_path / "vault", tmp_path / "engine", tmp_path / "state", tmp_path / "home"
    layer = vault / "03-INFRA" / "agent-universal-layer"
    (layer / "mcp").mkdir(parents=True)
    (layer / "skills").mkdir(parents=True)
    (engine / "agent-universal-layer" / "skills").mkdir(parents=True)
    (engine / "deploy" / "n8n").mkdir(parents=True)
    (engine / "deploy" / "n8n" / "docker-compose.yml").write_text(COMPOSE, encoding="utf-8")
    (state / "nexgen").mkdir(parents=True)
    home.mkdir()
    monkeypatch.setenv("NEXGEN_HOME", str(home))
    monkeypatch.setenv("AGENT_STATE_DIR", str(state))
    monkeypatch.setenv("AGENT_VAULT_DATA", str(vault))
    monkeypatch.setenv("AGENT_ENGINE_ROOT", str(engine))
    servers = {
        "github": {"exposure": "lazy", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-github@2025.4.8"],
                   "targets": ["claude"]},
        "fine": {"exposure": "eager", "command": "npx", "args": ["-y", "fine-mcp@1.0.0"], "targets": ["claude"]},
        "n8n-mcp": {"transport": "http", "url": "http://127.0.0.1:5678/mcp-server/http", "targets": ["claude"]},
    }
    (layer / "mcp" / "manifest.yaml").write_text(yaml.safe_dump({"servers": servers}), encoding="utf-8")
    (layer / "skills" / "skills.manifest.yaml").write_text(yaml.safe_dump({"schema_version": 1, "skills": {}}), encoding="utf-8")
    return {"vault": vault, "engine": engine, "state": state, "home": home}


def _latest(package: str) -> str | None:
    return {"@modelcontextprotocol/server-github": "2025.4.8", "fine-mcp": "1.0.0", "n8n": "2.42.4"}.get(package)


def _abandoned(package: str, version: str) -> str | None:
    return "Package no longer supported." if package == "@modelcontextprotocol/server-github" else None


def _watch(m, **resolvers):
    return depwatch.run_depwatch(
        vault_data=m["vault"], state_dir=m["state"], git_ls_remote=lambda repo: None,
        npm_latest_version=_latest, **resolvers,
    )


def _collect(m):
    return extensions.collect(home=m["home"], vault_data=m["vault"], engine_root=m["engine"], state_dir=m["state"])


def _by_name(rows):
    return {r["name"]: r for r in rows}


class _Reply(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_the_registry_answer_for_an_exact_version_is_read(monkeypatch):
    asked = []

    def fake(url, timeout):
        asked.append(url)
        return _Reply(json.dumps({"version": "2025.4.8", "deprecated": "Package no longer supported."}).encode())

    monkeypatch.setattr(depwatch.urllib.request, "urlopen", fake)
    assert depwatch._npm_deprecation("@modelcontextprotocol/server-github", "2025.4.8") == "Package no longer supported."
    assert asked == ["https://registry.npmjs.org/@modelcontextprotocol%2Fserver-github/2025.4.8"]


def test_a_version_that_is_not_deprecated_says_nothing(monkeypatch):
    monkeypatch.setattr(depwatch.urllib.request, "urlopen", lambda url, timeout: _Reply(b'{"version": "1.0.0"}'))
    assert depwatch._npm_deprecation("fine-mcp", "1.0.0") is None


def test_an_unreachable_registry_says_nothing(monkeypatch):
    def down(url, timeout):
        raise OSError("offline")

    monkeypatch.setattr(depwatch.urllib.request, "urlopen", down)
    assert depwatch._npm_deprecation("fine-mcp", "1.0.0") is None


def test_an_abandoned_pin_is_reported_even_though_nothing_newer_exists(machine):
    result = _watch(machine, npm_deprecation=_abandoned)
    github = next(f for f in result.findings if "server-github" in f.what)
    assert github.stale is False and github.deprecated == "Package no longer supported."
    report = (machine["state"] / "third-party-upgrades.md").read_text(encoding="utf-8")
    assert "## Deprecated upstream" in report and "Package no longer supported." in report
    assert "fine-mcp" not in report.split("## Deprecated upstream")[1].split("##")[0]


def test_the_status_file_carries_the_deprecation_for_info(machine):
    _watch(machine, npm_deprecation=_abandoned)
    sidecar = json.loads((machine["state"] / "nexgen" / depwatch.STATUS_FILE_NAME).read_text(encoding="utf-8"))
    flagged = {pin["what"]: pin["deprecated"] for pin in sidecar["pins"] if pin.get("deprecated")}
    assert list(flagged.values()) == ["Package no longer supported."]
    # an abandoned pin is not a newer version: the update notices keep their meaning
    assert sidecar["stale_count"] == 1  # only the n8n image


def test_offline_the_deprecation_is_not_even_asked(machine):
    asked = []
    depwatch.run_depwatch(
        vault_data=machine["vault"], state_dir=machine["state"], git_ls_remote=lambda repo: None,
        npm_latest_version=lambda package: None,
        npm_deprecation=lambda package, version: asked.append(package),
    )
    assert asked == []


def test_info_names_the_abandoned_server(machine):
    _watch(machine, npm_deprecation=_abandoned)
    data = _collect(machine)
    assert _by_name(data["mcp"])["github"]["update"]["deprecated"] == "Package no longer supported."
    assert "deprecated" not in _by_name(data["mcp"])["fine"]["update"]
    assert [row["name"] for row in extensions.deprecations(data)] == ["github"]
    text = "\n".join(info_mod._extension_lines(data, show_all=False))
    assert "no longer supported" in text and "github" in text


def test_the_engine_default_n8n_image_is_watched_against_the_n8n_release(machine):
    result = _watch(machine, npm_deprecation=_abandoned)
    image = next(f for f in result.findings if f.kind == "docker-image")
    assert image.what == "MCP server 'n8n-mcp' (docker n8nio/n8n, the engine's default image)"
    assert (image.pinned, image.upstream, image.stale) == ("2.35.3", "2.42.4", True)
    row = _by_name(_collect(machine)["mcp"])["n8n-mcp"]["update"]
    assert (row["state"], row["pinned"], row["upstream"]) == ("stale", "2.35.3", "2.42.4")
    # it is the engine's shipped default, not necessarily what the server runs, and info says so
    text = "\n".join(info_mod._extension_lines(_collect(machine), show_all=False))
    assert "default image is 2.35.3" in text and "pinned 2.35.3" not in text


def test_an_image_already_at_the_release_is_current(machine):
    (machine["engine"] / "deploy" / "n8n" / "docker-compose.yml").write_text(COMPOSE.replace("2.35.3", "2.42.4"), encoding="utf-8")
    image = next(f for f in _watch(machine).findings if f.kind == "docker-image")
    assert image.stale is False


def test_no_compose_file_or_no_n8n_server_means_no_image_pin(machine):
    (machine["engine"] / "deploy" / "n8n" / "docker-compose.yml").unlink()
    assert not [f for f in _watch(machine).findings if f.kind == "docker-image"]
    (machine["engine"] / "deploy" / "n8n" / "docker-compose.yml").write_text(COMPOSE, encoding="utf-8")
    manifest = machine["vault"] / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml"
    servers = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    del servers["servers"]["n8n-mcp"]
    manifest.write_text(yaml.safe_dump(servers), encoding="utf-8")
    assert not [f for f in _watch(machine).findings if f.kind == "docker-image"]


def test_the_guardian_holds_an_image_and_the_bump_never_rewrites_it(machine):
    image = next(f for f in _watch(machine).findings if f.kind == "docker-image")
    verdict = judge_finding(image)
    assert verdict.verdict == "hold"
    assert "unknown pin kind" not in " ".join(verdict.reasons)
    assert _target_of_pin(image, None) is None
