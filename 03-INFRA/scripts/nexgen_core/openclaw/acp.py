"""The OpenCode agent and the ACP bridge: one component, one decision each.

One job: say whether this machine gets the agent that runs real OpenCode one to
one, and build the configuration blocks that make an ACP lane possible -- the
bridge itself, and one persistent binding per conversation routed to a harness.
The agent entry itself stays in `profile.py`, because it needs the workspace
rules that live there; what is here is everything that does not.

Two facts are not preferences. An agent with `runtime.type: "acp"` answers as
the harness only inside a bound ACP session: in a direct chat OpenClaw answers
with the default model instead, which is why a lane is always written as both
halves -- the runtime block and the binding (`harness_lanes.py` writes them
together). `gemini` never appears in the allow list: Google's terms do not allow
its client outside Google's own applications, and the doctor warns about a seat
that is already there. `agy` is refused for the same family of reasons: it is a
CLI backend on Google's own client, and ACP for Antigravity would need Google's
separate proprietary server.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from nexgen_core.openclaw.detect import Detection

#: The agent that drives real OpenCode one to one, through ACP sessions. The id never
#: changes with the language, because it is also an allow-list entry and a
#: workspace folder: only the name a person reads is translated.
SVILUPPO_ID = "sviluppo"
SVILUPPO_NAMES = {"it": "sviluppo", "en": "developer"}

#: The ACP harnesses the engine may allow, in a fixed order, and never `gemini`.
ACP_HARNESSES = ("claude", "codex", "opencode")

#: The harness a squad may declare with `harness:`, and the ACP agent id it starts.
#: `agy` is absent on purpose, and `acp_agent` says why when asked for it.
HARNESS_TO_ACP: dict[str, str] = {"claude": "claude", "codex": "codex", "opencode": "opencode"}

#: The one harness that is a CLI backend and never an ACP session: Google's own client.
CLI_ONLY_HARNESS = "agy"


def sviluppo_enabled(detected: Detection, wanted: bool = True) -> bool:
    """Whether the OpenCode agent is proposed.

    Three facts, all of them: the `opencode` command is on PATH (otherwise the
    ACP harness has nothing to launch), the OpenCode Go key is detected (the
    agent runs DeepSeek through that key), and the person did not opt out.
    """
    return bool(wanted and detected.opencode and detected.opencode_go)


def acp_allowed_agents(detected: Detection) -> list[str]:
    """Which ACP harnesses this machine can actually run."""
    available = {
        "claude": detected.claude.available,
        "codex": detected.codex.available,
        "opencode": detected.opencode and detected.opencode_go,
    }
    return [harness for harness in ACP_HARNESSES if available[harness]]


def acpx_entry(detected: Detection | None = None) -> dict[str, Any]:
    """The acpx plugin block, fresh each time so no two callers share one dict.

    `approve-all` lets a non-interactive session work at all, and `deny` makes
    a prompt nobody can answer a refusal instead of a crash. When OpenCode is
    installed, the bridge launches that one (`<exe> acp`) instead of its own
    default `npx opencode-ai acp`, which is the old V1 and dies on a V2 config.
    """
    config: dict[str, Any] = {"permissionMode": "approve-all", "nonInteractivePermissions": "deny"}
    if detected is not None and detected.opencode_command:
        config["agents"] = {"opencode": {"command": detected.opencode_command, "args": ["acp"]}}
    return {"enabled": True, "config": config}


def acp_block(detected: Detection, *, sviluppo: bool = True, acpx: bool = False) -> dict[str, Any] | None:
    """The ACP bridge, only when this machine has something to bridge to."""
    if not (acpx or sviluppo_enabled(detected, sviluppo)):
        return None
    return {"enabled": True, "backend": "acpx", "allowedAgents": acp_allowed_agents(detected)}


def acp_agent(harness: Any) -> str:
    """The ACP agent id a declared `harness:` starts, or a refusal naming the reason.

    `agy` is refused by name rather than by omission: Google's own client talks to
    Google, which is why it is the one backend the engine allows as a CLI backend,
    and ACP for Antigravity would need Google's separate `agy_acp_server` -- a
    Google plan outside Google's clients, on the terms `terms.py` records.
    """
    name = str(harness or "").strip().lower()
    if name in HARNESS_TO_ACP:
        return HARNESS_TO_ACP[name]
    if name == CLI_ONLY_HARNESS:
        raise ValueError(
            f"{CLI_ONLY_HARNESS} is a CLI backend, never an ACP session: ACP for it would need "
            "Google's own agy_acp_server, which carries a Google plan outside Google's clients"
        )
    raise ValueError(f"harness without an ACP adapter: {harness!r}")


def binding(
    agent_id: str, harness: Any, workspace: str, match: Mapping[str, Any],
) -> dict[str, Any]:
    """One persistent ACP binding: the conversation a channel routes to this harness.

    `match` is the routing shape the person already wrote for that agent, copied as
    it is -- a Telegram topic, a Discord thread, a chat id. The label is the pair a
    person reads in the panel, and the harness named in it is the ACP agent id, so a
    harness with no ACP adapter is refused here instead of in a binding that cannot
    start. The runtime block alone is not a lane: without this binding a direct chat
    answers with the default model instead of the harness, which is the bug of
    2026-10-23.
    """
    agent = acp_agent(harness)
    return {
        "type": "acp",
        "agentId": str(agent_id),
        "match": dict(match),
        "acp": {"mode": "persistent", "cwd": str(workspace), "label": f"{agent_id}-{agent}"},
    }

