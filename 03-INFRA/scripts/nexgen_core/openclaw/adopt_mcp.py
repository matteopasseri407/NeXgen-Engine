"""Adopting a live MCP server: one manifest row, lazy, with no credential inside it.

A server added in OpenClaw's panel lives only in `openclaw.json`; every other
CLI reaches the canonical library and therefore has never heard of it. Adoption
turns that live entry into a manifest row, and the row is the same one
`nexgen mcp add` writes, because it goes through the same writer: validated,
atomic, backed up, refused when the name is taken.

Lazy by default: a connector nobody has asked to be in every prompt belongs
behind the gateway, where `nexgen mcp plan` can show it and any CLI can reach
it. Rendering is deliberately not done here -- the manifest is the source, and
the next `nexgen sync apply` writes the CLIs from it, exactly as for a hand
added server.

One job: the write path of one server. What may be adopted is
`adopt_vetting`'s answer, and how a whole pass is ordered is `adopt`'s.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from nexgen_core.i18n import t
from nexgen_core.mcp_add import build_entry, insert_entry
from nexgen_core.mcp_placement import CLIS
from nexgen_core.openclaw.adopt_scan import UnmanagedServer


@dataclass(frozen=True)
class ServerAdoption:
    """What happened to one server, in words a person can act on."""

    name: str
    adopted: bool
    note: str


#: The mark that says where an adopted row came from, and what OpenClaw calls it.
#: It rides beside the row the shared writer produces, not inside it: `nexgen mcp add`
#: writes library entries, and provenance is the adopter's own knowledge.
PROVENANCE = "openclaw"


def _stamped(entry: dict[str, Any], origin: str, openclaw_id: str | None) -> dict[str, Any]:
    """The row plus its provenance, so a later pass can retire what it adopted."""
    if origin:
        entry["origin"] = origin
    if openclaw_id:
        entry["openclaw_id"] = openclaw_id
    return entry


def _http_row(
    name: str,
    live: Mapping[str, Any],
    *,
    origin: str = PROVENANCE,
    openclaw_id: str | None = None,
) -> dict[str, Any]:
    """The manifest row of a remote server, or a refusal when its token is a literal."""
    from nexgen_core.renderer_cli import LITERAL_SECRET

    auth = live.get("auth") if isinstance(live.get("auth"), dict) else {}
    auth_env = auth.get("env")
    if auth_env == LITERAL_SECRET:
        raise ValueError(t(
            "'{name}' carries a credential in its headers: replace it with ${{VARIABLE}} "
            "and store the value with `nexgen-secrets set`", name=name,
        ))
    # A remote server's row has no environment: the engine's own renderer writes headers
    # and timeouts only, so an environment block here would be dropped without a word.
    # `_refuse_what_the_inverse_would_drop` catches it before the inverse loses it.
    url = live.get("url")
    if not isinstance(url, str) or not url:
        raise ValueError(t("'{name}' has no usable URL to adopt", name=name))
    return _stamped(build_entry(
        name=name, targets=list(CLIS), command=None, args=None, url=url,
        auth_env=auth_env if isinstance(auth_env, str) else None,
        env=None, lazy=True, readonly=False,
    ), origin, openclaw_id or name)


def _stdio_row(
    name: str,
    live: Mapping[str, Any],
    *,
    origin: str = PROVENANCE,
    openclaw_id: str | None = None,
) -> dict[str, Any]:
    """The manifest row of a locally launched server, its shapes checked by the writer."""
    command = live.get("command")
    args = live.get("args")
    env = live.get("env")
    return _stamped(build_entry(
        name=name, targets=list(CLIS),
        command=command if isinstance(command, str) else None,
        args=args if isinstance(args, list) else None,
        url=None, auth_env=None,
        env=env if isinstance(env, Mapping) else None,
        lazy=True, readonly=False,
    ), origin, openclaw_id or name)


def _refuse_what_the_inverse_would_drop(server: UnmanagedServer) -> None:
    """A live entry the inverse cannot carry intact is a refusal, never a quiet trim.

    `renderer_cli._adopt_entry` reads OpenClaw's remote shape as a URL plus an auth
    reference, so an `env` block beside a URL is dropped on the way in. The person would
    discover it when the server stopped working, which is the kind of silence adoption
    exists to avoid.
    """
    spec = server.spec
    remote = spec.get("url") is not None or spec.get("type") == "http"
    env = spec.get("env")
    if remote and isinstance(env, Mapping) and env:
        raise ValueError(t(
            "'{name}' passes environment variables to a remote server, and the manifest has no "
            "place for them: nothing was adopted and nothing was dropped", name=server.name,
        ))


def manifest_entry(
    server: UnmanagedServer,
    *,
    origin: str = PROVENANCE,
    openclaw_id: str | None = None,
) -> dict[str, Any]:
    """The canonical row for one live entry, in the renderer's own vocabulary.

    The translation is the renderer's inverse, not a second dialect: a live
    entry is read by `renderer_cli._adopt_entry` -- the function that already
    reads OpenClaw's `mcp.servers` shape -- and then validated by the writer
    that will publish it, so an entry the engine cannot honor is refused here
    rather than written and discovered later.
    """
    from nexgen_core.renderer_cli import _adopt_entry

    _refuse_what_the_inverse_would_drop(server)
    live = _adopt_entry("openclaw", dict(server.spec))
    effective_id = openclaw_id or getattr(server, "openclaw_id", None) or getattr(server, "plugin_id", None) or server.name
    if live.get("transport") == "http":
        return _http_row(server.name, live, origin=origin, openclaw_id=effective_id)
    return _stdio_row(server.name, live, origin=origin, openclaw_id=effective_id)


def adopt_server(server: UnmanagedServer, *, home=None, vault_data=None) -> ServerAdoption:
    """Declare one live server in the manifest, lazily, atomically, or say why not."""
    try:
        entry = manifest_entry(server)
    except ValueError as exc:
        return ServerAdoption(server.name, False, str(exc))
    code, message = insert_entry(server.name, entry, home=home, vault_data=vault_data, targets=list(CLIS))
    return ServerAdoption(server.name, code == 0, message)
