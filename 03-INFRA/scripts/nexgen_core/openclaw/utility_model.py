"""Which model answers OpenClaw's short internal work, and why it is not the primary.

One job: name the `agents.defaults.utilityModel` ref this machine should use for the
model calls nobody types -- generated session and thread titles, progress narration,
activity recaps, saved transcript summaries, side chat -- or say nothing, so the file in
force keeps whatever the person and OpenClaw had decided.

Why this exists. Those are separate model requests, billed to `agents.defaults.model`
while `utilityModel` is unset, and the engine wrote the key nowhere. The squad's own
agent pays 78,717 tokens to open one session (docs/proposals/daru-burn.md, measured on
the live gateway); a chat that also asks its most expensive model to invent that chat's
title has spent the scarce window on a label. The key costs one line and removes that
call from the primary model.

The order is a fact about price, not a preference: a model this machine serves costs
nothing per call and keeps the text at home, then the flat Go key's flash model, then
the Codex subscription. `anthropic/*` is never proposed, because a Claude call is the
five-hour window that one runaway agent spent in two minutes. When this machine serves
nothing cheaper than the model OpenClaw already chose for titles, the engine writes no
key: OpenClaw's own small-model default for the primary provider is a better answer than
a pinned guess.
"""
from __future__ import annotations

from typing import Any

from nexgen_core.openclaw.detect import Detection
from nexgen_core.openclaw.local_models import local_models

def _cheap_refs() -> tuple[str, str]:
    """The Go flash and the Codex model, cheapest first, from the module that owns them.

    Never a Claude model, and never a copy kept in step by hand: `profile` owns those two
    refs, and reading them at call time is what stops this module from drifting when one
    of them is upgraded. The import is here rather than at the top because `profile`
    imports this module.
    """
    from nexgen_core.openclaw.profile import CODEX_MODEL, GO_MAIN_MODEL

    return GO_MAIN_MODEL, CODEX_MODEL


def _defaults(current: dict[str, Any] | None) -> dict[str, Any]:
    """The `agents.defaults` block of the configuration in force, or an empty one."""
    agents = current.get("agents") if isinstance(current, dict) else None
    block = agents.get("defaults") if isinstance(agents, dict) else None
    return block if isinstance(block, dict) else {}


def declares_utility_model(current: dict[str, Any] | None) -> bool:
    """Whether the file already answers this question, in either shape the key accepts.

    An empty string is the documented way to switch utility routing off, so it is a
    decision like a ref is: writing over it would undo something the person asked for.
    An empty mapping answers nothing and is treated as absent.
    """
    value = _defaults(current).get("utilityModel")
    if isinstance(value, str):
        return True
    return isinstance(value, dict) and bool(value)


def utility_ref(detected: Detection) -> str | None:
    """The utility model for this machine, or None when it serves nothing cheaper.

    A local model first, in the order detection reports them, because a free call on
    this machine keeps both the money and the text at home. Then the Go flash, then
    Codex. A machine with only a Claude login gets nothing: its own provider default is
    a Claude model, and pinning a different one would be the engine choosing a price it
    cannot see.
    """
    served = local_models(detected)
    if served:
        return served[0].ref
    go, codex = _cheap_refs()
    if getattr(detected, "opencode_go", False):
        return go
    if getattr(getattr(detected, "codex", None), "available", False):
        return codex
    return None


def utility_block(detected: Detection, current: dict[str, Any] | None) -> dict[str, str]:
    """The `agents.defaults` fragment to propose: the one key, or nothing at all."""
    if declares_utility_model(current):
        return {}
    ref = utility_ref(detected)
    return {"utilityModel": ref} if ref else {}
