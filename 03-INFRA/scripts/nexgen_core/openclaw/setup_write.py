"""Merging the proposal into `openclaw.json`, atomically, with a way back.

One job: take the sections the engine owns, put them inside the file that is
already in force without losing a single key the person wrote, publish the
result in one atomic step, and be able to put the previous file back. Nothing
here decides *when* that happens: the gateway must be stopped first, and that
is the orchestrator's business.

Three rules are not preferences:

- a key the person wrote outside the proposal survives, at every depth;
- `plugins.allow` is a set, so it is merged and sorted rather than replaced:
  replacing it would silently disable a plugin the person installed;
- the gateway token is generated only when there is none, and never returned
  in a printable form. `with_token` says whether it generated one; no caller
  ever receives the value.
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from nexgen_core.files import atomic_write_text
from nexgen_core.openclaw import agent_pairs
from nexgen_core.openclaw.acp import SVILUPPO_ID
from nexgen_core.openclaw.profile import PENDING_TOKEN

#: The suffix a backup carries, so the file it protects is still recognisable.
BACKUP_MARKER = ".pre-nexgen-"

#: Values an earlier engine wrote and this one no longer proposes, by path. A key
#: is removed only when it still holds exactly that value: anything the person
#: changed by hand is theirs and stays. (2026-10-23: the OpenCode agent ran on
#: an ACP runtime, which in a direct chat answered with the default model.
#: 2026-10-24: toolSearch was forced to false; OpenClaw's native structured Tool
#: Search is now preserved instead.)
RETIRED_VALUES: tuple[tuple[tuple[str, ...], Any], ...] = (
    (("agents", "entries", "sviluppo", "runtime"),
     {"type": "acp", "acp": {"agent": "opencode", "backend": "acpx", "mode": "persistent"}}),
    (("tools", "toolSearch"), False),
)


def _merge_dict(current: dict[str, Any], proposed: dict[str, Any]) -> dict[str, Any]:
    """A recursive merge that never deletes: proposal wins, absent keys stay."""
    merged = dict(current)
    for key, value in proposed.items():
        existing = merged.get(key)
        if isinstance(value, dict) and isinstance(existing, dict):
            merged[key] = _merge_dict(existing, value)
        else:
            merged[key] = value
    return merged


def _union_allow(current: Any, proposed: Any) -> list[str]:
    """`plugins.allow` as a sorted set: the union, never the proposal alone."""
    items: set[str] = set()
    for source in (current, proposed):
        if isinstance(source, list):
            items.update(str(item) for item in source)
    return sorted(items)


def _merge_models(current: Any, proposed: list[Any]) -> list[Any]:
    """Model entries merged by `id`: a model the person added is not deleted.

    The list is keyed by `id` even though OpenClaw stores it as an array, so
    replacing it whole would drop a hand-written model for the same provider.
    """
    merged = [item for item in current if isinstance(item, dict)] if isinstance(current, list) else []
    positions = {str(item.get("id")): index for index, item in enumerate(merged)}
    for item in proposed:
        if not isinstance(item, dict):
            continue
        key = str(item.get("id"))
        if key in positions:
            merged[positions[key]] = _merge_dict(merged[positions[key]], item)
        else:
            positions[key] = len(merged)
            merged.append(dict(item))
    return merged


def _merge_plugin_entries(current: Any, proposed: Any) -> Any:
    """Plugin entries merged per plugin, then the allow list unioned.

    The set-aware pass runs here too: `subagent.allowedModels` and the other
    list-valued leaves live inside this section, so a plain merge would replace
    a list the person wrote. `plugins.allow` is always written as a sorted set,
    even when there is no plugin section in force yet: an unsorted list would
    differ from the one on disk at every run and the file would be rewritten
    over and over.
    """
    if not isinstance(proposed, dict):
        return proposed
    base = current if isinstance(current, dict) else {}
    merged = _merge_sets(base, proposed, _merge_dict(base, proposed))
    if "allow" in proposed:
        merged["allow"] = _union_allow(base.get("allow"), proposed["allow"])
    return merged


#: List-valued leaves that are sets, not sequences. `alsoAllow` and `deny` are
#: one model's tool permissions; `send` is the ids one agent may message;
#: `allowedModels` is the models a plugin may name for a background run.
#: Replacing any of them whole would silently remove what the person added.
#: `fallbacks` is the ordered chain a plan hands over to: the person's own entries
#: stay first and the policy only adds what is missing.
SET_LISTS = frozenset({"alsoAllow", "deny", "send", "allowedModels", "fallbacks"})

#: One list that is a set only on a specific path. `tools.agentToAgent.allow`
#: names every agent allowed to talk, and replacing it would cut the person's
#: own agents out of a gateway that already worked.
PATH_SET_LISTS = frozenset({("tools", "agentToAgent", "allow")})


def _ordered_union(current: list[Any], proposed: list[Any]) -> list[Any]:
    """The person's entries first, then the engine's, without duplicates."""
    union: list[Any] = []
    for item in [*current, *proposed]:
        if item not in union:
            union.append(item)
    return union


def _merge_sets(current: Any, proposed: Any, merged: Any, path: tuple[str, ...] = ()) -> Any:
    """Re-merges the leaves that are sets; everywhere else the proposal won.

    `_merge_dict` replaces every non-dict value, which is right for a title and
    wrong for a permission list: this pass runs over the same shape and keeps
    both sides of the list-valued leaves named in `SET_LISTS`, plus the exact
    paths in `PATH_SET_LISTS`.
    """
    if not (isinstance(current, dict) and isinstance(proposed, dict) and isinstance(merged, dict)):
        return merged
    for key, value in proposed.items():
        child = path + (str(key),)
        if isinstance(value, dict):
            merged[key] = _merge_sets(current.get(key), value, merged.get(key), child)
        elif (
            (key in SET_LISTS or child in PATH_SET_LISTS)
            and isinstance(value, list)
            and isinstance(current.get(key), list)
        ):
            merged[key] = _ordered_union(current[key], value)
    return merged


def _binding_key(binding: Any) -> tuple[str, str]:
    """What makes two bindings the same entry: the agent and its match."""
    if not isinstance(binding, dict):
        return "", ""
    match = binding.get("match") if isinstance(binding.get("match"), dict) else {}
    return str(binding.get("agentId", "")), json.dumps(match, sort_keys=True, ensure_ascii=False, default=str)


def _merge_bindings(current: Any, proposed: Any) -> list[Any]:
    """The person's bindings first, then the ones this setup adds. Never a replacement."""
    merged = [dict(item) for item in current if isinstance(item, dict)] if isinstance(current, list) else []
    seen = {_binding_key(item) for item in merged}
    for binding in proposed if isinstance(proposed, list) else []:
        if not isinstance(binding, dict):
            continue
        key = _binding_key(binding)
        if key not in seen:
            merged.append(dict(binding))
            seen.add(key)
    return merged


