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

import collections
import json
import math
import queue
import os
import re
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import zlib
from pathlib import Path
from typing import Any

SERVER_NAME = "lazy-mcp"
SERVER_VERSION = "0.2.1"
FRAMING = "headers"
PROTOCOL_VERSION = "2026-07-28"
#: Version offered when the waiter has to open the legacy handshake with a
#: stdio server: the `mcp` SDK 1.x (FastMCP) refuses `tools/list` before
#: `initialize`, while the 2026-07-28 contract is stateless and needs none.
LEGACY_PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_PROTOCOL_VERSIONS = ("2026-07-28", "2025-11-25", "2025-03-26", "2024-11-05")
UNSUPPORTED_PROTOCOL_VERSION = -32022
SERVER_CAPABILITIES: dict[str, Any] = {"tools": {"listChanged": False}}

INDEX_MAX_TOKENS = int(os.environ.get("LAZY_MCP_INDEX_MAX_TOKENS", "4000"))
INDEX_TTL = int(os.environ.get("LAZY_MCP_INDEX_TTL", "300"))
IDLE_MS = int(os.environ.get("LAZY_MCP_IDLE_MS", "600000"))
MAX_CONCURRENT_REQUESTS = int(os.environ.get("LAZY_MCP_MAX_CONCURRENCY", "32"))
STDERR_KEEP_LINES = 40
STDERR_SHOWN_CHARS = 600


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
SSE_ACCEPT = "application/json, text/event-stream"
LIST_TTL_MS = 60000
LIST_CACHE_SCOPE = "private"

#: MCP tool annotations, advisory only (a server can claim read-only and
#: still mutate): they are surfaced to help the caller choose, never to
#: replace this proxy's own fail-closed confirmation gate.
HINT_KEYS = ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint")

#: `resultType` is required on every result by 2026-07-28. Older clients pass
#: unknown result fields through, so the same envelope serves both eras.
RESULT_TYPE = "complete"
MAX_REPLY_BYTES = 8 * 1024 * 1024
TRANSPORT_ERROR = -32000


class _TransportError(RuntimeError):
    """A safe diagnostic, never the raw peer payload or exception message."""


