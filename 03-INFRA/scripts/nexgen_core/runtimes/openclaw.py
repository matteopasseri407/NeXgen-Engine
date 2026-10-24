"""OpenClaw adapter: one `nexgen` plugin, deposited next to its state directory.

OpenClaw runs native plugins inside its gateway process, so everything the engine gives
Claude Code reaches OpenClaw through a single plugin, split into jobs that know nothing
about each other (`guardrail.mjs`, `briefing.mjs`, `quota.mjs`, `workshop.mjs`,
`governor.mjs`, `council.mjs`, `sup.mjs`, `sup-tools.mjs`, `evidence.mjs`, registered by
`index.mjs`):

- the **guardrail** on `before_tool_call`, the same private body the other CLIs run;
- the **vault briefing** after a compaction, the same text Claude's checkpoint hook injects;
- **`/quota`**, which runs the engine and forwards the lines it already rendered;
- **`/council`** and **`/sup`**, the same, and the **supervision tools** an agent drives;
- the **evidence check**, which holds a local model's answer to the receipts of its own turn;
- the **governor** on `before_model_resolve`, which starts a turn on the fallback when the
  plan the turn would use is spent, from the readings the beat leaves beside it;
- the shared **`engine-run.mjs`**, the one way any of them starts the engine.

What this adapter deliberately does NOT do: write `openclaw.json`. Changing the `plugins.*`
block while the gateway runs breaks it, so enabling the plugin is a separate step
(`nexgen openclaw setup`, with the gateway stopped). Here the plugin is deposited -- byte for
byte, with its sidecars -- and nothing else in OpenClaw's own file is touched.

The posture is not OpenClaw's own schema either: it is written into the engine-owned
`nexgen.config.json` beside the plugin, in the neutral vocabulary the private manifest speaks
(`bypass` / `accept-edits` / `ask`), and the plugin renders it. That keeps the switch off
`openclaw.json`, which is read at gateway start and must not change under a running one.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from nexgen_core.openclaw.config_file import read_config
from nexgen_core.openclaw.evidence import evidence_section
from nexgen_core.paths import openclaw_config, resolve_home
from nexgen_core.runtimes.base import (
    EVENT_SINK_NAME,
    GUARDRAIL_CORE_NAME,
    GUARDRAIL_SIDECAR_NAME,
    GuardrailError,
    Runtime,
)

#: The engine's plugin directory (package.json + manifest + index.mjs + the jobs),
#: deployed as a whole into `<state dir>/extensions/<PLUGIN_ID>/`. The folder keeps the name it
#: had when it held only the guardrail: it is the source of a byte-for-byte copy, never a path
#: anyone configures.
_PLUGIN_SOURCE_DIRNAME = "openclaw-guardrail-plugin"
#: What OpenClaw's state extensions folder calls it, and therefore the plugin id
#: `plugins.allow` must name. The setup command imports this constant.
PLUGIN_ID = "nexgen"
#: The name unit C deposited it under. It is removed on sight: one plugin, one id.
LEGACY_PLUGIN_ID = "nexgen-guardrail"
#: The engine-owned switches beside the plugin: briefing, posture, engine, governor, evidence.
#: Never `openclaw.json`.
PLUGIN_CONFIG_NAME = "nexgen.config.json"
#: The briefing text, shared with Claude's checkpoint hook (`hooks/vault-briefing.txt`).
BRIEFING_NAME = "vault-briefing.txt"
#: The sidecar key the governor job reads: the block line, the age limit, the chains.
GOVERNOR_SECTION = "governor"
#: The readings the beat leaves for that job, in the plugin's own folder so the plugin
#: finds them without knowing anything about the engine's paths.
GOVERNOR_STATE_NAME = "nexgen-quota-state.json"


def state_dir(home: Path | None = None) -> Path:
    """OpenClaw's state directory: the folder its own configuration file lives in."""
    return openclaw_config(resolve_home(home)).parent


def plugin_dir(home: Path | None = None) -> Path:
    """Where OpenClaw loads this plugin from: `<state dir>/extensions/<PLUGIN_ID>`."""
    return state_dir(home) / "extensions" / PLUGIN_ID


