"""Antigravity MCP dialect (canonical file + fan-out symlinks)."""
from __future__ import annotations

import contextlib
import copy
from pathlib import Path
from typing import Any

from nexgen_core.files import atomic_write_text, backup_file, publish_symlink
from nexgen_core.i18n import t
from nexgen_core.mcp_render import (
    http_bridge_entry,
    load_json_object,
    merge_entry,
    write_json_config,
)
from nexgen_core.paths import ANTIGRAVITY_CONSUMER_DIRS, antigravity_config

#: The per-server keys this renderer writes. Every other key of an entry is Antigravity's
#: own state, documented in its MCP page: `disabled`, `enabledTools`, `disabledTools`,
#: `tools.<tool>.eager`/`background`, `timeoutSeconds`, `oauth`, `authProviderType`.
_OWNED_KEYS = frozenset({"command", "args", "env", "serverUrl", "url", "headers"})


def _consumer_paths(renderer, canonical: Path) -> list[Path]:
    """Every path besides the canonical file that Antigravity reads its servers from."""
    paths = [renderer.home / ".gemini" / directory / "mcp_config.json" for directory in ANTIGRAVITY_CONSUMER_DIRS]
    return [path for path in paths if path != canonical]


def _link_or_copy(target: Path, canonical: Path) -> None:
    """Points `target` at `canonical`, or copies it where links are not allowed.

    Windows denies symlink creation without a privilege, and a copy holding the
    canonical bytes is what Antigravity reads there: what matters is that no
    variant is left behind. A copy that fails too is not raised from here: the
    fan-out check reads the copies back on the next doctor run and reports the
    one that did not land.
    """
    try:
        publish_symlink(target, canonical)
    except OSError:
        with contextlib.suppress(OSError):  # nexgen: allow reason="last resort after a refused link; the fan-out check reports a copy that did not land"
            target.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(target, canonical.read_text(encoding="utf-8"))


def _servers_kept_in_copies(renderer, canonical: Path) -> dict[str, Any]:
    """Servers that live only in a real file at one of the consumer paths.

    Those paths used to be deleted and replaced by a link to the canonical
    file with no backup, so whatever someone had added to one of them (a
    server added from Antigravity's own screen, or by hand) disappeared at
    the next guard cycle. They are carried into the canonical file instead,
    where the rest of the engine already treats unknown servers as the
    user's. A copy that cannot be read contributes nothing, but it is still
    backed up before it is replaced.
    """
    kept: dict[str, Any] = {}
    for path in _consumer_paths(renderer, canonical):
        if path.is_symlink() or not path.is_file():
            continue
        with contextlib.suppress(ValueError, OSError):
            servers = load_json_object(path).get("mcpServers")
            if isinstance(servers, dict):
                for name, entry in servers.items():
                    kept.setdefault(name, entry)
    return kept


def _agy_backend_is_local(renderer) -> bool:
    """Whether OpenClaw drives the local `agy` CLI here: the plugin and the CLI share one home."""
    from nexgen_core.mcp_render import load_json_object
    from nexgen_core.openclaw.terms import local_agy_backend
    from nexgen_core.paths import openclaw_config

    try:
        data = load_json_object(openclaw_config(renderer.home))
    except (OSError, ValueError):  # nexgen: allow reason="an unreadable OpenClaw config never changes what agy outside it mounts"
        return False
    return local_agy_backend(data) is not None


def _servers_openclaw_mounts_directly(renderer, own: dict[str, Any]) -> dict[str, Any]:
    """What `agy` must see when OpenClaw drives it too: the direct servers, never the gateway.

    The `google-antigravity-cli` plugin starts the same `agy`, from the same home, so the
    file this dialect writes is read on both sides. The plugin bridges in, per run, the
    servers OpenClaw gives the agent as `openclaw__<name>`, the `lazy-mcp` gateway among
    them when OpenClaw mounts it. Then a lazy server is already served there, and a direct
    copy here would be started at every turn, outside OpenClaw's `tools.deny`. Only when
    OpenClaw mounts no gateway is a direct copy the one road to a lazy server.
    """
    from nexgen_core.mcp_placement import DIRECT, GATEWAY, place

    manifest = renderer._manifest_servers()  # noqa: SLF001 - the placement rule is asked by name
    openclaw_direct = renderer.load_resolved_servers("openclaw")
    bridged_gateway = GATEWAY in openclaw_direct
    merged = dict(own)
    for name, entry in openclaw_direct.items():
        srv = manifest.get(name)
        if name == GATEWAY or not isinstance(srv, dict):
            continue
        placement = place(srv, "antigravity")
        if placement.kind == DIRECT or (placement.mounted and not bridged_gateway):
            merged[name] = entry
    merged.pop(GATEWAY, None)
    return merged


def _expected_servers(renderer) -> dict[str, Any]:
    """The servers Antigravity's file must carry here; the doctor asks it via `servers_written_for`."""
    servers = renderer.load_resolved_servers("antigravity")
    if _agy_backend_is_local(renderer):
        servers = _servers_openclaw_mounts_directly(renderer, servers)
    return servers


def _legacy_repeats_the_canonical_servers(legacy: Path, canonical: Path) -> bool:
    """True when every server the legacy file mounts is also in the canonical file.

    That is the engine's own stale output: the fan-out copies the canonical bytes
    where links cannot be created, and the app saving the canonical file then
    leaves that copy behind. A legacy file holding a server of its own is a second
    source of truth instead, and choosing between the two is a person's call.
    """
    try:
        legacy_servers = load_json_object(legacy).get("mcpServers")
        canonical_servers = load_json_object(canonical).get("mcpServers")
    except (OSError, ValueError):  # nexgen: allow reason="a file that is not JSON cannot be proven redundant, so the render stops and names both paths as it did"
        return False
    if not isinstance(legacy_servers, dict) or not isinstance(canonical_servers, dict):
        return False
    return set(legacy_servers) <= set(canonical_servers)


