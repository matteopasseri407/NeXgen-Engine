#!/usr/bin/env python3
"""lazy-mcp.py — the waiter: universal MCP 2.0 index + on-demand load proxy.

Always-mounted MCP server with a tiny schema (three meta-tools). Real
servers declared `lazy: true` in the canonical manifest are NOT mounted in
the CLIs; this proxy exposes their tool names as a compact index, loads a
full tool schema into the model's context on demand, and forwards calls to
the real server. That is the Claude-Code style deferral replicated for CLIs
without native support (opencode, codex, antigravity) — and the foundation
that scales when the manifest grows to tens of servers: the bootstrap pays
three meta-tools, never the schemas.

Protocol: MCP 2.0 (revision 2026-07-28), same envelope as the engine's
reference server (vault_ocr_mcp.py): stateless, version negotiated per
request via `params._meta["io.modelcontextprotocol/protocolVersion]`,
`server/discover` for capabilities, `resultType` on every result, and
`ttlMs`/`cacheScope` on the cacheable ones. Legacy handshake methods are
answered for the CLIs that still open with them.

Contract with the advisory reviews (Kimi, Opus 5):
- the index is built LIVE from each real server's tools/list (TTL-cached
  per session, never baked);
- `lazy_load` is the explicit schema-delivery step BEFORE any `lazy_call`:
  the model writes arguments only after having seen the full definition;
- loading is an availability gate, not a safety gate. `lazy_call` forwards
  only what the manifest declares read-only; anything else goes through
  `lazy_mutate`, a separate tool, so a CLI that asks the person before it runs
  a tool can approve reads and still ask about writes (one tool for both made
  that all-or-nothing). `confirm: true` on `lazy_mutate` is the model
  acknowledging the call, not a person's approval: the person's approval is the
  CLI's own prompt for that tool, which a bypass posture removes by design.
  Every action is appended to an audit log;
- the index budget is enforced: over LAZY_MCP_INDEX_MAX_TOKENS the index
  degrades to server granularity (names only) instead of growing forever;
- spawned servers die after LAZY_MCP_IDLE_MS of silence: a bordello of MCP
  servers must not mean a bordello of resident processes;
- requests are served concurrently (one slow call must not freeze a ping or a
  call to another server; one server's calls stay serial, as its pipe is);
- a child server does not inherit the secrets in the proxy's environment, and
  what it prints on stderr is kept (redacted) and shown when it fails, instead
  of the proxy answering "tool not found" and the cause being thrown away.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess  # noqa: F401 - the suite patches `lazy_mcp.subprocess.Popen`; the runs in the handle share it
import sys
import threading
import time
from pathlib import Path
from typing import Any

#: The gateway is started as a script (its own folder is then sys.path[0]) and is also loaded from a file by the
#: test suite and by mcp-trim.py, where it is not. Appended rather than inserted: this folder also holds
#: render.py, and nothing here may shadow a module that is already on the path.
_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.append(_HERE)

import lazy_env  # noqa: E402 - resolved from the gateway's own folder, prepared just above
import lazy_manifest  # noqa: E402 - same
import lazy_server_handle  # noqa: E402 - same
from lazy_server_handle import MAX_REPLY_BYTES, PROTOCOL_VERSION  # noqa: E402 - re-exported here, see below

SERVER_NAME = "lazy-mcp"
SERVER_VERSION = "0.2.1"
FRAMING = "headers"
SUPPORTED_PROTOCOL_VERSIONS = ("2026-07-28", "2025-11-25", "2025-03-26", "2024-11-05")
UNSUPPORTED_PROTOCOL_VERSION = -32022
SERVER_CAPABILITIES: dict[str, Any] = {"tools": {"listChanged": False}}

INDEX_MAX_TOKENS = int(os.environ.get("LAZY_MCP_INDEX_MAX_TOKENS", "4000"))
MAX_CONCURRENT_REQUESTS = int(os.environ.get("LAZY_MCP_MAX_CONCURRENCY", "32"))


def _state_dir() -> Path:
    """The engine's state directory: one resolver, so the audit log and the provisioning state follow
    NEXGEN_HOME and XDG_STATE_HOME like everything else. Falls back to the historical location when
    the engine package is not importable from here."""
    for scripts in (Path(__file__).resolve().parents[2] / "scripts",):
        if str(scripts) not in sys.path:
            sys.path.insert(0, str(scripts))
    try:
        from nexgen_core.paths import resolve_state_dir

        return resolve_state_dir()
    except Exception:  # noqa: BLE001 - the proxy must still start without the engine package
        return Path(os.environ.get("AGENT_STATE_DIR") or os.environ.get("XDG_STATE_HOME")
                    or str(Path.home() / ".local" / "state"))


def _log_path() -> str:
    return os.environ.get("LAZY_MCP_LOG") or str(_state_dir() / "lazy-mcp-audit.jsonl")


LIST_TTL_MS = 60000
LIST_CACHE_SCOPE = "private"

#: MCP tool annotations, advisory only (a server can claim read-only and
#: still mutate): they are surfaced to help the caller choose, never to
#: replace this proxy's own fail-closed confirmation gate.
HINT_KEYS = ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint")

#: `resultType` is required on every result by 2026-07-28. Older clients pass
#: unknown result fields through, so the same envelope serves both eras.
RESULT_TYPE = "complete"
MAX_REQUEST_BYTES = 8 * 1024 * 1024
_AUDIT_LOCK = threading.Lock()


def _process_helpers():
    scripts = str(Path(__file__).resolve().parents[2] / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    from nexgen_core.processes import force_stop_process_tree, no_window_kwargs, windows_command_argv
    return force_stop_process_tree, windows_command_argv, no_window_kwargs


#: One load of the gateway owns one cache: a test that resets `_MACHINE_ENV_CACHE` on the loaded module is
#: resetting the only one this module uses. The parsing and the POSIX rule live in lazy_env.
_MACHINE_ENV_CACHE: tuple[tuple, dict[str, str]] = ((), {})


def _machine_environment() -> dict[str, str]:
    """The variables this user's machine declares for every session: `~/.config/environment.d/*.conf`.

    A CLI started over SSH, from a service or from another launcher inherits none of them, and then a server
    that needs one of its tokens silently went missing ("Vercel is not there"). The gateway resolves the
    variables a manifest entry *declares* from here when the process does not have them, so what a server can
    reach no longer depends on how the CLI happened to be launched. POSIX only: Windows keeps user variables in
    the registry, which every process inherits. This file owns the cache and the config home; lazy_env parses.
    """
    global _MACHINE_ENV_CACHE
    config_home = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    _MACHINE_ENV_CACHE, values = lazy_env.machine_environment(config_home, _MACHINE_ENV_CACHE)
    return values


def _env_default(name: str, default: str = "") -> str:
    """A variable from the process, then from the machine's session; the default only when neither has it."""
    return lazy_env.env_default(name, default, _machine_environment())


