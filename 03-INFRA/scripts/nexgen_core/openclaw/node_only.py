"""Whether this machine is only a node of another gateway, read from files.

One job: answer the one question the doctor and the OpenClaw checks ask -- "is this
machine a node and not a gateway?" -- from the role it recorded and, for the machines
already turned into a node by a path that recorded nothing, from the service OpenClaw
itself installed and the Companion app's own node switch.

Why it exists: a PC that stops being a gateway keeps `~/.openclaw/openclaw.json` where
it is, because the node still reads `nodeHost` from it (`node_retire` keeps it on
purpose). Judged with a gateway's rules, that leftover copy turns a working node red,
and `nexgen update` rolls a good release back for a failure this machine does not
have. So the fact is read here once, and the gateway checks retire.

Read-only by construction: the role file, the node service file OpenClaw wrote
(`node.cmd` on Windows, an `openclaw*node*.service` unit or a LaunchAgent), and the
app's `settings.json`, which is never written. The local gateway detector is the
existing one, handed a runner that starts nothing: the doctor may not launch a CLI,
and on a node the answer is already in the files.
"""
from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nexgen_core.i18n import t
from nexgen_core.openclaw.companion import COMPANION_DIRNAME, read_settings
from nexgen_core.openclaw.node_status import parse_node_service_command
from nexgen_core.openclaw.node_transition import detect_local_gateway
from nexgen_core.openclaw_service import RunResult
from nexgen_core.paths import openclaw_config, resolve_home
from nexgen_core.report import CheckOutcome, Severity

#: The hosts that mean "the gateway on this PC": a node's service points elsewhere.
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0"})

#: What decided it. The words are for the doctor's own detail, never a person's screen.
DECLARED = "declared"
SERVICE = "service"
APP = "app"

#: The id of the one line that says why the gateway checks stayed quiet.
NODE_MODE_CHECK_ID = "openclaw.node_mode"


@dataclass(frozen=True)
class NodeOnly:
    """The answer, and the one fact that produced it."""

    is_node_only: bool
    reason: str | None = None

    def __bool__(self) -> bool:
        """So a caller reads the question, not a field: `if detect_node_only(home):`."""
        return self.is_node_only


def node_only_outcome(check_id: str) -> CheckOutcome:
    """The one line a check says instead of judging a gateway on a node."""
    return CheckOutcome(
        id=check_id,
        severity=Severity.OK,
        message=t("This machine is a node: the agent configuration lives on the gateway."),
    )


def detect_node_only(
    home: Path | None = None,
    *,
    platform: str | None = None,
    env: Mapping[str, str] | None = None,
    systemd_user_dir: Path | None = None,
    appdata: Path | None = None,
    startup_dir: Path | None = None,
) -> NodeOnly:
    """Whether this machine is a node and not a gateway, and what said so.

    The recorded role is the machine's own answer and wins. Without it -- a PC made
    a node by `nexgen openclaw node --apply` before the role was recorded -- the
    node service and the app are the evidence, and only when no local gateway
    service is installed: a machine that also serves a gateway is a gateway.
    """
    resolved = resolve_home(home)
    system = platform or sys.platform
    values = dict(os.environ) if env is None else {str(k): str(v) for k, v in env.items()}
    if _declared_node(resolved):
        return NodeOnly(True, DECLARED)
    service = _node_service_files(openclaw_config(resolved, values).parent, resolved, system, systemd_user_dir)
    points_elsewhere = any(_remote_host(_text(path)) for path in service)
    app_wants_node = _app_wants_node(resolved, values, system)
    if not points_elsewhere and not app_wants_node:
        return NodeOnly(False)
    if _local_gateway_installed(resolved, system, appdata, startup_dir, systemd_user_dir):
        return NodeOnly(False)
    return NodeOnly(True, SERVICE if points_elsewhere else APP)


def _declared_node(home: Path) -> bool:
    """The role this machine recorded, read without importing the installer's module eagerly."""
    from nexgen_core import roles

    return roles.declared_role(home=home) == roles.NODE_ROLE


