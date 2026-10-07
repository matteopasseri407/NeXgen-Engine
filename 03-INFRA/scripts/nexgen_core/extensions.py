"""What is installed: MCP servers and skills, where each came from, what it is pinned to, whether upstream moved.

Read-only and offline. The upstream answer is the last one the dependency watch left in the machine's state folder
(`third-party-status.json` and the guardian's verdicts), so `nexgen info` stays instant and never reaches the network.

Provenance is one word per item, the same for both kinds:

- ``engine``: shipped with the engine and updated with it (`nexgen update`).
- ``yours``: lives in your Vault or on your disk; nothing updates it but you.
- ``third-party``: somebody else's package, repository or service; the dependency watch tells you when it moved and
  `nexgen skills bump` / `nexgen mcp bump` raise the pin after the guardian has looked at it.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from nexgen_core.config import ConfigError, load_mcp_manifest, load_skills_manifest
from nexgen_core.depwatch import STATUS_FILE_NAME, _command_tokens, _npm_spec_tokens
from nexgen_core.mcp_placement import CLIS, place
from nexgen_core.paths import mcp_manifest, resolve_engine_root, resolve_home, resolve_state_dir, resolve_vault_data, skills_manifest
from nexgen_core.thirdparty_names import short_name

ENGINE, YOURS, THIRD_PARTY = "engine", "yours", "third-party"

#: The server the engine's own deploy publishes behind a variable, not behind a path.
_ENGINE_URL_VARS = ("${VAULT_LIBRARY_URL}",)

_ABSOLUTE = re.compile(r"^(?:/|~|[A-Za-z]:[\\/])")


#: Launchers that fetch somebody else's package: whatever they are given as arguments, the code is not ours or yours.
_PACKAGE_LAUNCHERS = ("npx", "npx.cmd", "uvx", "pipx", "bunx", "docker", "deno")


def mcp_provenance(srv: dict[str, Any]) -> str:
    tokens = _command_tokens(srv)
    url = str(srv.get("url") or "")
    blob = " ".join([*tokens, url])
    executable = Path(tokens[0].replace("\\", "/")).name.lower() if tokens else ""
    if executable in _PACKAGE_LAUNCHERS:
        return THIRD_PARTY
    if "${AGENT_ENGINE_ROOT}" in blob or executable.startswith("nexgen") or any(v in url for v in _ENGINE_URL_VARS):
        return ENGINE
    if "${AGENT_VAULT_DATA}" in blob or any(_ABSOLUTE.match(tok) for tok in tokens):
        return YOURS
    return THIRD_PARTY


def _mcp_pin(srv: dict[str, Any]) -> tuple[str, str]:
    """(what it is pinned to, how that reads): `pinned`, `unpinned` (runs whatever the registry serves), or `none`."""
    tokens = _command_tokens(srv)
    wrapped = srv.get("wraps")
    wrapped_specs = _npm_spec_tokens([str(w) for w in wrapped]) if isinstance(wrapped, list) else []
    is_npx = bool(tokens) and tokens[0].lower() in ("npx", "npx.cmd")
    specs = _npm_spec_tokens(tokens[1:]) if is_npx else []
    if specs or wrapped_specs:
        return ", ".join(specs + wrapped_specs), "pinned"
    if is_npx:
        package = next((tok for tok in tokens[1:] if not tok.startswith("-")), "")
        if package:
            return package, "unpinned"
    return "", "none"


def _cells(srv: dict[str, Any]) -> dict[str, str]:
    return {cli: place(srv, cli).kind for cli in CLIS}


def _summarize_where(cells: dict[str, str]) -> str:
    """`direct`, `gateway`, `mixed` (some CLIs one way, some the other) or `off`; CLIs that do not get it are not counted."""
    kinds = {kind for kind in cells.values() if kind != "absent"}
    if not kinds:
        return "off"
    return next(iter(kinds)) if len(kinds) == 1 else "mixed"


def _why_off(srv: dict[str, Any]) -> str:
    """The reason a server is mounted nowhere: the one from a CLI it is aimed at, not "not in targets" for the others."""
    reasons = [place(srv, cli).why for cli in CLIS]
    return next((why for why in reasons if why != "not in targets"), reasons[0])


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


class _Upstream:
    """The last answer from the dependency watch and the guardian, looked up by item."""

    def __init__(self, state_dir: Path) -> None:
        status = _read_json(state_dir / "nexgen" / STATUS_FILE_NAME)
        self.checked_at: float | None = status.get("checked_at") if isinstance(status.get("checked_at"), (int, float)) else None
        self._pins: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for pin in status.get("pins") or []:
            if isinstance(pin, dict) and pin.get("what"):
                self._pins.setdefault(self._key(str(pin["what"])), []).append(pin)
        if not self._pins:
            # A status file from before it carried the pins: it still names what is stale.
            for what in status.get("stale") or []:
                self._pins.setdefault(self._key(str(what)), []).append({"what": what, "stale": True})
        self._verdicts: dict[tuple[str, str], dict[str, Any]] = {}
        guard = _read_json(state_dir / "nexgen" / "third-party-guard.json")
        for tier in ("auto", "batch", "hold"):
            for item in guard.get(tier) or []:
                if isinstance(item, dict) and item.get("what"):
                    self._verdicts[self._key(str(item["what"]))] = {**item, "tier": tier}

    @staticmethod
    def _key(what: str) -> tuple[str, str]:
        return ("mcp" if what.startswith("MCP server") else "skill", short_name(what))

    def lookup(self, kind: str, name: str) -> dict[str, Any]:
        pins = self._pins.get((kind, name), [])
        verdict = self._verdicts.get((kind, name))
        stale = next((p for p in pins if p.get("stale")), None)
        if stale is not None:
            out: dict[str, Any] = {
                "state": "stale",
                "pinned": stale.get("pinned") or (verdict or {}).get("pinned"),
                "upstream": stale.get("upstream") or (verdict or {}).get("upstream"),
            }
            if verdict:
                out["verdict"] = {"auto": "ready", "batch": "ready", "hold": "held"}[verdict["tier"]]
                out["plain"] = verdict.get("plain") or ""
            return out
        if any(p.get("upstream") for p in pins):
            return {"state": "current"}
        return {"state": "unknown"}


def _load_servers(vault_data: Path) -> tuple[dict[str, dict[str, Any]], str | None]:
    from nexgen_core import mcp_trials

    path = mcp_manifest(vault_data)
    if not path.is_file():
        return {}, None
    try:
        return mcp_trials.overlay(load_mcp_manifest(path).get("servers", {})), None
    except ConfigError as exc:
        return {}, str(exc)


def mcp_rows(servers: dict[str, dict[str, Any]], upstream: _Upstream) -> list[dict[str, Any]]:
    rows = []
    for name, srv in servers.items():
        if not isinstance(srv, dict):
            continue
        cells = _cells(srv)
        pin, pin_state = _mcp_pin(srv)
        denied = srv.get("tools_deny")
        allowed = srv.get("tools_allow")
        state = "off" if srv.get("enabled") is False else "trial" if "_trial_expires" in srv else "on"
        row = {
            "name": name,
            "provenance": mcp_provenance(srv),
            "state": state,
            "tier": srv.get("tier") or "",
            "where": _summarize_where(cells),
            "why_off": _why_off(srv) if _summarize_where(cells) == "off" else "",
            "placement": cells,
            "clis": [cli for cli, kind in cells.items() if kind != "absent"],
            "pin": pin,
            "pin_state": pin_state,
            "hidden_tools": len(denied) if isinstance(denied, list) else 0,
            "only_tools": len(allowed) if isinstance(allowed, list) else 0,
            "update": upstream.lookup("mcp", name) if pin_state == "pinned" else {"state": "n/a"},
        }
        rows.append(row)
    return rows


def _skill_pin(entry: dict[str, Any]) -> str:
    if entry.get("origin") == "github" and entry.get("commit"):
        return str(entry["commit"])[:9]
    deps = entry.get("deps")
    if isinstance(deps, dict):
        if deps.get("kind") == "npx" and deps.get("spec"):
            return str(deps["spec"])
        if deps.get("kind") == "git" and deps.get("rev"):
            return str(deps["rev"])[:9]
    install = entry.get("install")
    if isinstance(install, list):
        specs = _npm_spec_tokens([str(tok) for tok in install])
        if specs:
            return ", ".join(specs)
    return str(entry.get("version") or "")


def _engine_copy(name: str, engine_root: Path, vault_data: Path) -> str | None:
    """For a skill the engine also ships: whether the Vault's own copy still matches it (`same`/`differs`)."""
    from nexgen_core.skill_sources import same_tree_content

    shipped = engine_root / "agent-universal-layer" / "skills" / name
    if not shipped.is_dir():
        return None
    copy = vault_data / "03-INFRA" / "agent-universal-layer" / "skills" / name
    if not copy.is_dir():
        return None
    return "same" if same_tree_content(shipped, copy) else "differs"


