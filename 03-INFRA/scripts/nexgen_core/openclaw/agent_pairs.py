"""The agent-to-agent lists an earlier engine wrote, and how they leave a file.

One job: recognise the closed star an older engine wrote into
`tools.agentToAgent.allow` and into each agent's `send`, and take it out of the
document being merged while leaving a list the person edited untouched. Why:
OpenClaw's default is that every pair may talk, the engine now writes no list at
all, and an install that already carries the old one has to heal on the next
setup. The recognition lives here so `setup_write` keeps its own one job, which
is merging without losing a key.

The old value is computed, never guessed: `legacy_allow` and `legacy_send`
reproduce exactly what the previous engine's hardcoded pairs produced for the
agents a file declares. Anything that differs is the person's narrowing.
"""
from __future__ import annotations

from typing import Any

#: The messaging pairs an earlier engine hardcoded and wrote into
#: `tools.agentToAgent.allow` and into every agent's `send` list. Nothing writes
#: them any more; they are kept here only so the migration can recognise the old
#: value and leave a person's edit alone.
LEGACY_EDGES: tuple[tuple[str, str], ...] = (
    ("main", "capo"),
    ("main", "automazioni"),
    ("main", "sviluppo"),
    ("capo", "sviluppo"),
)


def _agent_ids(entries: Any) -> set[str]:
    """The agent ids a mapping or an iterable of ids names."""
    values = entries.keys() if isinstance(entries, dict) else entries
    try:
        return {str(item) for item in values}
    except TypeError:  # nexgen: allow reason="no ids at all is the honest answer for a value that is not a roster"
        return set()


def legacy_allow(entries: Any, squad_sends: Any = None) -> set[str]:
    """The `tools.agentToAgent.allow` an earlier engine would have written here.

    It exists only to recognise that old value, so a file the engine itself wrote
    can lose it. The old value was the agents taking part in the hardcoded edges,
    plus the agent and the targets of every `send` a squad declared.
    """
    present = _agent_ids(entries)
    allowed: set[str] = set()
    for left, right in LEGACY_EDGES:
        if left in present and right in present:
            allowed.update((left, right))
    for agent_id, targets in squad_sends.items() if isinstance(squad_sends, dict) else ():
        allowed.add(str(agent_id))
        allowed.update(str(target) for target in targets if isinstance(targets, list))
    return allowed


def legacy_send(agent_id: str, entries: Any) -> set[str]:
    """The per-agent `send` list an earlier engine would have written for this agent."""
    present = _agent_ids(entries)
    partners: set[str] = set()
    for left, right in LEGACY_EDGES:
        if left not in present or right not in present:
            continue
        if agent_id == left:
            partners.add(right)
        elif agent_id == right:
            partners.add(left)
    return partners


def _squad_sends(agents: Any) -> dict[str, list[str]]:
    """The `send` lists the squad declares, which the proposal carries on each entry.

    `build_config` writes none of its own any more, so a `send` in the proposal is
    the person's narrowing and the migration must leave it where it is.
    """
    entries = agents.get("entries") if isinstance(agents, dict) else None
    if not isinstance(entries, dict):
        return {}
    declared: dict[str, list[str]] = {}
    for agent_id, entry in entries.items():
        tools = entry.get("tools") if isinstance(entry, dict) else None
        partners = tools.get("agentToAgent") if isinstance(tools, dict) else None
        send = partners.get("send") if isinstance(partners, dict) else None
        if isinstance(send, list):
            declared[str(agent_id)] = [str(item) for item in send]
    return declared


def _without_leaf(
    entry: dict[str, Any], tools: dict[str, Any], partners: dict[str, Any], key: str,
) -> dict[str, Any]:
    """The entry without one key of its `agentToAgent` block, rebuilt and never edited.

    An empty block is no block: `{}` beside a schema that reads absence as "open"
    is a value OpenClaw may read as meaningful, and it is not.
    """
    rest = {name: value for name, value in partners.items() if name != key}
    new_tools = {name: value for name, value in tools.items() if name != "agentToAgent"}
    if rest:
        new_tools["agentToAgent"] = rest
    updated = {name: value for name, value in entry.items() if name != "tools"}
    if new_tools:
        updated["tools"] = new_tools
    return updated


def _pruned_sends(entries: dict[str, Any], sends: dict[str, list[str]]) -> dict[str, Any]:
    """The entries whose old star `send` has to go: rebuilt, never edited in place."""
    pruned: dict[str, Any] = {}
    for agent_id, entry in entries.items():
        tools = entry.get("tools") if isinstance(entry, dict) else None
        partners = tools.get("agentToAgent") if isinstance(tools, dict) else None
        send = partners.get("send") if isinstance(partners, dict) else None
        if not isinstance(send, list) or str(agent_id) in sends:
            continue
        if {str(item) for item in send} != legacy_send(str(agent_id), entries):
            continue
        pruned[agent_id] = _without_leaf(entry, tools, partners, "send")
    return pruned


def _retire_allow(document: dict[str, Any], entries: Any, sends: dict[str, list[str]]) -> None:
    """Takes the old `allow` out of the tools block, and the block when it is only that."""
    tools = document.get("tools")
    block = tools.get("agentToAgent") if isinstance(tools, dict) else None
    allow = block.get("allow") if isinstance(block, dict) else None
    if not isinstance(allow, list):
        return
    if {str(item) for item in allow} != legacy_allow(entries, sends):
        return
    rest = {name: value for name, value in block.items() if name != "allow"}
    remaining = {name: value for name, value in tools.items() if name != "agentToAgent"}
    if rest:
        remaining["agentToAgent"] = rest
    document["tools"] = remaining


def retire(document: dict[str, Any], proposed: dict[str, Any]) -> None:
    """Takes the closed star an earlier engine wrote out of the messaging lists.

    The engine used to write `tools.agentToAgent.allow` and one `send` per agent,
    which cut off every agent outside that star as soon as one squad agent narrowed
    its own. OpenClaw's default is open, so nothing is replaced: the old value is
    removed only where it is still exactly the old value. A `send` the squad
    declares is the person's too, and stays even when the value happens to match.

    The branches that change are rebuilt rather than edited in place: a section
    only the proposal carries is the proposal's own object, and editing it would
    change the caller's document, which `setup_write` promises never to do.
    """
    sends = _squad_sends(proposed.get("agents"))
    agents = document.get("agents")
    entries = agents.get("entries") if isinstance(agents, dict) else None
    if isinstance(entries, dict):
        pruned = _pruned_sends(entries, sends)
        if pruned:
            document["agents"] = {**agents, "entries": {**entries, **pruned}}
            entries = document["agents"]["entries"]
    _retire_allow(document, entries or {}, sends)
