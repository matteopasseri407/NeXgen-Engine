"""The model each unattended job runs, and the two decisions that keep them cheap.

One job: say whether OpenClaw's background work runs at all, which model it uses
when the agents' default model is local, and what to write for both.

Why this exists. OpenClaw resolves dreaming, compaction, the pre-compaction
memory flush and active memory through the agent's default model when their own
key is unset. On a machine whose default is a local model, the 03:00 dreaming
sweep, an automatic compaction and a recall all load that model while nobody is
watching: the person chose a local model for a conversation, not for a sweep that
takes the whole computer for minutes.

Dreaming is a different question, and its default is slower. Upstream runs it
unless somebody stops it (`plugins.entries.memory-core.config.dreaming.enabled`,
default `true`, cadence `0 3 * * *`), and one sweep is a light, REM and deep phase
over the recent notes and sessions on whatever model the sweep resolves: on a
metered plan that is a nightly bill nobody asked for, and on an always-on bot it
grows with the memory. The engine writes `frequency: "0 3 * * 0"` where the file
says nothing, so the memory consolidation still happens and is paid for on one
night in seven. `enabled: true` (upstream's own nightly cadence, kept on purpose),
a `frequency` and a `model` of the person's are that decision and are never
overwritten; `enabled: false` is the person who wants no sweep at all.

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
from nexgen_core.openclaw import claude_cli
from nexgen_core.report import CheckOutcome, Severity

#: The providers `local_models` serves from: a ref under one of these is a model
#: on this machine, whatever the detection answered this time.
LOCAL_PROVIDERS = ("ollama", "llama-cpp")

#: CLI harness backends that cannot do isolated completion.
CLI_HARNESS_PROVIDERS = frozenset({"google-antigravity-cli", "claude-cli", "codex"})

#: One sweep a week, Sunday at 03:00, where the file states no cadence of its own. The
#: memory consolidation still happens, and it is paid for on one night in seven.
DREAMING_WEEKLY = "0 3 * * 0"

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


def is_cli_harness(ref: str | None, config: dict[str, Any] | None = None, agent: str = "") -> bool:
    """Whether a model ref runs on a CLI harness backend that cannot do isolated completion."""
    return cli_harness_runtime(config, ref, agent) is not None


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


def cli_harness_runtime(config: dict[str, Any] | None, ref: str | None, agent: str = "") -> str | None:
    """The CLI-harness backend OpenClaw would run this ref on, or None.

    Two shapes name a backend, and only one of them is a prefix. A standalone route
    carries the backend as its provider (`google-antigravity-cli/agy-model`); a Claude
    Code or Codex agent keeps an ordinary `anthropic/...` or `openai/...` ref and pins
    the backend in `agentRuntime`, which is the shape upstream recommends
    (docs.openclaw.ai/gateway/config-agents/runtime-and-cli-backends). So the prefix
    alone cannot see two of the three harnesses, and the ref's runtime is resolved
    through `claude_cli.runtime_id`, the resolver the rest of the engine already uses.

    With no `agent` every agent's pin counts: a job key such as
    `agents.defaults.compaction.model` names no agent, and the job must not land on a
    harness whichever agent the pin was written for.
    """
    text = str(ref or "").strip()
    if not text:
        return None
    provider = text.split("/", 1)[0]
    if provider in CLI_HARNESS_PROVIDERS:
        return provider
    if not config:
        return None
    names = [agent] if agent else [""]
    entries = _at(config, ("agents", "entries"))
    if not agent and isinstance(entries, dict):
        names.extend(str(name) for name in entries)
    for name in names:
        runtime = claude_cli.runtime_id(config, name, text)
        if runtime in CLI_HARNESS_PROVIDERS:
            return runtime
    return None


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


def agent_model_chains(config: dict[str, Any] | None) -> dict[str, list[str]]:
    """Every agent's ordered model chain (primary and fallbacks)."""
    entries = _at(config, ("agents", "entries"))
    if not isinstance(entries, dict):
        return {}
    chains: dict[str, list[str]] = {}
    for name, entry in entries.items():
        model = entry.get("model") if isinstance(entry, dict) else entry
        if isinstance(model, dict):
            primary = _named(model.get("primary"))
            fallbacks = [_named(f) for f in model.get("fallbacks", []) if _named(f)]
            chain = [primary] if primary else []
            chain.extend([f for f in fallbacks if f and f not in chain])
            if chain:
                chains[str(name)] = chain
        else:
            ref = _named(model)
            if ref:
                chains[str(name)] = [ref]
    return chains


