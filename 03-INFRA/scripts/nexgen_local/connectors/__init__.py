"""Personal connectors owned by the engine: Gmail and Drive, read-only.

The runtime already has a working private adapter (``workspace_mcp.py`` in the
vault's agent layer); this package is the product side: the same Google REST
shapes, the same machine-local token store, but engine-owned code with the
lane's receipt and outcome contract. No MCP server here on purpose: these are
in-process read tools for the lane, not a new service to mount.

Rules:

- Read-only. There is deliberately no send/reply/upload: writes need a
  propose/approve gate that does not exist yet, so they are not exposed.
- Stdlib only (urllib), like the adapter. No ``google-api-python-client``.
- Secrets never live here: the client secret comes from the environment or
  the machine-local 0600 file, the tokens from the machine-local store.
- Never trigger an interactive login on your own: without tokens the
  connectors fail closed with a message that names the one-time login step.
  The human does that step, never an unattended run.
- Every failure supplies a display message to ``ToolRegistry._refuse``, which
  records an explicit error status. Empty searches declare empty status;
  the engine picks ABSENCE vs ERROR without interpreting the message.
"""

from .auth import AuthError, ConnectorError, NeedsLogin

__all__ = ["AuthError", "ConnectorError", "NeedsLogin"]
