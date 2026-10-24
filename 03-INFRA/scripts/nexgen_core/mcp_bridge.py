#!/usr/bin/env python3
"""mcp_bridge.py — the engine's stdio MCP servers, reachable over HTTP from a gateway that cannot launch them.

The Windows companion app runs OpenClaw inside an app-owned WSL distro where
interop is off and `C:` is not mounted, so a connector declared as a Windows
command (`python C:\\...\\lazy-mcp.py`, `node ...\\playwright-human-safe.mjs`) never
starts there. OpenClaw speaks Streamable HTTP, so this bridge runs on the Windows
side, starts those same connectors as children, and serves them at `/mcp/<name>`.

One job: the command surface (start, stop, status), the token, where the listener
sits, and the state file that lets a later command find this run again. The HTTP
transport lives in `mcp_bridge_server`, the child processes in `mcp_bridge_channel`,
and the choice of which connectors to front in `mcp_bridge_targets`.

Security is the reason most of the lines here exist:

- loopback by default. The one other address worth binding is the virtual WSL
  interface, because in WSL's default NAT mode the distro reaches the Windows host
  at that address and not at 127.0.0.1 (mirrored networking is the exception, and
  there loopback works). `0.0.0.0` is accepted only as an explicit `--host`, and
  only with a token;
- every request carries `Authorization: Bearer <token>`, compared in constant
  time. The token is generated into the engine's own secrets deposit, at 0600,
  and is never printed, logged or written into the state file;
- the state file carries a nonce, and `/health` echoes it. `status` and `stop`
  therefore talk to the listener they think they are talking to, and a leftover
  state file from a killed run is recognised as stale instead of being believed;
- nothing starts this automatically. A listener that outlives a forgotten
  terminal is a port held open with a token in it.
"""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Sequence

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.i18n import t  # noqa: E402 - sys.path shim for a directly executed checkout
from nexgen_core.mcp_bridge_server import McpHttpBridgeServer  # noqa: E402 - sys.path shim for a directly executed checkout
from nexgen_core.mcp_bridge_state import (  # noqa: E402 - sys.path shim for a directly executed checkout
    answering,
    ask_to_stop,
    endpoint_url,
    public_record,
    read_state,
    state_file_path,
    verified,
    write_state,
)
from nexgen_core.mcp_bridge_targets import (  # noqa: E402 - sys.path shim for a directly executed checkout
    UnknownBridgeServer,
    select_backends,
)
from nexgen_core.processes import spawn_detached  # noqa: E402 - sys.path shim for a directly executed checkout

#: Where a gateway that runs on the same machine should be pointed.
DEFAULT_HOST = "127.0.0.1"

#: High enough to be out of the way of the engine's own services.
DEFAULT_PORT = 8765

#: The variable the bearer token is read from, and the name the token gets in the
#: engine's secrets deposit.
TOKEN_ENV = "NEXGEN_MCP_BRIDGE_TOKEN"

#: How long `start` waits for the detached listener to answer before calling it a failure.
START_TIMEOUT_SECONDS = 20.0

#: How long `stop` waits for the listener to go away after asking it to.
STOP_TIMEOUT_SECONDS = 5.0


class BridgeError(RuntimeError):
    """A problem the person can fix by changing the command, said as one sentence."""


# -- the token -------------------------------------------------------------

def ensure_bridge_token(name: str = TOKEN_ENV) -> str:
    """The bridge token, generated into the engine's deposit when it is missing.

    The deposit (`~/.config/nexgen/secrets.env`, 0600) is where this engine keeps the
    credentials its own processes read, and `deposit_env` is the reader every other
    part of the engine already agrees on. A token that cannot be stored is an error,
    not an ephemeral value: an ephemeral one changes on every start, and the gateway
    configured with yesterday's token fails with a bare 401.
    """
    from nexgen_core.deposit_env import deposit_path, read_deposit
    from nexgen_core.stack.secrets import ensure

    current = os.environ.get(name, "").strip()
    if current:
        return current
    stored = read_deposit().get(name, "").strip()
    if not stored:
        ensure(deposit_path(), [name])
        stored = read_deposit().get(name, "").strip()
    if not stored:
        raise BridgeError(
            t("Could not store {name} in {path}: the file exists but this process cannot read it",
              name=name, path=deposit_path())
        )
    # The detached listener arrives through this process's environment, and the
    # token must not travel in an argv where every process on the machine sees it.
    os.environ[name] = stored
    return stored


def _stored_token(name: str = TOKEN_ENV) -> str:
    """The token already on this machine, without generating one: for status and stop."""
    from nexgen_core.deposit_env import read_deposit

    return os.environ.get(name, "").strip() or read_deposit().get(name, "").strip()


# -- where the listener sits ----------------------------------------------

def _run_capture(argv: list[str], timeout: float = 5.0) -> str | None:
    """A short command's stdout, or None: asking the network stack must never take the command down."""
    from nexgen_core.processes import no_window_kwargs

    try:
        result = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired):  # nexgen: allow reason="asking the network stack is best effort; a machine without the interface is reported by the caller"
        return None
    return result.stdout if result.returncode == 0 else None


