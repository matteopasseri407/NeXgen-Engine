"""What a secret looks like, defined once.

Four places used to decide that for themselves: the connector manifest guard refused values that
looked like credentials, the lazy-mcp proxy hid them in a child's stderr, the vault-mcp server hid
them in search snippets, and the leak-scan gate blocked them in commits and Council briefs. Each
list was written when its author needed it, so each knew a different set of providers: a GitHub
fine-grained token or an Anthropic key was refused by one and printed by another.

This module is the runtime definition. The leak-scan patterns (`leak-scan/leak_patterns.yaml`) stay
what they are, the commit-time gate and its allowlist; a test (`test_nexgen_secret_shapes.py`) feeds
one corpus of synthetic credentials through every consumer, so a provider added to one place and
forgotten in another fails the build instead of leaking in production. The vault-mcp container
ships without this package and keeps a mirror of `PROVIDER_TOKEN`, held to the same corpus.
"""
from __future__ import annotations

import re

#: Credentials that announce themselves: a provider prefix and a body. Deliberately a little wider
#: than the commit gate (shorter minimum lengths): here a false positive costs a redacted word, a
#: miss costs a credential in a log.
PROVIDER_TOKEN = re.compile(
    r"(?<![A-Za-z0-9])(?:"
    r"AKIA[0-9A-Z]{12,}"
    r"|gh[pousr]_[A-Za-z0-9]{20,}"
    r"|github_pat_[A-Za-z0-9_]{20,}"
    r"|sk-ant-[A-Za-z0-9_-]{20,}"
    r"|sk-(?:proj|svcacct|admin)-[A-Za-z0-9_-]{20,}"
    r"|sk-[A-Za-z0-9_-]{16,}"
    r"|AGE-SECRET-KEY-1[A-Z0-9]{20,}"
    r"|hf_[A-Za-z0-9]{30,}"
    r"|npm_[A-Za-z0-9]{36}"
    r"|AIza[0-9A-Za-z_-]{30,}"
    r"|(?:sk|pk)_(?:live|test)_[A-Za-z0-9]{16,}"
    r"|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"
    r")"
)

#: A private key block, whatever the algorithm word (or none) in its header.
PEM_BLOCK = re.compile(r"-----BEGIN [A-Z0-9 ]+-----.*?-----END [A-Z0-9 ]+-----", re.DOTALL)

#: High-entropy runs with no provider prefix.
LONG_HEX = re.compile(r"\b[A-Fa-f0-9]{40,}\b")
LONG_BASE64 = re.compile(r"\b[A-Za-z0-9+/_-]{43,}={0,2}\b")

#: A value that is a credential wearing a plain string: what a manifest entry must never carry.
SECRET_VALUE = re.compile(f"{PROVIDER_TOKEN.pattern}|{LONG_HEX.pattern}|{LONG_BASE64.pattern}")

#: An environment-variable NAME that is delimited as a secret (`GITHUB_TOKEN`, `DB_PASSWORD`, `X_KEY`).
#: Strict on purpose: it decides what a child process may inherit, and `AUTHOR` is not `AUTH`.
SECRET_NAME = re.compile(
    r"(?:^|_)(?:TOKEN|SECRET|PASSWORD|PASSWD|PASSPHRASE|CREDENTIALS?|API_?KEY|PRIVATE_?KEY|ACCESS_?KEY|AUTH|COOKIE)(?:_|$)"
    r"|_KEY$|_PAT$",
    re.IGNORECASE,
)

#: A name that merely CONTAINS a secret word. Loose on purpose: it decides what a person may type
#: into a manifest as plain text, where a refusal is cheap and the message says what to do instead.
SENSITIVE_NAME_SUBSTRING = re.compile(
    r"(?i)(token|secret|password|passwd|pwd|api[_-]?key|access[_-]?key|"
    r"private[_-]?key|client[_-]?secret|credential|auth)"
)

REDACTED = "[redacted]"

_BEARER = re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{8,}")
_LABELLED = re.compile(r"(?i)\b(token|apikey|api_key|password|secret)\b(\s*[=:]\s*)[A-Za-z0-9._~+/=-]{12,}")


def is_secret_name(name: str) -> bool:
    return bool(SECRET_NAME.search(name))


def looks_like_secret_value(value: str) -> bool:
    return bool(SECRET_VALUE.search(value))


def redact(text: str, *, high_entropy: bool = False) -> str:
    """Hides the credentials in `text` that can be recognised by shape.

    A labelled value is only hidden when it is itself long enough to be a credential, so
    "invalid token: expired" keeps the part that says what is wrong. `high_entropy` also hides
    any long hex or base64-looking run, which is right for free text going to a stranger and wrong
    for a diagnostic: a 50-character file path looks exactly like one.
    """
    text = PEM_BLOCK.sub(REDACTED, text)
    text = _BEARER.sub(rf"\1 {REDACTED}", text)
    text = _LABELLED.sub(rf"\1\2{REDACTED}", text)
    text = PROVIDER_TOKEN.sub(REDACTED, text)
    if high_entropy:
        text = LONG_HEX.sub(REDACTED, text)
        text = LONG_BASE64.sub(REDACTED, text)
    return text
