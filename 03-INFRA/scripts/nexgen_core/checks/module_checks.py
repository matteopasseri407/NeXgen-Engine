"""Checks for the module catalog and its per-machine state."""
from __future__ import annotations

from pathlib import Path

from nexgen_core.i18n import t
from nexgen_core.modules import load_catalog, load_state_file
from nexgen_core.report import CheckOutcome, Severity


def check_modules_catalog(engine_root: Path, vault_data: Path) -> CheckOutcome:
    """The catalog is the module vocabulary: a typo there derails every
    per-machine state file, so a broken catalog is a failure. The state file
    is data: a declaration for an unknown module or an unsupported state is
    named as a warning, never silently ignored."""
    try:
        catalog = load_catalog(engine_root)
    except Exception as exc:
        return CheckOutcome(
            id="modules.catalog",
            severity=Severity.BROKEN,
            message=t("The module catalog is invalid: {error}", error=exc),
            action=t("Fix agent-universal-layer/modules/modules.yaml and rerun the sync."),
        )

    try:
        declared = load_state_file(vault_data)
    except Exception as exc:
        return CheckOutcome(
            id="modules.catalog",
            severity=Severity.WARN,
            message=t("The per-machine module state could not be read: {error}", error=exc),
        )

    problems: list[str] = []
    for mid, state in declared.items():
        module = catalog.get(mid)
        if module is None:
            problems.append(t("'{mid}' is declared but not in the catalog", mid=mid))
            continue
        if not module.supports(state):
            problems.append(
                t("'{mid}' declares state '{state}', not supported by the catalog ({supported})",
                  mid=mid, state=state, supported=", ".join(module.states)),
            )

    if problems:
        return CheckOutcome(
            id="modules.catalog",
            severity=Severity.WARN,
            message=t("The module state file declares things the catalog does not: {problems}", problems="; ".join(problems)),
            action=t("Run 'nexgen modules list' to see the supported states and fix modules.state.yaml."),
        )
    return CheckOutcome(
        id="modules.catalog",
        severity=Severity.OK,
        message=t("Module catalog valid with {count} modules", count=len(catalog)),
    )