def cloud_model(config: dict[str, Any] | None, planned: dict[str, str] | None = None) -> str | None:
    """The first model an agent already runs that is not local and not a CLI harness."""
    chains = agent_model_chains(config)
    if planned:
        for agent, ref in planned.items():
            named_ref = _named(ref)
            if named_ref:
                existing = chains.get(agent, [])
                chains[agent] = [named_ref] + [m for m in existing if m != named_ref]

    ordered = [agent for agent in AGENT_ORDER if agent in chains]
    ordered.extend(sorted(agent for agent in chains if agent not in AGENT_ORDER))
    for agent in ordered:
        for ref in chains[agent]:
            if not is_local(ref) and not is_cli_harness(ref, config, agent):
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


def _memory_core_on(current: dict[str, Any] | None) -> bool:
    """Whether the memory-core plugin is switched on: only then does its dreaming sweep exist."""
    entry = _at(current, ("plugins", "entries", "memory-core"))
    entry = entry if isinstance(entry, dict) else {}
    allow = _at(current, ("plugins", "allow"))
    named = isinstance(allow, list) and "memory-core" in {str(item) for item in allow}
    return entry.get("enabled") is True or (named and entry.get("enabled") is not False)


def _dreaming_settings(config: dict[str, Any] | None) -> dict[str, Any]:
    """`plugins.entries.memory-core.config.dreaming`, or an empty mapping."""
    entry = _at(config, ("plugins", "entries", "memory-core"))
    dreaming = _at(entry if isinstance(entry, dict) else None, ("config", "dreaming"))
    return dreaming if isinstance(dreaming, dict) else {}


def dreaming_asked_for(config: dict[str, Any] | None) -> bool:
    """Whether the person configured the sweep themselves: `enabled`, a model, or a cadence.

    Any of the three is a decision about a job the engine does not start: `enabled: true`
    with upstream's own `0 3 * * *` is the old nightly sweep kept on purpose, a `frequency`
    is a cadence of their own, and a `model` names what it runs on. None of them is
    overwritten, and the engine's own weekly cadence applies only where all three are
    missing. A plugin that is not loaded runs no sweep, so that machine is not asked.
    """
    dreaming = _dreaming_settings(config)
    asked = (dreaming.get("enabled") is True or bool(_named(dreaming.get("model")))
             or bool(_named(dreaming.get("frequency"))))
    return asked and _memory_core_on(config)


def _dreaming(current: dict[str, Any] | None, cloud: str | None) -> dict[str, Any] | None:
    """The sweep: weekly where the file says nothing, and off the local model if asked for.

    Upstream runs dreaming every night at 03:00 unless somebody stops it, over the recent
    notes and sessions, on whatever model the sweep resolves: on a metered plan that is a
    nightly bill nobody asked for, and it grows with the memory. One sweep a week keeps
    the consolidation and pays for it on one night in seven. `enabled: false` is the
    person who wants no sweep at all, and anything they configured themselves wins.
    """
    if not _memory_core_on(current):
        # Nothing is proposed inside a plugin that is not loaded: an entry there would be a
        # key the engine opens for a job that cannot run, and OpenClaw's doctor would then
        # add the plugin to `plugins.allow` for it.
        return None
    dreaming = _dreaming_settings(current)
    if dreaming.get("enabled") is False:
        return None
    proposed: dict[str, Any] = {}
    if not dreaming_asked_for(current):
        proposed["frequency"] = DREAMING_WEEKLY
    if cloud is not None and not _named(dreaming.get("model")):
        entry = _at(current, ("plugins", "entries", "memory-core"))
        entry = entry if isinstance(entry, dict) else {}
        gate = _gate(entry.get("subagent"), cloud)
        if gate is not None:
            proposed["model"] = cloud
            return {"subagent": gate, "config": {"dreaming": proposed}}
    return {"config": {"dreaming": proposed}} if proposed else None


def _active_memory_enabled(current: dict[str, Any] | None) -> bool:
    """Whether the active-memory plugin is switched on: only then does its model matter."""
    entry = _at(current, ("plugins", "entries", "active-memory"))
    entry = entry if isinstance(entry, dict) else {}
    allow = _at(current, ("plugins", "allow"))
    named = isinstance(allow, list) and "active-memory" in {str(item) for item in allow}
    return entry.get("enabled") is True or (named and entry.get("enabled") is not False)


