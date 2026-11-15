"""Tests for the engine's gateway calls, against a fake `openclaw` CLI.

Every call is now `openclaw gateway call <method> ...`, so what has behaviour is
the argv the engine builds, the payload it hands back, and the error it raises
for each failure envelope the CLI prints. The runner is replaced here, so no
gateway is started, no socket is opened and no real command runs.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import nexgen_core.openclaw.gateway_rpc as gateway_rpc  # noqa: E402
from nexgen_core.cli.openclaw_cmds import cmd_rpc_health  # noqa: E402
from nexgen_core.openclaw.gateway_rpc import (  # noqa: E402
    DEFAULT_TIMEOUT,
    STARTUP_GRACE_S,
    GatewayAuthError,
    GatewayConflictError,
    GatewayConnectionError,
    GatewayProtocolError,
    GatewayResponseError,
    GatewayRpcError,
    GatewayScopeError,
    GatewayTimeoutError,
    config_get,
    config_patch,
    health,
    plugins_install,
    plugins_reload,
)
from nexgen_core.openclaw_service import RunResult  # noqa: E402


def _ok(payload: object) -> RunResult:
    """A successful CLI answer: the payload the gateway sent, as JSON."""
    return RunResult(0, json.dumps(payload), "")


def _failed(error: dict) -> RunResult:
    """A failed CLI answer: the envelope `openclaw gateway call --json` prints and exit 1."""
    return RunResult(1, json.dumps({"ok": False, "error": error}), "")


@contextmanager
def fake_cli(*answers: RunResult) -> Iterator[list[tuple[list[str], float]]]:
    """Replace the CLI runner for one test and record every argv it was given."""
    recorded: list[tuple[list[str], float]] = []
    queue = list(answers)

    def runner(argv: list[str], deadline: float) -> RunResult:
        recorded.append((argv, deadline))
        return queue.pop(0) if queue else RunResult(0, "{}", "")

    original = gateway_rpc.run_command
    gateway_rpc.run_command = runner
    try:
        yield recorded
    finally:
        gateway_rpc.run_command = original


def test_health_asks_the_cli_for_the_gateway_health_method():
    """`health()` is one CLI call, with the method, the empty params and the JSON flag."""
    with fake_cli(_ok({"ok": True, "uptimeMs": 42000})) as calls:
        payload = health()

    assert calls[0][0] == [
        "openclaw", "gateway", "call", "health",
        "--params", "{}",
        "--json",
        "--timeout", "10000",
    ]
    assert payload == {"ok": True, "uptimeMs": 42000}


def test_config_get_asks_the_cli_for_config_dot_get():
    """`config_get()` asks for `config.get`, with no params of its own."""
    with fake_cli(_ok({"hash": "rev-42", "config": {"gateway": {"port": 18789}}})) as calls:
        payload = config_get()

    assert calls[0][0][3] == "config.get"
    assert json.loads(calls[0][0][5]) == {}
    assert payload["hash"] == "rev-42"


def test_config_patch_sends_raw_json_base_hash_and_replace_paths():
    """A patch keeps the gateway's own payload shape: `raw` as a JSON string, baseHash, replacePaths."""
    with fake_cli(_ok({"ok": True, "changedPaths": ["agents"]})) as calls:
        payload = config_patch(
            {"agents": {"list": []}},
            base_hash="rev-42",
            replace_paths=["agents"],
        )

    params = json.loads(calls[0][0][5])
    assert params == {
        "raw": '{"agents": {"list": []}}',
        "baseHash": "rev-42",
        "replacePaths": ["agents"],
    }
    assert calls[0][0][3] == "config.patch"
    assert payload["changedPaths"] == ["agents"]


def test_config_patch_sends_only_what_it_was_given():
    """No base hash and no replace paths means neither key appears in the params."""
    with fake_cli(_ok({"ok": True})) as calls:
        config_patch('{"gateway": {"port": 18789}}')

    assert json.loads(calls[0][0][5]) == {"raw": '{"gateway": {"port": 18789}}'}


