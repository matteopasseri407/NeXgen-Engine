"""A module says what third-party software it carries, so the update machinery does not have to know.

`upstream:` in the module catalog names, per component, the file that holds its pin and where the
newest release is read. These tests cover the declaration (what is accepted, what is refused), the
reading of a pin from a file, and that the catalog the engine ships still describes files that exist.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

INFRA = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(INFRA / "scripts"))

from nexgen_core import depwatch  # noqa: E402
from nexgen_core.config import ConfigError  # noqa: E402
from nexgen_core.modules import ModuleUpstream, load_catalog, load_external_module  # noqa: E402

DOCKER = {"name": "n8n", "kind": "docker", "image": "n8nio/n8n", "pinned_in": "deploy/n8n/docker-compose.yml", "latest": "npm:n8n"}
NPM = {
    "name": "playwright", "kind": "npm", "package": "@playwright/mcp", "pinned_in": "mcp/wrapper.mjs",
    "pattern": "const VERSION = '([^']+)';", "latest": "npm:@playwright/mcp",
}


def _engine(tmp_path: Path, upstream) -> Path:
    modules = tmp_path / "engine" / "agent-universal-layer" / "modules"
    modules.mkdir(parents=True, exist_ok=True)
    body = {"schema_version": 2, "modules": {"thing": {
        "label": "A thing", "kind": "service", "states": ["absent", "local"], "upstream": upstream,
    }}}
    (modules / "modules.yaml").write_text(yaml.safe_dump(body), encoding="utf-8")
    return tmp_path / "engine"


def test_both_kinds_of_component_are_read(tmp_path):
    module = load_catalog(_engine(tmp_path, [DOCKER, NPM]))["thing"]
    docker, npm = module.upstream
    assert (docker.kind, docker.image, docker.latest, docker.compare) == ("docker", "n8nio/n8n", "npm:n8n", "patch")
    assert (npm.kind, npm.package, npm.pattern) == ("npm", "@playwright/mcp", "const VERSION = '([^']+)';")


def test_a_module_without_components_declares_none(tmp_path):
    assert load_catalog(_engine(tmp_path, None))["thing"].upstream == ()


def test_a_rolling_release_can_ask_to_be_compared_by_minor_only(tmp_path):
    (component,) = load_catalog(_engine(tmp_path, [{**DOCKER, "compare": "minor", "latest": "git-tags:firecrawl/firecrawl"}]))["thing"].upstream
    assert (component.compare, component.latest) == ("minor", "git-tags:firecrawl/firecrawl")


@pytest.mark.parametrize("broken, fragment", [
    ({**DOCKER, "kind": "pip"}, "unknown kind"),
    ({**DOCKER, "surprise": 1}, "unknown fields"),
    ({k: v for k, v in DOCKER.items() if k != "image"}, "needs 'image'"),
    ({k: v for k, v in DOCKER.items() if k != "latest"}, "missing 'latest'"),
    ({**DOCKER, "latest": "dockerhub:n8n"}, "'latest' must be"),
    ({**DOCKER, "latest": "npm:"}, "'latest' must be"),
    ({**DOCKER, "compare": "major"}, "'compare' must be"),
    ({**DOCKER, "pinned_in": "/etc/hosts"}, "must stay inside"),
    ({**DOCKER, "pinned_in": "../outside.yml"}, "must stay inside"),
    ({**DOCKER, "pinned_in": "~/secrets.env"}, "must stay inside"),
    # the same rule on every platform: a Windows spelling is refused on Linux and a POSIX one on Windows
    ({**DOCKER, "pinned_in": "C:\\Windows\\system32\\x.yml"}, "must stay inside"),
    ({**DOCKER, "pinned_in": "C:relative.yml"}, "must stay inside"),
    ({**DOCKER, "pinned_in": "\\\\server\\share\\x.yml"}, "must stay inside"),
    ({**DOCKER, "pinned_in": "..\\outside.yml"}, "must stay inside"),
    ({**DOCKER, "pinned_in": "deploy/../../outside.yml"}, "must stay inside"),
    ({**DOCKER, "pinned_in": "\\etc\\hosts"}, "must stay inside"),
    ({k: v for k, v in NPM.items() if k != "pattern"}, "needs 'package' and 'pattern'"),
    ({**NPM, "pattern": "const VERSION = (["}, "not a valid regex"),
    ({**NPM, "pattern": "const VERSION = '.*';"}, "needs a group"),
])
def test_a_declaration_that_cannot_be_trusted_is_refused(tmp_path, broken, fragment):
    with pytest.raises(ConfigError, match=fragment):
        load_catalog(_engine(tmp_path, [broken]))


def test_the_list_must_be_a_list_of_maps(tmp_path):
    with pytest.raises(ConfigError, match="must be a list"):
        load_catalog(_engine(tmp_path, {"name": "n8n"}))
    with pytest.raises(ConfigError, match="must be a map"):
        load_catalog(_engine(tmp_path, ["n8n"]))


def test_a_repository_that_is_a_module_can_declare_its_components(tmp_path):
    repo = tmp_path / "voice"
    repo.mkdir()
    (repo / "nexgen-module.yaml").write_text(yaml.safe_dump({"schema_version": 2, "modules": {"voice": {
        "label": "Voice", "kind": "feature", "states": ["absent", "local"], "upstream": [NPM],
    }}}), encoding="utf-8")
    assert load_external_module(repo).upstream[0].package == "@playwright/mcp"


def test_a_docker_pin_is_read_from_the_tag_of_its_image(tmp_path):
    compose = tmp_path / "deploy" / "n8n"
    compose.mkdir(parents=True)
    component = ModuleUpstream(**{k: v for k, v in DOCKER.items()})
    for line, expected in [
        ("    image: ${N8N_IMAGE:-n8nio/n8n:2.35.3}", "2.35.3"),
        ("    image: n8nio/n8n:2.42.4", "2.42.4"),
        ("    image: ${N8N_IMAGE:-n8nio/n8n@sha256:abc123}", None),
        ("    image: someone/else:9.9.9", None),
        ("    # image: n8nio/n8n:1.0.0", None),
    ]:
        (compose / "docker-compose.yml").write_text(f"services:\n  n8n:\n{line}\n", encoding="utf-8")
        assert depwatch._module_pin_version(tmp_path, component) == expected, line


def test_an_npm_pin_is_read_with_the_declared_pattern(tmp_path):
    (tmp_path / "mcp").mkdir()
    (tmp_path / "mcp" / "wrapper.mjs").write_text("const MARKER = 'x';\nconst VERSION = '0.0.78';\n", encoding="utf-8")
    assert depwatch._module_pin_version(tmp_path, ModuleUpstream(**NPM)) == "0.0.78"


def test_a_pin_file_that_is_gone_or_reached_through_a_link_is_not_read(tmp_path):
    component = ModuleUpstream(**NPM)
    assert depwatch._module_pin_version(tmp_path, component) is None
    outside = tmp_path / "outside.mjs"
    outside.write_text("const VERSION = '9.9.9';\n", encoding="utf-8")
    base = tmp_path / "base"
    (base / "mcp").mkdir(parents=True)
    try:
        (base / "mcp" / "wrapper.mjs").symlink_to(outside)
    except OSError:
        pytest.skip("symlinks not available")
    assert depwatch._module_pin_version(base, component) is None


def test_the_catalog_the_engine_ships_describes_files_that_exist_in_the_shape_it_says():
    """The first thing to break when a compose file or the launcher is reformatted."""
    catalog = load_catalog(INFRA)
    declared = {mid: m.upstream for mid, m in catalog.items() if m.upstream}
    assert {"n8n", "firecrawl", "browser"} <= set(declared)
    for module_id, components in declared.items():
        for component in components:
            assert depwatch._module_pin_version(INFRA, component), f"{module_id}/{component.name}: pin not found in {component.pinned_in}"


@pytest.mark.parametrize("pinned, newest, compare, stale", [
    ("2.11.99", "2.11.481", "minor", False),
    ("2.11.99", "2.12.0", "minor", True),
    ("2.11.99", "2.11.481", "patch", True),
    ("2.35.3", "2.42.4", "patch", True),
    ("2.42.4", "2.42.4", "patch", False),
    ("2.43.0", "2.42.4", "patch", False),
])
def test_a_rolling_release_is_only_news_on_a_new_minor(pinned, newest, compare, stale):
    assert depwatch._is_stale("docker-image", pinned, newest, compare) is stale


def _tags(monkeypatch, listing: str, returncode: int = 0):
    monkeypatch.setattr(depwatch.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, returncode, listing, ""))


def test_the_newest_release_tag_is_the_highest_plain_version(monkeypatch):
    _tags(monkeypatch, "\n".join(f"abc\trefs/tags/{t}" for t in (
        "v2.9.0", "v2.11.99", "v2.11.481", "v2.11.100", "v2.10", "nightly", "v3.0.0-rc1", "release-2.20.0",
    )))
    assert depwatch._git_latest_tag("firecrawl/firecrawl") == "2.11.481"


def test_no_tags_or_an_unreachable_remote_says_nothing(monkeypatch):
    _tags(monkeypatch, "")
    assert depwatch._git_latest_tag("o/r") is None
    _tags(monkeypatch, "boom", returncode=128)
    assert depwatch._git_latest_tag("o/r") is None

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("git", 8)

    monkeypatch.setattr(depwatch.subprocess, "run", timeout)
    assert depwatch._git_latest_tag("o/r") is None
