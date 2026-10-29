"""The doctor's lines about the local Google Antigravity CLI backend.

One job: say how the file drives `agy`, and warn when the plugin was told to let
`agy` approve its own tool calls (or omitted permissionMode, which defaults to
`skip` in the plugin). Which ids count as a Google plan, and which single one does
not, is `terms`' decision; here we only read the switch that plugin was given,
because a CLI that approves every call itself is a machine that runs whatever the
model asks for.

Two more facts belong here and not in the permission line, because they are not
about the mode at all: whether the plugin the configuration enables is installed
at the pinned release, and whether the `agy` command it drives is on PATH and
trusted with the agent folders. Before this, a machine with no plugin at all was
told about `permissionMode`, which pointed the person at a setting while the
plugin that reads it was missing. The install command in the action is the same
one `nexgen openclaw setup` runs, so the two cannot disagree.
"""
from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from nexgen_core.i18n import t
from nexgen_core.openclaw import agy_prereqs
from nexgen_core.openclaw.agy_backend import AUTO_APPROVING_MODE, GUARDED_MODES
from nexgen_core.openclaw.terms import local_agy_backend
from nexgen_core.processes import command_line_text
from nexgen_core.report import CheckOutcome, Severity

#: The plugin's own permission modes for `agy`. `skip` is the one that hands tool
#: approval to the CLI, and it is the plugin's default when none is declared;
#: `sandbox` and `settings` leave a boundary in place. The vocabulary is
#: `agy_backend`'s: the setup writes one of these, this file judges it.

CHECK_ID = "openclaw.google.cli_backend"
#: The plugin on disk, and the `agy` command it drives: two facts of their own,
#: kept apart from the mode so a missing plugin is never reported as a mode.
PLUGIN_CHECK_ID = "openclaw.google.cli_backend.plugin"
AGY_CHECK_ID = "openclaw.google.cli_backend.agy"

Which = Callable[[str], "str | None"]


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


def _install_action(plugin: dict[str, str] | None) -> str:
    """The exact command that puts the pinned plugin on this gateway."""
    if plugin is None:
        return t("Check agent-universal-layer/openclaw/agy-plugin.json: it holds the plugin release the "
                 "engine expects.")
    return t("Run '{command}'.",
             command=command_line_text(agy_prereqs.install_argv("openclaw", plugin)))


def _plugin_line(backend: str, state_dir: Path | None) -> CheckOutcome | None:
    """Whether the plugin the configuration enables is there, at the pinned release."""
    if state_dir is None:
        return None
    plugin = agy_prereqs.plugin_pin()
    installed = agy_prereqs.installed_version(state_dir, backend)
    if installed is None:
        return CheckOutcome(
            id=PLUGIN_CHECK_ID,
            severity=Severity.WARN,
            message=t("The configuration runs an agent on '{backend}', but the plugin is not installed in "
                      "{path}: OpenClaw cannot run that agent.", backend=backend,
                      path=str(agy_prereqs.extension_dir(state_dir, backend))),
            action=_install_action(plugin),
        )
    if plugin is not None and installed != plugin["version"]:
        return CheckOutcome(
            id=PLUGIN_CHECK_ID,
            severity=Severity.WARN,
            message=t("The '{backend}' plugin is {version}, not the pinned {pinned}: the engine configures "
                      "and tests one exact release.", backend=backend, version=installed,
                      pinned=plugin["version"]),
            action=_install_action(plugin),
        )
    return None


def _agy_line(
    backend: str, config: dict[str, Any], home: Path | None, which: Which | None,
) -> CheckOutcome | None:
    """Whether the `agy` command is there, and trusted with the folders the agents work in."""
    if (which or shutil.which)("agy") is None:
        return CheckOutcome(
            id=AGY_CHECK_ID,
            severity=Severity.WARN,
            message=t("The '{backend}' plugin runs the 'agy' command, which is not on PATH: install the "
                      "Antigravity CLI from Google, then run 'agy' once and sign in.", backend=backend),
            action=t("Install the Antigravity CLI from Google, then run 'agy' once and sign in."),
        )
    if home is None:
        return None
    missing = agy_prereqs.untrusted(agy_prereqs.agent_workspaces(config), agy_prereqs.trusted_workspaces(home))
    if not missing:
        return None
    return CheckOutcome(
        id=AGY_CHECK_ID,
        severity=Severity.WARN,
        message=t("'agy' has not been trusted with these agent folders: {folders}", folders=", ".join(missing)),
        action=t("Run 'agy' once in each of them and accept the trust prompt; 'agy' records the folder in "
                 "{settings} under {key}.",
                 settings=agy_prereqs.settings_path(home), key=agy_prereqs.TRUST_KEY),
    )


def check_local_agy(
    config: dict[str, Any],
    *,
    state_dir: Path | None = None,
    home: Path | None = None,
    which: Which | None = None,
) -> list[CheckOutcome]:
    """Every line this backend gets, and none at all on a file that never names it.

    The facts are separate lines on purpose: the plugin on disk, the `agy` command
    it drives, and the mode the plugin was given. A missing plugin used to be
    reported as a `permissionMode` problem, which pointed the person at a setting
    while the plugin that reads it was absent. A check that cannot see enough --
    no state folder, no home -- says nothing about that fact rather than guessing.
    """
    backend = local_agy_backend(config)
    if backend is None:
        return []
    outcomes = [
        outcome for outcome in (
            _plugin_line(backend, state_dir),
            _agy_line(backend, config, home, which),
        )
        if outcome is not None
    ]
    outcomes.append(_mode_line(backend, config))
    return outcomes


def _mode_line(backend: str, config: dict[str, Any]) -> CheckOutcome:
    """The permission mode the plugin was given: the one line this backend always has."""
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
