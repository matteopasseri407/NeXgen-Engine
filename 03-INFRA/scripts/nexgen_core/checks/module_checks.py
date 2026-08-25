"""Checks for the module catalog and its per-machine state."""
from __future__ import annotations

from pathlib import Path

from nexgen_core.i18n import t
from nexgen_core.modules import derive_state, external_paths, load_catalog, load_state_file
from nexgen_core.report import CheckOutcome, Severity


def check_modules_catalog(engine_root: Path, vault_data: Path) -> CheckOutcome:
    """The catalog is the module vocabulary: a typo there derails every
    per-machine state file, so a broken catalog is a failure. The state file
    is data: a declaration for an unknown module or an unsupported state is
    named as a warning, never silently ignored."""
    try:
        catalog = load_catalog(engine_root, external=external_paths(vault_data))
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
    external = sum(1 for m in catalog.values() if m.source)
    return CheckOutcome(
        id="modules.catalog",
        severity=Severity.OK,
        message=t("Module catalog valid with {count} modules ({external} external)",
                  count=len(catalog), external=external),
    )


def check_modules_ready(engine_root: Path, vault_data: Path) -> list[CheckOutcome]:
    """Every module this machine switched on, and whether it can actually run.

    Requirements first, because they are cheap and static; then the module's
    own health command, which is the only thing that can tell installed from
    working. The guard never runs that command -- executing module-supplied
    code from a timer is what the declaration model exists to avoid -- so the
    doctor is where it belongs.
    """
    from nexgen_core.module_install import check_requirements, run_health_check

    outcomes: list[CheckOutcome] = []
    try:
        catalog = load_catalog(engine_root, external=external_paths(vault_data))
        states = derive_state(catalog, load_state_file(vault_data))
    except Exception as exc:
        return [CheckOutcome(
            id="modules.ready",
            severity=Severity.WARN,
            message=t("Module readiness could not be assessed: {error}", error=exc),
        )]

    for item in states:
        if item.state != "local" or not (item.module.provides or item.module.health):
            continue
        mid = item.module.id
        unmet = check_requirements(item.module)
        if unmet:
            blocking = [u for u in unmet if u.blocks_install]
            outcomes.append(CheckOutcome(
                id=f"modules.ready.{mid}",
                severity=Severity.BROKEN if blocking else Severity.WARN,
                message=t("Module '{mid}' is not ready: {problems}",
                          mid=mid, problems="; ".join(str(u) for u in unmet)),
                action=item.module.requires.setup or None,
            ))
            continue
        healthy, detail = run_health_check(item.module)
        if healthy is None:
            outcomes.append(CheckOutcome(
                id=f"modules.ready.{mid}",
                severity=Severity.OK,
                message=t("Module '{mid}': requirements met ({detail})", mid=mid, detail=detail),
            ))
        elif healthy:
            outcomes.append(CheckOutcome(
                id=f"modules.ready.{mid}",
                severity=Severity.OK,
                message=t("Module '{mid}' healthy: {detail}", mid=mid, detail=detail),
            ))
        else:
            outcomes.append(CheckOutcome(
                id=f"modules.ready.{mid}",
                severity=Severity.BROKEN,
                message=t("Module '{mid}' installed but not working: {detail}", mid=mid, detail=detail),
                action=item.module.requires.setup or None,
            ))
    return outcomes
