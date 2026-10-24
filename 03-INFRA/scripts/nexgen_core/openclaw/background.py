"""The model each unattended job runs, and the proposal that keeps it off a local one.

One job: decide which model OpenClaw's background work uses when the agents'
default model is local, and say what to write so it uses a cloud model instead.

Why this exists. OpenClaw resolves dreaming, compaction, the pre-compaction
memory flush and active memory through the agent's default model when their own
key is unset. On a machine whose default is a local model, the 03:00 dreaming
sweep, an automatic compaction and a recall all load that model while nobody is
watching: the person chose a local model for a conversation, not for a sweep that
takes the whole computer for minutes.

The keys, taken from the installed 2026.10.6 documentation and not from memory:

- `plugins.entries.memory-core.config.dreaming.model`, which needs
  `plugins.entries.memory-core.subagent.allowModelOverride: true`, and
  `allowedModels` to narrow the targets;
- `agents.defaults.compaction.model` and `agents.defaults.compaction.memoryFlush.model`,
  neither of which needs a gate;
- `plugins.entries.active-memory.config.model`, written with the same gate so a
  recall may name it explicitly.

Three rules are not preferences:

- a key the person already set is theirs, at every one of these paths, and is
  never replaced;
- an explicit `allowModelOverride: false` is a decision, not a gap: without the
  gate OpenClaw refuses the model, so nothing is proposed for that plugin;
- `ollama` and `llama-cpp` are the only two local providers `local_models` can
  produce, so the provider a ref names is enough to tell local from cloud and
  neither module needs the other.
"""
from __future__ import annotations

from typing import Any

from nexgen_core.i18n import t
from nexgen_core.report import CheckOutcome, Severity

#: The providers `local_models` serves from: a ref under one of these is a model
#: on this machine, whatever the detection answered this time.
LOCAL_PROVIDERS = ("ollama", "llama-cpp")

#: The order the agents are read in when looking for a cloud model: `main` first,
#: because it is the agent a person already uses most and its model is the one
#: the engine put on a cloud subscription when there was one.
AGENT_ORDER = ("main", "capo", "automazioni", "sviluppo")

#: The four jobs this module speaks about, with the words a person reads.
JOBS = {
    "dreaming": "dreaming",
    "compaction": "compaction",
    "memory_flush": "the memory flush",
    "active_memory": "active memory",
}


def is_local(ref: str | None) -> bool:
    """Whether a `provider/model` ref names a model served from this machine."""
    return bool(ref) and str(ref).split("/", 1)[0] in LOCAL_PROVIDERS


def _at(document: Any, path: tuple[str, ...]) -> Any:
    """One nested value, or None when any step of the path is missing."""
    current = document
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _named(value: Any) -> str | None:
    """The ref written in a value, or None when it is absent, empty or not a string."""
    return value.strip() if isinstance(value, str) and value.strip() else None


def agent_model_refs(config: dict[str, Any] | None) -> dict[str, str]:
    """Every agent's model, read from the string, object or ACP-primary shape."""
    entries = _at(config, ("agents", "entries"))
    if not isinstance(entries, dict):
        return {}
    refs: dict[str, str] = {}
    for name, entry in entries.items():
        model = entry.get("model") if isinstance(entry, dict) else entry
        if isinstance(model, dict):
            # An ACP agent selects its harness model through `model.primary`.
            model = model.get("primary")
        ref = _named(model)
        if ref:
            refs[str(name)] = ref
    return refs


def resolved_models(config: dict[str, Any] | None, planned: dict[str, str] | None = None) -> dict[str, str]:
    """Every agent's model as the file will read after a write: the plan wins, the rest stays."""
    resolved = agent_model_refs(config)
    resolved.update({agent: ref for agent, ref in (planned or {}).items() if _named(ref)})
    return resolved


def default_model(config: dict[str, Any] | None, planned: dict[str, str] | None = None) -> str | None:
    """The model an unattended job runs when its own key is not set.

    OpenClaw resolves it through the system agent when one is configured, then
    through `agents.defaults.model.primary`, then through the agent that exists;
    `main` is the agent the engine always creates, so it is the last answer.
    """
    resolved = resolved_models(config, planned)
    system = _named(_at(config, ("agents", "defaults", "systemAgent", "agentId")))
    if system and system in resolved:
        return resolved[system]
    primary = _named(_at(config, ("agents", "defaults", "model", "primary")))
    if primary:
        return primary
    return resolved.get("main")


def cloud_model(config: dict[str, Any] | None, planned: dict[str, str] | None = None) -> str | None:
    """The first model an agent already runs that is not local, in a stable order."""
    resolved = resolved_models(config, planned)
    ordered = [agent for agent in AGENT_ORDER if agent in resolved]
    ordered.extend(sorted(agent for agent in resolved if agent not in AGENT_ORDER))
    for agent in ordered:
        ref = resolved[agent]
        if not is_local(ref):
            return ref
    return None