def _declared_value(name: str) -> str:
    """A credential a manifest entry declares it needs: the process, then the machine's session, then the deposit."""
    return lazy_env.declared_value(name, _machine_environment())


#: The waiter expands the SAME inline dialect the renderer does (`{{ .os }}`,
#: `{{ if eq .os "windows" }}…{{ end }}`): a server defined once in the
#: manifest must not render differently depending on which path serves it.
#: The implementation lives in nexgen_core.config, next to the file's other
#: lazily-imported engine pieces. Only an IMPORT failure degrades to
#: passthrough (with a once-per-process warning): a malformed template is
#: an error the entry must carry, because sending a literal `{{ }}` to a
#: spawn is exactly the quiet wrong the dialect exists to prevent.
_template_warning_shown = False


def _template_context() -> dict[str, str]:
    """Same resolvers the renderer uses, same values: one {{ .vault }} must
    not mean two paths depending on which code expands it."""
    vault = os.environ.get("AGENT_VAULT_DATA") or os.environ.get("KNOWLEDGE_VAULT_PATH")
    if not vault:
        import nexgen_core.paths as _paths

        vault = str(_paths.resolve_vault_data())
    import nexgen_core.paths as _paths

    return {
        "os": {"win32": "windows", "darwin": "darwin"}.get(sys.platform, "linux"),
        "home": str(_paths.resolve_home()),
        "vault": vault,
        "engine": _resolve_engine_root(),
    }


