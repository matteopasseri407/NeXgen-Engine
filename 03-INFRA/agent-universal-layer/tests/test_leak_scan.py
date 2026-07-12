"""Regression tests for leak-scan/leak_scan.py's allowlist logic.

leak_scan.py has no HOME/env dependency (patterns and text are passed in
directly), so unlike agent_sync.py/skills-sync.py it does not need the
sandbox fixture -- it is loaded as a plain module, the same way
council.py's own `_load_leak_scan()` loads it at runtime.

Audit finding 29 (2026-07-10): scan_units() used to decide whether an
allowlisted pattern "explains away" a match by checking
`any(a.search(tok) for a in allow_re)` -- i.e. does an allow pattern appear
ANYWHERE inside the isolated matched token, rather than does it account for
the WHOLE token. A value that merely *contains* an allowlisted value as a
substring (an allowlisted value appearing near/inside a genuinely
suspicious one on the same line) used to be silently cleared along with it.

A couple of test strings below are built by concatenating string literals
(the same trick test_council_relay.py uses for its synthetic AWS key)
instead of writing the shape out whole: this file's own diff goes through
the same leak-scan gate it tests, so the illustrative "suspicious" values
must not appear as a single contiguous token in the source text itself.
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
LEAK_SCAN_DIR = REPO / "03-INFRA" / "agent-universal-layer" / "leak-scan"
PATTERNS_FILE = LEAK_SCAN_DIR / "leak_patterns.yaml"


def load_leak_scan_module():
    spec = importlib.util.spec_from_file_location("leak_scan_under_test", LEAK_SCAN_DIR / "leak_scan.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def leak_scan():
    return load_leak_scan_module()


@pytest.fixture(scope="module")
def real_patterns(leak_scan):
    """The actual public pattern file, not a hand-rolled copy: a test built
    against a private copy of the rules could pass while the real
    leak_allow entries drift underneath it."""
    return leak_scan.load_patterns(PATTERNS_FILE)


def _scan_line(leak_scan, patterns, allow, text: str):
    unit = leak_scan.Unit("line", 1, text)
    return leak_scan.scan_units([unit], patterns, allow, [])


# --- the false negative this fix closes ------------------------------------

def test_value_merely_containing_an_allowlisted_email_is_still_flagged(leak_scan, real_patterns):
    """A real address with something else concatenated onto the local part
    of the allowlisted generic commit-trailer address must still be
    blocked: it is a different value that just happens to contain the
    allowlisted one as a substring, on the same (single) matched token."""
    patterns, allow = real_patterns
    # Built by concatenation, not written out whole: no digit run either
    # (a 7+ digit prefix would also trip the unrelated "long numeric id"
    # hard pattern and mask whether the email allow-check was exercised).
    suspicious_email = "leak-vector-" + "noreply@anthropic.com"
    line = "escalate to " + suspicious_email + " for review"

    findings = _scan_line(leak_scan, patterns, allow, line)

    assert findings, "a distinct value containing an allowlisted substring must not be waved through"
    assert any(f.blocking for f in findings)


def test_ip_merely_containing_an_allowlisted_address_is_still_flagged(leak_scan, real_patterns):
    """A real, distinct IPv4 address that happens to contain the
    allowlisted wildcard address as a substring must not be exempted by
    that unrelated allow entry."""
    patterns, allow = real_patterns
    # Built by concatenation, not written out whole (see module docstring).
    distinct_ip = "20" + ".0.0.0"
    line = "internal gateway at " + distinct_ip + " leaked in config"

    findings = _scan_line(leak_scan, patterns, allow, line)

    assert findings, "a distinct address must not be cleared just because it contains the allowlisted one"
    assert any(f.blocking for f in findings)


def test_scan_units_allowlist_check_is_a_fullmatch_not_a_substring_search(leak_scan):
    """Direct unit check on the primitive itself, independent of the real
    pattern file: an allow regex must cover the whole token to clear it."""
    patterns = [("h0", r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", True)]
    wildcard_ip = "0.0.0.0"
    allow = [re.escape(wildcard_ip)]

    # The wildcard itself: allow pattern fully covers the token -> cleared.
    cleared = leak_scan.scan_units(
        [leak_scan.Unit("l", 1, "gw " + wildcard_ip + " up")], patterns, allow, [],
    )
    assert cleared == []

    # A distinct address containing it as a substring (built by
    # concatenation, not written out whole -- see module docstring): the
    # allow pattern only matches a fragment of the token -> kept.
    distinct_ip = "90" + ".0.0.0"
    kept = leak_scan.scan_units(
        [leak_scan.Unit("l", 1, "gw " + distinct_ip + " up")], patterns, allow, [],
    )
    assert len(kept) == 1
    assert kept[0].blocking is True


# --- innocent cases must stay clean (no new false positives) --------------

@pytest.mark.parametrize(
    "line",
    [
        "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>",
        "curl http://127.0.0.1:8080/health",
        "bind = 0.0.0.0",
        "broadcast = 255.255.255.255",
        "home = /home/user/project",
        "container HOME=/home/node",
        r"C:\Users\user\AppData",
        "MAX_UPLOAD_BYTES = 15728640",
        "old_sha = 0000000000000000000000000000000000000000",
    ],
)
def test_allowlisted_values_still_pass_clean(leak_scan, real_patterns, line):
    patterns, allow = real_patterns

    findings = _scan_line(leak_scan, patterns, allow, line)

    assert findings == [], f"legit allowlisted case wrongly flagged: {line!r} -> {findings}"


# --- SHA/digest pinning must not be mistaken for a leaked hex secret -------
#
# Packages P8 (GitHub Actions SHA-pinning) and P10 (Docker digest-pinning) of
# the 2026-07-12 hardening pass both hit the h4 long-hex pattern as a false
# positive on a legitimate, publicly-verifiable pin. The fix narrows h4 with
# two fixed-width negative lookbehinds instead of allowlisting individual SHA
# values one at a time (which would not scale to future action/image bumps).


def _synthetic_sha40() -> str:
    """Built by concatenation, not written out whole (see module docstring
    and the existing AWS-key test below): a 40-char hex run that must never
    appear contiguous in this file's own source, or the leak-scan hook that
    guards this very repo would (correctly) flag this test file itself."""
    return "9c091bb2" + "1b7c1c1d" + "1991bb90" + "8d89e4e9" + "dddfe3e0"


def test_github_actions_sha_pin_after_at_is_not_flagged(leak_scan, real_patterns):
    patterns, allow = real_patterns
    line = f"uses: actions/checkout@{_synthetic_sha40()}  # v7.0.0"

    findings = _scan_line(leak_scan, patterns, allow, line)

    hex_hits = [f for f in findings if f.blocking and f.kind.startswith("pattern:h")]
    assert hex_hits == [], f"a SHA right after '@' must not be flagged: {hex_hits}"


def test_docker_digest_pin_after_sha256_is_not_flagged(leak_scan, real_patterns):
    patterns, allow = real_patterns
    digest = "ab" * 32  # 64 hex chars, sha256 digest shape
    line = f"FROM python:3.12-slim@sha256:{digest}"

    findings = _scan_line(leak_scan, patterns, allow, line)

    hex_hits = [f for f in findings if f.blocking and f.kind.startswith("pattern:h")]
    assert hex_hits == [], f"a digest right after 'sha256:' must not be flagged: {hex_hits}"


def test_bare_long_hex_without_safe_prefix_is_still_flagged(leak_scan, real_patterns):
    """The exclusion is narrow: the exact same 40-hex-char value with no
    '@'/'sha256:' immediately before it must still be caught (this is not a
    blanket exemption for anything that looks like a commit SHA)."""
    patterns, allow = real_patterns
    line = f"token={_synthetic_sha40()}"

    findings = _scan_line(leak_scan, patterns, allow, line)

    hex_hits = [f for f in findings if f.blocking and f.kind.startswith("pattern:h")]
    assert hex_hits, "a long hex value with no safe-context prefix must still be flagged"


# --- unquoted high-entropy token (audit finding, 2026-07-12) ---------------
#
# leak_hard used to catch a 16+ char mixed-case alnum token only when it was
# wrapped in quotes. The same token unquoted -- a .env line (NOME=valore),
# a bare "Authorization: Bearer <token>" / "X-Api-Key: <token>" header, or
# the same thing pasted into a markdown table cell -- used to pass clean.


def _synthetic_unquoted_token() -> str:
    """Built by concatenation, not written out whole (see module docstring):
    a 16+ char run mixing upper/lower/digit, used WITHOUT surrounding quotes
    so it only exercises the new unquoted leak_hard pattern (the older
    quoted-only pattern requires literal quote chars around the token)."""
    return "Zq7" + "Rk9" + "Tn2" + "Vb8" + "Wp4" + "Xy6"


def test_unquoted_token_in_dotenv_style_line_is_now_flagged(leak_scan, real_patterns):
    patterns, allow = real_patterns
    token = _synthetic_unquoted_token()
    line = f"API_TOKEN={token}"

    findings = _scan_line(leak_scan, patterns, allow, line)

    assert findings, "unquoted .env-style NAME=value with a high-entropy value must be flagged"
    assert any(f.blocking for f in findings)


def test_unquoted_bearer_token_in_authorization_header_is_now_flagged(leak_scan, real_patterns):
    patterns, allow = real_patterns
    token = _synthetic_unquoted_token()
    line = f"Authorization: Bearer {token}"

    findings = _scan_line(leak_scan, patterns, allow, line)

    assert findings, "a bare 'Authorization: Bearer <token>' header must be flagged"
    assert any(f.blocking for f in findings)


def test_unquoted_bearer_token_in_markdown_table_cell_is_now_flagged(leak_scan, real_patterns):
    """Same token as the header test above, just pasted into a markdown
    table row (pipe-delimited cells) instead of a plain header line."""
    patterns, allow = real_patterns
    token = _synthetic_unquoted_token()
    line = f"| Authorization | Bearer {token} |"

    findings = _scan_line(leak_scan, patterns, allow, line)

    assert findings, "the same token inside a markdown table cell must be flagged"
    assert any(f.blocking for f in findings)


def test_unquoted_high_entropy_generic_header_value_is_now_flagged(leak_scan, real_patterns):
    """Generic 'Header-Name: value' shape, not just the Bearer scheme."""
    patterns, allow = real_patterns
    token = _synthetic_unquoted_token()
    line = f"X-Api-Key: {token}"

    findings = _scan_line(leak_scan, patterns, allow, line)

    assert findings, "a generic 'Header: <high-entropy value>' line must be flagged"
    assert any(f.blocking for f in findings)


@pytest.mark.parametrize(
    "line",
    [
        # comparison/walrus operators must not be mistaken for an assignment
        "if resultValueOne == expectedValueTwo:",
        "if a <= someLongVariableName1:",
        "if a != someOtherVariable2Yes:",
        "walrus := someLongVariableName1here",
        # snake/kebab identifiers: no contiguous 16+ alnum run (matches the
        # existing rationale for the quoted pattern -- see comment in
        # leak_patterns.yaml)
        "bearer_token_env_var=some_snake_case_value_here",
        "Authorization: Bearer TELEGRAM_BOT_TOKEN_NAME",
        "agent-universal-layer",
    ],
)
def test_unquoted_pattern_does_not_flag_identifiers_and_operators(leak_scan, real_patterns, line):
    """The new unquoted pattern must stay anchored to '=', 'Bearer ', ': ' --
    ordinary code (comparisons, walrus assignment) and snake/kebab
    identifiers must not trip it."""
    patterns, allow = real_patterns

    findings = _scan_line(leak_scan, patterns, allow, line)

    hard_unquoted_hits = [f for f in findings if f.blocking and f.kind.startswith("pattern:h")]
    assert hard_unquoted_hits == [], f"must not be flagged: {line!r} -> {hard_unquoted_hits}"


# --- explicit non-regression: an AWS-shaped key is never let through -------

def test_synthetic_aws_key_id_is_still_blocked(leak_scan, real_patterns):
    """A council seat can hallucinate an AWS-shaped access key id (see
    tests/test_council_relay.py::test_relay_redacts_generated_secret_before_the_next_stage,
    which exercises this exact string through council.py's egress_gate /
    redact_generated_output, both backed by this scan_units()). No leak_allow
    entry may ever exempt it, before or after this fix."""
    patterns, allow = real_patterns
    synthetic_secret = "AKIA" + "12345" + "67890" + "ABCDEF"

    findings = _scan_line(leak_scan, patterns, allow, f"credentials: {synthetic_secret}")

    assert any(f.blocking and f.kind.startswith("pattern:h") for f in findings)
