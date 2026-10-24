"""The harnesses the engine can drive, one adapter each, looked up by name.

One job: hold the registry and answer "which adapter is `claude`". The engine
never imports a concrete adapter: it asks here, so adding a harness is one entry
and no caller changes. A name nobody registered is a refusal that lists the names
there are, not a silent fallback to OpenCode.
"""
from __future__ import annotations

from . import base
from .antigravity import AntigravityAdapter
from .claude import ClaudeAdapter
from .codex import CodexAdapter
from .local import LocalAdapter
from .openclaw import OpenClawAdapter
from .opencode import OpenCodeAdapter

#: Every adapter, by the name the journal and the lane's config use.
_ADAPTERS: dict[str, base.Adapter] = {
    adapter.name: adapter
    for adapter in (
        OpenCodeAdapter(), ClaudeAdapter(), CodexAdapter(), AntigravityAdapter(), LocalAdapter(),
        OpenClawAdapter(),
    )
}
#: The other spellings an owner may type, mapped to the canonical name.
ALIASES = {
    "opencode2": "opencode",
    "claude-code": "claude",
    "claudecode": "claude",
    "codex-cli": "codex",
    "antigravity": "agy",
    "gemini": "agy",
    "local-model": "local",
}


def resolve(name: str) -> base.Adapter:
    """The adapter this name means, refused with the list of names when nobody knows it."""
    wanted = (name or "").strip().casefold()
    canonical = ALIASES.get(wanted, wanted)
    found = _ADAPTERS.get(canonical)
    if found is None:
        raise base.AdapterError(
            f"harness sconosciuto: {name!r} (conosciuti: {', '.join(names())})"
        )
    return found


def get(name: str) -> base.Adapter:
    """`resolve`, named the way the rest of the engine reads better."""
    return resolve(name)


def names() -> tuple[str, ...]:
    """Every canonical harness name, in the order the table lists them."""
    return tuple(_ADAPTERS)


def adapters() -> tuple[base.Adapter, ...]:
    """Every adapter, for the capability table."""
    return tuple(_ADAPTERS.values())


def capability_lines() -> list[str]:
    """One line per harness: what it can do, what it cannot, how a push is stopped."""
    return [
        f"{adapter.name} · {adapter.label} · {adapter.capabilities.line()}" for adapter in adapters()
    ]


def supports(name: str, operation: str) -> bool:
    """Whether this harness declares the operation; an unknown name is not supported."""
    try:
        return resolve(name).capabilities.has(operation)
    except base.AdapterError:  # nexgen: allow reason="un nome sconosciuto non supporta nessuna operazione, che è la risposta negativa"
        return False
