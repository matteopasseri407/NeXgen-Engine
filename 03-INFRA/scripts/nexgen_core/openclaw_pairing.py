"""OpenClaw pairing management, in its two stages.

Handles device pairing workflows including QR display instructions, pending
request discovery (with sensitive token stripping), and device approval, and the
second stage a node needs: the requests waiting on the gateway, the approval of
the commands it would expose, and the nodes the gateway already knows. A node
gets in only after both stages, and the two request ids are different, so the
module keeps them apart and never confuses one for the other.
Never exposes or prints auth tokens.

The listed devices are also what the setup and the doctor read when they say
whether the Windows app is paired, so the two questions about an entry -- does
it name the app, and could the gateway be asked at all -- live here beside the
listing itself.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from typing import Any, Callable

from nexgen_core.openclaw_service import RunResult, default_runner
from nexgen_core.processes import no_window_kwargs

SENSITIVE_KEY_SUBSTRINGS = ("token", "secret", "authkey", "private", "credential")

#: A value attached to a key that names a secret: `token: x`, `authKey=x`, `password = "..."`.
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)\b([A-Za-z0-9_-]*"
    r"(?:token|secret|authkey|private|credential|api[_-]?key|password|pairing[_-]?code)"
    r"[A-Za-z0-9_-]*)"
    r"(\s*[:=]\s*)"
    r"(\"[^\"]*\"|'[^']*'|\S+)"
)

#: An HTTP bearer credential, which `devices approve` may echo.
_BEARER = re.compile(r"(?i)\b(bearer\s+)\S+")


def _scrub_text(text: str) -> str:
    """The text with the value of every secret-looking key replaced by `[redacted]`.

    The structured path drops whole fields; this one is for the free text a
    CLI prints, where the token can sit on the same line as the device id.
    """
    scrubbed = _SENSITIVE_ASSIGNMENT.sub(r"\1\2[redacted]", text)
    return _BEARER.sub(r"\1[redacted]", scrubbed)


def qr() -> dict[str, str]:
    """Return the terminal command for the user to display their pairing QR code."""
    return {
        "command": "openclaw qr",
        "instructions": (
            "Esegui 'openclaw qr' in una finestra di terminale interattiva per "
            "visualizzare il codice QR da scansionare con l'app mobile OpenClaw."
        ),
    }


def _strip_tokens(device_record: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of the device record with all token/secret fields purged."""
    clean: dict[str, Any] = {}
    for key, val in device_record.items():
        if any(substr in key.lower() for substr in SENSITIVE_KEY_SUBSTRINGS):
            continue
        if key == "summary" and isinstance(val, str):
            clean[key] = _scrub_text(val)
        elif isinstance(val, dict):
            clean[key] = _strip_tokens(val)
        elif isinstance(val, list):
            clean[key] = [_strip_tokens(x) if isinstance(x, dict) else x for x in val]
        else:
            clean[key] = val
    return clean


def _parse_text_devices(output: str) -> list[dict[str, Any]]:
    """Fallback parser for textual device listings when --json is unsupported."""
    devices: list[dict[str, Any]] = []
    for line in output.splitlines():
        line_clean = line.strip()
        if not line_clean:
            continue
        match_id = re.search(r"(?:ID:\s*|id[:=]\s*)([a-zA-Z0-9_\-]+)", line_clean, re.IGNORECASE)
        if match_id:
            dev_id = match_id.group(1)
            devices.append({"id": dev_id, "summary": _scrub_text(line_clean), "status": "pending"})
    return devices


#: The two tables `openclaw devices list --json` prints, and the name of the id a
#: waiting request is approved by. Both are read from the release's own parser
#: (`dist/devices-cli.runtime-*.mjs`: `parseDevicePairingList` returns exactly
#: `{pending, paired}`), not guessed from the shape of a record.
_PAIRING_TABLES = ("pending", "paired")
_REQUEST_ID = "requestId"


