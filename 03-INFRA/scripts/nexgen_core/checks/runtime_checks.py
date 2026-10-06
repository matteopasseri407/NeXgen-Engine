"""Whether the interpreter the engine runs under can import what the engine declares core."""
from __future__ import annotations

from pathlib import Path

from nexgen_core import runtime
from nexgen_core.i18n import t
from nexgen_core.paths import installed_as_package, resolve_runtime_dir
from nexgen_core.report import CheckOutcome, Severity


def check_engine_runtime(home: Path, engine_root: Path) -> CheckOutcome:
    """Asks the *running* interpreter, which is the one the launchers chose.

    That is the question that matters: a machine can have the libraries in some
    environment and still run the Council under one that lacks them, which is how the
    resumable relay and the `drive` server were dead here with every check green.
    """
    missing = runtime.missing_imports()
    if not missing:
        return CheckOutcome(
            id="env.runtime",
            severity=Severity.OK,
            message=t("The engine's Python environment has every essential library."),
        )
    names = ", ".join(runtime.ESSENTIAL_IMPORTS[m] for m in missing)
    message = t("The engine's Python environment is missing: {names}.", names=names)
    if installed_as_package():
        return CheckOutcome(
            id="env.runtime",
            severity=Severity.BROKEN,
            message=message,
            action=t("Reinstall the engine with your package manager (uv tool install --force, or pipx reinstall)."),
        )
    checkout = engine_root.parent

    def remedy() -> bool:
        runtime.ensure_runtime(resolve_runtime_dir(home), checkout, log=lambda _line: None)
        return True

    return CheckOutcome(
        id="env.runtime",
        severity=Severity.BROKEN,
        message=message,
        action=t("Run 'nexgen runtime ensure' (the guard also provisions it on its next cycle)."),
        remedy=remedy if (checkout / "pyproject.toml").is_file() else None,
    )
