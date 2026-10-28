"""The `gateway` section the engine proposes, and nothing else.

One job: say what the gateway block should contain, given the two choices that
affect it and the file in force. It was a private function in `profile.py` until
that file reached its size ceiling; the behaviour is unchanged and pinned by
`test_nexgen_openclaw_profile.py`.

`mode` is the person's once it exists. A node that declares `remote` is a client
of a gateway elsewhere: writing `local` beside its `remote.url` makes the file
incoherent and can raise a second listener on the machine. A node also opens no
listener here, so `bind` and `auth` are not proposed at all; they are written
only where the engine writes the mode, which is a file that has none.
"""
from __future__ import annotations

from typing import Any


def gateway_block(
    *,
    lan: bool,
    serve: bool,
    current: dict[str, Any] | None,
    port: int,
    pending_token: str,
) -> dict[str, Any]:
    """The gateway block to propose, keeping the role, the token and the tailscale mode."""
    gateway = current.get("gateway") if isinstance(current, dict) else None
    declared = gateway.get("mode") if isinstance(gateway, dict) else None
    mode = declared.strip() if isinstance(declared, str) and declared.strip() else None
    auth = gateway.get("auth") if isinstance(gateway, dict) else None
    token = auth.get("token") if isinstance(auth, dict) else None
    tailscale = gateway.get("tailscale") if isinstance(gateway, dict) else None
    block = dict(tailscale) if isinstance(tailscale, dict) else {"mode": "off"}
    if serve:
        # The pair OpenClaw documents for a machine with no screen: the listener
        # never leaves loopback and Tailscale serves it over HTTPS on the tailnet.
        # The rest of the section is the person's and stays: only the mode changes.
        block["mode"] = "serve"
    proposed: dict[str, Any] = {"port": port}
    if mode is None:
        proposed["mode"] = "local"
    if mode != "remote":
        proposed["bind"] = "lan" if lan else "loopback"
        proposed["auth"] = {
            "mode": "token",
            "token": token if isinstance(token, str) and token.strip() else pending_token,
        }
    # The person may have chosen Tailscale for remote access: turning it off
    # silently would break a setup that works and was not the engine's to undo.
    proposed["tailscale"] = block
    return proposed
