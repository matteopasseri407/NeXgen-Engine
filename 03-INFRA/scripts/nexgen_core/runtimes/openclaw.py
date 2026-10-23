"""OpenClaw adapter: the guardrail plugin, deposited next to its state directory.

OpenClaw runs native plugins inside its gateway process. Its guardrail hook is
`api.on("before_tool_call", handler)`, a JS callback, not a command launched in a
separate process: the static adapter at
`agent-universal-layer/hooks/openclaw-guardrail-plugin/` (already prepared, never
touched here) translates that callback into the same stdin/stdout JSON the
guardrail body already speaks for Claude, Antigravity and OpenCode -- one policy,
several CLIs, never duplicated.

What this adapter deliberately does NOT do: write `openclaw.json`. Changing the
`plugins.*` block while the gateway runs breaks it, so enabling the plugin is a
separate step (`nexgen openclaw setup`, with the gateway stopped). Here the
plugin is deposited -- byte for byte, with its sidecar -- and nothing else in
OpenClaw's own file is touched. There is no posture either: OpenClaw's command
policy (`tools.exec`, exec approvals) is its own schema, and this engine has no
verified rendering for it. Asking for one is answered with "nothing applied",
which is the truth.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from nexgen_core.paths import openclaw_config
from nexgen_core.runtimes.base import GUARDRAIL_SIDECAR_NAME, GuardrailError, Runtime

#: The engine's plugin directory (package.json + manifest + index.mjs), deployed as
#: a whole into `<state dir>/extensions/nexgen-guardrail/`.
_PLUGIN_SOURCE_DIRNAME = "openclaw-guardrail-plugin"
#: What OpenClaw's state extensions folder calls it, and therefore the plugin id
#: `plugins.allow` must name.
PLUGIN_ID = "nexgen-guardrail"


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

    def read_posture(self, home: Path) -> str | None:
        """No posture this engine can read: OpenClaw's exec policy is its own schema."""
        del home
        return None

    def apply_posture(self, home: Path, posture: str) -> str | None:
        """No posture this engine can render: `openclaw.json` stays OpenClaw's file."""
        del home, posture
        return None

    def guardrail_sidecar(self, home: Path) -> Path | None:
        return self._plugin_dir(home) / GUARDRAIL_SIDECAR_NAME

    def install_event_sink(self, home: Path, sink_source: Path) -> str | None:
        """Not wired for OpenClaw: a plugin here needs the same `plugins.*` enablement."""
        del home, sink_source
        return None

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

        # 1) The plugin itself: every file of the engine's directory, byte for byte.
        #    Copying the folder instead of a list of names is what keeps an added
        #    manifest field from being silently deployed on one machine and not the
        #    next.
        changed = self._deploy_plugin(source_dir, plugin_dir)

        # 2) The shared core every adapter imports, beside the plugin.
        changed |= self.deploy_guardrail_core(plugin_dir, engine_hooks_dir)

        # 3) The guardrail body (the policy, private to the Vault), under hooks/.
        body_dst = plugin_dir / "hooks" / hook_name
        changed |= self.deploy_bytes(body_dst, hook_source.read_bytes())

        # 4) The sidecar: which body to run and with what timeout, read fresh on
        #    every call, so a changed manifest takes effect without a restart.
        changed |= self.write_guardrail_sidecar(
            plugin_dir / GUARDRAIL_SIDECAR_NAME, body=body_dst, home=home,
        )
        if not changed:
            return None
        return f"openclaw: guardrail plugin deposited in {plugin_dir}"

    @staticmethod
    def _deploy_plugin(source_dir: Path, plugin_dir: Path) -> bool:
        """Copies the plugin's own files; True if any of them changed on disk."""
        changed = False
        for source in sorted(source_dir.iterdir()):
            if not source.is_file():
                continue
            changed |= Runtime.deploy_bytes(plugin_dir / source.name, source.read_bytes())
        return changed