def governor_state_path(home: Path | None = None) -> Path:
    """Where the beat publishes the readings the plugin judges a turn against."""
    return plugin_dir(home) / GOVERNOR_STATE_NAME


def engine_deposit() -> dict[str, str]:
    """How the plugin runs the engine: the interpreter and the CLI module, or nothing.

    Read from the engine that is running right now rather than from PATH, because the `nexgen`
    launcher is a `.cmd` on Windows and starting it means starting `cmd.exe` -- which the plugin
    must never do. A checkout, a venv and an installed package all answer the same two paths.
    When either cannot be proven the map is empty, and the plugin says it is not configured
    instead of running something else.
    """
    import sys

    import nexgen_core

    location = getattr(nexgen_core, "__file__", None)
    if not location or not sys.executable:
        return {}
    entry = Path(location).resolve().parent / "cli" / "__init__.py"
    if not entry.is_file():
        return {}
    return {"command": sys.executable, "entry": str(entry)}

#: Neutral vocabulary -> the word the plugin reads. The mapping is the identity on purpose:
#: OpenClaw's plugin renders the neutral posture itself, so the engine writes no dialect here.
_POSTURE_RENDER = {"bypass": "bypass", "accept-edits": "accept-edits", "ask": "ask"}


#: Files that stay beside the plugin folder in the engine and land inside it once deposited:
#: the guardrail core every adapter imports, and the briefing text the plugin reads.
_ADJACENT_PLUGIN_FILES = (GUARDRAIL_CORE_NAME, BRIEFING_NAME)

#: The sidecar and the switches sit in the plugin's folder but are the engine's own files,
#: written after the copy, and the event sink is a hook this version no longer ships: none of
#: them is a plugin source, so copying the folder can never pick one up as if it were.
_NOT_PLUGIN_SOURCES = (
    PLUGIN_CONFIG_NAME,
    GUARDRAIL_SIDECAR_NAME,
    EVENT_SINK_NAME,
    "events.mjs",
)


def plugin_files(engine_hooks_dir: Path) -> dict[str, Path]:
    """The plugin's public files, by the name they land under in its own folder.

    One list with two readers, on purpose: the deposit copies exactly these files into
    OpenClaw's `extensions/` folder and `nexgen_core.openclaw.plugin_package` packs exactly
    these files into the installable tarball. A job added to the plugin therefore reaches
    both destinations, or neither, and the private policy body is never on the list because
    the deposit writes it itself from the Vault.

    Raises `GuardrailError` when the plugin folder, its core or its briefing is missing: a
    plugin without them fails at the first tool call, and a package built from half a plugin
    would fail the same way on a machine the engine cannot inspect first.
    """
    source_dir = engine_hooks_dir / _PLUGIN_SOURCE_DIRNAME
    if not source_dir.is_dir():
        raise GuardrailError(f"openclaw: missing engine plugin ({source_dir})")
    files = {
        source.name: source
        for source in sorted(source_dir.iterdir())
        if source.is_file() and source.name not in _NOT_PLUGIN_SOURCES
    }
    for name in _ADJACENT_PLUGIN_FILES:
        source = engine_hooks_dir / name
        if not source.is_file():
            raise GuardrailError(f"openclaw: missing {name} beside the engine plugin ({source})")
        files[name] = source
    return files