@pytest.mark.parametrize(("source", "expected"), [
    ("clawhub:sample-pkg", {"source": "clawhub", "packageName": "sample-pkg"}),
    ("npm:@nexgen/plugin", {"source": "npm", "spec": "@nexgen/plugin"}),
    ("git:github.com/x/y", {"source": "git", "spec": "github.com/x/y"}),
    ("bundled:nexgen", {"source": "bundled", "pluginId": "nexgen"}),
    ("local:/opt/plugin", {"source": "local", "path": "/opt/plugin"}),
    ("/opt/plugin", {"source": "local", "path": "/opt/plugin"}),
    ("sample-pkg", {"source": "npm", "spec": "sample-pkg"}),
    ({"source": "npm", "spec": "x"}, {"source": "npm", "spec": "x"}),
])
def test_plugins_install_translates_a_source_specifier(source, expected):
    """A source string becomes the gateway's install params; a dict is passed through."""
    with fake_cli(_ok({"ok": True, "installed": True})) as calls:
        plugins_install(source)

    assert calls[0][0][3] == "plugins.install"
    assert json.loads(calls[0][0][5]) == expected


def test_plugins_reload_names_the_plugin_it_reloads():
    """A reload carries the plugin id in the gateway's own `plugins` array shape."""
    with fake_cli(_ok({"ok": True, "pluginIds": ["nexgen"]})) as calls:
        payload = plugins_reload("nexgen")

    assert json.loads(calls[0][0][5]) == {"plugins": [{"pluginId": "nexgen"}]}
    assert calls[0][0][3] == "plugins.reload"
    assert payload["pluginIds"] == ["nexgen"]


def test_url_and_token_reach_the_cli_as_its_own_flags():
    """An explicit URL and token are the CLI's documented flags for an explicit target."""
    with fake_cli(_ok({"ok": True})) as calls:
        health(url="ws://127.0.0.1:19999", token="tok-cli")

    argv = calls[0][0]
    assert argv[-4:] == ["--url", "ws://127.0.0.1:19999", "--token", "tok-cli"]


def test_a_token_nobody_supplied_is_left_to_the_cli():
    """With no token, the CLI resolves the configured one; the engine puts none in argv."""
    with fake_cli(_ok({"ok": True})) as calls:
        health()

    assert "--token" not in calls[0][0]
    assert "--url" not in calls[0][0]


def test_the_process_deadline_leaves_room_for_a_cold_cli_start():
    """The RPC timeout is the CLI's own budget; the process deadline is that plus the start."""
    with fake_cli(_ok({"ok": True})) as calls:
        health(timeout=2.0)

    assert calls[0][0][8] == "2000"
    assert calls[0][1] == 2.0 + STARTUP_GRACE_S
    assert DEFAULT_TIMEOUT == 10.0


def test_a_payload_that_is_not_an_object_is_wrapped():
    """A JSON array or scalar answer keeps the {result: ...} shape the old client returned."""
    with fake_cli(_ok([1, 2])):
        assert health() == {"result": [1, 2]}


def test_a_success_without_json_is_a_protocol_error():
    """A zero exit that prints no JSON cannot be a payload, and says which method it was."""
    with fake_cli(RunResult(0, "all fine, thanks", "")):
        with pytest.raises(GatewayProtocolError) as raised:
            health()

    assert "health" in str(raised.value)
    assert "all fine, thanks" in str(raised.value)


def test_an_unreachable_gateway_raises_the_connection_error_with_the_cli_sentence():
    """A transport envelope is a connection error carrying the CLI's own sentence and URL."""
    with fake_cli(_failed({
        "type": "gateway_transport_error",
        "kind": "closed",
        "message": "Gateway not reachable at ws://127.0.0.1:18789 (ECONNREFUSED).",
    })):
        with pytest.raises(GatewayConnectionError) as raised:
            health()

    assert "not reachable" in str(raised.value)
    assert "ws://127.0.0.1:18789" in str(raised.value)


