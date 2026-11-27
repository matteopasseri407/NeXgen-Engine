"""Which providers this machine leaves out of the quota report, and what it says.

One job: read the optional `ignore:` key of the Vault's `quota.yaml` and answer
two questions about it — whether a row belongs to a provider this machine does
not use, and what the report says at the bottom about the ones it left out. An
entry names a provider by its id (`copilot`, `openai`) or by the name the report
prints (`Copilot`), compared without case, so the same plan is named whichever
reader wrote the row.

A machine that runs every plan it can reach needs no key, and everything here is
optional. A mistake stays local: a value that is not a list is refused whole and
named in one line, because a misspelt key must read as "ignore nothing" and never
as "ignore everything"; an entry that is not a name is named and skipped while
the other entries still apply. A file that cannot be read at all is already named
where its ceilings are read, so this reader stays silent and hides nothing.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from nexgen_core.i18n import t

#: The key, in the file that holds the ceilings, naming providers to leave out.
IGNORE_KEY = "ignore"


def load(path: Path) -> tuple[tuple[str, ...], list[str]]:
    """The providers the file hides, as written, and the entries it cannot use."""
    if not path.is_file():
        return (), []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):  # nexgen: allow reason="the same file already costs one line where its ceilings are read, and ignoring nothing is the shipped default"
        return (), []
    declared = data.get(IGNORE_KEY) if isinstance(data, dict) else None
    if declared is None:
        return (), []
    if not isinstance(declared, list):
        return (), [
            t("The 'ignore' key in {path} is not a list of providers: nothing is ignored.",
              path=path)
        ]
    names: list[str] = []
    notes: list[str] = []
    for position, entry in enumerate(declared, start=1):
        if isinstance(entry, str) and entry.strip():
            names.append(entry.strip())
        else:
            notes.append(t("The entry at position {position} of 'ignore' is not a provider name: ignored.",
                           position=position))
    return tuple(names), notes


def folded(names: tuple[str, ...]) -> frozenset[str]:
    """The entries as the case-folded keys a row is compared with."""
    return frozenset(name.strip().casefold() for name in names)


def hides(provider: str, provider_id: str, names: frozenset[str]) -> bool:
    """Whether this provider is one the file says to leave out.

    Either spelling an entry may use is enough: the id a chain speaks, or the
    name a person reads on the row. `copilot` names the same plan as `Copilot`.
    """
    return provider.strip().casefold() in names or provider_id.strip().casefold() in names


def note(names: tuple[str, ...]) -> str:
    """The one line naming the providers this machine was told to leave out."""
    return t("Ignored providers: {providers}.", providers=", ".join(names))