class OpenClawRuntime(Runtime):
    name = "openclaw"

    def is_installed(self, home: Path) -> bool:
        if shutil.which("openclaw"):
            return True
        # The config file IS a valid signal here, unlike OpenCode's: the MCP
        # renderer patches `~/.openclaw/openclaw.json` only when it already
        # exists and never creates it, and OpenClaw writes it on first launch.
        return openclaw_config(home).is_file()

    def _state_dir(self, home: Path) -> Path:
        return state_dir(home)

    def _plugin_dir(self, home: Path) -> Path:
        return plugin_dir(home)

    def _plugin_config_path(self, home: Path) -> Path:
        return self._plugin_dir(home) / PLUGIN_CONFIG_NAME

    # ---- posture --------------------------------------------------------

    def read_posture(self, home: Path) -> str | None:
        """The posture the engine last wrote for the plugin, in neutral vocabulary."""
        value = self._read_plugin_config(home).get("posture")
        return value if isinstance(value, str) and value in _POSTURE_RENDER else None

    def apply_posture(self, home: Path, posture: str) -> str | None:
        """Writes the posture where the plugin reads it, never into `openclaw.json`.

        No plugin, no guardrail: like every other adapter, a posture with nothing to hold it
        is nothing to apply. That is also what keeps a posture from removing prompts on a
        machine whose guardrail failed to deposit.
        """
        if posture not in _POSTURE_RENDER:
            return None
        sidecar = self.guardrail_sidecar(home)
        if sidecar is None or not sidecar.is_file():
            return None
        if self.read_posture(home) == posture:
            return None
        if not self._write_plugin_config(home, posture=_POSTURE_RENDER[posture]):
            return None
        return f"openclaw: posture '{posture}' applied in {self._plugin_config_path(home)}"

    # ---- guardrail, briefing, migration ---------------------------------

    def guardrail_sidecar(self, home: Path) -> Path | None:
        return self._plugin_dir(home) / GUARDRAIL_SIDECAR_NAME

    def install_guardrail(self, home: Path, hook_source: Path, engine_hooks_dir: Path) -> str | None:
        hook_name = hook_source.name
        if not Runtime._is_safe_hook_filename(hook_name):
            raise GuardrailError(f"openclaw: unsafe guardrail filename {hook_name!r}")
        if not openclaw_config(home).is_file():
            return None  # OpenClaw never set up here: no state directory to deposit into
        # 0) The plugin's public files, read before anything on disk moves: a broken engine
        #    install is reported while the folder it replaces is still there. This is the one
        #    list the package builder reads too, so an added job cannot be deployed on one
        #    machine and missing from the tarball built for the next.
        files = plugin_files(engine_hooks_dir)
        plugin_dir = self._plugin_dir(home)

        # 1) One plugin, one id: the folder unit C left behind is this engine's own deposit,
        #    never a folder to ask about. A folder that is not provably ours stays.
        migrated = self._remove_legacy_plugin(home)

        # 2) The plugin itself, byte for byte: its own files, the shared core every adapter
        #    imports and the briefing text Claude's checkpoint hook also injects.
        changed = self._deploy_plugin(plugin_dir, files)

        # 3) The guardrail body (the policy, private to the Vault), under hooks/.
        body_dst = plugin_dir / "hooks" / hook_name
        changed |= self.deploy_bytes(body_dst, hook_source.read_bytes())

        # 4) The sidecar: which body to run and with what timeout, read fresh on
        #    every call, so a changed manifest takes effect without a restart.
        changed |= self.write_guardrail_sidecar(
            plugin_dir / GUARDRAIL_SIDECAR_NAME, body=body_dst, home=home,
        )

        # 5) The engine's switches. The briefing follows the same fact Claude follows: the
        #    checkpoint hook registered on SessionStart. Without it, OpenClaw would brief a
        #    session Claude does not brief, which is exactly the divergence to avoid. The
        #    `engine` section is how `/quota` reaches this very interpreter without a shell.
        #    The `evidence` section names the local models whose answers are checked: derived
        #    from the configuration in force, so a cloud model is never in it.
        config_in_force, _ = read_config(openclaw_config(home))
        changed |= self._write_plugin_config(
            home,
            briefing={"enabled": self._briefing_installed(home)},
            engine=engine_deposit(),
            evidence=evidence_section(config_in_force),
        )

        if not changed and not migrated:
            return None
        if migrated and not changed:
            return f"openclaw: the old {LEGACY_PLUGIN_ID} plugin was removed from {plugin_dir.parent}"
        return f"openclaw: plugin deposited in {plugin_dir}"

    @staticmethod
    def _briefing_installed(home: Path) -> bool:
        """Does Claude Code already receive this briefing?

        The engine deploys no copy of the checkpoint hook, so its registration in Claude's own
        settings is the observable fact. OpenClaw's plugin follows that fact rather than
        inventing its own trigger, so the two hosts brief the same sessions.
        """
        from nexgen_core.runtimes.claude import checkpoint_registered

        return checkpoint_registered(home)

    # ---- governor -------------------------------------------------------

    def replace_governor(self, home: Path, section: dict[str, Any] | None) -> bool:
        """Replaces the fallback chains the plugin reads; True when the file changed.

        The section is replaced whole, unlike every other key in this file: a chain the
        owner deleted from the policy has to stop being offered, and a merge would keep
        the old agent entries forever. An empty policy removes the section, so taking
        the policy away stops the early handover on the next turn instead of leaving the
        last one running. The plugin reads the file on every call, so the gateway is
        never restarted for this and a change lands on the next turn.
        """
        folder = self._plugin_dir(home)
        if not folder.is_dir():
            return False  # no plugin, no reader: a section nobody reads is not written
        current = self._read_plugin_config(home)
        if not section:
            if GOVERNOR_SECTION not in current:
                return False
            current.pop(GOVERNOR_SECTION, None)
        else:
            if current.get(GOVERNOR_SECTION) == section:
                return False
            current = {**current, GOVERNOR_SECTION: section}
        path = self._plugin_config_path(home)
        self.atomic_write(path, json.dumps(current, indent=2, sort_keys=True) + "\n")
        return True

    # ---- event sink -----------------------------------------------------

    def remove_event_sink(self, home: Path) -> str | None:
        plugin_dir = self._plugin_dir(home)
        if not plugin_dir.is_dir():
            return None  # nothing was ever deposited here: no config to invent
        removed = self.remove_deployed(plugin_dir / EVENT_SINK_NAME)
        removed |= self.remove_deployed(plugin_dir / "events.mjs")
        config = self._read_plugin_config(home)
        changed_config = False
        if "events" in config:
            config.pop("events", None)
            path = self._plugin_config_path(home)
            self.atomic_write(path, json.dumps(config, indent=2, sort_keys=True) + "\n")
            changed_config = True
        if removed or changed_config:
            return f"openclaw: event sink removed from {plugin_dir}"
        return None

    # ---- plugin deposit and its config ----------------------------------

    @staticmethod
    def _deploy_plugin(plugin_dir: Path, files: dict[str, Path]) -> bool:
        """Writes the plugin's public files and purges the obsolete ones; True if anything changed."""
        changed = False
        for name, source in files.items():
            changed |= Runtime.deploy_bytes(plugin_dir / name, source.read_bytes())
        for obsolete in (EVENT_SINK_NAME, "events.mjs"):
            changed |= Runtime.remove_deployed(plugin_dir / obsolete)
        return changed

    def _remove_legacy_plugin(self, home: Path) -> bool:
        """Takes back the `nexgen-guardrail` folder, only when it is provably this engine's."""
        legacy = self._state_dir(home) / "extensions" / LEGACY_PLUGIN_ID
        if not legacy.is_dir():
            return False
        try:
            manifest = json.loads((legacy / "openclaw.plugin.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):  # nexgen: allow reason="an unreadable legacy manifest cannot prove the folder is ours, so it is left alone"
            return False  # cannot prove it is ours: a folder is never deleted on a guess
        if not isinstance(manifest, dict) or manifest.get("id") != LEGACY_PLUGIN_ID:
            return False
        shutil.rmtree(legacy, ignore_errors=True)
        return not legacy.exists()

    def _read_plugin_config(self, home: Path) -> dict[str, Any]:
        try:
            data = json.loads(self._plugin_config_path(home).read_text(encoding="utf-8"))
        except (OSError, ValueError):  # nexgen: allow reason="a missing or corrupt switch file means no switch is set, and the writers rebuild it"
            return {}
        return data if isinstance(data, dict) else {}

    def _write_plugin_config(self, home: Path, **sections: Any) -> bool:
        """Merges the named sections into `nexgen.config.json`; True if the file changed.

        One section per job, so a change to the briefing never rewrites the posture and a
        posture never switches the events off. A scalar replaces its key; a map is merged.
        """
        path = self._plugin_config_path(home)
        current = self._read_plugin_config(home)
        updated = dict(current)
        for key, value in sections.items():
            if isinstance(value, dict) and isinstance(updated.get(key), dict):
                updated[key] = {**updated[key], **value}
            else:
                updated[key] = value
        if updated == current and path.is_file():
            return False
        self.atomic_write(path, json.dumps(updated, indent=2, sort_keys=True) + "\n")
        return True
