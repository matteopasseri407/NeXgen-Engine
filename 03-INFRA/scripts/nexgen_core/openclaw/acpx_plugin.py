"""The acpx plugin this machine actually has, judged against the lanes the engine writes.

One job: read the installed acpx plugin -- its version and the adapters it
declares -- and say whether it can still start every harness `acp.allowedAgents`
names, warning when it cannot.

Why it exists: the engine does not install acpx. OpenClaw ships it as a catalogue
plugin (`@openclaw/acpx`, installed under `<state dir>/extensions/acpx`), the
engine only allows it, writes the ACP block and the bindings, and pins the
OpenCode command the bridge launches. So a machine where the plugin was updated,
replaced or left half-installed keeps a green configuration and a lane that never
starts, which is the class of failure a doctor line exists for.

The version is a floor, not a pin: a catalogue release newer than the one the
engine verified is normal and never a finding, while a downgrade, an unreadable
manifest, or an adapter that disappeared is named with the command that repairs
it. The reference is `MIN_VERIFIED_VERSION`, the release the lanes were verified
against, read from this machine on 2026-11-15.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nexgen_core.i18n import t
from nexgen_core.openclaw.pin import node_satisfies
from nexgen_core.report import CheckOutcome, Severity

#: The plugin id OpenClaw installs the ACP runtime under, and the folder it lands in.
PLUGIN_ID = "acpx"
EXTENSIONS_DIR = "extensions"
PACKAGE_JSON = "package.json"
PLUGIN_MANIFEST = "openclaw.plugin.json"
CHECK_ID = "openclaw.acp.acpx"

#: The oldest plugin whose adapters the engine's lanes were verified against
#: (`@openclaw/acpx` 2026.10.6, installed beside OpenClaw 2026.11.2 on this machine).
#: A floor rather than an exact pin: a newer catalogue plugin is what a normal
#: `openclaw update` leaves behind, and reporting that as drift would be noise.
MIN_VERIFIED_VERSION = "2026.10.6"

#: The adapter each allowed harness needs, as the plugin declares it in its own
#: `dependencies`. `opencode` has none on purpose: the engine pins its command in
#: `acp.py` (`acpx_entry`), so acpx launches the installed binary directly.
ADAPTERS: dict[str, str] = {
    "claude": "@agentclientprotocol/claude-agent-acp",
    "codex": "@agentclientprotocol/codex-acp",
}


@dataclass(frozen=True)
class Plugin:
    """What one installed acpx plugin says about itself."""

    version: str
    #: Every dependency it declares: the adapters are matched against this list.
    dependencies: tuple[str, ...]
    #: The harnesses it activates on, as its own plugin manifest declares them.
    harnesses: tuple[str, ...]


def read_plugin(state_dir: Path) -> Plugin | None:
    """The acpx plugin under `state_dir`, or None when this machine has none.

    A missing plugin is an absence the caller reports; a package that is there and
    cannot be parsed raises, because a version nobody can read is not a version
    this check may guess at.
    """
    folder = Path(state_dir) / EXTENSIONS_DIR / PLUGIN_ID
    package = _json_object(folder / PACKAGE_JSON)
    if package is None:
        return None
    manifest = _json_object(folder / PLUGIN_MANIFEST) or {}
    dependencies = package.get("dependencies") if isinstance(package.get("dependencies"), dict) else {}
    activation = manifest.get("activation") if isinstance(manifest.get("activation"), dict) else {}
    harnesses = activation.get("onAgentHarnesses")
    return Plugin(
        version=str(package.get("version") or "").strip(),
        dependencies=tuple(sorted(str(name) for name in dependencies)),
        harnesses=tuple(str(name) for name in harnesses) if isinstance(harnesses, list) else (),
    )


def check(config: dict[str, Any], state_dir: Path) -> CheckOutcome | None:
    """The plugin's version and adapters, against the harnesses this file allows.

    Silent when neither an ACP block nor an `acpx` allow entry is written: a
    plugin standing by is not a lane, and a doctor that reports it would be
    describing OpenClaw's own catalogue rather than this machine's decision.
    """
    acp = config.get("acp") if isinstance(config.get("acp"), dict) else {}
    plugins = config.get("plugins") if isinstance(config.get("plugins"), dict) else {}
    allow = plugins.get("allow") if isinstance(plugins.get("allow"), list) else []
    if acp.get("enabled") is not True and PLUGIN_ID not in {str(name) for name in allow}:
        return None
    allowed = acp.get("allowedAgents") if isinstance(acp.get("allowedAgents"), list) else []
    harnesses = {str(name).strip().lower() for name in allowed if str(name).strip()}

    try:
        plugin = read_plugin(state_dir)
    except ValueError as err:
        return CheckOutcome(
            id=CHECK_ID,
            severity=Severity.WARN,
            message=t("The acpx plugin's manifest cannot be read ({error}), so the version and the adapters "
                      "of the lanes this file configures are unknown.", error=err),
            action=t("Reinstall it: 'openclaw plugins install @openclaw/acpx'."),
        )
    if plugin is None:
        return CheckOutcome(
            id=CHECK_ID,
            severity=Severity.WARN,
            message=t("This file configures an ACP lane, but no acpx plugin is installed under {folder}.",
                      folder=Path(state_dir) / EXTENSIONS_DIR / PLUGIN_ID),
            action=t("Install it: 'openclaw plugins install @openclaw/acpx'."),
        )
    if not plugin.version:
        return CheckOutcome(
            id=CHECK_ID,
            severity=Severity.WARN,
            message=t("The acpx plugin installed here declares no version, so it cannot be held against the "
                      "{version} the engine's lanes were verified on.", version=MIN_VERIFIED_VERSION),
            action=t("Reinstall it: 'openclaw plugins install @openclaw/acpx'."),
        )
    return _verdict(plugin, harnesses)


def _verdict(plugin: Plugin, harnesses: set[str]) -> CheckOutcome:
    """The line for a plugin that is there and readable: its drift, or its version."""
    drift = _drift(plugin, harnesses)
    if drift:
        return CheckOutcome(
            id=CHECK_ID,
            severity=Severity.WARN,
            message=t("The acpx plugin this file relies on no longer matches it ({version}): {drift}. An ACP lane "
                      "can stop starting with the configuration unchanged.",
                      version=t("version {version}", version=plugin.version), drift="; ".join(drift)),
            action=t("Run 'openclaw plugins update {plugin}' to bring it back to this release's catalogue "
                     "version.", plugin=PLUGIN_ID),
        )
    return CheckOutcome(
        id=CHECK_ID,
        severity=Severity.OK,
        message=t("The acpx plugin is {version}, it activates on {harnesses}, and it carries the adapter "
                  "every harness this file allows ({allowed}) needs.",
                  version=plugin.version,
                  harnesses=", ".join(plugin.harnesses) or t("no declared harness list"),
                  allowed=", ".join(sorted(harnesses)) or t("no harness allowed")),
    )


def _drift(plugin: Plugin, harnesses: set[str]) -> list[str]:
    """Every way this plugin no longer matches the lanes the engine writes."""
    found: list[str] = []
    if not node_satisfies(plugin.version, f">={MIN_VERIFIED_VERSION}"):
        found.append(t("it is older than the {version} the engine's lanes were verified against",
                       version=MIN_VERIFIED_VERSION))
    missing = [
        t("it declares no adapter for '{harness}'", harness=harness)
        for harness in sorted(harnesses)
        if harness in ADAPTERS and ADAPTERS[harness] not in plugin.dependencies
    ]
    return [*found, *missing]


def _json_object(path: Path) -> dict[str, Any] | None:
    """The JSON object in `path`, or None when the file is not there.

    A file that is there and unreadable raises: the check reports that as the
    finding it is instead of reading an empty plugin.
    """
    if not path.is_file():
        return None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as err:
        raise ValueError(f"{path}: {err}") from err
    if not isinstance(loaded, dict):
        raise ValueError(f"{path}: not a JSON object")
    return loaded
