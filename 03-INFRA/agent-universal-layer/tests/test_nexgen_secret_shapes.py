"""One corpus of synthetic credentials, through every place that has to recognise one.

Each consumer used to carry its own list of what a secret looks like, and each list knew different
providers. A credential type added to one and forgotten in another is invisible until it leaks, so
this feeds the same strings through all of them: the shared module, the connector manifest guard,
the lazy-mcp stderr redaction, vault-mcp's snippet redaction (which keeps a mirror, because its
container ships without the engine package) and the commit-time leak gate.
"""
from __future__ import annotations

import importlib.util
import random
import string
import sys
from pathlib import Path

import pytest

INFRA = Path(__file__).resolve().parents[2]
for extra in (INFRA / "scripts", INFRA / "deploy" / "vault-mcp" / "src"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from nexgen_core import mcp_add, secret_shapes  # noqa: E402
from vault_mcp_server.vault import _redact_snippet  # noqa: E402

LEAK_DIR = INFRA / "agent-universal-layer" / "leak-scan"
LAZY = INFRA / "agent-universal-layer" / "mcp" / "lazy-mcp.py"


def corpus() -> dict[str, str]:
    """Built at runtime: this file must not itself look like a leak."""
    rng = random.Random(11)

    def body(length: int, alphabet: str = string.ascii_letters + string.digits) -> str:
        return "".join(rng.choice(alphabet) for _ in range(length))

    url_safe = string.ascii_letters + string.digits + "-_"
    return {
        "aws access key id": "AK" + "IA" + body(16, string.ascii_uppercase + string.digits),
        "github classic": "gh" + "p_" + body(36),
        "github oauth": "gh" + "o_" + body(36),
        "github fine-grained": "github" + "_pat_" + body(22) + "_" + body(59),
        "anthropic": "sk-" + "ant-api03-" + body(93, url_safe),
        "openai": "sk-" + body(48),
        "openai project": "sk-" + "proj-" + body(120, url_safe),
        "age secret key": "AGE-" + "SECRET-KEY-1" + body(58, string.ascii_uppercase + string.digits),
        "huggingface": "hf" + "_" + body(34),
        "npm": "npm" + "_" + body(36),
        "google api key": "AI" + "za" + body(35, url_safe),
        "stripe": "sk" + "_live_" + body(24),
        "jwt": "ey" + "J" + body(20, url_safe) + "." + body(20, url_safe) + "." + body(20, url_safe),
    }


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def lazy():
    return _load("lazy_mcp_for_shapes", LAZY)


@pytest.fixture(scope="module")
def leak_scan():
    return _load("leak_scan_for_shapes", LEAK_DIR / "leak_scan.py")


@pytest.mark.parametrize("provider", sorted(corpus()))
@pytest.mark.parametrize("template", ["{tok}", "API_KEY={tok}", "error: rejected {tok} for this request"])
def test_every_consumer_recognises_every_provider(provider, template, lazy, leak_scan):
    token = corpus()[provider]
    text = template.format(tok=token)

    assert secret_shapes.looks_like_secret_value(text), "shared module"
    assert token not in secret_shapes.redact(text), "shared redaction"
    assert mcp_add.SECRET_VALUE_RE.search(text), "connector manifest guard"
    assert token not in lazy._redact(text, {}), "lazy-mcp stderr redaction"
    assert token not in _redact_snippet(text), "vault-mcp snippet redaction (mirror)"

    patterns, allow = leak_scan.load_patterns(LEAK_DIR / "leak_patterns.yaml")
    findings = leak_scan.scan_units([leak_scan.Unit("sample", 1, text)], patterns, allow, [])
    assert any(f.blocking for f in findings), "commit-time leak gate"


def test_ordinary_prose_and_diagnostics_are_left_alone():
    for text in ("invalid token: expired", "ModuleNotFoundError: No module named 'drive_server'",
                 "listening on /home/user/.cache/some-long-directory-name/another-directory/x.py",
                 "the author said no", "Bearer"):
        assert secret_shapes.redact(text) == text
    assert not secret_shapes.looks_like_secret_value("hello world, this is not a credential")


def test_a_labelled_value_is_hidden_but_the_label_stays(lazy):
    out = lazy._redact("api_key=abcdefghijklmnop1234", {})
    assert out == "api_key=[redacted]"


def test_secret_names_are_delimited_not_substrings():
    assert secret_shapes.is_secret_name("GITHUB_TOKEN") and secret_shapes.is_secret_name("DB_PASSWORD")
    assert secret_shapes.is_secret_name("SIGNING_KEY")
    assert not secret_shapes.is_secret_name("AUTHOR") and not secret_shapes.is_secret_name("PATH")
    assert secret_shapes.SENSITIVE_NAME_SUBSTRING.search("AUTHOR")  # the loose form, for typed-in manifest values