def detect_wsl_host_ip(
    runner: Callable[[list[str]], str | None] | None = None,
    is_windows: bool | None = None,
) -> str | None:
    """The Windows host's address on the virtual WSL network, when there is one.

    `NEXGEN_WSL_HOST_IP` wins, because a machine whose adapter is named something the
    filter below does not match still needs a way to say the address out loud.
    """
    explicit = os.environ.get("NEXGEN_WSL_HOST_IP", "").strip()
    if explicit:
        return explicit
    if not (os.name == "nt" if is_windows is None else is_windows):
        return None
    run = runner or _run_capture
    output = run([
        "powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
        "(Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.InterfaceAlias -like '*WSL*' }"
        " | Select-Object -First 1 -ExpandProperty IPAddress)",
    ])
    for line in (output or "").splitlines():
        candidate = line.strip()
        if candidate.count(".") == 3 and all(part.isdigit() for part in candidate.split(".")):
            return candidate
    return None


def resolve_listen_address(
    host: str | None = None,
    wsl: bool = False,
    token: str | None = None,
) -> tuple[str, str]:
    """The address to bind and the address to hand to a gateway: `(bind, client)`.

    Loopback unless asked otherwise. `--wsl` asks for the virtual interface, and a
    missing one is an error: falling back to loopback would start a listener the
    distro cannot reach while the command reports success.
    """
    if wsl or (host or "").strip().lower() == "wsl":
        address = detect_wsl_host_ip()
        if not address:
            raise BridgeError(
                t("No WSL network interface found: pass --host with the address the distro can reach")
            )
        return address, address
    cleaned = (host or "").strip()
    if not cleaned:
        return DEFAULT_HOST, DEFAULT_HOST
    if cleaned in ("0.0.0.0", "::"):
        if not (token or "").strip():
            raise BridgeError(t("Refusing to bind {host} without a bearer token", host=cleaned))
        return cleaned, DEFAULT_HOST
    if cleaned in ("localhost", "::1"):
        return DEFAULT_HOST, DEFAULT_HOST
    return cleaned, cleaned


# -- start, run, stop, status ---------------------------------------------

def run_bridge(
    bind_host: str,
    port: int,
    token: str,
    backends: dict[str, dict[str, Any]],
    client_host: str,
    state_path: Path,
) -> dict[str, Any]:
    """Serves until stopped: the foreground path and the body of the detached listener."""
    try:
        server = McpHttpBridgeServer(
            (bind_host, port),
            token=token,
            backends=backends,
            client_host=client_host,
            nonce=secrets.token_hex(16),
        )
    except OSError as exc:
        # The listener's own stderr goes to the null device (it is detached), so
        # the reason a bind failed is left where `start` is already looking.
        write_state(state_path, {"error": str(exc), "failed_at": time.time()})
        raise
    state = {
        "pid": os.getpid(),
        "nonce": server.nonce,
        "bind_host": bind_host,
        "client_host": client_host,
        "port": server.server_address[1],
        "url": server.get_url(),
        "token_env": TOKEN_ENV,
        "servers": server.served_names(),
        "started_at": server.started_at,
    }
    write_state(state_path, state)
    try:
        server.serve()
    finally:
        server.shutdown_and_close()
        state_path.unlink(missing_ok=True)
    return state


def _backends_or_phrase(names: Sequence[str] | None, renderer=None) -> dict[str, dict[str, Any]]:
    """The connectors to front, with an unknown name turned into a sentence instead of a traceback."""
    try:
        return select_backends(names, renderer)
    except UnknownBridgeServer as exc:
        raise BridgeError(str(exc)) from exc


def start_bridge(
    host: str | None = None,
    port: int = DEFAULT_PORT,
    wsl: bool = False,
    names: Sequence[str] | None = None,
    foreground: bool = False,
    home: Path | None = None,
    wait: float = START_TIMEOUT_SECONDS,
    renderer=None,
    spawn: Callable[[list[str]], int | None] | None = None,
) -> dict[str, Any]:
    """Starts the listener, once. Returns what `status` would say about it afterwards."""
    state_path = state_file_path(home)
    current = status_bridge(home)
    if current.get("running"):
        return {"status": "already_running", **public_record(current)}

    backends = _backends_or_phrase(names, renderer)
    if not backends:
        raise BridgeError(
            t("Nothing to bridge: no stdio connector of the OpenClaw gateway is mounted on this machine")
        )
    token = ensure_bridge_token()
    bind_host, client_host = resolve_listen_address(host, wsl=wsl, token=token)

    if foreground:
        state = run_bridge(bind_host, port, token, backends, client_host, state_path)
        return {"status": "started", **public_record(state)}

    argv = [
        sys.executable, str(Path(__file__).resolve()), "run",
        "--host", bind_host,
        "--port", str(port),
        *[f"--server={name}" for name in backends],
    ]
    spawner = spawn or spawn_detached
    spawner(argv)

    deadline = time.monotonic() + max(0.0, wait)
    while time.monotonic() < deadline:
        state = read_state(state_path)
        if state is not None:
            failure = state.get("error")
            if failure:
                state_path.unlink(missing_ok=True)
                raise BridgeError(t("The bridge could not start: {reason}", reason=failure))
            health = verified(state, token)
            if health is not None:
                return {"status": "started", **public_record(state)}
        time.sleep(0.1)
    raise BridgeError(
        t("The bridge did not answer at {url} within {seconds} seconds",
          url=endpoint_url({"client_host": client_host, "port": port}), seconds=int(wait))
    )


