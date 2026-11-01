"""The manuals each agent reads, in the engine's language, rendered as text.

The three agents share one shape and differ in one thing only: what they own
and where the work they do not own goes. The text is generic on purpose —
no names, no paths, no facts about the person using the machine — because it
ships with the engine and is read on every machine that installs it.

`render_workspace` is pure: it returns the files, it never writes them. B2 is
what puts them on disk.
"""
from __future__ import annotations

import json
from typing import Any

from nexgen_core.i18n import detect_language
from nexgen_core.openclaw.manual_legacy import PREVIOUS_HANDOVER
from nexgen_core.openclaw.manual_text import (
    CLAUDE_POINTER,
    HANDOVER,
    MACHINE_CONTRACT,
    MANUALS,
    VAULT_WRITE_RULE,
)

#: The agents and how a caller may name them. The id is the key in
#: `agents.entries` and in the manuals below; the alias is the word the manuals
#: use for the same agent, so both spellings reach the same text.
AGENTS = {
    "main": "main",
    "assistente": "main",
    "capo": "capo",
    "automazioni": "automazioni",
    "sviluppo": "sviluppo",
}

#: The settings the `capo` workspace hands to Claude Code, which reads them itself.
CLAUDE_SETTINGS: dict[str, Any] = {"model": "claude-opus-5-5", "effortLevel": "high"}


def _language(lang: str | None) -> str:
    """`it` or `en`: anything else reads English, the language the engine writes in."""
    chosen = (lang or detect_language() or "en").strip().lower()
    return "it" if chosen.startswith("it") else "en"


def render_workspace(agent: str, lang: str | None = None, node: str | None = None) -> dict[str, str]:
    """The files one agent's workspace holds, path -> text.

    An agent that declares a machine (`node: "MatteoPC"`) reads the machine
    manual, which is one text for every PC with only the name changed: its job is
    to drive that machine's harnesses with `sup`, not a role of its own.

    The `capo` also gets the two files Claude Code reads on its own, so the same
    agent behaves the same way whether it is reached through OpenClaw or through
    Claude Code. The text names no other agent: the org chart arrives as the
    managed block `nexgen sync` writes from the roster.
    """
    name = _manual_name(agent, node)
    if name is None:
        raise ValueError(f"unknown agent {agent!r}; expected one of {sorted(AGENTS)}")
    language = _language(lang)
    text = MANUALS[language][name]
    if name == "node":
        text = text.replace("{node}", (node or "").strip())
    files = {"AGENTS.md": text + VAULT_WRITE_RULE[language]}
    if name == "capo":
        files["CLAUDE.md"] = CLAUDE_POINTER[language]
        files[".claude/settings.json"] = json.dumps(CLAUDE_SETTINGS, indent=2) + "\n"
    return files


def _manual_name(agent: str, node: str | None) -> str | None:
    """Which manual this workspace receives: the machine's, or the role's."""
    if (node or "").strip():
        return "node"
    return AGENTS.get(agent)


def machine_contract(node: str, lang: str | None = None) -> str:
    """The machine's rules alone, for a workspace whose manual the squad declares.

    A squad that writes its own `agents_md` says who the agent is; the contract
    says what it does with that machine's harnesses, and it is the engine's to
    keep in one place rather than a paragraph every machine's file copies.
    """
    return MACHINE_CONTRACT[_language(lang)].replace("{node}", node.strip())


def _previous_manual(agent: str, language: str, node: str | None = None) -> str:
    """The manual the previous release shipped for this agent, handover section included."""
    name = _manual_name(agent, node)
    text = MANUALS[language][name] + VAULT_WRITE_RULE[language]
    if name == "node":
        return text.replace("{node}", (node or "").strip())
    return text.replace(HANDOVER[language], PREVIOUS_HANDOVER[language][name], 1)


def shipped_manuals(agent: str, node: str | None = None) -> tuple[str, ...]:
    """The `AGENTS.md` this engine ships for one agent, in every language it writes.

    All of them, not the language of the run: this is the text an ownership test
    compares against, and a workspace prepared while the engine spoke another
    language is still the engine's file. An agent the engine knows no manual for
    (one a squad declares for itself, without a machine) has none, and nothing is
    ever replaced on that account. The previous release's wording is in the answer
    too: a workspace it prepared still holds exactly that text, and must not start
    looking like the person's now that the chart moved to its own block.
    """
    if _manual_name(agent, node) is None:
        return ()
    manuals: list[str] = []
    for language in MANUALS:
        for text in (
            render_workspace(agent, language, node)["AGENTS.md"],
            _previous_manual(agent, language, node),
        ):
            if text not in manuals:
                manuals.append(text)
    return tuple(manuals)
