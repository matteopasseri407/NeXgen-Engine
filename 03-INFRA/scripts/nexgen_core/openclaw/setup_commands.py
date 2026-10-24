"""The external programs `nexgen openclaw setup` runs, named in one place.

One job: turn a decision into the exact argv and environment. The orchestrator
decides *whether* a command runs; this module decides *what* it is, so a test
can assert the literal command and a change of flags touches one function only.

Every path arrives already resolved by the caller: on Windows npm is
`npm.cmd`, and a `.cmd` without its full path is not executable by
CreateProcess, which is how "OpenClaw is installed" once became "not
installed" on the machine this was written for.
"""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

#: Where a person without Node goes. The engine never installs it: a system-wide
#: runtime is the person's decision, and a download is not something a setup
#: command does behind their back.
NODE_DOWNLOAD_URL = "https://nodejs.org/en/download"

#: The post-install scripts OpenClaw's native dependencies need. npm blocks them
#: by default, and without them the gateway starts and then cannot load a model.
ALLOW_SCRIPTS = "@google/genai,esbuild,koffi,protobufjs,openclaw"


def install_argv(npm: str, version: str) -> list[str]:
    """The one command that puts the pinned release on this machine."""
    return [npm, "install", "-g", f"openclaw@{version}", f"--allow-scripts={ALLOW_SCRIPTS}"]


def onboard_argv(openclaw: str) -> list[str]:
    """Non-interactive onboarding, with the two waits the engine does itself.

    `--skip-health` because the gateway is not started yet (step 6 does it) and
    onboarding would otherwise block on a port nobody is listening to;
    `--no-install-daemon` because the engine installs the service itself, and
    two supervisors for one gateway fight over the same port.
    """
    return [
        openclaw, "onboard", "--non-interactive", "--accept-risk",
        "--skip-health", "--no-install-daemon",
    ]


def validate_argv(openclaw: str) -> list[str]:
    """Let OpenClaw judge the file that was just written."""
    return [openclaw, "config", "validate"]


def config_env(env: Mapping[str, str], path: Path) -> dict[str, str]:
    """The child's environment, pointed at the file this run wrote.

    An explicit `OPENCLAW_CONFIG_PATH` is what the brief promises: validation
    judges the new file even when the machine keeps its configuration
    somewhere else.
    """
    return {**{str(key): str(value) for key, value in env.items()}, "OPENCLAW_CONFIG_PATH": str(path)}