def _gate(subagent: Any, cloud: str) -> dict[str, Any] | None:
    """The override permission OpenClaw needs, or None when the person already said no."""
    block = subagent if isinstance(subagent, dict) else {}
    allowed = block.get("allowModelOverride")
    if allowed is not None and allowed is not True:
        return None
    gate: dict[str, Any] = {}
    if allowed is not True:
        gate["allowModelOverride"] = True
    # Always proposed: the merge unions it, so the person's own targets survive
    # and an allowlist that did not name this model is widened instead of replaced.
    gate["allowedModels"] = [cloud]
    return gate


def _dreaming(current: dict[str, Any] | None, cloud: str) -> dict[str, Any] | None:
    """The dreaming sweep's model, or None when the person owns this decision."""
    entry = _at(current, ("plugins", "entries", "memory-core"))
    entry = entry if isinstance(entry, dict) else {}
    dreaming = _at(entry, ("config", "dreaming"))
    dreaming = dreaming if isinstance(dreaming, dict) else {}
    if dreaming.get("enabled") is False or _named(dreaming.get("model")):
        return None
    gate = _gate(entry.get("subagent"), cloud)
    return None if gate is None else {"subagent": gate, "config": {"dreaming": {"model": cloud}}}


def _active_memory_enabled(current: dict[str, Any] | None) -> bool:
    """Whether the active-memory plugin is switched on: only then does its model matter."""
    entry = _at(current, ("plugins", "entries", "active-memory"))
    entry = entry if isinstance(entry, dict) else {}
    allow = _at(current, ("plugins", "allow"))
    named = isinstance(allow, list) and "active-memory" in {str(item) for item in allow}
    return entry.get("enabled") is True or (named and entry.get("enabled") is not False)


def _active_memory(current: dict[str, Any] | None, cloud: str) -> dict[str, Any] | None:
    """The recall model, written only for a plugin this machine actually runs."""
    if not _active_memory_enabled(current):
        return None
    entry = _at(current, ("plugins", "entries", "active-memory"))
    entry = entry if isinstance(entry, dict) else {}
    config = entry.get("config") if isinstance(entry.get("config"), dict) else {}
    if _named(config.get("model")):
        return None
    gate = _gate(entry.get("subagent"), cloud)
    return None if gate is None else {"subagent": gate, "config": {"model": cloud}}


def _compaction(current: dict[str, Any] | None, cloud: str) -> dict[str, Any] | None:
    """The two compaction models, each only where the person left the key empty."""
    block = _at(current, ("agents", "defaults", "compaction"))
    block = block if isinstance(block, dict) else {}
    flush = block.get("memoryFlush") if isinstance(block.get("memoryFlush"), dict) else {}
    proposed: dict[str, Any] = {}
    if not _named(block.get("model")):
        proposed["model"] = cloud
    if not _named(flush.get("model")):
        proposed["memoryFlush"] = {"model": cloud}
    return proposed or None


def blocks(planned: dict[str, str], current: dict[str, Any] | None) -> dict[str, Any]:
    """The sections that move the background jobs off a local default, or nothing.

    Nothing is proposed when the default is already cloud, when no agent has a
    cloud model to offer, or when every key involved is the person's own.
    """
    if not is_local(default_model(current, planned)):
        return {}
    cloud = cloud_model(current, planned)
    if cloud is None:
        return {}
    entries: dict[str, Any] = {}
    for plugin, block in (("memory-core", _dreaming(current, cloud)),
                          ("active-memory", _active_memory(current, cloud))):
        if block is not None:
            entries[plugin] = block
    sections: dict[str, Any] = {}
    if entries:
        sections["plugins"] = {"entries": entries}
    compaction = _compaction(current, cloud)
    if compaction is not None:
        sections["agents"] = {"defaults": {"compaction": compaction}}
    return sections


def jobs(config: dict[str, Any] | None) -> dict[str, str | None]:
    """Which model each job would run now, with its own key when set and the default otherwise."""
    fallback = default_model(config)
    chosen = {
        "dreaming": _named(_at(config, ("plugins", "entries", "memory-core", "config", "dreaming", "model"))),
        "compaction": _named(_at(config, ("agents", "defaults", "compaction", "model"))),
        "memory_flush": _named(_at(config, ("agents", "defaults", "compaction", "memoryFlush", "model"))),
        "active_memory": _named(_at(config, ("plugins", "entries", "active-memory", "config", "model"))),
    }
    return {job: (ref or fallback) for job, ref in chosen.items()}


def local_jobs(config: dict[str, Any] | None) -> dict[str, str]:
    """The jobs that would load a model from this machine, and which one."""
    return {job: ref for job, ref in jobs(config).items() if is_local(ref)}


def report(config: dict[str, Any] | None) -> CheckOutcome:
    """The doctor's one line: whether an unattended job would wake a local model."""
    local = local_jobs(config)
    if not local:
        return CheckOutcome(
            id="openclaw.background_model",
            severity=Severity.OK,
            message=t("No background job runs on a local model."),
        )
    names = sorted({t(JOBS[job]) for job in local})
    refs = sorted(set(local.values()))
    return CheckOutcome(
        id="openclaw.background_model",
        severity=Severity.WARN,
        message=t("These background jobs would run on the local model {models}: {jobs}.",
                  models=", ".join(refs), jobs=", ".join(names)),
        action=t("Run 'nexgen openclaw setup': it points them at the cloud model an agent already uses."),
    )
