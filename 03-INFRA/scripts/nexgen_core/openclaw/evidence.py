"""What the evidence check watches, and the one line that says whether it can run.

One job: turn the configuration in force into the `evidence` section the plugin reads, read that
section back, and answer the doctor with a single sentence. The list of providers is derived here
and nowhere else, for the same reason the cage is derived in one place: the plugin must never
decide for itself what "local" means, and a cloud model must never be checked by accident.

The switch is deposited, not hand-written. `nexgen.config.json` beside the plugin is the one file
the engine owns that OpenClaw does not read at startup, so a change of models takes effect on the
next call without touching `openclaw.json` under a running gateway.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nexgen_core.i18n import t
from nexgen_core.openclaw.local_models import LOCAL_PROVIDERS
from nexgen_core.report import CheckOutcome, Severity

#: The engine-owned file the plugin reads its switches from, and the section that holds this one.
PLUGIN_CONFIG_NAME = "nexgen.config.json"
SECTION = "evidence"


def _providers_block(config: dict[str, Any]) -> dict[str, Any]:
    """`models.providers` as a mapping, or an empty one."""
    models = config.get("models") if isinstance(config.get("models"), dict) else {}
    providers = models.get("providers") if isinstance(models.get("providers"), dict) else {}
    return providers


def _agent_model_refs(config: dict[str, Any]) -> list[str]:
    """Every agent's model string, whatever shape the entry uses."""
    agents = config.get("agents") if isinstance(config.get("agents"), dict) else {}
    entries = agents.get("entries") if isinstance(agents.get("entries"), dict) else {}
    refs: list[str] = []
    for entry in entries.values():
        model = entry.get("model") if isinstance(entry, dict) else entry
        if isinstance(model, dict):
            # An ACP agent selects its harness model through `model.primary`.
            model = model.get("primary")
        if isinstance(model, str) and model.strip():
            refs.append(model.strip())
    return refs


def local_refs(config: dict[str, Any] | None) -> list[str]:
    """Every local model the configuration can actually name, provider-qualified.

    Both places count, because OpenClaw uses both: a provider block lists the models the provider
    serves, and an agent names the one it runs. A model is local when its provider is one of
    `LOCAL_PROVIDERS`; anything else, including an unknown provider, is not this check's business.
    """
    if not isinstance(config, dict):
        return []
    refs: list[str] = []
    for provider, block in _providers_block(config).items():
        if str(provider) not in LOCAL_PROVIDERS:
            continue
        models = block.get("models") if isinstance(block, dict) else None
        for item in models if isinstance(models, list) else []:
            model_id = item.get("id") if isinstance(item, dict) else item
            if isinstance(model_id, str) and model_id:
                refs.append(f"{provider}/{model_id}")
    refs.extend(ref for ref in _agent_model_refs(config) if ref.split("/", 1)[0] in LOCAL_PROVIDERS)
    return sorted(set(refs))


def local_providers(config: dict[str, Any] | None) -> list[str]:
    """The local providers that have at least one model, sorted."""
    return sorted({ref.split("/", 1)[0] for ref in local_refs(config)})


def evidence_section(config: dict[str, Any] | None) -> dict[str, Any]:
    """The section the plugin reads: on when this machine serves a local model, and for which.

    A machine with no local model gets an explicit `off` rather than a missing section: the plugin
    and the doctor then read one shape, and "there is nothing to check here" is a fact on disk
    instead of an absence nobody can tell apart from a broken deposit.
    """
    providers = local_providers(config)
    return {"enabled": bool(providers), "providers": providers}


def plugin_evidence(plugin_dir: Path) -> dict[str, Any] | None:
    """The section the deposit left beside the plugin, or None when it is not there.

    None and an `off` section are different answers: the first means the file or the section was
    never written, which a sync repairs; the second means the engine wrote it and found no local
    model to watch.
    """
    path = plugin_dir / PLUGIN_CONFIG_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # nexgen: allow reason="a missing or unreadable switch file is reported as 'no section', and the doctor names the command that rewrites it"
        return None
    section = data.get(SECTION) if isinstance(data, dict) else None
    return section if isinstance(section, dict) else None


def _plugin_entry(config: dict[str, Any], plugin_id: str) -> dict[str, Any]:
    """The engine plugin's entry in `plugins.entries`, or an empty one."""
    plugins = config.get("plugins") if isinstance(config.get("plugins"), dict) else {}
    entries = plugins.get("entries") if isinstance(plugins.get("entries"), dict) else {}
    entry = entries.get(plugin_id)
    return entry if isinstance(entry, dict) else {}


def check_evidence(config: dict[str, Any], config_path: Path) -> CheckOutcome:
    """One line: is the check on, and for which local models.

    The section lives beside the plugin, not in `openclaw.json`: the engine writes it and the plugin
    reads it on every call. A plugin that is not enabled has no check, and saying so is the truth
    about that machine rather than a failure of it.
    """
    from nexgen_core.runtimes.openclaw import PLUGIN_ID  # lazy: the adapter imports this module

    if _plugin_entry(config, PLUGIN_ID).get("enabled") is not True:
        return CheckOutcome(
            id="openclaw.evidence",
            severity=Severity.OK,
            message=t("The engine plugin is not enabled here, so the evidence check is not installed."),
        )
    section = plugin_evidence(config_path.parent / "extensions" / PLUGIN_ID)
    if section is None:
        return CheckOutcome(
            id="openclaw.evidence",
            severity=Severity.WARN,
            message=t("The engine plugin is enabled but its evidence switch is missing: the check cannot run."),
            action=t("Run 'nexgen sync': it deposits the switch beside the plugin."),
        )
    providers = [str(item) for item in section.get("providers") or [] if str(item).strip()]
    if section.get("enabled") is not True or not providers:
        return CheckOutcome(
            id="openclaw.evidence",
            severity=Severity.OK,
            message=t("The evidence check is off: this machine names no local model whose answers it could check."),
        )
    return CheckOutcome(
        id="openclaw.evidence",
        severity=Severity.OK,
        message=t("The evidence check holds each answer of these local models to its own tool receipts: {providers}.",
                  providers=", ".join(providers)),
    )