def _expand_templates(text: str) -> str:
    global _template_warning_shown
    if "{{" not in text:
        return text
    here = Path(__file__).resolve()
    local_scripts = here.parents[2] / "scripts"
    for candidate in (_resolve_engine_root() + "/scripts", str(local_scripts)):
        if Path(candidate).is_dir() and candidate not in sys.path:
            sys.path.insert(0, candidate)
    try:
        from nexgen_core.config import expand_inline_templates
        return expand_inline_templates(text, _template_context())
    except ImportError as exc:
        # The expander itself is unreachable: degrade, visibly.
        if not _template_warning_shown:
            print(f"[lazy-mcp] inline templates not expanded ({exc})", file=sys.stderr)
            _template_warning_shown = True
        return text
    # TemplateError and everything else propagate: the caller marks the
    # entry unusable instead of spawning literal {{ }} text.


def _resolve_manifest() -> dict[str, Any]:
    return lazy_manifest.with_trials(_read_manifest())


def _read_manifest() -> dict[str, Any]:
    vault = Path(os.environ.get("AGENT_VAULT_DATA") or os.environ.get("KNOWLEDGE_VAULT_PATH")
                 or str(Path.home() / "KnowledgeVault"))
    path = vault / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml"
    if not path.is_file():
        return {}
    try:
        import yaml
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        print(f"[lazy-mcp] manifest unreadable ({path}): {exc}", file=sys.stderr)
        return {}


def _resolve_engine_root() -> str:
    env_root = os.environ.get("AGENT_ENGINE_ROOT")
    if env_root:
        return env_root
    here = Path(__file__).resolve().parents[2]
    if (here / "agent-universal-layer").is_dir():
        return str(here)
    fallback = Path.home() / ".nexgen-engine" / "03-INFRA"
    if fallback.is_dir():
        return str(fallback)
    return str(here)


def _server_context() -> dict[str, str]:
    vault_data = os.environ.get("AGENT_VAULT_DATA") or os.environ.get("KNOWLEDGE_VAULT_PATH") or str(Path.home() / "KnowledgeVault")
    return {"AGENT_ENGINE_ROOT": _resolve_engine_root(), "AGENT_VAULT_DATA": vault_data, "KNOWLEDGE_VAULT_PATH": vault_data}


def _credentials() -> lazy_env.Credentials:
    """The credential chain of this load, read through this file's machine-environment cache."""
    return lazy_env.Credentials(_machine_environment)


def _lazy_servers() -> dict[str, dict[str, Any]]:
    """The servers behind this gateway for the CLI it serves (placeholders expanded; `_unavailable` says why one cannot work)."""
    return lazy_manifest.lazy_servers(_resolve_manifest(), _server_context(), _credentials(), _expand_templates)


def served_entry(name: str) -> dict[str, Any] | None:
    """One server's entry regardless of where the plan places it (the trimming shim serves a server mounted directly)."""
    return lazy_manifest.served_entry(
        name, _resolve_manifest(), _server_context(), _credentials(), _expand_templates)



def _redact(text: str, env: dict[str, str]) -> str:
    """Hides, in text a child printed, the values of the secrets this proxy handed it, and the usual shapes."""
    return lazy_env.redact(text, env)


