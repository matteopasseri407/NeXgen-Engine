"""The module catalog entry and the pin it declares.

The catalog is a contract read by the installer, the dependency watch and the
doctor. A typo in it does not fail loudly on the machine: it makes the feature
invisible. These tests pin the entry and, above all, that the declared pattern
still finds the version in the file the entry points at.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

INFRA = Path(__file__).resolve().parents[2]

from nexgen_core.modules import load_catalog  # noqa: E402 - sys.path shim for cloned checkout


def openclaw_module():
    return load_catalog(INFRA)["openclaw"]


def test_the_catalog_declares_openclaw_as_a_host_feature():
    module = openclaw_module()
    assert module.kind == "feature"
    assert module.states == ("absent", "local")
    assert module.scope == "host"
    assert module.depends_on == ("memory",)
    assert module.health == "nexgen openclaw doctor"
    assert module.requires.binaries == ("node", "npm")


def test_the_upstream_is_the_npm_package_and_the_pin_is_one_file():
    module = openclaw_module()
    assert len(module.upstream) == 1
    upstream = module.upstream[0]
    assert upstream.kind == "npm"
    assert upstream.package == "openclaw"
    assert upstream.latest == "npm:openclaw"
    assert upstream.pinned_in == "agent-universal-layer/openclaw/pin.json"
    assert upstream.pattern


def test_the_declared_pattern_actually_finds_the_pin():
    """The catalog's pattern and the pin file must name the same release.

    Neither number is written here: a copy of the version in a test is a copy
    that goes stale, and this one did (it still said 2026.10.7 after the pin
    moved). The file is the source, and the pattern must find what is in it.
    """
    upstream = openclaw_module().upstream[0]
    text = (INFRA / upstream.pinned_in).read_text(encoding="utf-8")
    declared = json.loads(text)["version"]
    found = re.search(upstream.pattern, text)
    assert found is not None
    assert found.group(1) == declared


def test_the_package_reads_the_same_version_as_the_catalog_pins():
    from nexgen_core.openclaw.pin import pinned_version

    declared = json.loads((INFRA / "agent-universal-layer" / "openclaw" / "pin.json").read_text(encoding="utf-8"))
    assert pinned_version(INFRA) == declared["version"]
