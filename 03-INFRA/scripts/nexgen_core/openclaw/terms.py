"""Which OpenClaw settings name a Google plan, against Google's terms.

One job: name the ids, and keep them apart. A Google AI Studio key or Vertex (the
plain `google` plugin) is ordinary pay-per-use and allowed; the Gemini CLI runtime
and the Antigravity or Gemini CLI OAuth carry a consumer plan into a third-party
tool, which Google's terms forbid. The one road the person chose instead is a CLI
backend plugin that runs the official `agy` CLI as a local process: `agy` keeps the
login and the session, OpenClaw holds only the synthetic `antigravity-local-session`
marker and never sees a Google credential, so this is Google's own client talking to
Google. The doctor reports what this returns; nothing here writes.
"""
from __future__ import annotations

from typing import Any


#: What signs a Google *subscription* into OpenClaw: the Gemini CLI runtime and the
#: Antigravity/Gemini CLI OAuth. The plain `google` plugin is the AI Studio API key or
#: Vertex, which is ordinary pay-per-use and allowed.
_GOOGLE_SUBSCRIPTION_IDS = ("google-gemini-cli", "google-antigravity")

#: The one backend that is *not* a subscription, and the reason this module is
#: careful about prefixes: its id begins with a forbidden one. A CLI backend plugin
#: with this id runs `agy` on this machine, so no Google credential enters OpenClaw.
LOCAL_AGY_BACKEND = "google-antigravity-cli"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _is_subscription(value: Any) -> bool:
    """Whether this one id carries a Google plan.

    Exactly, never by prefix: `google-antigravity-cli` is a different backend that
    merely starts with `google-antigravity`, and a near miss such as a
    `google-antigravity-x` is not the approved backend either.
    """
    return str(value).strip().lower() in _GOOGLE_SUBSCRIPTION_IDS


def _model_backend(name: Any, spec: Any) -> str:
    """The backend a model entry runs on: its own runtime, or its route's prefix.

    OpenClaw selects a CLI backend from `agentRuntime.id` or from the provider prefix
    of a standalone route (`google-antigravity-cli/agy-model`), so both shapes name
    the same backend and both have to be read to tell them apart.
    """
    runtime = _dict(_dict(spec).get("agentRuntime")).get("id")
    if isinstance(runtime, str) and runtime.strip():
        return runtime
    route = str(name)
    return route.split("/", 1)[0] if "/" in route else ""


def _named_ids(config: dict[str, Any]) -> set[str]:
    """Every backend id this file names, in any of the places OpenClaw reads one."""
    plugins = _dict(config.get("plugins"))
    allow = plugins.get("allow") if isinstance(plugins.get("allow"), list) else []
    names = {str(entry) for entry in allow}
    for name, entry in _dict(plugins.get("entries")).items():
        if _dict(entry).get("enabled") is not False:
            names.add(str(name))
    models = _dict(_dict(_dict(config.get("agents")).get("defaults")).get("models"))
    names.update(_model_backend(name, spec) for name, spec in models.items())
    for profile in _dict(_dict(config.get("auth")).get("profiles")).values():
        names.add(str(_dict(profile).get("provider", "")))
    return {name.strip().lower() for name in names if name.strip()}


def google_subscriptions(config: dict[str, Any]) -> list[str]:
    """Every place where the file wires a Google plan in, by name."""
    found: list[str] = []
    allow = _dict(config.get("plugins")).get("allow")
    for entry in allow if isinstance(allow, list) else []:
        if _is_subscription(entry):
            found.append(str(entry))
    models = _dict(_dict(_dict(config.get("agents")).get("defaults")).get("models"))
    for name, spec in models.items():
        if _is_subscription(_model_backend(name, spec)):
            found.append(str(name))
    for name, profile in _dict(_dict(config.get("auth")).get("profiles")).items():
        provider = str(_dict(profile).get("provider", "")).lower()
        oauth = str(_dict(profile).get("mode", "")).lower() == "oauth" and provider.startswith("google")
        if _is_subscription(provider) or oauth:
            found.append(str(name))
    return found


def local_agy_backend(config: dict[str, Any]) -> str | None:
    """`google-antigravity-cli` when the file drives the local `agy` CLI, else None."""
    return LOCAL_AGY_BACKEND if LOCAL_AGY_BACKEND in _named_ids(config) else None