def _provision(name: str, spec: dict[str, Any]) -> dict[str, str]:
    """Provision the declared deps (npx pin or pinned git workspace).

    Lazy by design: runs exactly once, at the first spawn of the server.
    Fail-closed: a dep that cannot be verified or provisioned refuses the
    spawn with the reason (pin missing, network, build failure...).

    It lives here, next to the state directory it needs, and not in the transport: the handle asks this file for
    it at spawn time.
    """
    deps = spec.get("deps")
    if not deps:
        return {}
    # The waiter imports the nexgen_core that lives NEXT TO it: the
    # checkout that is actually running this file is guaranteed to carry
    # a compatible provisioner, while AGENT_ENGINE_ROOT may point at a
    # different (installed) engine version. The local candidate must end
    # up FIRST on sys.path, so insert the env one before it.
    here = Path(__file__).resolve()
    local_scripts = here.parents[2] / "scripts"
    candidates = []
    env_root = os.environ.get("AGENT_ENGINE_ROOT")
    if env_root:
        candidates.append(Path(env_root) / "scripts")
    candidates.append(local_scripts)
    for scripts in candidates:
        if str(scripts) not in sys.path:
            sys.path.insert(0, str(scripts))
    try:
        from nexgen_core.provision import ensure_deps
    except Exception as exc:
        return {"error": f"{name}: provisioning unavailable ({exc}); manifest 'deps' cannot be honoured"}
    state_dir = _state_dir()
    ctx, error = ensure_deps(deps, state_dir, install=True, server=name)
    if error:
        return {"error": f"{name}: {error}"}
    return ctx


class _ServerHandle(lazy_server_handle.ServerHandle):
    """One live connection to a lazy server, bound to this file's state.

    Provisioning, the credential chain, the process helpers and the reply limit are asked of this module at call
    time: the suite replaces those names here, as it always has, and every handle this gateway builds must see
    the replacement.
    """

    def __init__(self, name: str, spec: dict[str, Any]):
        super().__init__(
            name, spec,
            provisioning=lambda: _provision(name, spec),
            credentials=_credentials(),
            process_helpers=lambda: _process_helpers(),
            reply_limit=lambda: MAX_REPLY_BYTES,
            client_info={"name": SERVER_NAME, "version": SERVER_VERSION},
        )


