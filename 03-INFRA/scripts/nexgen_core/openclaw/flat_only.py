"""The providers that run on a flat plan, and the model refs the owner allows there.

One job: read the `flat_providers:` list the owner declares in the Vault's
`quota.yaml`, and refuse -- in one doctor line -- every model ref that names a
provider outside it. Three places in the file can spend, and all three are judged:
an agent's model, its fallback chain, and the model a subagent inherits.

Why it exists. `model_policy` writes `agents.defaults.modelPolicy.allow` as one
wildcard per provider this machine can reach and never narrows it, which is the
right default for a picker and also how a chain or a `subagents.model` entry can
quietly land a child on a provider billed per token. The list is the owner's rule,
so it is one key in the file the ceilings already live in; without it nothing is
restricted and nothing is refused, because the engine has no plan policy of its
own to fall back on.

Read-only: one file read, no model call, no network.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

from nexgen_core.i18n import t
from nexgen_core.openclaw.model_policy import provider_of, refs
from nexgen_core.report import CheckOutcome, Severity

#: The key the owner writes in `quota.yaml`, beside the ceilings and the failover chains.
KEY = "flat_providers"

#: The doctor check identifier.
CHECK_ID = "openclaw.flat_models"

#: What separates a provider from a model in a ref; a provider id carries none.
SEPARATOR = "/"


def read(target: Path) -> tuple[tuple[str, ...], list[str]]:
    """The provider ids `target` declares, and the entries that could not be used.

    The file is optional and so is the key: a machine that never wrote one is the
    normal state, not a fault. A malformed entry costs that entry and one note,
    never the command, and a key that is not a list costs the whole guard -- which
    the note says, because a silent "no list" would read as "nothing to restrict".
    """
    if not target.is_file():
        return (), []
    try:
        data = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        return (), [t("The flat provider list in {path} cannot be read: {error}", path=target, error=exc)]
    declared = data.get(KEY) if isinstance(data, dict) else None
    if declared is None:
        return (), []
    if not isinstance(declared, list):
        return (), [t("'flat_providers' is not a list of provider ids: ignored.")]

    providers: list[str] = []
    bad: list[str] = []
    for item in declared:
        text = item.strip() if isinstance(item, str) else ""
        if text and SEPARATOR not in text and not any(char.isspace() for char in text):
            if text not in providers:
                providers.append(text)
        elif item is not None:
            bad.append(str(item))
    return tuple(providers), [
        t("The flat provider {entry} is not a provider id: ignored.", entry=entry) for entry in bad
    ]


def load(home: Path | None = None, *, vault: Path | None = None) -> tuple[tuple[str, ...], list[str]]:
    """The list this machine declares, and what could not be used.

    The location is asked for rather than copied: `quota_policy.path` is the one
    place that knows where the ceilings and this list live.
    """
    from nexgen_core import quota_policy

    return read(quota_policy.path(home, vault=vault))


def declared(home: Path | None = None, *, vault: Path | None = None) -> tuple[str, ...]:
    """Only the provider ids, for the proposal that writes them into the allowlist."""
    return load(home, vault=vault)[0]


def model_refs(config: Mapping[str, Any]) -> list[tuple[str, str]]:
    """Every ref this configuration can spend on, with the key it was read from.

    A ref without a provider is left to the checks that judge aliases: it names no
    plan, so there is no plan here to hold it to.
    """
    agents = _dict(config.get("agents"))
    defaults = _dict(agents.get("defaults"))
    found = _labelled("agents.defaults.model", defaults.get("model"))
    found.extend(_labelled("agents.defaults.subagents.model", _dict(defaults.get("subagents")).get("model")))
    for agent_id, entry in _dict(agents.get("entries")).items():
        item = _dict(entry)
        found.extend(_labelled(f"agents.entries.{agent_id}.model", item.get("model")))
        found.extend(_labelled(
            f"agents.entries.{agent_id}.subagents.model", _dict(item.get("subagents")).get("model"),
        ))
    return found


def check(config: Mapping[str, Any], providers: Sequence[str], notes: Sequence[str] = ()) -> CheckOutcome:
    """One line: the refs outside the list as a failure, or what the guard judged.

    A failure, never a warning: a turn on a metered provider spends money nobody
    approved, and a warning is a line a person learns to skip. An unusable
    declaration is a warning instead, because the machine is fine and the list is
    the work.
    """
    if not providers:
        if notes:
            return CheckOutcome(
                id=CHECK_ID,
                severity=Severity.WARN,
                message=t("The flat provider list cannot be used as written: {notes}",
                          notes="; ".join(str(note) for note in notes)),
                action=t("Write 'flat_providers' as a list of provider ids in the Vault's quota.yaml."),
            )
        return CheckOutcome(
            id=CHECK_ID,
            severity=Severity.OK,
            message=t("No 'flat_providers' list is declared in the Vault's quota.yaml: every provider stays selectable."),
        )

    allowed = {str(provider) for provider in providers}
    judged = [(where, ref) for where, ref in model_refs(config) if provider_of(ref)]
    outside = [(where, ref) for where, ref in judged if provider_of(ref) not in allowed]
    if outside:
        return CheckOutcome(
            id=CHECK_ID,
            severity=Severity.BROKEN,
            message=t("These model refs run on a provider the flat list does not name: {items}",
                      items="; ".join(f"{where} -> {ref}" for where, ref in outside)),
            action=t("Move them to a declared flat provider, or add the provider to 'flat_providers' "
                     "in the Vault's quota.yaml."),
        )
    return CheckOutcome(
        id=CHECK_ID,
        severity=Severity.OK,
        message=t("Every model ref runs on a declared flat provider ({count} refs on {providers}).",
                  count=len(judged), providers=", ".join(str(provider) for provider in providers)),
    )


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _labelled(where: str, value: Any) -> list[tuple[str, str]]:
    """The refs in one `model` value, each tagged with the key that carries it."""
    return [(where, ref) for ref in refs(value)]