def _pairing_tables(data: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The requests waiting and the devices already paired, from the CLI's answer.

    A bare array is an answer from a build that printed the list alone, and it
    can only be the requests: a device that is already paired is not asking for
    anything.
    """
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)], []
    if not isinstance(data, dict):
        return [], []
    tables: list[list[dict[str, Any]]] = []
    for name in _PAIRING_TABLES:
        value = data.get(name)
        tables.append([item for item in value if isinstance(item, dict)] if isinstance(value, list) else [])
    return tables[0], tables[1]


def _with_id(device: dict[str, Any]) -> dict[str, Any]:
    """One record with a single `id` whichever table it came from.

    A waiting request is approved by its `requestId`; a device already paired is
    known by its `deviceId`. The engine speaks `id` everywhere else, so the three
    names are reconciled here, once, instead of at every call site.
    """
    clean = _strip_tokens(device)
    if str(clean.get("id") or "").strip():
        return clean
    for key in (_REQUEST_ID, "deviceId"):
        value = clean.get(key)
        if isinstance(value, str) and value.strip():
            clean["id"] = value.strip()
            break
    return clean


def _read_devices(runner: Callable[[list[str]], RunResult] | None) -> tuple[bool, str, Any]:
    """Run the documented query and hand back `(ok, stdout, parsed answer)`."""
    cmd = ["openclaw", "devices", "list", "--json"]
    run_cmd = runner or default_runner
    res = run_cmd(cmd)
    data: Any = None
    if res.stdout.strip():
        try:
            data = json.loads(res.stdout)
        except (json.JSONDecodeError, ValueError):  # nexgen: allow reason="the text table is the fallback when the CLI did not print json"
            data = None
    return res.returncode == 0, res.stdout, data


def pending(
    runner: Callable[[list[str]], RunResult] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Retrieve pending device pairing requests, stripping all tokens."""
    cmd = ["openclaw", "devices", "list", "--json"]
    commands = [cmd]
    if dry_run:
        return {"ok": True, "commands": commands, "devices": [], "dry_run": True}

    ok, stdout, data = _read_devices(runner)
    if data is None:
        devices = _parse_text_devices(stdout)
    else:
        requests, _ = _pairing_tables(data)
        devices = [_with_id(device) for device in requests]

    return {
        "ok": ok,
        "commands": commands,
        "devices": devices,
        "dry_run": False,
    }


#: The words a device record uses when it is the Windows app. The gateway's JSON
#: shape is not documented field by field, so this is a heuristic and the
#: sentences built on it say "names the app", never "is the app".
APP_DEVICE_WORDS = ("companion", "windows hub", "windows-hub")


def names_the_windows_app(device: dict[str, Any]) -> bool:
    """Whether a device record is the Windows app, from the words it carries."""
    words = " ".join(
        str(device.get(key) or "")
        for key in ("summary", "displayName", "clientId", "name", "deviceId", "id")
    ).casefold()
    return any(word in words for word in APP_DEVICE_WORDS)


def listed(runner: Callable[[list[str]], RunResult] | None = None) -> list[dict[str, Any]] | None:
    """Every device and request the gateway lists, or None when it cannot answer.

    `devices list` holds paired devices and pending requests together, so an
    entry here says the gateway knows that device, not that it approved it. A
    stopped gateway is a normal state: the caller says what it can and moves on
    instead of reporting a fault.
    """
    try:
        ok, stdout, data = _read_devices(runner)
    except OSError:  # nexgen: allow reason="a CLI that cannot be launched is an unanswered question, not a crash"
        return None
    if not ok:
        return None
    if data is None:
        return [_with_id(device) for device in _parse_text_devices(stdout)]
    requests, paired = _pairing_tables(data)
    return [_with_id(device) for device in (*requests, *paired)]


def approve(
    device_id: str,
    runner: Callable[[list[str]], RunResult] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Approve a pending device pairing request by ID."""
    clean_id = str(device_id).strip()
    if not clean_id:
        return {"ok": False, "error": "Device ID cannot be empty", "commands": [], "dry_run": dry_run}

    cmd = ["openclaw", "devices", "approve", clean_id]
    commands = [cmd]
    if dry_run:
        return {"ok": True, "commands": commands, "device_id": clean_id, "dry_run": True}

    run_cmd = runner or default_runner
    res = run_cmd(cmd)
    return {
        "ok": res.returncode == 0,
        "commands": commands,
        "device_id": clean_id,
        "output": _scrub_text(res.stdout.strip()),
        "dry_run": False,
    }


# ------------------------------------------------------------------ the node stage
#
# A node is a machine that runs OpenClaw's commands on the gateway's behalf. Device
# approval admits the connection; a second, separate request lists the commands the
# node would expose, and that one has an id of its own. Both are answered here, and
# neither is ever answered on its own initiative: the caller asks the person first.

#: A node query that has not answered in twenty seconds has a gateway behind it that
#: will not answer, and the caller (a doctor, usually) must not wait forever.
NODE_QUERY_TIMEOUT_SECONDS = 20.0

#: The keys a pending node request may carry its declared commands under. OpenClaw
#: writes `commands` in the pairing list; `declaredCommands` is what the node itself
#: advertises on connect, and an older payload can have only that one.
_COMMAND_KEYS = ("commands", "declaredCommands")


def default_node_runner(argv: list[str]) -> RunResult:
    """Run one node query, with a deadline: argv only, never a shell string.

    A gateway that is up answers in a second; one that is wedged answers never, and
    the deadline is what turns that into a silent "no nodes" instead of a hung
    command. On Windows `openclaw` is npm's batch shim, and a killed batch file
    leaves its node child holding the pipe open, so the deadline would never fire:
    the shim becomes node and the package entry, the way the alert transport does it.
    """
    from nexgen_core.megaphone import openclaw_launch

    found = shutil.which(argv[0]) if argv else None
    launch = openclaw_launch(found, shutil.which) if found else None
    if launch is None:
        return RunResult(returncode=1, stdout="", stderr="")
    try:
        done = subprocess.run(
            [*launch, *argv[1:]],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=NODE_QUERY_TIMEOUT_SECONDS,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):  # nexgen: allow reason="an OpenClaw that cannot be asked has no nodes to report, and silence is the answer the caller renders"
        return RunResult(returncode=1, stdout="", stderr="")
    return RunResult(done.returncode, done.stdout, done.stderr)


def node_name(node: dict[str, Any]) -> str:
    """The name a person recognises for a node, falling back to its ids."""
    for key in ("displayName", "nodeId", "requestId"):
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "?"


def node_platform(node: dict[str, Any]) -> str:
    """The platform a node reports, or an empty string when it reports none."""
    value = node.get("platform")
    return value.strip() if isinstance(value, str) else ""


def node_commands(node: dict[str, Any]) -> list[str]:
    """The commands a node declares, whichever key this release puts them under."""
    for key in _COMMAND_KEYS:
        value = node.get(key)
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
    return []


def _parse_text_nodes(output: str) -> list[dict[str, Any]]:
    """Fallback parser for the textual `nodes pending` table, when `--json` is unsupported.

    The table is a header row and then `request  node  ip  requested`; the request id
    is the first column, and the rest of the line is kept, scrubbed, as the summary so
    a person still sees which machine is asking.
    """
    nodes: list[dict[str, Any]] = []
    for line in output.splitlines():
        clean = line.strip()
        if not clean or clean.lower().startswith(("request", "no pending")):
            continue
        parts = [part.strip() for part in re.split(r"\t+|\s{2,}", clean) if part.strip()]
        if not parts:
            continue
        node: dict[str, Any] = {"requestId": parts[0], "summary": _scrub_text(clean), "status": "pending"}
        if len(parts) > 1:
            node["displayName"] = parts[1]
        nodes.append(node)
    return nodes


def nodes_pending(
    runner: Callable[[list[str]], RunResult] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Retrieve the node requests waiting on the gateway, stripping all tokens."""
    cmd = ["openclaw", "nodes", "pending", "--json"]
    commands = [cmd]
    if dry_run:
        return {"ok": True, "commands": commands, "nodes": [], "dry_run": True}

    run_cmd = runner or default_node_runner
    res = run_cmd(cmd)

    nodes: list[dict[str, Any]] = []
    if res.stdout.strip():
        try:
            data = json.loads(res.stdout)
            raw_list = data if isinstance(data, list) else data.get("pending", [])
            if isinstance(raw_list, list):
                nodes = [_strip_tokens(node) for node in raw_list if isinstance(node, dict)]
        except (json.JSONDecodeError, ValueError):  # nexgen: allow reason="fallback to text parsing when json is invalid"
            nodes = _parse_text_nodes(res.stdout)

    return {
        "ok": res.returncode == 0,
        "commands": commands,
        "nodes": nodes,
        "dry_run": False,
    }


def approve_node(
    request_id: str,
    runner: Callable[[list[str]], RunResult] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Approve the command surface a node asked for, by its own request id."""
    clean_id = str(request_id).strip()
    if not clean_id:
        return {"ok": False, "error": "Node request ID cannot be empty", "commands": [], "dry_run": dry_run}

    cmd = ["openclaw", "nodes", "approve", clean_id]
    commands = [cmd]
    if dry_run:
        return {"ok": True, "commands": commands, "request_id": clean_id, "dry_run": True}

    run_cmd = runner or default_node_runner
    res = run_cmd(cmd)
    return {
        "ok": res.returncode == 0,
        "commands": commands,
        "request_id": clean_id,
        "output": _scrub_text(res.stdout.strip()),
        "dry_run": False,
    }


def nodes_status(
    runner: Callable[[list[str]], RunResult] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """The nodes the gateway already knows, with the state it reports for each.

    An unreachable gateway is not an error here: `ok` is false and the list is empty,
    and the caller decides whether silence or a warning is the honest answer.
    """
    cmd = ["openclaw", "nodes", "status", "--json"]
    commands = [cmd]
    if dry_run:
        return {"ok": True, "commands": commands, "nodes": [], "dry_run": True}

    run_cmd = runner or default_node_runner
    res = run_cmd(cmd)

    nodes: list[dict[str, Any]] = []
    if res.stdout.strip():
        try:
            data = json.loads(res.stdout)
            raw_list = data.get("nodes", []) if isinstance(data, dict) else data
            if isinstance(raw_list, list):
                nodes = [_strip_tokens(node) for node in raw_list if isinstance(node, dict)]
        except (json.JSONDecodeError, ValueError):  # nexgen: allow reason="a gateway that prints something else has no node list this module can read"
            nodes = []

    return {
        "ok": res.returncode == 0,
        "commands": commands,
        "nodes": nodes,
        "dry_run": False,
    }