class Waiter:
    """Lazy proxy state: per-server handles, index budget, audit, idle sweep."""

    def __init__(self) -> None:
        self.handles: dict[str, _ServerHandle] = {}
        self.loaded: set[tuple[str, str]] = set()
        self._rid = 1000
        self._lock = threading.RLock()
        self._shutdown = threading.Event()
        self._sweeper = threading.Thread(target=self._sweep_loop, daemon=True)
        self._sweeper.start()

    def _sweep_loop(self) -> None:
        while not self._shutdown.wait(30):
            with self._lock:
                for name, handle in list(self.handles.items()):
                    if handle.active_calls == 0:
                        handle.stop_if_idle()

    def shutdown(self) -> None:
        """Request all owned process stops before waiting for any one server."""
        self._shutdown.set()
        with self._lock:
            handles = list(self.handles.values())
        workers = []

        def stop(handle):
            try:
                handle.stop()
            except Exception as exc:  # cleanup boundary, do not leak config/URLs
                print(f"[lazy-mcp] cleanup failed for {handle.name} ({type(exc).__name__})", file=sys.stderr)

        for handle in handles:
            worker = threading.Thread(target=stop, args=(handle,), daemon=True)
            worker.start()
            workers.append(worker)
        deadline = time.monotonic() + 5.0
        for worker in workers:
            worker.join(timeout=max(0.0, deadline - time.monotonic()))

    def _audit(self, server: str, tool: str, action: str, confirmed: bool = False) -> None:
        try:
            with _AUDIT_LOCK:
                with open(_log_path(), "a", encoding="utf-8") as fh:
                    fh.write(json.dumps({
                        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                        "server": server, "tool": tool, "action": action,
                        "confirmed": confirmed,
                    }) + "\n")
        except OSError:
            pass

    def _handle(self, name: str, spec: dict[str, Any]) -> _ServerHandle:
        with self._lock:
            h = self.handles.get(name)
            if h is None:
                h = _ServerHandle(name, spec)
                self.handles[name] = h
                return h
            if h.spec != spec:
                # Manifest changed under a live waiter (command/args/env/
                # url/readonly/deps): the old handle would serve the stale
                # config until process exit. Swap it; the old proc dies.
                try:
                    h.stop()
                except Exception:
                    pass
                h = _ServerHandle(name, spec)
                self.handles[name] = h
                # A dropped spec may have renamed the tool surface: prior
                # loads could authorize args against a stale schema.
                self.loaded = {(s, t) for (s, t) in self.loaded if s != name}
            return h

    def index(self) -> dict[str, Any]:
        servers = _lazy_servers()
        result: dict[str, Any] = {"servers": {}, "budget_tokens": INDEX_MAX_TOKENS,
                                  "cli": lazy_manifest.served_cli() or None}
        estimated = 0
        for name in sorted(servers):
            if servers[name].get("_unavailable"):
                # Not started: it cannot work in this session, and the reason is the useful part.
                result["servers"][name] = {"mutating": True, "tools": [], "error": servers[name]["_unavailable"],
                                           "unavailable": True}
                continue
            handle = self._handle(name, servers[name])
            tools = [t for t in handle.tools_list() if handle.visible(t.get("name", ""))]
            entries = []
            for t in tools:
                desc = (t.get("description") or "").split("\n", 1)[0][:100]
                entry: dict[str, Any] = {"name": t.get("name", ""), "hint": desc}
                annotations = t.get("annotations")
                if isinstance(annotations, dict):
                    hints = {
                        k: bool(annotations[k])
                        for k in HINT_KEYS
                        if k in annotations and isinstance(annotations[k], bool)
                    }
                    if hints:
                        entry["annotations"] = hints
                entries.append(entry)
                estimated += len(t.get("name", "")) // 4 + len(desc) // 4 + 2
            result["servers"][name] = {"mutating": not handle.readonly_server, "tools": entries}
            if not entries and handle.last_error:
                # An empty list used to be all there was, and "no tools" read as "nothing to offer".
                result["servers"][name]["error"] = handle.last_error
        # Budget enforcement: over budget the index degrades to names only
        # (server granularity), so a bordello of servers cannot blow the
        # bootstrap with descriptions.
        if estimated > INDEX_MAX_TOKENS:
            for name in result["servers"]:
                result["servers"][name]["tools"] = [{"name": t["name"]} for t in result["servers"][name]["tools"]]
            result["degraded"] = True
        return result

    def load(self, server: str, tool: str) -> dict[str, Any]:
        servers = _lazy_servers()
        if server not in servers:
            return {"error": f"unknown lazy server: {server}"}
        if servers[server].get("_unavailable"):
            return {"error": f"server '{server}' is unavailable: {servers[server]['_unavailable']}"}
        handle = self._handle(server, servers[server])
        tools = [t for t in handle.tools_list() if handle.visible(t.get("name", ""))]
        for t in tools:
            if t.get("name") == tool:
                self._audit(server, tool, "load")
                with self._lock:
                    self.loaded.add((server, tool))
                return {"tool": t}
        if not tools and handle.last_error:
            # The server never answered: that is not "tool not found", and the cause is the useful part.
            return {"error": f"server '{server}' is unavailable: {handle.last_error}"}
        return {"error": f"tool '{tool}' not found on {server}"}

    def call(
        self, server: str, tool: str, arguments: dict[str, Any], confirm: bool = False, *, via: str = "lazy_call",
    ) -> dict[str, Any]:
        servers = _lazy_servers()
        if server not in servers:
            return {"error": f"unknown lazy server: {server}"}
        if servers[server].get("_unavailable"):
            return {"error": f"server '{server}' is unavailable: {servers[server]['_unavailable']}"}
        handle = self._handle(server, servers[server])
        if not handle.visible(tool):
            self._audit(server, tool, "refused", confirmed=False)
            return {"error": f"tool '{tool}' on '{server}' is hidden by the manifest (tools_deny / tools_allow)"}
        with self._lock:
            was_loaded = (server, tool) in self.loaded
        if handle.is_mutating(tool):
            if via != "lazy_mutate":
                # A tool the manifest does not declare read-only goes through its own tool, so the CLI's
                # permission for lazy_call can be granted for reads without granting every write.
                self._audit(server, tool, "refused", confirmed=False)
                return {"error": (
                    f"'{tool}' on '{server}' is not explicitly read-only and requires "
                    "confirmation: call lazy_mutate with \"confirm\": true after reviewing "
                    "the arguments. (Read-only status is granted per server or tool "
                    "in the manifest; the default is fail-closed. lazy_mutate is the tool "
                    "your CLI asks the person about.)"
                )}
            if not confirm:
                self._audit(server, tool, "refused", confirmed=False)
                return {"error": f"'{tool}' on '{server}' changes state: call again with \"confirm\": true."}
        with self._lock:
            handle.active_calls += 1
        try:
            resp = handle.rpc("tools/call", {"name": tool, "arguments": arguments}, self._next_rid())
        finally:
            with self._lock:
                handle.active_calls -= 1
        self._audit(server, tool, "call", confirmed=confirm or not handle.is_mutating(tool))
        if not was_loaded:
            resp.setdefault("hint", "tool definition was never loaded with lazy_load before this call")
        return resp

    def _next_rid(self) -> int:
        self._rid += 1
        return self._rid


