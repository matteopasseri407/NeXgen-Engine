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

import os
import subprocess  # noqa: F401 - the suite patches `lazy_mcp.subprocess.Popen`; the spawns in the handle share it
import sys
from pathlib import Path
from typing import Any

#: The gateway is started as a script (its own folder is then sys.path[0]) and is also loaded from a file by the
#: test suite and by mcp-trim.py, where it is not. Appended rather than inserted: this folder also holds
#: render.py, and nothing here may shadow a module that is already on the path.
_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.append(_HERE)

import lazy_env  # noqa: E402 - resolved from the gateway's own folder, prepared just above
import lazy_frontend  # noqa: E402 - same
import lazy_manifest  # noqa: E402 - same
import lazy_server_handle  # noqa: E402 - same
import lazy_waiter  # noqa: E402 - same
from lazy_frontend import SERVER_NAME, SERVER_VERSION  # noqa: E402 - the wire identity, re-exported here
from lazy_server_handle import MAX_REPLY_BYTES  # noqa: E402 - re-exported here, see the handle below


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


class Waiter(lazy_waiter.Waiter):
    """Lazy proxy state, bound to this file's server resolution, handle class and audit log."""

    def __init__(self) -> None:
        super().__init__(servers=lambda: _lazy_servers(), new_handle=_ServerHandle, audit_path=_log_path)


WAITER = Waiter()




def main() -> int:
    """Run the gateway on stdin/stdout: the frontend owns the loop, this file owns the state it serves."""
    return lazy_frontend.serve(WAITER)


if __name__ == "__main__":
    sys.exit(main())