def stop_bridge(home: Path | None = None, wait: float = STOP_TIMEOUT_SECONDS) -> dict[str, Any]:
    """Asks the listener to stop and forgets it. Never kills a process by pid.

    A pid read out of a file can belong to somebody else by the time it is used:
    there is no question here that a `taskkill` would answer safely. The daemon is
    ours, it has a shutdown path, and a daemon that will not take it is reported
    instead of shot.
    """
    state_path = state_file_path(home)
    state = read_state(state_path)
    if state is None:
        return {"stopped": False, "reason": "not running"}
    token = _stored_token(str(state.get("token_env") or TOKEN_ENV))
    started = time.monotonic()
    if token:
        ask_to_stop(state, token)
    while time.monotonic() - started < max(0.0, wait):
        gone = (not answering(state)) if not token else (verified(state, token) is None)
        if gone:
            break
        time.sleep(0.1)
    still = answering(state) if not token else (verified(state, token) is not None)
    if still:
        return {
            "stopped": False,
            "reason": "still answering" if token else "no token to ask with",
            "url": state.get("url"),
            "pid": state.get("pid"),
        }
    state_path.unlink(missing_ok=True)
    return {"stopped": True, "url": state.get("url"), "pid": state.get("pid")}


def status_bridge(home: Path | None = None) -> dict[str, Any]:
    """What the listener this machine recorded is doing right now."""
    state = read_state(state_file_path(home))
    if state is None:
        return {"running": False}
    token = _stored_token(str(state.get("token_env") or TOKEN_ENV))
    if not token:
        # Without the token the nonce cannot be read back, but a dead port is
        # still a dead port: that much is answered without touching anybody.
        if not answering(state):
            return {**public_record(state), "running": False, "stale": True}
        return {**public_record(state), "running": True, "verified": False}
    health = verified(state, token)
    if health is None:
        return {**public_record(state), "running": False, "stale": True}
    return {
        **public_record(state),
        "running": True,
        "verified": True,
        "pid": health.get("pid", state.get("pid")),
        "servers": health.get("servers", state.get("servers", [])),
        "started_at": health.get("started_at", state.get("started_at")),
    }


def bridged_http_servers(home: Path | None = None) -> dict[str, dict[str, str]]:
    """The renderer's map for OpenClaw: each bridged connector as `url` plus the token's variable.

    Empty when the bridge is not running, so a renderer pass never writes a URL that
    nothing answers on: a connector pointed at a dead port fails at the first call,
    which is worse than a connector that was never rewritten.
    """
    status = status_bridge(home)
    if not status.get("running"):
        return {}
    state = read_state(state_file_path(home)) or {}
    host = str(state.get("client_host") or DEFAULT_HOST)
    port = int(state.get("port") or DEFAULT_PORT)
    token_env = str(state.get("token_env") or TOKEN_ENV)
    return {
        str(name): {"url": f"http://{host}:{port}/mcp/{name}", "token_env": token_env}
        for name in status.get("servers") or []
    }


# -- the command line ------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    """`run` is what `start` spawns; the rest is here so the module is testable alone."""
    import argparse

    parser = argparse.ArgumentParser(prog="mcp_bridge")
    sub = parser.add_subparsers(dest="verb", required=True)

    run = sub.add_parser("run", help="serve until stopped (the detached listener's own entry point)")
    run.add_argument("--host", default=DEFAULT_HOST)
    run.add_argument("--port", type=int, default=DEFAULT_PORT)
    run.add_argument("--server", action="append", dest="servers", default=None)

    sub.add_parser("stop", help="ask the listener to stop")
    sub.add_parser("status", help="what the listener is doing")

    args = parser.parse_args(argv)
    try:
        if args.verb == "run":
            token = ensure_bridge_token()
            backends = _backends_or_phrase(args.servers)
            bind_host, client_host = resolve_listen_address(args.host, token=token)
            run_bridge(bind_host, args.port, token, backends, client_host, state_file_path())
            return 0
        if args.verb == "stop":
            result = stop_bridge()
            if result["stopped"]:
                print(t("MCP HTTP bridge stopped"))
                return 0
            if result.get("reason") == "not running":
                print(t("MCP HTTP bridge is not running"))
                return 0
            print(t("MCP HTTP bridge is still answering at {url}", url=result.get("url", "")))
            return 1
        status = status_bridge()
        print(json.dumps(status, indent=2))
        return 0 if status.get("running") else 1
    except BridgeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except OSError as exc:
        print(t("Could not bind the bridge: {error}", error=exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