WAITER = Waiter()


def _meta_tools() -> list[dict[str, Any]]:
    return [
        {
            "name": "lazy_list",
            "description": "Index of servers behind the lazy proxy: tool names (plus one-line hints) per server, and the mutating flag. Call this first to see what exists; the full index has a token budget and degrades to names only when exceeded.",
            "annotations": {
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": True,
            },
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "lazy_load",
            "description": "Load the FULL definition (description + input schema) of one tool into context BEFORE calling it. Mandatory before lazy_call for any tool whose arguments you have not seen in full.",
            "annotations": {
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": True,
            },
            "inputSchema": {
                "type": "object",
                "properties": {
                    "server": {"type": "string", "description": "Server name from lazy_list"},
                    "tool": {"type": "string", "description": "Tool name from lazy_list"},
                },
                "required": ["server", "tool"],
                "additionalProperties": False,
            },
        },
        {
            "name": "lazy_call",
            "description": "Forward a call to a lazy server's READ-ONLY tool (the ones the manifest declares read-only). Refuses anything else: those go through lazy_mutate. Always lazy_load first.",
            "annotations": {
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": True,
            },
            "inputSchema": {
                "type": "object",
                "properties": {
                    "server": {"type": "string"},
                    "tool": {"type": "string"},
                    "arguments": {"type": "object", "description": "Arguments per the loaded schema"},
                },
                "required": ["server", "tool", "arguments"],
                "additionalProperties": False,
            },
        },
        {
            "name": "lazy_mutate",
            "description": "Forward a call that may change state, to a lazy server's tool the manifest does not declare read-only. Needs \"confirm\": true after you reviewed the arguments. This is the tool your CLI asks the person about: do not route writes around it.",
            "annotations": {
                "readOnlyHint": False,
                "destructiveHint": True,
                "idempotentHint": False,
                "openWorldHint": True,
            },
            "inputSchema": {
                "type": "object",
                "properties": {
                    "server": {"type": "string"},
                    "tool": {"type": "string"},
                    "arguments": {"type": "object", "description": "Arguments per the loaded schema"},
                    "confirm": {"type": "boolean", "description": "Must be true"},
                },
                "required": ["server", "tool", "arguments", "confirm"],
                "additionalProperties": False,
            },
        },
    ]