def _active_memory(current: dict[str, Any] | None, cloud: str | None) -> dict[str, Any] | None:
    """The recall model, written only for a plugin this machine actually runs."""
    if cloud is None or not _active_memory_enabled(current):
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
    """The sections that keep the unattended jobs cheap, or nothing.

    Two decisions that do not depend on each other. Dreaming is proposed on every machine
    that loads memory-core, because it runs by itself: one sweep a week where nobody said
    otherwise. The other three move to the cloud model an agent already uses only where the
    default is local or a CLI harness, because there is nothing to move when the default is
    already direct cloud.
    """
    default = default_model(current, planned)
    cloud = cloud_model(current, planned) if is_local(default) or is_cli_harness(default, current) else None
    entries: dict[str, Any] = {}
    for plugin, block in (("memory-core", _dreaming(current, cloud)),
                          ("active-memory", _active_memory(current, cloud))):
        if block is not None:
            entries[plugin] = block
    sections: dict[str, Any] = {}
    if entries:
        sections["plugins"] = {"entries": entries}
    compaction = _compaction(current, cloud) if cloud else None
    if compaction is not None:
        sections["agents"] = {"defaults": {"compaction": compaction}}
    return sections


def jobs(config: dict[str, Any] | None) -> dict[str, str | None]:
    """Which model each job would run now, and None for a job that does not run at all."""
    fallback = default_model(config)
    chosen = {
        "dreaming": _named(_at(config, ("plugins", "entries", "memory-core", "config", "dreaming", "model"))),
        "compaction": _named(_at(config, ("agents", "defaults", "compaction", "model"))),
        "memory_flush": _named(_at(config, ("agents", "defaults", "compaction", "memoryFlush", "model"))),
        "active_memory": _named(_at(config, ("plugins", "entries", "active-memory", "config", "model"))),
    }
    resolved = {job: (ref or fallback) for job, ref in chosen.items()}
    if _dreaming_settings(config).get("enabled") is False or not _memory_core_on(config):
        # A sweep that cannot happen: naming a model for it would report a job that is not
        # there, and the doctor reads this mapping.
        resolved["dreaming"] = None
    return resolved


def local_jobs(config: dict[str, Any] | None) -> dict[str, str]:
    """The jobs that would load a model from this machine, and which one."""
    return {job: ref for job, ref in jobs(config).items() if is_local(ref)}


def harness_jobs(config: dict[str, Any] | None) -> dict[str, str]:
    """The jobs that would run on a CLI harness backend that cannot do isolated completion."""
    return {job: ref for job, ref in jobs(config).items() if is_cli_harness(ref, config)}


def report(config: dict[str, Any] | None) -> CheckOutcome:
    """The doctor's one line: whether an unattended job would wake a local model or a CLI harness.

    Both faults are in the line when both are there: they do not depend on each other,
    and a machine can carry both at once, so an early return would report the first and
    hide the second until somebody fixed it and looked again.
    """
    harness = harness_jobs(config)
    local = local_jobs(config)
    if not harness and not local:
        return CheckOutcome(
            id="openclaw.background_model",
            severity=Severity.OK,
            message=t("No background job runs on a local model."),
        )
    problems: list[str] = []
    if harness:
        problems.append(t(
            "These background jobs would run on a CLI harness that does not support isolated completion ({models}): {jobs}.",
            models=", ".join(sorted(set(harness.values()))),
            jobs=", ".join(sorted({t(JOBS[job]) for job in harness})),
        ))
    if local:
        problems.append(t(
            "These background jobs would run on the local model {models}: {jobs}.",
            models=", ".join(sorted(set(local.values()))),
            jobs=", ".join(sorted({t(JOBS[job]) for job in local})),
        ))
    return CheckOutcome(
        id="openclaw.background_model",
        severity=Severity.WARN,
        message=" ".join(problems),
        # The harness half needs a direct model to exist before any setup can point a
        # job at it, so that is the one action when both halves are present.
        action=t("Configure a direct cloud model or fallback under agents.entries.main.model that is not a CLI harness.")
        if harness else
        t("Run 'nexgen openclaw setup': it points them at the cloud model an agent already uses."),
    )