def merged_document(current: dict[str, Any] | None, proposed: dict[str, Any]) -> dict[str, Any]:
    """The file to write: the person's keys, the engine's values.

    `current` is the configuration in force as a value (it may be None when
    there is no file yet); the result is a new document and neither input is
    modified.
    """
    base = dict(current) if isinstance(current, dict) else {}
    merged = _merge_sets(base, proposed, _merge_dict(base, proposed))

    if isinstance(proposed.get("plugins"), dict):
        merged["plugins"] = _merge_plugin_entries(base.get("plugins"), proposed["plugins"])

    if isinstance(proposed.get("bindings"), list):
        merged["bindings"] = _merge_bindings(base.get("bindings"), proposed["bindings"])

    proposed_providers = proposed.get("models", {}).get("providers")
    if isinstance(proposed_providers, dict):
        merged_models = dict(merged.get("models") or {})
        providers = dict(merged_models.get("providers") or {})
        current_providers = base.get("models", {}).get("providers") if isinstance(base.get("models"), dict) else {}
        for provider, block in proposed_providers.items():
            if not isinstance(block, dict) or not isinstance(block.get("models"), list):
                continue
            existing = current_providers.get(provider) if isinstance(current_providers, dict) else None
            current_list = existing.get("models") if isinstance(existing, dict) else None
            merged_block = _merge_dict(providers.get(provider) or {}, block)
            merged_block["models"] = _merge_models(current_list, block["models"])
            providers[provider] = merged_block
        merged_models["providers"] = providers
        merged["models"] = merged_models
    _keep_model_fallbacks(base, merged)
    _drop_uncreated_agent(merged)
    _retire_replaced(merged, proposed)
    _retire(merged)
    agent_pairs.retire(merged, proposed)
    _drop_squad_model_entries(merged)
    return merged