INSTRUCTIONS = (
    "lazy-mcp is the universal on-demand loader for the MCP servers declared "
    "lazy in the manifest. Workflow: 1) lazy_list to see what exists; "
    "2) lazy_load(server, tool) to bring the full schema into context; "
    "3) lazy_call to forward a read-only tool, or lazy_mutate (with "
    "confirm: true) for one that may change state. Every action is audited."
)


def _requested_protocol(req: dict[str, Any]) -> str | None:
    meta = (req.get("params") or {}).get("_meta") or {}
    if isinstance(meta, dict):
        v = meta.get("io.modelcontextprotocol/protocolVersion")
        if isinstance(v, str) and v:
            return v
    return None


_OUT_LOCK = threading.Lock()


def _emit(message: dict[str, Any]) -> None:
    """Replies come from several threads now; one line must never interleave with another."""
    line = json.dumps(message) + "\n"
    with _OUT_LOCK:
        sys.stdout.write(line)
        sys.stdout.flush()


def _result(req_id: Any, value: dict[str, Any], *, ttl_ms: int | None = None,
            cache_scope: str | None = None) -> None:
    payload = dict(value)
    payload.setdefault("resultType", RESULT_TYPE)
    meta = dict(payload.get("_meta") or {})
    meta.setdefault("io.modelcontextprotocol/serverInfo",
                    {"name": SERVER_NAME, "version": SERVER_VERSION})
    payload["_meta"] = meta
    if ttl_ms is not None:
        payload.setdefault("ttlMs", ttl_ms)
        payload.setdefault("cacheScope", cache_scope or "private")
    _emit({"jsonrpc": "2.0", "id": req_id, "result": payload})


def _error(req_id: Any, code: int, message: str, data: Any = None) -> None:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    _emit({"jsonrpc": "2.0", "id": req_id, "error": err})


def _handle(req: dict[str, Any]) -> None:
    method = req.get("method")
    req_id = req.get("id")

    requested = _requested_protocol(req)
    if requested is not None and requested not in SUPPORTED_PROTOCOL_VERSIONS:
        if req_id is not None:
            _error(req_id, UNSUPPORTED_PROTOCOL_VERSION,
                   f"unsupported protocol version: {requested}",
                   {"supported": list(SUPPORTED_PROTOCOL_VERSIONS), "requested": requested})
        return

    if method == "server/discover":
        _result(req_id, {
            "supportedVersions": list(SUPPORTED_PROTOCOL_VERSIONS),
            "capabilities": SERVER_CAPABILITIES,
            "instructions": INSTRUCTIONS,
        }, ttl_ms=LIST_TTL_MS, cache_scope=LIST_CACHE_SCOPE)
        return
    if method == "initialize":
        protocol = (req.get("params") or {}).get("protocolVersion")
        if protocol not in SUPPORTED_PROTOCOL_VERSIONS:
            protocol = PROTOCOL_VERSION
        _result(req_id, {
            "protocolVersion": protocol,
            "capabilities": SERVER_CAPABILITIES,
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })
        return
    if method in ("notifications/initialized",):
        return
    if method == "ping":
        _result(req_id, {})
        return
    if method == "tools/list":
        _result(req_id, {"tools": _meta_tools()}, ttl_ms=LIST_TTL_MS, cache_scope=LIST_CACHE_SCOPE)
        return
    if method == "tools/call":
        params = req.get("params") or {}
        name = params.get("name", "")
        args = params.get("arguments") or {}
        if name == "lazy_list":
            _result(req_id, {"content": [{"type": "text", "text": json.dumps(WAITER.index(), ensure_ascii=False)}]})
            return
        if name == "lazy_load":
            res = WAITER.load(str(args.get("server", "")), str(args.get("tool", "")))
            if "error" in res:
                _result(req_id, {"isError": True, "content": [{"type": "text", "text": res["error"]}]})
                return
            _result(req_id, {"content": [{"type": "text", "text": json.dumps(res["tool"], ensure_ascii=False)}]})
            return
        if name in ("lazy_call", "lazy_mutate"):
            res = WAITER.call(
                str(args.get("server", "")), str(args.get("tool", "")),
                args.get("arguments") or {}, bool(args.get("confirm", False)), via=name,
            )
            if isinstance(res, dict) and "error" in res:
                msg = res["error"] if isinstance(res["error"], str) else res["error"].get("message", "error")
                _result(req_id, {"isError": True, "content": [{"type": "text", "text": msg}]})
                return
            content = []
            if isinstance(res, dict) and res.get("result"):
                content = res["result"].get("content") or []
            hint = res.get("hint") if isinstance(res, dict) else None
            if hint:
                content = content + [{"type": "text", "text": f"[hint] {hint}"}]
            _result(req_id, {"content": content})
            return
        _result(req_id, {"isError": True, "content": [{"type": "text", "text": f"unknown tool: {name}"}]})
        return
    _error(req_id, -32601, f"method not found: {method}")