def skill_rows(vault_data: Path, engine_root: Path, home: Path, upstream: _Upstream) -> tuple[list[dict[str, Any]], str | None, list[str]]:
    """Rows for the declared skills, an error if the manifest cannot be read, and the library entries outside it."""
    from nexgen_core.skills import SkillMaterializer

    path = skills_manifest(vault_data)
    if not path.is_file():
        return [], None, []
    try:
        declared = load_skills_manifest(path).get("skills", {})
    except ConfigError as exc:
        return [], str(exc), []
    library = SkillMaterializer(vault_data=vault_data, engine_root=engine_root, home=home).library_dir
    present = {p.name for p in library.iterdir() if p.is_dir()} if library.is_dir() else set()
    rows = []
    for name, entry in declared.items():
        if not isinstance(entry, dict):
            continue
        origin = str(entry.get("origin") or "vault")
        copy = _engine_copy(name, engine_root, vault_data) if origin == "vault" else None
        provenance = ENGINE if origin == "engine" or copy is not None else YOURS if origin == "vault" else THIRD_PARTY
        pin = _skill_pin(entry)
        rows.append({
            "name": name,
            "provenance": provenance,
            "origin": origin,
            "exposure": str(entry.get("exposure") or "lazy"),
            "pin": pin,
            "in_library": name in present,
            "engine_copy": copy,
            "update": upstream.lookup("skill", name) if provenance == THIRD_PARTY and pin else {"state": "n/a"},
        })
    return rows, None, sorted(present - set(declared))