def _retire_replaced(document: dict[str, Any], proposed: dict[str, Any]) -> None:
    """Drops the legacy keys of an owned section when the proposal writes their replacement.

    The merge keeps every key the person wrote, at every depth, and that is
    right until OpenClaw retires one: a file that still carries `security` and
    `ask` would then keep them beside the `mode` the proposal writes, and the
    schema refuses exactly that pair (`mode cannot be combined with security or
    ask in the same exec object`, zod-schema.agent-runtime in 2026.10.6, enforced
    by `config validate`). The engine cannot tell a key the person wrote from one
    an older OpenClaw left behind, so it removes neither on its own: only where
    its own replacement lands, which is what this pass does.
    """
    tools = proposed.get("tools") if isinstance(proposed.get("tools"), dict) else None
    exec_proposed = tools.get("exec") if isinstance(tools, dict) else None
    if not isinstance(exec_proposed, dict) or "mode" not in exec_proposed:
        return
    merged_tools = document.get("tools") if isinstance(document.get("tools"), dict) else None
    merged_exec = merged_tools.get("exec") if isinstance(merged_tools, dict) else None
    if not isinstance(merged_exec, dict):
        return
    for legacy in ("security", "ask"):
        merged_exec.pop(legacy, None)


def _keep_model_fallbacks(base: dict[str, Any], merged: dict[str, Any]) -> None:
    """A fallback chain the person wrote by hand survives a proposal that names none.

    OpenClaw stores an agent's model as `{primary, fallbacks}` once someone edits
    the fallback chips in its panel, while the engine proposes a plain string. A
    replacement would delete those chains silently, on the next setup, and the
    person would only notice when a spent plan stopped a turn again. The proposal
    wins on everything else about the model; the chains are the person's.
    """
    current_agents = base.get("agents", {}).get("entries") if isinstance(base.get("agents"), dict) else None
    merged_agents = merged.get("agents", {}).get("entries") if isinstance(merged.get("agents"), dict) else None
    if not isinstance(current_agents, dict) or not isinstance(merged_agents, dict):
        return
    for agent_id, entry in current_agents.items():
        before = entry.get("model") if isinstance(entry, dict) else None
        chain = before.get("fallbacks") if isinstance(before, dict) else None
        if not isinstance(chain, list) or not chain:
            continue
        target = merged_agents.get(agent_id)
        model = target.get("model") if isinstance(target, dict) else None
        if isinstance(model, dict):
            if not model.get("fallbacks"):
                target["model"] = {**model, "fallbacks": list(chain)}
        elif isinstance(model, str) and model.strip():
            target["model"] = {"primary": model, "fallbacks": list(chain)}


def _without(block: dict[str, Any], key: str) -> list[Any] | None:
    """`block[key]` without the agent this run does not create, or None to leave it be."""
    values = block.get(key)
    if not isinstance(values, list):
        return None
    kept = [value for value in values if str(value) != SVILUPPO_ID]
    return None if len(kept) == len(values) else kept


def _drop_uncreated_agent(document: dict[str, Any]) -> None:
    """Takes the OpenCode agent out of the permission lists while no entry declares it.

    `sviluppo` is the one agent the engine can decide not to create (`--no-sviluppo`,
    no `opencode` command, or no Go key), and an earlier run or the squad's own
    `send` may still name it. OpenClaw cannot route an id no `agents.entries`
    declares, so the id goes; an agent the person added to the same list, and the
    `*` pattern a person may write, are not the engine's to remove.

    The branches that change are rebuilt rather than edited in place: a section
    only the proposal carries is the proposal's own object, and editing it would
    change the caller's document, which this module promises never to do.
    """
    agents = document.get("agents")
    entries = agents.get("entries") if isinstance(agents, dict) else None
    if not isinstance(entries, dict) or SVILUPPO_ID in entries:
        return
    tools = document.get("tools")
    block = tools.get("agentToAgent") if isinstance(tools, dict) else None
    kept = _without(block, "allow") if isinstance(block, dict) else None
    if kept is not None:
        document["tools"] = {**tools, "agentToAgent": {**block, "allow": kept}}
    pruned: dict[str, Any] = {}
    for agent_id, entry in entries.items():
        inner = entry.get("tools") if isinstance(entry, dict) else None
        partners = inner.get("agentToAgent") if isinstance(inner, dict) else None
        send = _without(partners, "send") if isinstance(partners, dict) else None
        if send is not None:
            pruned[agent_id] = {**entry, "tools": {**inner, "agentToAgent": {**partners, "send": send}}}
    if pruned:
        document["agents"] = {**agents, "entries": {**entries, **pruned}}


