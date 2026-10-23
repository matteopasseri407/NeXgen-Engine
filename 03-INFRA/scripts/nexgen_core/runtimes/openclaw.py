"""OpenClaw adapter: one `nexgen` plugin, deposited next to its state directory.

OpenClaw runs native plugins inside its gateway process, so everything the engine gives
Claude Code reaches OpenClaw through a single plugin, split into three jobs that know nothing
about each other (`guardrail.mjs`, `briefing.mjs`, `quota.mjs`, registered by `index.mjs`):

- the **guardrail** on `before_tool_call`, the same private body the other CLIs run;
- the **vault briefing** after a compaction, the same text Claude's checkpoint hook injects;
- **`/quota`**, which runs the engine and forwards the lines it already rendered.

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

from nexgen_core.paths import openclaw_config
from nexgen_core.runtimes.base import EVENT_SINK_NAME, GUARDRAIL_SIDECAR_NAME, GuardrailError, Runtime

#: The engine's plugin directory (package.json + manifest + index.mjs + the three jobs),
#: deployed as a whole into `<state dir>/extensions/<PLUGIN_ID>/`. The folder keeps the name it
#: had when it held only the guardrail: it is the source of a byte-for-byte copy, never a path
#: anyone configures.
_PLUGIN_SOURCE_DIRNAME = "openclaw-guardrail-plugin"
#: What OpenClaw's state extensions folder calls it, and therefore the plugin id
#: `plugins.allow` must name. The setup command imports this constant.
PLUGIN_ID = "nexgen"
#: The name unit C deposited it under. It is removed on sight: one plugin, one id.
LEGACY_PLUGIN_ID = "nexgen-guardrail"
#: The engine-owned switches beside the plugin: briefing, posture. Never `openclaw.json`.
PLUGIN_CONFIG_NAME = "nexgen.config.json"
#: The briefing text, shared with Claude's checkpoint hook (`hooks/vault-briefing.txt`).
BRIEFING_NAME = "vault-briefing.txt"


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
        """OpenClaw's state directory: the folder its own config file lives in."""
        return openclaw_config(home).parent

    def _plugin_dir(self, home: Path) -> Path:
        return self._state_dir(home) / "extensions" / PLUGIN_ID

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
        source_dir = engine_hooks_dir / _PLUGIN_SOURCE_DIRNAME
        if not source_dir.is_dir():
            raise GuardrailError(f"openclaw: missing engine plugin ({source_dir})")
        plugin_dir = self._plugin_dir(home)

        # 0) One plugin, one id: the folder unit C left behind is this engine's own deposit,
        #    never a folder to ask about. A folder that is not provably ours stays.
        migrated = self._remove_legacy_plugin(home)

        # 1) The plugin itself: every file of the engine's directory, byte for byte.
        #    Copying the folder instead of a list of names is what keeps an added
        #    job or manifest field from being silently deployed on one machine and not
        #    the next.
        changed = self._deploy_plugin(source_dir, plugin_dir)

        # 2) The shared core every adapter imports, beside the plugin.
        changed |= self.deploy_guardrail_core(plugin_dir, engine_hooks_dir)

        # 3) The briefing text, the same file Claude's checkpoint hook injects.
        changed |= self._deploy_briefing(plugin_dir, engine_hooks_dir)

        # 4) The guardrail body (the policy, private to the Vault), under hooks/.
        body_dst = plugin_dir / "hooks" / hook_name
        changed |= self.deploy_bytes(body_dst, hook_source.read_bytes())

        # 5) The sidecar: which body to run and with what timeout, read fresh on
        #    every call, so a changed manifest takes effect without a restart.
        changed |= self.write_guardrail_sidecar(
            plugin_dir / GUARDRAIL_SIDECAR_NAME, body=body_dst, home=home,
        )

        # 6) The engine's switches. The briefing follows the same fact Claude follows: the
        #    checkpoint hook registered on SessionStart. Without it, OpenClaw would brief a
        #    session Claude does not brief, which is exactly the divergence to avoid. The
        #    `engine` section is how `/quota` reaches this very interpreter without a shell.
        changed |= self._write_plugin_config(
            home,
            briefing={"enabled": self._briefing_installed(home)},
            engine=engine_deposit(),
        )

        if not changed and not migrated:
            return None
        if migrated and not changed:
            return f"openclaw: the old {LEGACY_PLUGIN_ID} plugin was removed from {plugin_dir.parent}"
        return f"openclaw: plugin deposited in {plugin_dir}"

    def _deploy_briefing(self, plugin_dir: Path, engine_hooks_dir: Path) -> bool:
        source = engine_hooks_dir / BRIEFING_NAME
        if not source.is_file():
            raise GuardrailError(f"openclaw: missing the shared briefing text ({source})")
        return self.deploy_bytes(plugin_dir / BRIEFING_NAME, source.read_bytes())

    @staticmethod
    def _briefing_installed(home: Path) -> bool:
        """Does Claude Code already receive this briefing?

        The engine deploys no copy of the checkpoint hook, so its registration in Claude's own
        settings is the observable fact. OpenClaw's plugin follows that fact rather than
        inventing its own trigger, so the two hosts brief the same sessions.
        """
        from nexgen_core.runtimes.claude import checkpoint_registered

        return checkpoint_registered(home)

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
    def _deploy_plugin(source_dir: Path, plugin_dir: Path) -> bool:
        """Copies the plugin's own files and removes obsolete plugin files; True if any of them changed on disk."""
        changed = False
        source_names = set()
        for source in sorted(source_dir.iterdir()):
            if not source.is_file():
                continue
            source_names.add(source.name)
            changed |= Runtime.deploy_bytes(plugin_dir / source.name, source.read_bytes())
        for obsolete in (EVENT_SINK_NAME, "events.mjs"):
            if obsolete not in source_names:
                target = plugin_dir / obsolete
                if target.is_file():
                    target.unlink()
                    changed = True
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
