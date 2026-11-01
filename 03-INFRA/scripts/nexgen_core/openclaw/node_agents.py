"""The agents whose shell is a paired machine, not the gateway.

One job: read and write the `tools.exec.host`/`node` pair that decides where an
agent's commands run, so the squad, the manual and the org chart all agree on
which agent is the remote control of which machine. Why: an agent that stands
for a PC has to know it, and every other agent has to know whom to hand that
PC's work to; that answer is already a shape inside the configuration, and one
reader of that shape is enough.

Nothing here reads a file or the clock: entries arrive as they are under
`agents.entries`.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

#: The squad key that declares a machine in one line: `node: "MatteoPC"`. It is
#: shorthand for `tools.exec: {host: node, node: "MatteoPC"}`, which is the shape
#: the engine reads everywhere else, and the one OpenClaw already understands.
NODE_KEY = "node"

#: The exec host that runs a command on a paired machine instead of the gateway.
NODE_HOST = "node"


def exec_node(entry: Any) -> str | None:
    """The machine this agent's commands run on, or None when they run on the gateway.

    A `host: node` with no machine named is not a machine agent: OpenClaw would
    fall back to a routing decision of its own, and the engine never guesses a
    name it was not given.
    """
    tools = entry.get("tools") if isinstance(entry, Mapping) else None
    block = tools.get("exec") if isinstance(tools, Mapping) else None
    if not isinstance(block, Mapping) or block.get("host") != NODE_HOST:
        return None
    name = block.get(NODE_KEY)
    return name.strip() if isinstance(name, str) and name.strip() else None


def exec_on_node(node: str) -> dict[str, str]:
    """The exec block that points one agent's shell at a paired machine."""
    return {"host": NODE_HOST, NODE_KEY: node.strip()}


def node_agents(entries: Mapping[str, Any] | None) -> dict[str, str]:
    """Agent id -> the machine its shell runs on, for every agent pinned to one."""
    if not isinstance(entries, Mapping):
        return {}
    found: dict[str, str] = {}
    for agent_id, entry in entries.items():
        node = exec_node(entry)
        if node:
            found[str(agent_id)] = node
    return found