def _inert(_argv: list[str], _timeout: float) -> RunResult:
    """The detector's command paths, answered without starting anything: not installed here."""
    return RunResult(127, "", "")


def _local_gateway_installed(
    home: Path,
    system: str,
    appdata: Path | None,
    startup_dir: Path | None,
    systemd_user_dir: Path | None,
) -> bool:
    """Whether a gateway service is installed here, from its files and never from a process.

    `detect_local_gateway` asks `openclaw gateway status --json` and `schtasks` for a
    gateway that has no unit: both are handed the inert runner, so the answer is the
    service files this machine really has, which is all the doctor may read.
    """
    gateway = detect_local_gateway(
        home=home, platform=system, runner=_inert, appdata=appdata,
        startup_dir=startup_dir, systemd_user_dir=systemd_user_dir,
    )
    return gateway.present


def _node_service_files(
    state_dir: Path, home: Path, system: str, systemd_user_dir: Path | None,
) -> list[Path]:
    """Where OpenClaw writes this platform's node service, in the order it names them."""
    if system == "win32":
        return [state_dir / "node.cmd"]
    if system == "darwin":
        return sorted((home / "Library" / "LaunchAgents").glob("*openclaw*node*.plist"))
    directory = systemd_user_dir or (home / ".config" / "systemd" / "user")
    return sorted(directory.glob("openclaw*node*.service"))


def _text(path: Path) -> str:
    """One service file as text, or nothing when it is absent or unreadable."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:  # nexgen: allow reason="a service file that cannot be read is not evidence of a node"
        return ""


def _remote_host(text: str) -> str | None:
    """The gateway a node service file points at, when that gateway is not on this PC.

    The `node run` judgement is OpenClaw's own parser (`node_status`), so this reads
    the same shape `openclaw node status --json` reports. A plist wraps every argument
    in `<string>` tags, which are turned into spaces first: the tokens stay the same.
    """
    plain = text.replace("<string>", " ").replace("</string>", " ")
    tokens = [token.strip("\"'") for token in plain.split()]
    is_node, _url = parse_node_service_command(tokens)
    if not is_node:
        return None
    host = _host_argument(tokens)
    if not host or host.casefold().strip("[]") in LOCAL_HOSTS:
        return None
    return host


def _host_argument(tokens: Sequence[str]) -> str | None:
    """The value of `--host`, in either of the two shapes a command line writes it."""
    for index, token in enumerate(tokens):
        if token == "--host" and index + 1 < len(tokens):
            return tokens[index + 1]
        if token.startswith("--host="):
            return token.split("=", 1)[1]
    return None


def _app_wants_node(home: Path, values: Mapping[str, str], system: str) -> bool:
    """Whether the Companion app is set to be a node of a gateway elsewhere.

    The app is only on Windows, and its files belong to it: they are read here and
    never written. The remote gateway is the existing reader's answer; the node
    switch is one boolean, read at any depth because the app's schema is its own.
    """
    if not system.startswith("win"):
        return False
    roaming = Path(values.get("APPDATA") or (home / "AppData" / "Roaming"))
    settings_dir = roaming / COMPANION_DIRNAME
    if not any(_switch(_json(settings_dir / name), "enablenodemode")
               for name in ("settings.json", "gateways.json")):
        return False
    app = read_settings(settings_dir)
    return app.points_remote and not app.owns_local_gateway


def _json(path: Path) -> Any:
    """One app file as JSON, or None when it is absent or not readable JSON."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # nexgen: allow reason="a missing or malformed app file is not evidence of a node"
        return None


def _switch(document: Any, name: str) -> bool:
    """Whether `name` is switched on anywhere in the document, matched case-insensitively."""
    if isinstance(document, dict):
        for key, value in document.items():
            if str(key).casefold() == name and value is True:
                return True
            if _switch(value, name):
                return True
    elif isinstance(document, list):
        return any(_switch(item, name) for item in document)
    return False