def test_a_gateway_timeout_raises_the_timeout_error():
    """ETIMEDOUT on the transport is the engine's timeout, not a plain connection failure."""
    with fake_cli(_failed({
        "type": "gateway_transport_error",
        "kind": "timeout",
        "code": "ETIMEDOUT",
        "message": "Gateway not reachable at ws://127.0.0.1:18789 (timed out).",
        "timeoutMs": 200,
    })):
        with pytest.raises(GatewayTimeoutError):
            health(timeout=0.2)


def test_a_stale_base_hash_raises_the_conflict_error():
    """A base-hash mismatch on config.patch is the optimistic-concurrency conflict."""
    with fake_cli(_failed({
        "type": "gateway_request_error",
        "code": "INVALID_REQUEST",
        "message": "config base hash mismatch; re-run config.get and retry",
        "details": {"code": "CONFIG_BASE_HASH_MISMATCH"},
        "retryable": True,
    })):
        with pytest.raises(GatewayConflictError) as raised:
            config_patch({"agents": {"list": []}}, base_hash="rev-stale")

    assert "mismatch" in str(raised.value).lower()


def test_a_missing_scope_raises_the_scope_error():
    """FORBIDDEN from the gateway is a scope error, whatever sentence carries it."""
    with fake_cli(_failed({
        "type": "gateway_request_error",
        "code": "FORBIDDEN",
        "message": "missing scope: operator.admin",
        "retryable": False,
    })):
        with pytest.raises(GatewayScopeError) as raised:
            config_patch({"agents": {}}, base_hash="rev-42")

    assert "operator.admin" in str(raised.value)


def test_a_rejected_token_raises_the_auth_error_and_never_prints_the_token():
    """An auth failure keeps the CLI's sentence, and the token the engine passed stays out of it."""
    secret = "super-secret-password-xyz"
    with fake_cli(_failed({
        "type": "gateway_request_error",
        "code": "AUTH_TOKEN_MISMATCH",
        "message": "authentication failed: invalid gateway token",
        "retryable": False,
    })):
        with pytest.raises(GatewayAuthError) as raised:
            health(token=secret)

    assert secret not in str(raised.value)
    assert "authentication failed" in str(raised.value).lower()


def test_missing_credentials_are_reported_as_an_auth_error():
    """The CLI's credentials envelope means the same thing to the engine: no usable token."""
    with fake_cli(_failed({
        "type": "gateway_credentials_required",
        "message": "gateway url override requires explicit credentials",
    })):
        with pytest.raises(GatewayAuthError) as raised:
            health(url="ws://127.0.0.1:19999")

    assert "explicit credentials" in str(raised.value)


def test_a_protocol_mismatch_raises_the_protocol_error():
    """A version refusal from the gateway is a protocol error, not a generic failure."""
    with fake_cli(_failed({
        "type": "gateway_request_error",
        "code": "PROTOCOL_MISMATCH",
        "message": "protocol mismatch: expected 4, got 3",
        "retryable": False,
    })):
        with pytest.raises(GatewayProtocolError) as raised:
            health()

    assert "protocol mismatch" in str(raised.value).lower()


def test_a_typed_failure_keeps_its_code_and_details():
    """Anything else is a response error, and the caller can still read the gateway's code."""
    with fake_cli(_failed({
        "type": "gateway_request_error",
        "code": "INVALID_REQUEST",
        "message": "plugins.install: unknown source",
        "details": {"field": "source"},
        "retryable": False,
    })):
        with pytest.raises(GatewayResponseError) as raised:
            plugins_install("npm:x")

    assert raised.value.code == "INVALID_REQUEST"
    assert raised.value.details == {"field": "source"}
    assert isinstance(raised.value, GatewayRpcError)


def test_a_failure_the_cli_cannot_describe_keeps_its_last_sentence():
    """Not every failure is JSON: a killed or absent CLI is a connection error with its sentence."""
    with fake_cli(RunResult(127, "", "eseguibile openclaw non trovato")):
        with pytest.raises(GatewayConnectionError) as raised:
            health()

    assert "openclaw non trovato" in str(raised.value)