def _retire(document: dict[str, Any]) -> None:
    """Drops what an earlier engine wrote and no longer proposes, if still untouched."""

    for path, value in RETIRED_VALUES:
        parent: Any = document
        for key in path[:-1]:
            parent = parent.get(key) if isinstance(parent, dict) else None
        if isinstance(parent, dict) and path[-1] in parent:
            target = parent.get(path[-1])
            if isinstance(value, bool):
                if isinstance(target, bool) and target == value:
                    del parent[path[-1]]
            elif target == value:
                del parent[path[-1]]


def _drop_squad_model_entries(document: dict[str, Any]) -> None:
    """Takes the `models.entries` an earlier engine wrote out of the file.

    The squad's `models` table belongs in `agents.defaults.models`; before that was
    fixed the engine merged it into a root `models.entries` instead, and OpenClaw
    refuses the whole file over it ("models: Unrecognized key: entries"): the root
    `models` object accepts only `mode`, `providers` and `catalogRefresh`. No version
    of OpenClaw ever read that key, so nothing a person wrote can live in it, and
    removing it is how a machine broken by the old engine heals on the next run.
    """
    models = document.get("models")
    if isinstance(models, dict) and "entries" in models:
        # Rebuilt rather than edited in place: a section only the proposal carries is the
        # proposal's own object, and this module promises never to change its caller's input.
        document["models"] = {key: value for key, value in models.items() if key != "entries"}


def generate_token() -> str:
    """A URL-safe secret, 32 bytes of entropy, generated only when missing."""
    return secrets.token_urlsafe(32)


def with_token(
    document: dict[str, Any],
    token_factory: Callable[[], str] = generate_token,
) -> tuple[dict[str, Any], bool]:
    """The document with a real gateway token, and whether one was generated.

    An existing token is the person's and is kept; the placeholder a plan
    prints (or a file left behind by an interrupted run) counts as missing.
    The value is returned inside the document only, never to a printer.
    """
    gateway = document.get("gateway")
    auth = gateway.get("auth") if isinstance(gateway, dict) else None
    if not isinstance(auth, dict):
        return document, False
    token = auth.get("token")
    if isinstance(token, str) and token.strip() and token != PENDING_TOKEN:
        return document, False
    return {**document, "gateway": {**gateway, "auth": {**auth, "token": token_factory()}}}, True


def render(document: dict[str, Any]) -> str:
    """The bytes `openclaw.json` receives: JSON, one key per line, trailing LF."""
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def backup_path(path: Path, stamp: str) -> Path:
    """Where the copy of the previous file lives: beside it, with the stamp."""
    return path.with_name(f"{path.name}{BACKUP_MARKER}{stamp}")


def _copy_private(source: Path, target: Path) -> None:
    """Copy complete bytes beside the target, then publish them in one rename.

    `shutil.copy2` writes into the destination as it reads: a crash during the
    emergency restore left `openclaw.json` half written. The temp file is
    fsynced and published with `os.replace`, so the destination is either the
    old file or the new one, never a prefix of either.
    """
    fd, name = tempfile.mkstemp(prefix=f"{target.name}.", suffix=".tmp", dir=target.parent)
    tmp = Path(name)
    try:
        with os.fdopen(fd, "wb") as out, source.open("rb") as src:
            shutil.copyfileobj(src, out)
            out.flush()
            os.fsync(out.fileno())
        _private_mode(tmp)
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def backup_file(path: Path, stamp: str) -> Path | None:
    """Copy the file in force aside; None when there is nothing to protect."""
    if not path.is_file():
        return None
    target = backup_path(path, stamp)
    _copy_private(path, target)
    return target


def _private_mode(path: Path) -> None:
    """0600 on POSIX: the file holds the gateway token, and so does its backup.

    `copy2` gives the backup the source's mode, which is 0644 when
    `openclaw onboard` created it; on POSIX that is every local user reading
    the token. Windows has no POSIX mode to set here.
    """
    if os.name != "nt":
        os.chmod(path, 0o600)


def restore_file(path: Path, backup: Path | None) -> None:
    """Put the previous file back, or remove the one this run created."""
    if backup is not None and backup.is_file():
        _copy_private(backup, path)
        return
    path.unlink(missing_ok=True)


def write_document(path: Path, text: str) -> None:
    """Publish the document in one atomic step, private from the first byte.

    `preserve_mode=False`: the temporary file comes from `mkstemp` at 0600,
    and the previous file's bits are never carried across, so a 0644 left by
    `openclaw onboard` cannot reappear on a file that now holds the token.
    """
    atomic_write_text(path, text, preserve_mode=False)
    _private_mode(path)