def _settle_legacy_layout(renderer, canonical: Path, *, write: bool) -> None:
    """Moves the real file to the officially read path when an older release left a link there.

    Releases before this one wrote `.gemini/antigravity/mcp_config.json` and
    fanned links out from it, `.gemini/config/mcp_config.json` among them.
    Google reads that last one, so the real file has to be there: left as a
    link, an app that saves the file replaces the link with a copy of its own
    and the two diverge with nothing noticing.

    The old content is backed up, published at the canonical path, and the old
    location becomes one more copy pointing back at it. Two real files that
    disagree stop the render with both paths named: picking one would drop
    whatever only the other held, so that is a person's call. The exception is a
    stale copy that holds no server of its own (what the fan-out leaves on a
    machine where links cannot be created): it is backed up and re-published from
    the canonical file, because refusing would stop every render until a human
    moved a file aside.
    """
    legacy = renderer.home / ".gemini" / "antigravity" / canonical.name
    if canonical.is_symlink():
        if legacy.is_symlink():
            return  # not the layout this migration knows: leave it to a person
        try:
            points_at_legacy = canonical.resolve() == legacy.resolve()
        except OSError:  # nexgen: allow reason="an unresolvable link is not the legacy layout, so there is nothing to migrate"
            return
        if not points_at_legacy:
            return
        # A target an uninstall removed is the same layout with nothing in it:
        # the render writes the content, and the link is rebuilt from here.
        text = legacy.read_text(encoding="utf-8") if legacy.is_file() else ""
        if write:
            if legacy.is_file():
                backup_file(legacy, keep=3)
            atomic_write_text(canonical, text)
            _link_or_copy(legacy, canonical)
        return
    if not legacy.is_file() or legacy.is_symlink():
        return
    if canonical.is_file() and canonical.read_bytes() != legacy.read_bytes():
        if not _legacy_repeats_the_canonical_servers(legacy, canonical):
            raise ValueError(
                f"Refusing to render {canonical}: it and {legacy} are both real files with different content. "
                "Move one of them aside, then render again."
            )
        if write:
            backup_file(legacy, keep=3)
            _link_or_copy(legacy, canonical)


def _render_impl(renderer, write: bool = False) -> tuple[bool, str]:
    servers = _expected_servers(renderer)
    cfg_file = antigravity_config(renderer.home)
    _settle_legacy_layout(renderer, cfg_file, write=write and not renderer.previewing)
    existing = load_json_object(cfg_file)
    before = copy.deepcopy(existing)

    mcp_servers = existing.get("mcpServers", {})
    if not isinstance(mcp_servers, dict):
        raise ValueError(f"Could not render {cfg_file}: mcpServers must be an object")
    for name, entry in _servers_kept_in_copies(renderer, cfg_file).items():
        mcp_servers.setdefault(name, entry)
    for retired in renderer.retired_server_names():
        mcp_servers.pop(retired, None)
    renderer._drop_unmounted(mcp_servers, servers, cli_target="antigravity")

    for name, srv in servers.items():
        if srv.get("transport") == "http" or srv.get("url"):
            entry = http_bridge_entry(renderer, srv)
        else:
            entry = {
                "command": srv.get("command", ""),
                "args": srv.get("args", []),
                "env": srv.get("env", {}),
            }
        mcp_servers[name] = merge_entry(mcp_servers.get(name), entry, _OWNED_KEYS)

    existing["mcpServers"] = mcp_servers
    if write:
        if cfg_file.is_file() and not renderer.previewing:
            # The app's own MCP screen saves into this same file, and one of those
            # saves can land between the read above and this write: rebase on the
            # file as it is now and re-apply only `mcpServers`, the table this
            # render owns, so nothing the app wrote in between is lost. The re-read
            # sits after the table is computed on purpose: any earlier and the save
            # it protects against still has the whole write to land in.
            fresh = load_json_object(cfg_file)
            freshly_read = copy.deepcopy(fresh)
            fresh["mcpServers"] = mcp_servers
            write_json_config(renderer, cfg_file, freshly_read, fresh)
        else:
            write_json_config(renderer, cfg_file, before, existing)
        if not renderer.previewing:
            _fan_out_antigravity(renderer, cfg_file)
    return True, t("Antigravity configuration updated")


def render(renderer, write: bool = False) -> tuple[bool, str]:
    """Generates Antigravity's MCP configuration and fans it out to its consumers."""
    return _render_impl(renderer, write)


def _fan_out_antigravity(renderer, canonical: Path) -> None:
    """Points every path Antigravity reads from at the canonical file.

    A real file that already holds the canonical bytes is left alone (the copy
    from the last cycle: replacing it again every half hour was churn), and one
    that differs is backed up before it is replaced, never just deleted. Where
    links can't be created (Windows without privileges) `_link_or_copy` copies
    instead: what matters is that no variant is left behind.
    """
    for target in _consumer_paths(renderer, canonical):
        if target.is_symlink() and target.resolve() == canonical.resolve():
            continue
        if not target.is_symlink() and target.is_file() and target.read_bytes() == canonical.read_bytes():
            continue
        if not target.is_symlink() and target.is_file():
            backup_file(target, keep=3)
        _link_or_copy(target, canonical)
