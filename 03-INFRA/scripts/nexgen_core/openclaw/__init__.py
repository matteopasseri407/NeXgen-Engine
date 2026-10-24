"""OpenClaw as the engine's own harness: detection, profile, plan and checks.

OpenClaw is the interface (web panel, phone app) through which a person uses
the flat subscriptions they already pay for and the models on their own
machine. Unit B1 reads and proposes; it writes nothing and starts nothing:

- :mod:`detect` says what this machine already has, without ever opening a
  credential file;
- :mod:`profile` is the pure function that derives the configuration the engine
  owns from what was detected and what the person chose;
- :mod:`templates` renders the agent manuals, in the engine's language;
- :mod:`plan` diffs the proposal against the file in force and prints it;
- :mod:`doctor` proves a live configuration is one OpenClaw can actually use;
- :mod:`gateway_rpc` speaks the WebSocket JSON-RPC protocol to the gateway.

`nexgen openclaw setup` and `restart` belong to B2 and must reuse these
functions rather than recompute the same answers.
"""
from __future__ import annotations

from nexgen_core.openclaw.detect import Detection, LocalModels, Login, detect  # noqa: F401
from nexgen_core.openclaw.gateway_rpc import (  # noqa: F401
    GatewayAuthError,
    GatewayConflictError,
    GatewayConnectionError,
    GatewayProtocolError,
    GatewayResponseError,
    GatewayRpcClient,
    GatewayRpcError,
    GatewayScopeError,
    GatewayTimeoutError,
    config_get,
    config_patch,
    health,
    plugins_install,
    plugins_reload,
)
from nexgen_core.openclaw.pin import pin_path, pinned_version  # noqa: F401
from nexgen_core.openclaw.profile import Choices, build_config  # noqa: F401
from nexgen_core.openclaw.templates import render_workspace  # noqa: F401