def collect(*, home: Path | None = None, vault_data: Path | None = None, engine_root: Path | None = None,
            state_dir: Path | None = None) -> dict[str, Any]:
    resolved_home = resolve_home(home)
    vault = resolve_vault_data(resolved_home, vault_data)
    engine = engine_root if engine_root is not None else resolve_engine_root(resolved_home)
    state = resolve_state_dir(resolved_home, override=state_dir)
    upstream = _Upstream(state)
    servers, mcp_error = _load_servers(vault)
    skills, skills_error, outside = skill_rows(vault, engine, resolved_home, upstream)
    return {
        "mcp": mcp_rows(servers, upstream),
        "skills": skills,
        "skills_outside_manifest": outside,
        "errors": [e for e in (mcp_error, skills_error) if e],
        "upstream_checked_at": upstream.checked_at,
    }


def counts(items: list[dict[str, Any]]) -> dict[str, int]:
    out = {ENGINE: 0, YOURS: 0, THIRD_PARTY: 0}
    for item in items:
        out[item["provenance"]] += 1
    return out


def updates(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Every third-party item upstream has moved past: kind, name, from, to, and whether the guardian cleared it."""
    out = []
    for kind, rows in (("mcp", data["mcp"]), ("skill", data["skills"])):
        for row in rows:
            update = row["update"]
            if update.get("state") == "stale":
                out.append({"kind": kind, "name": row["name"], **update})
    return out


def age_text(checked_at: float | None, now: float | None = None) -> str | None:
    if checked_at is None:
        return None
    hours = max(0.0, ((now if now is not None else time.time()) - checked_at) / 3600)
    if hours < 1:
        return "<1h"
    if hours < 48:
        return f"{int(hours)}h"
    return f"{int(hours // 24)}d"