_SLOTS = threading.BoundedSemaphore(MAX_CONCURRENT_REQUESTS)


def _respond_request_too_large(raw_line: str) -> None:
    """The request exceeded MAX_REQUEST_BYTES: answer instead of hanging the client."""
    try:
        req = json.loads(raw_line[:65536])
        req_id = req.get("id") if isinstance(req, dict) else None
    except (ValueError, UnicodeError):
        req_id = None
    if req_id is not None:
        _error(req_id, -32600, f"request exceeds {MAX_REQUEST_BYTES} bytes")


def _guarded(req: Any) -> None:
    """Handles one request; whatever goes wrong becomes an answer, never silence (a client waiting on a
    reply that a dead thread will never send waits for ever)."""
    try:
        _handle(req)
    except Exception as exc:  # noqa: BLE001 - the loop must survive any single request
        req_id = req.get("id") if isinstance(req, dict) else None
        if req_id is not None:
            _error(req_id, -32603, f"internal error ({type(exc).__name__})")


def _dispatch(req: dict[str, Any]) -> None:
    """Runs a tool call on its own thread. Before, the reader handled each request in turn: a six-second
    call to one server held a ping and a call to another server for six seconds, and a 600-second n8n
    timeout would have frozen every server behind it for ten minutes. A server's own calls stay serial
    (its pipe is), so only unrelated work overlaps. Threads are daemons so a stuck call cannot keep the
    process alive after the client has gone."""
    if not _SLOTS.acquire(blocking=False):
        if req.get("id") is not None:
            _error(req["id"], -32000, f"too many requests in flight (limit {MAX_CONCURRENT_REQUESTS})")
        return

    def run() -> None:
        try:
            _guarded(req)
        finally:
            _SLOTS.release()

    threading.Thread(target=run, daemon=True, name="lazy-mcp-request").start()


def main() -> int:
    previous = {}

    def interrupted(signum, _frame):
        raise SystemExit(128 + signum)

    if threading.current_thread() is threading.main_thread():
        for signum in (signal.SIGTERM, getattr(signal, "SIGBREAK", signal.SIGTERM)):
            if signum not in previous:
                previous[signum] = signal.signal(signum, interrupted)
    try:
        if FRAMING == "headers":
            while True:
                line = sys.stdin.readline(MAX_REQUEST_BYTES + 2)
                if not line:
                    break
                if len(line.encode("utf-8", errors="replace")) > MAX_REQUEST_BYTES + 1:
                    _respond_request_too_large(line)
                    continue
                line = line.strip()
                if not line:
                    continue
                try:
                    req = json.loads(line)
                except (ValueError, UnicodeError):
                    continue
                if isinstance(req, dict) and req.get("method") == "tools/call":
                    _dispatch(req)
                else:
                    _guarded(req)
        return 0
    finally:
        WAITER.shutdown()
        for signum, handler in previous.items():
            signal.signal(signum, handler)


if __name__ == "__main__":
    sys.exit(main())
