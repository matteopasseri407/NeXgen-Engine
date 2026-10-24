"""How the gateway observes its configuration, and when that means the whole home.

One job: decide whether the gateway process must run with `CHOKIDAR_USEPOLLING=1`,
say what to write in the configuration to make it so, and read back what the file
in force already says.

Both facts come from OpenClaw 2026.10.6's own code, not from a guess. When hot
reload admits an observation root it steps up one level from the folder that holds
`openclaw.json` (`path.dirname` of the state directory) and watches that. On
Windows the state directory is `~/.openclaw`, so the admitted root is the home
directory, and the native backend there is a single recursive
`ReadDirectoryChangesW` handle over the whole tree: every file any program
creates under the home is an event the gateway has to fold. Thousands of them,
from the engine's own test runs, is what filled the queue and ended with an
`EBADF` on a file that another process had already deleted, an error the
filesystem layer does not treat as recoverable, after which hot reload switched
itself off. In `poll` mode that library never starts the native hub at all and
polls the configuration file alone.

Linux is left alone on purpose. The same one-level step-up happens there, but the
watcher registers a non-recursive watch per directory for an entry scope, so with
`~/.openclaw/openclaw.json` it watches `~/.openclaw` and not the home, and
`CHOKIDAR_USEPOLLING` would only cost the memory index and the skills refresh
their native events. The doctor says which of the two it is.

Where the value comes from: OpenClaw applies the configuration's own `env` block
to the gateway's process environment during the gateway-run bootstrap, before the
reload manager creates the watcher, and a value the process already has is never
overridden (`docs/help/environment.md`: "applied only if missing"). It is not
below `plugins` or `gateway`, so it survives a reinstall of the service, and it
needs no wrapper and no user-wide environment variable.

One consequence worth stating: a change of mode takes effect at the next start.
The setup writes the key and lets the ordinary write path restart the gateway;
it never restarts anything for this key alone, and the plan says when the key is
new.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from nexgen_core.i18n import t
from nexgen_core.paths import is_inside
from nexgen_core.report import CheckOutcome, Severity

#: The variable OpenClaw reads. Any value but `false`, `0` or an empty string
#: selects polling, case-insensitively; `1` is what a person expects to read.
POLLING_VAR = "CHOKIDAR_USEPOLLING"
POLLING_VALUE = "1"


def is_windows(platform: str | None) -> bool:
    """Whether this is Windows, which is the only place the root handle is recursive."""
    return str(platform or sys.platform).startswith("win")


def observation_root(state_dir: Path) -> Path:
    """The directory OpenClaw admits as its root: one level above the configuration."""
    return Path(state_dir).parent


def watches_the_home(state_dir: Path | None, home: Path | None) -> bool:
    """Whether the admitted root contains the home, which is the case for `~/.openclaw`."""
    if state_dir is None:
        return False
    return is_inside(home, observation_root(state_dir))


def _polling_value(value: Any) -> bool:
    """The same test OpenClaw applies: set, and neither `false` nor `0`, case-insensitively."""
    if not isinstance(value, str) or not value.strip():
        return False
    return value.strip().lower() not in ("false", "0")


def forces_polling(config: dict[str, Any] | None) -> bool:
    """Whether the configuration in force makes the gateway poll instead of using events."""
    env = config.get("env") if isinstance(config, dict) else None
    block = env.get("vars") if isinstance(env, dict) else None
    value = block.get(POLLING_VAR) if isinstance(block, dict) else None
    return _polling_value(value)


def env_block(
    state_dir: Path | None, home: Path | None, platform: str | None,
) -> dict[str, Any] | None:
    """The `env` section that keeps the gateway off the home, or None when it is not needed."""
    if not is_windows(platform) or not watches_the_home(state_dir, home):
        return None
    return {"vars": {POLLING_VAR: POLLING_VALUE}}


def report(
    config: dict[str, Any] | None, config_path: Path, home: Path, platform: str | None,
) -> CheckOutcome:
    """The doctor's one line: whether the gateway watches the whole home recursively."""
    state_dir = Path(config_path).parent
    root = observation_root(state_dir)
    forced = forces_polling(config)
    if not watches_the_home(state_dir, home):
        if forced:
            return CheckOutcome(
                id="openclaw.observation",
                severity=Severity.WARN,
                message=t("Polling is forced, but the gateway watches {root} and not your home: "
                          "nothing needs it any more.", root=root),
                action=t("Remove env.vars.{name} from {path} and restart the gateway.",
                         name=POLLING_VAR, path=config_path),
            )
        return CheckOutcome(
            id="openclaw.observation",
            severity=Severity.OK,
            message=t("The gateway watches {root}, which does not contain your home.", root=root),
        )
    if not is_windows(platform):
        return CheckOutcome(
            id="openclaw.observation",
            severity=Severity.OK,
            message=t("The gateway's root is {root}, and on Linux it watches one directory "
                      "rather than the whole tree.", root=root),
        )
    if forced:
        return CheckOutcome(
            id="openclaw.observation",
            severity=Severity.OK,
            message=t("The configuration tells the gateway to poll instead of watching {root} "
                      "recursively; that applies from the next gateway start.", root=root),
        )
    return CheckOutcome(
        id="openclaw.observation",
        severity=Severity.WARN,
        message=t("The gateway watches the whole of {root} with one recursive handle: every "
                  "file created under it wakes the configuration reload.", root=root),
        action=t("Run 'nexgen openclaw setup': it adds env.vars.{name} to the configuration. "
                 "Restart the gateway once afterwards.", name=POLLING_VAR),
    )
