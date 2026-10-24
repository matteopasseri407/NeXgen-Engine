"""The OpenCode agent and the ACP bridge: one component, one decision each.

One job: say whether this machine gets the agent that runs real OpenCode one to
one, and build the two configuration blocks that make it possible. The agent
entry itself stays in `profile.py`, because it needs the workspace rules that
live there; what is here is everything that does not.

Two facts are not preferences. An agent with `runtime.type: "acp"` answers as
the harness only inside a bound ACP session: in a direct chat OpenClaw answers
with the default model instead, so the OpenCode agent is a native agent on the
Go key that opens real OpenCode sessions with `sessions_spawn` (runtime `acp`).
`gemini` never appears in the allow list: Google's terms do not allow its client
outside Google's own applications, and the doctor warns about a seat that is
already there.
"""
from __future__ import annotations

from typing import Any

from nexgen_core.openclaw.detect import Detection

#: The agent that drives real OpenCode one to one, through ACP sessions. The id never
#: changes with the language, because it is also an allow-list entry and a
#: workspace folder: only the name a person reads is translated.
SVILUPPO_ID = "sviluppo"
SVILUPPO_NAMES = {"it": "sviluppo", "en": "developer"}

#: The ACP harnesses the engine may allow, in a fixed order, and never `gemini`.
ACP_HARNESSES = ("claude", "codex", "opencode")


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