def test_a_cli_that_says_nothing_still_names_the_exit_code():
    """An empty answer is still a sentence the person can act on, never an empty message."""
    with fake_cli(RunResult(3, "", "")):
        with pytest.raises(GatewayConnectionError) as raised:
            health()

    assert "exited 3" in str(raised.value)


def test_a_failure_json_without_an_error_key_is_still_a_sentence():
    """A failed call whose JSON carries no error is reported by its exit code, not by a fragment."""
    with fake_cli(RunResult(2, '{"ok": false}', "")):
        with pytest.raises(GatewayConnectionError) as raised:
            health()

    assert "exited 2" in str(raised.value)


def test_cli_rpc_health_command_prints_text_json_and_errors():
    """`nexgen openclaw rpc health` keeps its human line, its JSON shape and its error path."""
    with fake_cli(
        _ok({"ok": True, "uptimeMs": 42000, "plugins": {"loaded": ["nexgen"], "errors": []}}),
        _ok({"ok": True, "uptimeMs": 42000, "plugins": {"loaded": ["nexgen"], "errors": []}}),
    ):
        args = argparse.Namespace(url=None, token=None, timeout=2.0, json=False)
        out = io.StringIO()
        with redirect_stdout(out):
            code = cmd_rpc_health(args)
        text = out.getvalue()

        assert code == 0
        assert "healthy" in text.lower()
        assert "42000" in text

        args_json = argparse.Namespace(url=None, token=None, timeout=2.0, json=True)
        out_json = io.StringIO()
        with redirect_stdout(out_json):
            code_json = cmd_rpc_health(args_json)
        payload = json.loads(out_json.getvalue())

        assert code_json == 0
        assert payload["uptimeMs"] == 42000

    with fake_cli(_failed({
        "type": "gateway_transport_error",
        "kind": "closed",
        "message": "Gateway not reachable at ws://127.0.0.1:18789 (ECONNREFUSED).",
    })):
        err_args = argparse.Namespace(url=None, token=None, timeout=0.5, json=False)
        err_out = io.StringIO()
        with redirect_stderr(err_out):
            code_err = cmd_rpc_health(err_args)

        assert code_err == 1
        assert "Error:" in err_out.getvalue()


def test_the_error_classes_keep_the_hierarchy_callers_branch_on():
    """A timeout is a connection error, and everything is a gateway error."""
    assert issubclass(GatewayTimeoutError, GatewayConnectionError)
    assert issubclass(GatewayResponseError, GatewayRpcError)
    assert issubclass(GatewayConflictError, GatewayRpcError)


#: A child interpreter where the socket library is refused. The finder answers before
#: any site-packages lookup, so the result never depends on what this machine has.
_WITHOUT_WEBSOCKETS = """
import sys


class _NoWebsockets:
    def find_spec(self, name, path=None, target=None):
        if name == "websockets" or name.startswith("websockets."):
            raise ModuleNotFoundError(f"No module named {name!r}", name=name)
        return None


sys.meta_path.insert(0, _NoWebsockets())
"""


def _run_without_websockets(script: str) -> subprocess.CompletedProcess:
    """Run `script` in a fresh interpreter that cannot import the socket library."""
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(SCRIPTS_DIR), env.get("PYTHONPATH", "")]))
    return subprocess.run(
        [sys.executable, "-c", _WITHOUT_WEBSOCKETS + script],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120, check=False, env=env,
    )


def test_the_gateway_module_still_imports_without_a_socket_library():
    """Talking to the gateway needs no socket library of ours: the CLI owns that connection.

    This is the regression the deleted `ws_transport` module existed for (the V2 smoke
    job installs pytest and pyyaml only and died during collection), and it now holds
    for a simpler reason: the module no longer imports a socket client at all.
    """
    result = _run_without_websockets(
        "import nexgen_core.openclaw.gateway_rpc as rpc\n"
        "print(rpc.DEFAULT_TIMEOUT)\n"
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "10.0"