def _process_helpers():
    scripts = str(Path(__file__).resolve().parents[2] / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    from nexgen_core.processes import force_stop_process_tree, windows_command_argv
    return force_stop_process_tree, windows_command_argv


def _matching_reply(raw: bytes, rid: int) -> dict[str, Any] | None:
    try:
        reply = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise _TransportError("invalid JSON response") from exc
    if not isinstance(reply, dict) or reply.get("jsonrpc") != "2.0":
        raise _TransportError("invalid JSON-RPC response")
    if "method" in reply or type(reply.get("id")) is not type(rid) or reply["id"] != rid:
        return None  # notification, reverse request, or reply to another request
    if ("result" in reply) == ("error" in reply):
        raise _TransportError("response needs exactly one result or error")
    if not isinstance(reply.get("result", reply.get("error")), dict):
        raise _TransportError("response result or error must be an object")
    return reply


_MACHINE_ENV_CACHE: tuple[tuple, dict[str, str]] = ((), {})


def _machine_environment() -> dict[str, str]:
    """The variables this user's machine declares for every session: `~/.config/environment.d/*.conf`.

    A CLI inherits them only if it was started from the graphical session; started over SSH, from a service or
    from some other launcher it does not, and then a server that needs one of its tokens silently went missing
    ("Vercel is not there"). The gateway resolves the variables a manifest entry *declares* from here when the
    process does not have them, so what a server can reach no longer depends on how the CLI happened to be
    launched. POSIX only: Windows keeps user variables in the registry, which every process inherits.
    """
    global _MACHINE_ENV_CACHE
    if os.name == "nt":
        return {}
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    files = sorted(Path(base, "environment.d").glob("*.conf")) if Path(base, "environment.d").is_dir() else []
    try:
        signature = tuple((str(f), f.stat().st_mtime_ns) for f in files)
    except OSError:
        return {}
    if signature == _MACHINE_ENV_CACHE[0]:
        return _MACHINE_ENV_CACHE[1]
    values: dict[str, str] = {}
    for path in files:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for raw in lines:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            values[key.strip()] = value
    _MACHINE_ENV_CACHE = (signature, values)
    return values


def _env_default(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    if value is None:
        value = _machine_environment().get(name)
    return default if value is None else value


def _expand_placeholders(text: str, ctx: dict[str, str]) -> str:
    def repl(match):
        name, default = match.group(1), ""
        if ":-" in name:
            name, default = name.split(":-", 1)
        return ctx.get(name, _env_default(name, default))
    return re.sub(r"\$\{([^}]+)\}", repl, text)


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


def _with_trials(data: dict[str, Any]) -> dict[str, Any]:
    """The manifest plus the servers on trial on this machine (never synced, they expire by themselves)."""
    scripts = str(Path(__file__).resolve().parents[2] / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    try:
        from nexgen_core import mcp_trials

        servers = mcp_trials.overlay(dict(data.get("servers") or {}))
    except Exception as exc:  # noqa: BLE001 - a broken trial file must never take the gateway down
        print(f"[lazy-mcp] trials ignored ({type(exc).__name__})", file=sys.stderr)
        return data
    return {**data, "servers": servers}


def _resolve_manifest() -> dict[str, Any]:
    return _with_trials(_read_manifest())


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


def _placement_module():
    """The engine's one placement rule (nexgen_core.mcp_placement), or None when the package is unreachable."""
    scripts = str(Path(__file__).resolve().parents[2] / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    try:
        from nexgen_core import mcp_placement
    except ImportError as exc:
        print(f"[lazy-mcp] placement rule unavailable ({exc}); serving every lazy server", file=sys.stderr)
        return None
    return mcp_placement


def served_cli() -> str:
    """The CLI this gateway is mounted in, as the renderer told it (LAZY_MCP_CLI), or '' when it was not told."""
    return os.environ.get("LAZY_MCP_CLI", "").strip().lower()


def _served_by_gateway(srv: dict[str, Any], cli: str, placement) -> bool:
    """Whether this gateway, mounted in `cli`, serves `srv`.

    It used to serve every `lazy: true` server to every CLI, so a server mounted directly in a CLI was also in
    that CLI's gateway, and `targets` and `enabled` were ignored. Now it serves exactly what the plan routes
    behind it for the CLI it is in. A gateway that was never told its CLI (a config written before this) keeps
    the old behaviour until the next guard cycle rewrites it.
    """
    if cli and placement is not None:
        return placement.place(srv, cli).kind == placement.GATEWAY_KIND
    return bool(srv.get("lazy")) or srv.get("exposure") == "lazy"


_PURE_REFERENCE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-)?\}$")


def _server_context() -> dict[str, str]:
    vault_data = os.environ.get("AGENT_VAULT_DATA") or os.environ.get("KNOWLEDGE_VAULT_PATH") or str(Path.home() / "KnowledgeVault")
    return {"AGENT_ENGINE_ROOT": _resolve_engine_root(), "AGENT_VAULT_DATA": vault_data, "KNOWLEDGE_VAULT_PATH": vault_data}


def _missing_credentials(srv: dict[str, Any]) -> list[str]:
    """The variables this entry needs and this session does not have.

    What counts: `require_env` (the manifest's own gate), a bearer token it declares (`auth.env`), and any
    `env` entry that is a plain reference to a secret-looking variable. A missing one used to make a server
    vanish from the index, or fail with a bare 401, with nothing saying why.
    """
    needed: list[str] = []
    if srv.get("require_env"):
        needed.append(str(srv["require_env"]))
    auth = srv.get("auth")
    if isinstance(auth, dict) and auth.get("env"):
        needed.append(str(auth["env"]))
    if isinstance(srv.get("env"), dict):
        for value in srv["env"].values():
            match = _PURE_REFERENCE.match(str(value).strip())
            if match and _is_secret_name(match.group(1)):
                needed.append(match.group(1))
    seen: list[str] = []
    for name in needed:
        if name not in seen and not _env_default(name):
            seen.append(name)
    return seen


def _prepare_entry(name: str, srv: dict[str, Any], ctx: dict[str, str]) -> dict[str, Any] | None:
    """A manifest entry ready to spawn: platform override applied, placeholders and templates expanded.

    Carries `_unavailable` (why it cannot work in this session) instead of being dropped, so the index can say
    "needs VERCEL_API_TOKEN" rather than leave the server out in silence. None when the entry is unusable.
    """
    entry = {k: v for k, v in srv.items()}
    if sys.platform == "win32" and "windows" in entry:
        win_override = entry.pop("windows")
        if isinstance(win_override, dict):
            entry.update(win_override)
    entry.setdefault("readonly", False)
    entry.setdefault("readonly_tools", [])
    missing = _missing_credentials(entry)
    if missing:
        entry["_unavailable"] = "needs " + ", ".join(missing) + " in this session's environment"
    # DEPS_WORKSPACE is a SELF-PRESERVING token here: its real value is
    # known only at spawn time, after provisioning. Expanding it now to
    # anything else (including '') would destroy the token before the
    # spawn can substitute the provisioned workspace.
    load_ctx = dict(ctx)
    load_ctx["DEPS_WORKSPACE"] = "${DEPS_WORKSPACE}"
    try:
        if entry.get("command"):
            entry["command"] = _expand_placeholders(_expand_templates(str(entry["command"])), load_ctx)
        if isinstance(entry.get("args"), list):
            entry["args"] = [_expand_placeholders(_expand_templates(str(a)), load_ctx) for a in entry["args"]]
        if entry.get("url"):
            entry["url"] = _expand_placeholders(_expand_templates(str(entry["url"])), load_ctx)
        if isinstance(entry.get("env"), dict):
            entry["env"] = {k: _expand_placeholders(_expand_templates(str(v)), load_ctx) for k, v in entry["env"].items()}
    except Exception as exc:
        # Fail closed per entry: this server is withdrawn from the
        # index with the reason on stderr, instead of spawning literal
        # {{ }} text or taking the whole index down with it.
        print(f"[lazy-mcp] server '{name}' withdrawn: inline template error ({exc})", file=sys.stderr)
        return None
    return entry


def _lazy_servers() -> dict[str, dict[str, Any]]:
    """The servers behind this gateway for the CLI it serves (placeholders expanded; `_unavailable` says why one cannot work)."""
    data = _resolve_manifest()
    cli = served_cli()
    placement = _placement_module() if cli else None
    ctx = _server_context()
    out: dict[str, dict[str, Any]] = {}
    for name, srv in (data.get("servers") or {}).items():
        if not isinstance(srv, dict) or name == "lazy-mcp" or not _served_by_gateway(srv, cli, placement):
            continue
        entry = _prepare_entry(name, srv, ctx)
        if entry is not None:
            out[name] = entry
    return out


def served_entry(name: str) -> dict[str, Any] | None:
    """One server's entry regardless of where the plan places it (the trimming shim serves a server mounted directly)."""
    srv = (_resolve_manifest().get("servers") or {}).get(name)
    return _prepare_entry(name, srv, _server_context()) if isinstance(srv, dict) else None


#: What a child server may inherit. The proxy's own environment holds every token the user's shell
#: exports; a server that needs one declares it (`env: {NAME: "${NAME}"}` in the manifest entry) instead
#: of getting all of them. The list is what runtimes need to start at all (`npx`, `node`, python,
#: docker, a proxy) plus the engine's own variables; the MCP SDK's default is stricter still.
_INHERITED_EXACT = frozenset({
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "TERM", "LANG", "LANGUAGE", "TZ", "TMPDIR", "TMP", "TEMP",
    "PWD", "DISPLAY", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS", "VIRTUAL_ENV", "PYTHONPATH",
    "PYTHONIOENCODING", "PYTHONUTF8", "NODE_PATH", "NODE_OPTIONS", "NODE_EXTRA_CA_CERTS", "SSL_CERT_FILE",
    "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
    "NO_PROXY", "DOCKER_HOST", "COLORTERM", "LD_LIBRARY_PATH",
    # Windows
    "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT", "USERPROFILE", "USERNAME", "USERDOMAIN",
    "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432",
    "COMMONPROGRAMFILES", "HOMEDRIVE", "HOMEPATH", "ALLUSERSPROFILE", "PUBLIC", "COMPUTERNAME", "OS",
    "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE",
})
_INHERITED_PREFIXES = (
    "LC_", "XDG_", "NEXGEN_", "AGENT_", "KNOWLEDGE_VAULT_", "NPM_CONFIG_", "NODE_", "NVM_", "VOLTA_", "UV_",
    "PIPX_", "PYTHON", "LAZY_MCP_",
)
def _secret_shapes():
    """The shared definition of what a secret looks like (see nexgen_core.secret_shapes)."""
    scripts = str(Path(__file__).resolve().parents[2] / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    from nexgen_core import secret_shapes

    return secret_shapes


def _is_secret_name(name: str) -> bool:
    """A name that looks like a secret is never inherited, even under an allowed prefix."""
    return _secret_shapes().is_secret_name(name)


def _child_environment(declared: dict[str, str] | None = None) -> dict[str, str]:
    """The environment a child server starts with: the allowed part of the proxy's own, then what the
    manifest entry declared (declared means wanted, so a secret named there is passed on purpose)."""
    inherited = {
        name: value for name, value in os.environ.items()
        if (name.upper() in _INHERITED_EXACT or name.upper().startswith(_INHERITED_PREFIXES))
        and not _is_secret_name(name)
    }
    inherited.update(declared or {})
    return inherited


def _redact(text: str, env: dict[str, str]) -> str:
    """Hides, in text a child printed, the values of the secrets this proxy handed it, and the usual shapes."""
    for name, value in env.items():
        if _is_secret_name(name) and len(value) >= 6:
            text = text.replace(value, "[redacted]")
    return _secret_shapes().redact(text)


class _ServerHandle:
    """One live connection to a lazy server (stdio subprocess or HTTP)."""

    def __init__(self, name: str, spec: dict[str, Any]):
        self.name = name
        self.spec = spec
        self.proc: subprocess.Popen | None = None
        self.index: list[dict[str, Any]] | None = None
        self.index_at = 0.0
        self.last_use = time.time()
        # FAIL-CLOSED classification: a tool is mutating unless explicitly
        # allowlisted. MCP tool annotations are advisory and come from
        # untrusted servers, so they are never trusted here; the manifest
        # grants read-only status per server (`readonly: true`) or per tool
        # (`readonly_tools: [...]`). Anything else requires confirmation.
        self.readonly_server = bool(spec.get("readonly"))
        self.readonly_tools = set(spec.get("readonly_tools") or [])
        #: Tools the manifest hides (`tools_deny`) or restricts to (`tools_allow`): never listed, never callable.
        self.tools_deny = set(spec.get("tools_deny") or [])
        self.tools_allow = set(spec.get("tools_allow") or [])
        self.active_calls = 0
        self._init_done = False
        self._rid = 600
        self._rpc_lock = threading.RLock()
        self._modern_protocol = False
        self._transport_failed = False
        self._io_worker: threading.Thread | None = None
        #: The last thing the server's stderr said, and why the last request failed. Both used to be
        #: thrown away (stderr went to DEVNULL), which turned "the server crashed on an import" into
        #: "tool not found" with nothing to say why.
        self._stderr_lines: collections.deque[str] = collections.deque(maxlen=STDERR_KEEP_LINES)
        self._stderr_lock = threading.Lock()
        self._child_env: dict[str, str] = {}
        self.last_error: str | None = None

    def visible(self, tool: str) -> bool:
        if tool in self.tools_deny:
            return False
        return not self.tools_allow or tool in self.tools_allow

    def is_mutating(self, tool: str) -> bool:
        return not (self.readonly_server or tool in self.readonly_tools)

    def _provision(self) -> dict[str, str]:
        """Provision the declared deps (npx pin or pinned git workspace).

        Lazy by design: runs exactly once, at the first spawn of the server.
        Fail-closed: a dep that cannot be verified or provisioned refuses the
        spawn with the reason (pin missing, network, build failure...).
        """
        deps = self.spec.get("deps")
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
            return {"error": f"{self.name}: provisioning unavailable ({exc}); manifest 'deps' cannot be honoured"}
        state_dir = _state_dir()
        ctx, error = ensure_deps(deps, state_dir, install=True, server=self.name)
        if error:
            return {"error": f"{self.name}: {error}"}
        return ctx

    def _start_stdio(self) -> None:
        if self.proc and self.proc.poll() is None:
            return
        if self.proc is not None:
            self.stop()  # descendants may still own pipes after their parent exits
        ctx = self._provision()
        if "error" in ctx:
            raise RuntimeError(ctx["error"])
        cmd = [self.spec["command"]] + list(self.spec.get("args", []))
        declared = {str(k): str(v) for k, v in (self.spec.get("env") or {}).items()}
        if ctx:
            cmd = [_expand_placeholders(c, ctx) for c in cmd]
            declared = {k: _expand_placeholders(v, ctx) for k, v in declared.items()}
        env = _child_environment(declared)
        if ctx:
            env = {k: _expand_placeholders(v, ctx) for k, v in env.items()}
        self._child_env = env
        is_win = sys.platform == "win32"
        kwargs: dict[str, Any] = {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": False,
            "env": env,
        }
        if not is_win:
            kwargs["start_new_session"] = True
        _, command_argv = _process_helpers()
        self.proc = subprocess.Popen(command_argv(cmd), **kwargs)
        self._process_group = self.proc.pid if not is_win else None
        with self._stderr_lock:
            self._stderr_lines.clear()
        threading.Thread(target=self._drain_stderr, args=(self.proc.stderr,), daemon=True).start()
        # A respawned process has not been initialized: the handshake state
        # belongs to the process, not to the handle.
        self._init_done = False
        self._modern_protocol = False

    def _drain_stderr(self, stream) -> None:
        """Keeps the tail of the child's stderr. A full pipe would block the server, so it is always read."""
        try:
            for raw in iter(lambda: stream.readline(4096), b""):
                text = raw.decode("utf-8", "replace").rstrip()
                if text:
                    with self._stderr_lock:
                        self._stderr_lines.append(text)
        except (OSError, ValueError):
            pass  # the pipe was closed under us: the process is gone

    def stderr_tail(self) -> str:
        """What the server last said on stderr, redacted and short enough to put in an error."""
        with self._stderr_lock:
            lines = list(self._stderr_lines)
        if not lines:
            return ""
        text = _redact(" | ".join(lines[-6:]), self._child_env)
        return text[-STDERR_SHOWN_CHARS:]

    def _request_params(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self._modern_protocol or method == "initialize":
            return params
        meta = params.get("_meta")
        return {**params, "_meta": {
            **(meta if isinstance(meta, dict) else {}),
            "io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION,
            "io.modelcontextprotocol/clientCapabilities": {},
        }}

    def _timeout(self, method: str) -> float:
        key = "tool" if method == "tools/call" else "startup"
        timeouts = self.spec.get("timeouts") or {}
        value = timeouts.get(key, 90) if isinstance(timeouts, dict) else None
        if isinstance(value, bool):
            raise _TransportError("invalid declared timeout")
        try:
            timeout = float(value)
        except (TypeError, ValueError) as exc:
            raise _TransportError("invalid declared timeout") from exc
        if not math.isfinite(timeout) or timeout <= 0:
            raise _TransportError("invalid declared timeout")
        return timeout

    def _error(self, message: str) -> dict[str, Any]:
        return {"error": {"code": TRANSPORT_ERROR, "message": f"{self.name}: {message}"}}

    def _exchange(self, operation, timeout: float, *, stdio: bool) -> dict[str, Any]:
        """Bound all pipe/socket I/O, including a blocked stdin write.

        The worker never starts a process after cancellation. Provisioning
        retains its own bounded contract; this deadline starts with RPC I/O.
        No tool call is retried when its outcome becomes unknown.
        """
        cancelled = threading.Event()
        deadline = time.monotonic() + timeout
        completed: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)

        def run():
            try:
                result = operation(cancelled, deadline)
            except _TransportError as exc:
                result = self._error(str(exc))
            except urllib.error.HTTPError as exc:
                result = self._error(f"HTTP {exc.code}")
            except TimeoutError:
                result = self._error("RPC timeout; operation outcome may be unknown")
            except Exception as exc:  # transport boundary, raw diagnostics may carry secrets
                result = self._error(f"transport failed ({type(exc).__name__})")
            completed.put(result)

        worker = threading.Thread(target=run, daemon=True)
        if stdio:
            self._io_worker = worker
        worker.start()
        try:
            result = completed.get(timeout=max(0.0, deadline - time.monotonic()))
        except queue.Empty:
            cancelled.set()
            if stdio:
                self.stop()
            worker.join(timeout=0.2)
            if stdio and not worker.is_alive():
                self._close_pipes()
            return self._error("RPC timeout; operation outcome may be unknown")
        except BaseException:  # cancellation must also stop in-flight pipe I/O
            cancelled.set()
            if stdio:
                self.stop()
            worker.join(timeout=0.2)
            raise
        finally:
            self.last_use = time.time()
        if stdio and result.get("error", {}).get("code") == TRANSPORT_ERROR:
            self.stop()  # malformed/partial transport cannot poison the next request
        worker.join(timeout=0.2)
        if stdio:
            self._io_worker = None
            if result.get("error", {}).get("code") == TRANSPORT_ERROR and not worker.is_alive():
                self._close_pipes()
        return result

    def _rpc_stdio(self, method: str, params: dict[str, Any], rid: int) -> dict[str, Any]:
        try:
            timeout = self._timeout(method)
            self._start_stdio()
            params = self._request_params(method, params)
            payload = (json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params}) + "\n").encode("utf-8")
        except _TransportError as exc:
            return self._error(str(exc))
        except Exception as exc:  # launch/serialization boundary, no raw environment values
            return self._error(f"failed to start request ({type(exc).__name__})")
        proc = self.proc
        assert proc and proc.stdin and proc.stdout

        def exchange(cancelled, deadline):
            proc.stdin.write(payload)
            proc.stdin.flush()
            received = 0
            while not cancelled.is_set() and time.monotonic() < deadline:
                line = proc.stdout.readline(MAX_REPLY_BYTES + 1)
                if not line:
                    raise _TransportError("process output closed before a matching response")
                received += len(line)
                if received > MAX_REPLY_BYTES:
                    raise _TransportError("response exceeds output limit")
                if not line.strip():
                    continue
                reply = _matching_reply(line, rid)
                if reply is not None:
                    return reply
            raise _TransportError("RPC timeout; operation outcome may be unknown")

        return self._exchange(exchange, timeout, stdio=True)

    def _rpc_http(self, method: str, params: dict[str, Any], rid: int) -> dict[str, Any]:
        try:
            timeout = self._timeout(method)
            params = self._request_params(method, params)
            body = json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params}).encode()
            req = urllib.request.Request(self.spec["url"], data=body, headers={
                "Content-Type": "application/json", "Accept": SSE_ACCEPT,
            })
            auth = self.spec.get("auth") or {}
            auth_env = auth.get("env") if isinstance(auth, dict) else None
            if auth_env:
                token = _env_default(auth_env)
                if not token:
                    return self._error(f"missing credential: {auth_env} is not set in this session's environment")
                req.add_header("Authorization", f"Bearer {token}")
        except _TransportError as exc:
            return self._error(str(exc))
        except Exception as exc:  # configuration boundary, URLs/headers stay private
            return self._error(f"invalid HTTP request ({type(exc).__name__})")

        def exchange(cancelled, deadline):
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                sse = "text/event-stream" in resp.headers.get("Content-Type", "").lower()
                pending = bytearray()
                event: list[bytes] = []
                received = 0
                while not cancelled.is_set() and time.monotonic() < deadline:
                    # read1 returns available bytes rather than waiting for
                    # EOF or a full buffer on an open SSE connection.
                    chunk = resp.read1(65536)
                    received += len(chunk)
                    if received > MAX_REPLY_BYTES:
                        raise _TransportError("response exceeds output limit")
                    pending.extend(chunk)
                    if sse:
                        while b"\n" in pending:
                            line, _, rest = pending.partition(b"\n")
                            pending = bytearray(rest)
                            line = line.rstrip(b"\r")
                            if line.startswith(b"data:"):
                                event.append(line[5:].lstrip(b" "))
                            elif not line and event:
                                reply = _matching_reply(b"\n".join(event), rid)
                                event.clear()
                                if reply is not None:
                                    return reply
                            if cancelled.is_set() or time.monotonic() >= deadline:
                                raise _TransportError("RPC timeout; operation outcome may be unknown")
                    if not chunk:
                        if sse:
                            if event:
                                reply = _matching_reply(b"\n".join(event), rid)
                                if reply is not None:
                                    return reply
                            raise _TransportError("HTTP stream closed before a matching response")
                        reply = _matching_reply(bytes(pending), rid)
                        if reply is None:
                            raise _TransportError("HTTP response ID does not match request")
                        return reply
            raise _TransportError("RPC timeout; operation outcome may be unknown")

        return self._exchange(exchange, timeout, stdio=False)

    def rpc(self, method: str, params: dict[str, Any], rid: int) -> dict[str, Any]:
        with self._rpc_lock:
            if self.spec.get("url"):
                reply = self._rpc_http(method, params, rid)
            else:
                reply = self._rpc_stdio(method, params, rid)
            self._transport_failed = reply.get("error", {}).get("code") == TRANSPORT_ERROR
            if self._transport_failed:
                # Give the child a moment to finish printing why it is dying, then say it.
                if not self.spec.get("url"):
                    time.sleep(0.15)
                tail = self.stderr_tail()
                error = reply["error"]
                if tail:
                    error["message"] = f"{error['message']} | server stderr: {tail}"
                self.last_error = error["message"]
            elif "result" in reply:
                self.last_error = None
            return reply

    def _next_rid(self) -> int:
        self._rid += 1
        return self._rid

    def _notify_stdio(self, method: str, params: dict[str, Any]) -> bool:
        """Bound notification writes too, without waiting for a reply."""
        proc = self.proc
        if not proc or not proc.stdin:
            return False
        payload = (json.dumps({"jsonrpc": "2.0", "method": method, "params": params}) + "\n").encode()

        def send(cancelled, deadline):
            proc.stdin.write(payload)
            proc.stdin.flush()
            return {}

        reply = self._exchange(send, self._timeout(method), stdio=True)
        self._transport_failed = reply.get("error", {}).get("code") == TRANSPORT_ERROR
        return "error" not in reply

    def _initialize_stdio(self) -> bool:
        """Open the MCP handshake with a legacy stdio server.

        The stateless 2026-07-28 contract needs no handshake, but servers
        built on the `mcp` SDK 1.x (FastMCP) refuse `tools/list` until
        `initialize` has been honoured, answering "Received request before
        initialization was complete". Sending only the list request left
        them indexed with zero tools, silently unreachable. The handshake
        is idempotent per live process: a respawn resets the state where the
        process is spawned.
        """
        if self._init_done and self.proc is not None and self.proc.poll() is None:
            return True
        resp = self.rpc("initialize", {
            "protocolVersion": LEGACY_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        }, self._next_rid())
        if "error" in resp or "result" not in resp:
            return False
        if not self._notify_stdio("notifications/initialized", {}):
            return False
        self._init_done = True
        return True

    def tools_list(self) -> list[dict[str, Any]]:
        with self._rpc_lock:
            return self._tools_list()

    def _tools_list(self) -> list[dict[str, Any]]:
        if self.index is not None and time.time() - self.index_at < INDEX_TTL:
            return self.index
        # The first frame on a connection decides its protocol era
        # (handshake vs 2026-07-28) and cannot be changed later: probing with
        # the version-key envelope first pinned dual-era servers (mcp SDK 2.x,
        # e.g. drive-mcp) to the modern era, where the legacy initialize is
        # then refused and every request needs a complete envelope - the
        # server indexed with zero tools, forever. Bare requests open the
        # handshake era that every server in the fleet speaks (dual-era ones
        # included); the envelope is tried only on a fresh spawn, for a
        # modern-only server.
        resp = self.rpc("tools/list", {}, 900 + zlib.crc32(self.name.encode()) % 100)
        if resp.get("error", {}).get("code") == TRANSPORT_ERROR:
            return []
        if ("error" in resp or "result" not in resp) and not self.spec.get("url"):
            # Handshake server (mcp SDK 1.x, custom servers): it refuses the
            # list until `initialize` has been honoured.
            if self._initialize_stdio():
                resp = self.rpc("tools/list", {}, 900 + zlib.crc32(self.name.encode()) % 100)
            if self._transport_failed:
                return []
        if "error" in resp or "result" not in resp:
            # Modern-only server: respawn so the epoch-defining first frame is
            # a complete 2026-07-28 envelope. Both keys are required;
            # clientCapabilities: {} is the minimal valid value.
            self.stop()
            modern = {"_meta": {
                "io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION,
                "io.modelcontextprotocol/clientCapabilities": {},
            }}
            resp = self.rpc("tools/list", modern, 900 + zlib.crc32(self.name.encode()) % 100)
            if "result" in resp:
                self._modern_protocol = True
        if "error" in resp or "result" not in resp:
            # Error or malformed reply: report no tools WITHOUT caching.
            # Caching the failure would wedge the server as "tool not found"
            # for INDEX_TTL even after it recovers.
            return []
        tools = (resp.get("result") or {}).get("tools")
        if not isinstance(tools, list) or any(not isinstance(tool, dict) for tool in tools):
            return []  # malformed indexes are not cached as successful emptiness
        self.index = tools
        self.index_at = time.time()
        return tools

    def idle(self) -> bool:
        return bool(self.proc and self.proc.poll() is None and time.time() - self.last_use > IDLE_MS / 1000)

    def stop_if_idle(self) -> None:
        # Index/load handshakes are RPCs too. The sweeper must not kill them
        # just because Waiter.call's activity counter is zero.
        if self._rpc_lock.acquire(blocking=False):
            try:
                if self.idle():
                    self.stop()
            finally:
                self._rpc_lock.release()

    def stop(self) -> None:
        if self.proc is not None:
            force_stop, _ = _process_helpers()
            force_stop(self.proc, process_group=getattr(self, "_process_group", None))
            self._process_group = None
            self._init_done = False
            if self._io_worker is None or not self._io_worker.is_alive():
                self._close_pipes()

    def _close_pipes(self) -> None:
        if self.proc is not None:
            for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass



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
        result: dict[str, Any] = {"servers": {}, "budget_tokens": INDEX_MAX_TOKENS, "cli": served_cli() or None}
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
            for line in sys.stdin:
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
