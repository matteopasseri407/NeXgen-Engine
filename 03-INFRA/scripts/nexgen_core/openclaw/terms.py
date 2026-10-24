"""Which OpenClaw settings sign a Google subscription in, against Google's terms.

One job: name them. A Google AI Studio key or Vertex (the plain `google` plugin) is
ordinary pay-per-use and allowed; the Gemini CLI runtime and the Antigravity or
Gemini CLI OAuth carry a consumer plan into a third-party tool, which Google's terms
forbid. The doctor reports what this returns; nothing here writes.
"""
from __future__ import annotations

from typing import Any


#: What signs a Google *subscription* into OpenClaw: the Gemini CLI runtime and the
#: Antigravity/Gemini CLI OAuth. The plain `google` plugin is the AI Studio API key or
#: Vertex, which is ordinary pay-per-use and allowed.
_GOOGLE_SUBSCRIPTION_IDS = ("google-gemini-cli", "google-antigravity")


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def google_subscriptions(config: dict[str, Any]) -> list[str]:
    """Every place where the file wires a Google plan in, by name."""
    found: list[str] = []
    allow = _dict(config.get("plugins")).get("allow")
    for entry in allow if isinstance(allow, list) else []:
        if str(entry).lower() in _GOOGLE_SUBSCRIPTION_IDS:
            found.append(str(entry))
    models = _dict(_dict(_dict(config.get("agents")).get("defaults")).get("models"))
    for name, spec in models.items():
        if str(_dict(_dict(spec).get("agentRuntime")).get("id", "")).lower() in _GOOGLE_SUBSCRIPTION_IDS:
            found.append(str(name))
    for name, profile in _dict(_dict(config.get("auth")).get("profiles")).items():
        provider = str(_dict(profile).get("provider", "")).lower()
        oauth = str(_dict(profile).get("mode", "")).lower() == "oauth" and provider.startswith("google")
        if provider in _GOOGLE_SUBSCRIPTION_IDS or oauth:
            found.append(str(name))
    return found
