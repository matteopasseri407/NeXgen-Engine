"""The doctor's line about the local Google Antigravity CLI backend.

One job: say how the file drives `agy`, and warn when the plugin was told to let
`agy` approve its own tool calls (or omitted permissionMode, which defaults to
`skip` in the plugin). Which ids count as a Google plan, and which single one does
not, is `terms`' decision; here we only read the switch that plugin was given,
because a CLI that approves every call itself is a machine that runs whatever the
model asks for.
"""
from __future__ import annotations

from typing import Any

from nexgen_core.i18n import t
from nexgen_core.openclaw.terms import local_agy_backend
from nexgen_core.report import CheckOutcome, Severity

#: The plugin's own permission modes for `agy`. `skip` is the one that hands tool
#: approval to the CLI, and it is the plugin's default when none is declared;
#: `sandbox` and `settings` leave a boundary in place.
AUTO_APPROVING_MODE = "skip"
GUARDED_MODES = ("sandbox", "settings")

CHECK_ID = "openclaw.google.cli_backend"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _permission_mode(config: dict[str, Any], backend: str) -> str | None:
    """The mode the plugin's own entry declares, or None when it declares none."""
    entries = _dict(_dict(config.get("plugins")).get("entries"))
    value = _dict(_dict(entries.get(backend)).get("config")).get("permissionMode")
    return value.strip().lower() if isinstance(value, str) and value.strip() else None


def _says_nothing(backend: str, mode: str) -> CheckOutcome:
    """The quiet cases: a mode that keeps a boundary."""
    if mode in GUARDED_MODES:
        return CheckOutcome(
            id=CHECK_ID,
            severity=Severity.OK,
            message=t("The local '{backend}' CLI backend is configured: 'agy' keeps its own login and "
                      "OpenClaw holds no Google credential (permissionMode '{mode}').",
                      backend=backend, mode=mode),
        )
    return CheckOutcome(
        id=CHECK_ID,
        severity=Severity.WARN,
        message=t("The local '{backend}' CLI backend declares permissionMode '{mode}', which is neither "
                  "'skip' nor 'sandbox'/'settings': the doctor cannot say whether 'agy' approves a tool "
                  "call by itself.", backend=backend, mode=mode),
        action=t("Set plugins.entries.{backend}.config.permissionMode to 'sandbox' or 'settings'.",
                 backend=backend),
    )


def check_local_agy(config: dict[str, Any]) -> CheckOutcome | None:
    """The line, or None on a file that never names the backend."""
    backend = local_agy_backend(config)
    if backend is None:
        return None
    mode = _permission_mode(config, backend) or AUTO_APPROVING_MODE
    if mode != AUTO_APPROVING_MODE:
        return _says_nothing(backend, mode)
    return CheckOutcome(
        id=CHECK_ID,
        severity=Severity.WARN,
        message=t("The local '{backend}' CLI backend runs 'agy' with permissionMode '{mode}': 'agy' "
                  "approves every tool call by itself, with no approval from a person.",
                  backend=backend, mode=mode),
        action=t("Set plugins.entries.{backend}.config.permissionMode to 'sandbox' or 'settings'.",
                 backend=backend),
    )
