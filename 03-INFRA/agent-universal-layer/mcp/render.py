#!/usr/bin/env python3
"""MCP generator — Vault 2.0 Phase 1.
Reads manifest.yaml and, for each CLI, builds the MCP config in the right
dialect.
  - default (--diff): compares against the live file, does NOT write. Secrets
    are reduced to <AUTH> on both sides: the structure is compared without
    ever touching the tokens.
  - --write CLI: regenerates ONLY that CLI's MCP section from the manifest,
    with a surgical substitution (the rest of the file stays intact), in the
    file's own style. Makes a backup, validates, and AUTO-BLOCKS if a non-MCP
    section would end up modified.
  - Servers OUTSIDE THE MANIFEST in the live file: kept as-is and flagged by
    default (additive rule). An exact name listed under `retired_servers` is
    the deliberate exception: every generated CLI removes that stale entry.
  - Exit codes for --write: 0 = written or already compliant, 2 = blocked by
    a safety guard (see the STOP message), 3 = nothing to patch. For
    antigravity/opencode, 3 means the CLI itself isn't installed on this
    machine at all (an installed-but-never-launched antigravity/opencode
    gets its config file bootstrapped instead of skipped, so a fresh install
    never stays unconfigured forever). For claude/codex, 3 still means the
    CLI's own default config file does not exist yet -- unlike the other two,
    their writers never fabricate one from scratch.
  - --expected-servers CLI: prints (one name per line, nothing else) the
    manifest server names that target CLI and pass require_env filtering.
    Machine-consumable, meant for other scripts (agent-doctor.sh/.ps1's
    --strict block) to derive their own expected-server set instead of
    hardcoding it. Exit 0, or 2 on a manifest error.
  - --revert CLI: restore that CLI's native config from the most recent
    render.py backup (<file>.bak-*), backing up the current file first so the
    revert is itself undoable. Read-mostly and additive: touches only that
    CLI's own config file and its .bak-* siblings.
  - --adopt CLI: read-only onboarding helper. Lists the servers in a CLI's
    LIVE config that are NOT in the manifest and prints a DRAFT manifest.yaml
    entry for each (secrets redacted to <AUTH>, env-var names kept). Writes
    nothing; exit 0."""
from __future__ import annotations
import argparse, contextlib, difflib, io, json, os, platform, re, shutil, sys, time
from pathlib import Path
try:
    import tomllib
except ModuleNotFoundError:
    try:
        import tomli as tomllib
    except ModuleNotFoundError:
        tomllib = None
TOMLDecodeError = tomllib.TOMLDecodeError if tomllib is not None else ValueError

# Rendering must not add Python cache files beside user-owned data merely by
# validating a manifest or printing a diff.
sys.dont_write_bytecode = True

HOME = Path.home()
HERE = Path(__file__).parent
SCRIPTS_DIR = HERE.parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
from config_schema import (  # noqa: E402
    ConfigValidationError,
    jsonc_top_level_value_span,
    load_mcp_manifest_document,
    parse_jsonc,
    set_jsonc_top_level_value,
)

# The manifest is DATA (the user's real server list, concrete values), never
# something the engine repo should serve — read it from vault_data, not from
# HERE (this script may be running from a cloned engine, where the sibling
# manifest.yaml is only the generic product template).
VAULT_DATA = Path(
    os.environ.get("AGENT_VAULT_DATA")
    or os.environ.get("KNOWLEDGE_VAULT_PATH")
    or str(HOME / "KnowledgeVault")
)
MANIFEST = VAULT_DATA / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml"
IS_WINDOWS = platform.system() == "Windows"
WINDOWS_CMD_ENV_LIMIT = 8191
ENGINE_ROOT = Path(os.environ.get("AGENT_ENGINE_ROOT") or HERE.parent.parent)
LOCAL_PATH_PLACEHOLDERS = {
    "${AGENT_ENGINE_ROOT}": str(ENGINE_ROOT),
    "${AGENT_VAULT_DATA}": str(VAULT_DATA),
    "${KNOWLEDGE_VAULT_PATH}": str(Path(os.environ.get("KNOWLEDGE_VAULT_PATH") or VAULT_DATA)),
}


def _opencode_config_path() -> Path:
    """Return OpenCode's native config path for the current platform."""
    xdg_dir = HOME / ".config" / "opencode"
    xdg_candidates = [xdg_dir / name for name in ("opencode.jsonc", "opencode.json", "config.json")]
    for candidate in xdg_candidates:
        if candidate.is_file():
            return candidate
    if not IS_WINDOWS:
        return xdg_candidates[0]
    appdata_dir = Path(os.environ.get("APPDATA") or (HOME / "AppData" / "Roaming")) / "opencode"
    appdata_candidates = [appdata_dir / name for name in ("opencode.jsonc", "opencode.json", "config.json")]
    # OpenCode 1.18 reports this XDG-style directory as its native Windows
    # config root. Keep APPDATA as a compatibility fallback only when it is
    # the sole existing config.
    for candidate in appdata_candidates:
        if candidate.is_file():
            return candidate
    return xdg_candidates[0]


def _codex_config_path() -> Path:
    """Codex config root: honor CODEX_HOME when set, else ~/.codex. Mirrors
    council/routing.py's own resolution so render reads and writes the same
    file the runtime actually uses -- a CODEX_HOME redirect previously made
    --write report "WRITTEN" to a file Codex never mounted."""
    root = Path(os.environ.get("CODEX_HOME") or str(Path.home() / ".codex"))
    return root / "config.toml"


def _opencode_binary_present():
    """Best-effort 'OpenCode is installed on this machine' probe, mirroring
    agent-doctor.sh/.ps1's own convention (PATH lookup, falling back to the
    known ~/.opencode/bin install location) so a config directory only gets
    bootstrapped for a CLI that is actually here to use it."""
    if shutil.which("opencode"):
        return True
    bin_dir = HOME / ".opencode" / "bin"
    names = ("opencode.exe", "opencode.cmd", "opencode") if IS_WINDOWS else ("opencode",)
    return any((bin_dir / name).is_file() for name in names)


def _antigravity_present():
    """Best-effort 'Antigravity is installed on this machine' probe.

    Deliberately NOT "~/.gemini exists".  agent-sync creates ~/.gemini itself
    -- the AGENTS.md link under antigravity-cli/, the mcp_config.json symlinks
    under config/ and antigravity-ide/ -- so treating that directory as the
    install marker made the provisioner react to its OWN footprint: the first
    apply saw nothing and skipped, a later phase created ~/.gemini, and the
    NEXT apply bootstrapped the config.  A run that only converges the second
    time is invisible where a 30-minute timer exists and permanent where it
    does not, which is precisely what test_apply_is_idempotent catches.

    Probe the product's own footprint instead, the way the OpenCode probe
    above already does: the CLI binary, its known install location, and the
    settings file Antigravity writes on first launch and this provisioner
    never touches."""
    if shutil.which("agy"):
        return True
    names = ("agy.exe", "agy.cmd", "agy") if IS_WINDOWS else ("agy",)
    if any((HOME / ".local" / "bin" / name).is_file() for name in names):
        return True
    return (HOME / ".gemini" / "antigravity-cli" / "settings.json").is_file()

# Antigravity reaches HTTP MCP servers through this local bridge.  It is
# intentionally exact: an implicit update via npm exec (what the bridge
# actually launches mcp-remote through) would run new code as the user.
MCP_REMOTE_PACKAGE = "mcp-remote@0.1.38"

SECRET_KEY = re.compile(r"(token|secret|password|authorization|bearer|api[_-]?key|cookie)", re.I)
LONGTOK = re.compile(r"^[A-Za-z0-9_\-\.=+/]{40,}$")
RUNTIME_ENV_PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def _resolve_windows_command(command):
    normalized = {
        "npx": "npx.cmd",
        "node": "node.exe",
        "python3": "python",
    }.get(command, command)
    return shutil.which(normalized) or normalized


def _comspec():
    """Path of cmd.exe, the only interpreter that can run a .cmd/.bat shim.

    MCP clients launch stdio commands directly (CreateProcess) and do not
    consistently resolve .cmd shims; routing a shim through cmd.exe /d /s /c
    is the same contract council.py/routing.py already apply to their own
    subprocesses (their `_windows_command_argv`)."""
    return os.environ.get("ComSpec") or "cmd.exe"


def _windows_node_path(command_path):
    """Return an MCP-safe PATH with the Node shim directory first.

    Windows batch shims installed by npm invoke ``node`` by name.  Resolving
    npx.cmd itself to an absolute path is therefore insufficient when Node's
    directory is absent from PATH.  If the inherited PATH is already beyond
    cmd.exe's 8191-character limit, use a small system-only fallback so the
    MCP can still initialize instead of inheriting a known-broken value.
    """
    launcher_dir = str(Path(command_path).parent)
    node_path = shutil.which("node.exe") or shutil.which("node")
    node_dir = str(Path(node_path).parent) if node_path else launcher_dir
    existing = os.environ.get("PATH", "")
    entries = [launcher_dir, node_dir, *(entry for entry in existing.split(os.pathsep) if entry.strip())]
    deduped = []
    seen = set()
    for entry in entries:
        key = entry.strip().rstrip("\\/").casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(entry.strip())
    candidate = os.pathsep.join(deduped)
    if len(candidate) <= WINDOWS_CMD_ENV_LIMIT:
        return candidate
    system_root = os.environ.get("SystemRoot") or r"C:\Windows"
    fallback = [
        launcher_dir,
        node_dir,
        str(Path(system_root) / "System32"),
        system_root,
        str(Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0"),
        str(Path(system_root) / "System32" / "OpenSSH"),
    ]
    fallback_deduped = []
    fallback_seen = set()
    for entry in fallback:
        key = entry.rstrip("\\/").casefold()
        if key in fallback_seen:
            continue
        fallback_seen.add(key)
        fallback_deduped.append(entry)
    return os.pathsep.join(fallback_deduped)


def _expand_local_path_placeholders(server):
    """Expand only approved local path variables, never token placeholders."""
    expanded = dict(server)
    for field in ("command", "args"):
        value = expanded.get(field)
        values = value if isinstance(value, list) else [value]
        materialized = []
        for item in values:
            if not isinstance(item, str):
                materialized.append(item)
                continue
            for placeholder, replacement in LOCAL_PATH_PLACEHOLDERS.items():
                item = item.replace(placeholder, replacement)
            materialized.append(item)
        if isinstance(value, list):
            expanded[field] = materialized
        elif value is not None:
            expanded[field] = materialized[0]
    return expanded


def _expand_runtime_env_value(value):
    """Materialize non-sensitive ${VAR} and ${VAR:-default} placeholders.

    MCP clients parse their config as data, not through a shell.  Leaving a
    URL such as ``${VAULT_LIBRARY_URL}`` in a Node-based client therefore
    reaches ``new URL()`` literally and fails before the MCP can connect.
    Secret-shaped variables are deliberately never copied into generated
    config; bearer auth stays an environment reference in each CLI dialect.
    """
    if not isinstance(value, str):
        return value

    def replace(match):
        variable, default = match.groups()
        if SECRET_KEY.search(variable):
            return match.group(0)
        configured = os.environ.get(variable, "")
        if configured:
            return configured
        return default if default is not None else match.group(0)

    return RUNTIME_ENV_PLACEHOLDER.sub(replace, value)


def _expand_runtime_env_placeholders(server):
    """Expand runtime endpoint values without ever materializing credentials."""
    expanded = dict(server)
    if "url" in expanded:
        expanded["url"] = _expand_runtime_env_value(expanded["url"])
    if isinstance(expanded.get("env"), dict):
        expanded["env"] = {
            key: _expand_runtime_env_value(value)
            for key, value in expanded["env"].items()
        }
    return expanded

def toml_loads(text):
    if tomllib is None:
        sys.exit("render.py needs Python 3.11+ or tomli for TOML: pip install tomli")
    return tomllib.loads(text)

def redact(obj, key=None, sensitive=False):
    protected = sensitive or bool(key and SECRET_KEY.search(str(key))) or str(key).casefold() in {
        "env", "auth", "headers"
    }
    if isinstance(obj, dict):
        return {k: redact(v, k, protected) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(x, key, protected) for x in obj]
    if protected:
        return "<AUTH>"
    if isinstance(obj, str):
        if "${" in obj or "{env:" in obj:
            return "<AUTH>"
        if obj.lower().startswith("authorization:") or "bearer " in obj.lower():
            return "<AUTH>"
        if LONGTOK.match(obj) and any(c.isdigit() for c in obj):
            return "<AUTH>"
    return obj


def redact_for_log(obj):
    """Preserve MCP structure for diagnostics without emitting live scalars."""
    if isinstance(obj, dict):
        return {key: redact_for_log(value) for key, value in obj.items()}
    if isinstance(obj, list):
        return [redact_for_log(value) for value in obj]
    return "<REDACTED>"

# ---- per-dialect rendering (REAL values: env-ref where needed) ------------------

def r_claude(s):
    if s["transport"] == "stdio":
        return {"type": "stdio", "command": s["command"], "args": s.get("args", []), "env": s.get("env", {})}
    # header via env-ref: Claude Code expands ${VAR} in headers at startup,
    # so the token never stays in plaintext in .claude.json.
    return {"type": "http", "url": s["url"],
            "headers": {"Authorization": f"Bearer ${{{s['auth']['env']}}}"}}

def r_codex(s):
    if s["transport"] == "stdio":
        d = {"command": s["command"], "args": s.get("args", [])}
        if s.get("env"):
            d["env"] = s["env"]
        return d
    t = s.get("timeouts", {})
    return {"url": s["url"], "bearer_token_env_var": s["auth"]["env"],
            "startup_timeout_sec": float(t.get("startup", 120)),
            "tool_timeout_sec": float(t.get("tool", 120))}

def r_antigravity(s):
    if s["transport"] == "stdio":
        return {"command": s["command"], "args": s.get("args", []), "env": s.get("env", {})}
    # Antigravity's Windows launcher mangles spaces inside stdio args before
    # mcp-remote can expand an inherited token reference. The engine-owned
    # bridge derives a no-space header from the named environment variable at
    # runtime, so no credential is materialized in the generated JSON.
    command = _resolve_windows_command("node") if IS_WINDOWS else "node"
    env = {"PATH": _windows_node_path(command)} if IS_WINDOWS else {}
    return {"command": command,
            "args": [str(ENGINE_ROOT / "agent-universal-layer" / "mcp" / "mcp-http-bridge.mjs"),
                     s["url"], s["auth"]["env"], MCP_REMOTE_PACKAGE],
            "env": env}

def r_opencode(s):
    if s["transport"] == "stdio":
        d = {"type": "local", "command": [s["command"], *s.get("args", [])], "enabled": True}
        if s.get("timeouts", {}).get("tool"):
            d["timeout"] = int(float(s["timeouts"]["tool"]) * 1000)
        if s.get("env"):
            d["environment"] = s["env"]
        return d
    url = "{env:%s}" % s["url_env"] if s.get("url_env") else s["url"]
    auth = "Bearer {env:%s}" % s["auth"]["env"]
    return {"type": "remote", "url": url, "headers": {"Authorization": auth}, "enabled": True, "oauth": False}

CLI = {
    "claude":      dict(render=r_claude,      name=lambda n: n),
    "codex":       dict(render=r_codex,       name=lambda n: n.replace("-", "_")),
    "antigravity": dict(render=r_antigravity, name=lambda n: n),
    "opencode":    dict(render=r_opencode,    name=lambda n: n),
}

def os_view(s):
    """View of the server for the current OS: if we're on Windows and the
    server has a 'windows:' block (command/args/env/... override), apply it;
    otherwise discard the 'windows' key. This way the single manifest serves
    both OSes — Windows values get populated by running render.py on the
    Windows machine, not guessed. Common interpreter wrapper names are
    normalized after the explicit override is applied, because MCP clients
    launch stdio commands directly and do not consistently resolve .cmd
    shims the way an interactive shell does."""
    if IS_WINDOWS:
        merged = {**s, **(s.get("windows") or {})}
        merged.pop("windows", None)
        merged = _expand_local_path_placeholders(merged)
        if merged.get("transport") == "stdio":
            command = merged.get("command")
            resolved = _resolve_windows_command(command)
            if command in {"npx", "npx.cmd"}:
                merged["env"] = {
                    **(merged.get("env") or {}),
                    "PATH": _windows_node_path(resolved),
                }
            if str(resolved).lower().endswith((".cmd", ".bat")):
                # A resolved shim (npx.cmd and friends) is not directly
                # executable by MCP clients that launch stdio via
                # CreateProcess (native codex, antigravity) -- WinError 193.
                # Route it through cmd.exe exactly like council.py/routing.py
                # do for their own subprocess spawns (their
                # `_windows_command_argv`), so the generated config reaches
                # every client, not only the Node ones that happen to resolve
                # shims themselves.
                merged["command"] = _comspec()
                merged["args"] = ["/d", "/s", "/c", resolved, *(merged.get("args") or [])]
            else:
                merged["command"] = resolved
        return _expand_runtime_env_placeholders(merged)
    return _expand_runtime_env_placeholders(
        _expand_local_path_placeholders({k: v for k, v in s.items() if k != "windows"})
    )

def _env_present(var):
    """True if the env var is defined and non-empty."""
    v = os.environ.get(var, "").strip()
    return bool(v)

def _required_ok(s):
    """Respect manifest `require_env`: skip a server if its gating env var
    is unset. Lets a Local-Only install omit Cloud-Only MCP (firecrawl, n8n,
    vault-library, vault-ocr) instead of pointing them at dead ports."""
    req = s.get("require_env")
    if not req:
        return True
    return _env_present(req)

class ManifestView(dict):
    """Filtered active servers plus explicit cross-CLI retirement names."""

    def __init__(self, *args, retired=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.retired = tuple(retired)


def load_manifest(quiet=False):
    """quiet=True suppresses the '>>> skip [...]' chatter -- used by
    --expected-servers, whose output must be machine-consumable (names
    only, one per line), never mixed with human-readable status lines."""
    raw, retired = load_mcp_manifest_document(MANIFEST)
    out = ManifestView(retired=retired)
    for n, s in raw.items():
        s = os_view(s)
        if not _required_ok(s):
            if not quiet:
                print(f">>> skip [{n}]: require_env {s.get('require_env')!r} not set or empty (Local-Only?)")
            continue
        out[n] = s
    return out


def _load_manifest_or_stop(quiet=False):
    try:
        return load_manifest(quiet=quiet)
    except ConfigValidationError as exc:
        print(f">>> STOP: invalid MCP manifest ({exc}). Fix the data source before retrying.", file=sys.stderr)
        return None

def _retired_keys(manifest, cli):
    return {CLI[cli]["name"](name) for name in getattr(manifest, "retired", ())}


def keep_extras(gen, live, label, retired=()):
    """A server in the live file but NOT in the manifest is not drift to be
    deleted: it's something new installed by an agent (vault rule: 'it's not
    drift, it's the new standard everyone should get'). It is KEPT as-is and
    flagged until it gets registered in the manifest. Codex already does this
    by design (per-section patch); here the JSON writers get aligned to it."""
    out = dict(gen)
    retired = set(retired)
    for k in live:
        if k not in out:
            if k in retired:
                print(f">>> RETIRED [{label}]: server '{k}' REMOVED by explicit manifest tombstone.")
                continue
            out[k] = live[k]
            print(f">>> OUTSIDE THE MANIFEST [{label}]: server '{k}' KEPT. Register it in manifest.yaml to propagate it everywhere.")
    return out


def preserve_server_fields(gen, live):
    """Preserve fields added by a live MCP client on managed servers.

    MCP clients may materialize runtime metadata or an environment overlay
    that is intentionally absent from the portable manifest. The manifest
    remains authoritative for fields it declares, while undeclared live
    fields stay additive and are carried through the surgical writers.
    """
    out = {}
    for name, spec in gen.items():
        merged = dict(spec)
        current = live.get(name)
        if isinstance(current, dict):
            for key, value in current.items():
                merged.setdefault(key, value)
        out[name] = merged
    return out

# ---- loading live configs (MCP section only) ---------------------------

def load_current(cli):
    path = None
    try:
        if cli == "claude":
            path = HOME / ".claude.json"
            return json.loads(path.read_text("utf-8")).get("mcpServers", {})
        if cli == "codex":
            path = _codex_config_path()
            d = toml_loads(path.read_text("utf-8"))
            return {k: {kk: vv for kk, vv in v.items() if kk != "tools"} for k, v in d.get("mcp_servers", {}).items()}
        if cli == "antigravity":
            path = HOME / ".gemini/antigravity/mcp_config.json"
            d = json.loads(path.read_text("utf-8"))
            return {k: {kk: vv for kk, vv in v.items() if kk != "$typeName"} for k, v in d.get("mcpServers", {}).items()}
        if cli == "opencode":
            path = _opencode_config_path()
            return json.loads(path.read_text("utf-8")).get("mcp", {})
    except FileNotFoundError:
        return None     # CLI not installed on this machine
    except (json.JSONDecodeError, TOMLDecodeError) as e:
        # A file that EXISTS but fails to parse is a materially different
        # state than "CLI not installed" (the FileNotFoundError case above):
        # returning None here would read as "not installed" to every caller
        # and silently skip real drift on a corrupted live config -- exactly
        # the "worst possible false-green" agent-doctor.sh already guards
        # against for render.py's own exit code, but this path bypassed it
        # by never reaching a STOP/exit at all (beta-readiness review,
        # 2026-07-13). Same message/exit-code convention as every --write
        # path below (`>>> STOP: ... not valid JSON/TOML ...`, exit 2).
        print(f">>> STOP: {path.name} is not valid JSON/TOML ({e}). Fix it or restore a .bak-* backup before rerunning.")
        sys.exit(2)
    return {}

# ---- structural diff (--diff mode) -------------------------------------

def diff_struct(path, cur, exp, out):
    if isinstance(exp, dict) and isinstance(cur, dict):
        for k in sorted(set(exp) | set(cur)):
            if k not in cur:
                out.append(f"    - {path}{k}: MISSING in the live file (expected value redacted)")
            elif k not in exp:
                out.append(f"    + {path}{k}: extra in the live file (value redacted)")
            else:
                diff_struct(f"{path}{k}.", cur[k], exp[k], out)
    elif cur != exp:
        out.append(f"    ~ {path[:-1]}: live and expected values differ (redacted)")


def _codex_alias_collisions(names):
    grouped = {}
    for name in names:
        grouped.setdefault(name.replace("-", "_").casefold(), []).append(name)
    return [sorted(group) for group in grouped.values() if len(group) > 1]

def cmd_diff(as_json=False):
    """Compare every CLI's live MCP config against the manifest.

    Two output shapes over ONE scan. The human report is the default and is
    printed by _diff_scan() exactly as it always was; `as_json` runs the same
    function with stdout captured and emits a machine-readable document
    instead. Deliberately not two code paths: a second implementation of the
    comparison would be free to disagree with the one people read.

    The machine shape exists because a global pass/fail counter cannot express
    "this CLI was skipped on purpose". agent-sync does not write Claude's
    config while Claude is running, but the old summary line still folded
    Claude's drift into one total, so every apply failed for as long as Claude
    stayed open. Per-CLI counts let the caller exclude exactly what it chose
    to skip, and nothing else."""
    if not as_json:
        return _diff_scan()[0]
    with contextlib.redirect_stdout(io.StringIO()):
        rc, report = _diff_scan()
    print(json.dumps(report, indent=2))
    return rc


def _diff_scan():
    """Returns (exit_code, report). Prints the human report as a side effect."""
    man = _load_manifest_or_stop()
    if man is None:
        return 2, {"schema": 1, "clis": {}, "totals": {"ok": 0, "diff": 0, "extra": 0, "stopped": 0}}
    ok = bad = extra = stopped = 0
    per_cli = {}

    def record(name, **counts):
        per_cli[name] = {"present": False, "stopped": False, "ok": 0, "diff": 0, "extra": 0, **counts}

    for cli, spec in CLI.items():
        print(f"\n========== {cli.upper()} ==========")
        cli_ok = cli_bad = cli_extra = 0
        try:
            current = load_current(cli)
        except SystemExit:
            # load_current() already printed the '>>> STOP: ... not valid
            # JSON/TOML ...' message for THIS CLI's section and exits(2).
            # One corrupted live config must not abort the whole diff loop
            # (that would hide real drift on every OTHER CLI behind a single
            # broken file) -- isolate it here, keep scanning the rest, and
            # let the overall exit code reflect that something stopped.
            stopped += 1
            record(cli, present=True, stopped=True)
            continue
        except OSError as exc:
            # A locked/unreadable config file (chmod 000, held open
            # exclusively elsewhere, a permission error -- the realistic
            # Windows failure mode) is a different exception class than the
            # SystemExit path above (load_current only converts a PARSE
            # failure to SystemExit; a read failure propagates as OSError
            # untouched) but deserves the exact same per-CLI isolation:
            # STOP this CLI's section, keep scanning the rest.
            print(f">>> STOP: {cli} config unreadable ({exc}).")
            stopped += 1
            record(cli, present=True, stopped=True)
            continue
        if current is None:
            print("  (config not present: CLI not installed here, or installed but never launched yet, skipped)")
            record(cli, present=False)
            continue
        if cli == "codex":
            for aliases in _codex_alias_collisions(current):
                print(
                    "  [ALIAS COLLISION] Codex treats these names as the same key: "
                    + ", ".join(aliases)
                    + ". Kept unchanged; reconcile explicitly after backup."
                )
                bad += 1
                cli_bad += 1
        wanted = {n: s for n, s in man.items() if cli in s["targets"]}
        seen = set()
        for name, s in wanted.items():
            key = spec["name"](name); seen.add(key)
            exp = spec["render"](s)
            if isinstance(current.get(key), dict) and isinstance(exp, dict):
                exp = preserve_server_fields({key: exp}, {key: current[key]})[key]
            if key not in current:
                print(f"  [MISSING]  {name} -> the live file has no '{key}'"); bad += 1
                cli_bad += 1
                continue
            out = []
            diff_struct("", current[key], exp, out)
            if out:
                print(f"  [DIFF]   {name}"); print("\n".join(out)); bad += 1
                cli_bad += 1
            else:
                print(f"  [OK]     {name}"); ok += 1
                cli_ok += 1
        retired = _retired_keys(man, cli)
        for k in sorted(set(current) - seen):
            if k in retired:
                print(f"  [RETIRED] '{k}' remains in the live file (removed by --write)")
                bad += 1
                cli_bad += 1
            else:
                print(f"  [EXTRA]  '{k}' in the live file but not in the manifest (kept by --write: register it to propagate it)")
                extra += 1
                cli_extra += 1
        record(cli, present=True, ok=cli_ok, diff=cli_bad, extra=cli_extra)
    summary = f"\n---- summary: {ok} servers match, {bad} with differences, {extra} outside the manifest"
    if stopped:
        summary += f", {stopped} CLI(s) STOPPED (corrupted live config, see above)"
    summary += " ----"
    print(summary)
    report = {
        "schema": 1,
        "clis": per_cli,
        "totals": {"ok": ok, "diff": bad, "extra": extra, "stopped": stopped},
    }
    return (2 if stopped else 0), report

# ---- per-style serializers ------------------------------------------------

def s_inline(obj, ind=0):
    """OpenCode: inline array of scalars, expanded objects."""
    pad, pad2 = "  " * ind, "  " * (ind + 1)
    if isinstance(obj, dict):
        if not obj:
            return "{}"
        body = ",\n".join(f'{pad2}{json.dumps(k, ensure_ascii=False)}: {s_inline(v, ind + 1)}' for k, v in obj.items())
        return "{\n" + body + "\n" + pad + "}"
    if isinstance(obj, list):
        if all(not isinstance(x, (dict, list)) for x in obj):
            return "[" + ", ".join(json.dumps(x, ensure_ascii=False) for x in obj) + "]"
        body = ",\n".join(f'{pad2}{s_inline(x, ind + 1)}' for x in obj)
        return "[\n" + body + "\n" + pad + "]"
    return json.dumps(obj, ensure_ascii=False)

def s_standard(obj, ind=0):
    """Antigravity: standard json.dump indent=2 (expanded arrays)."""
    return json.dumps(obj, indent=2, ensure_ascii=False)

def reorder(gen, live):
    """Aligns gen's key order to live's (values still come from gen)."""
    if isinstance(gen, dict) and isinstance(live, dict):
        out = {k: reorder(gen[k], live[k]) for k in live if k in gen}
        for k in gen:
            out.setdefault(k, gen[k])
        return out
    return gen

def _value_span(text, brace_idx):
    """Index right after the '}' that closes the object started at brace_idx,
    skipping strings (and braces inside strings, e.g. {env:...})."""
    depth = 0; i = brace_idx; in_str = esc = False
    while i < len(text):
        c = text[i]
        if in_str:
            if esc: esc = False
            elif c == "\\": esc = True
            elif c == '"': in_str = False
        elif c == '"': in_str = True
        elif c == "{": depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise ValueError("closing brace not found")

def _insert_new_top_level_key(raw, key, indent_exact):
    """Inserts `"key": {}` as a new top-level entry in a JSON object's text,
    right after the opening brace, preserving everything else untouched.
    Only used when the key is entirely absent (fresh install, e.g. a CLI
    launched for the first time with zero MCP servers configured): never to
    replace an existing key sitting at an unexpected indent, which stays a
    STOP (found in a cross-vendor audit, 2026-07-09: without this, a brand
    new config file with no "mcpServers" section at all blocked sync forever
    with no way out except a manual edit)."""
    open_idx = raw.index("{")
    close_idx = _value_span(raw, open_idx) - 1
    body = raw[open_idx + 1:close_idx]
    m = re.search(r'\n(\s+)\S', body)
    indent = indent_exact if indent_exact is not None else (m.group(1) if m else "  ")
    entry = f'{indent}{json.dumps(key)}: {{}}'
    new_body = ("\n" + entry + "\n") if not body.strip() else ("\n" + entry + "," + body)
    return raw[:open_idx + 1] + new_body + raw[close_idx:]

# ---- generic surgical writer for JSON files -----------------------------

def _prune_backups(path, keep=3):
    """Keeps only the last `keep` .bak-* backups of a file, deletes older ones."""
    backs = sorted(path.parent.glob(path.name + ".bak-*"))
    for old in backs[:-keep]:
        try:
            old.unlink()
        except OSError:
            pass

def _owner_only_mode(path):
    try:
        return (path.stat().st_mode & 0o700) or 0o600
    except OSError:
        return 0o600

def _secure_create_text(path, text, encoding="utf-8", mode=0o600):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            fd = None
            f.write(text)
    finally:
        if fd is not None:
            os.close(fd)

def _secure_backup(path, text, encoding="utf-8"):
    stem = path.name + ".bak-" + time.strftime("%Y%m%d-%H%M%S")
    for attempt in range(100):
        suffix = "" if attempt == 0 else f"-{attempt}"
        bak = path.with_name(stem + suffix)
        try:
            _secure_create_text(bak, text, encoding=encoding, mode=_owner_only_mode(path))
            return bak
        except FileExistsError:
            continue
    raise FileExistsError(f"could not create a unique backup for {path}")

def _atomic_write_text(path, text, encoding="utf-8"):
    """write_text() truncates then writes: a CLI re-reading the file (this
    runs on a 30-minute recurring timer, config files are live) can catch it
    mid-write and see a truncated/empty JSON. Write to a same-directory temp
    file and os.replace() it in, which POSIX/Windows both guarantee atomic
    for a rename onto an existing path. Create the temp file with owner-only
    permissions from the start: these files can contain MCP auth material, so
    creating first and chmodding later would expose a small but real window."""
    mode = _owner_only_mode(path)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    if tmp.exists():
        tmp = path.with_name(f"{path.name}.{os.getpid()}-{time.monotonic_ns()}.tmp")
    _secure_create_text(tmp, text, encoding=encoding, mode=mode)
    delay = 0.05
    for attempt in range(5):
        try:
            os.replace(tmp, path)
            break
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(delay)
            delay *= 2

def _read_live_json(path):
    """Read a live JSON/JSONC config for merge purposes. An existing-but-
    empty file (0 bytes, or whitespace only -- e.g. left behind by an
    interrupted first launch, 2026-07-30 incident) holds no data to lose:
    treat it as a fresh empty object instead of a parse failure. Anything
    else that fails to parse is real corruption and still raises, so callers
    keep STOPping and pointing at a .bak-* backup exactly as before."""
    raw = path.read_text("utf-8")
    if raw.strip() == "":
        return {}
    return parse_jsonc(raw) if path.suffix == ".jsonc" else json.loads(raw)


def write_json_section(path, key, new_section, live_section, serialize, indent_exact=None):
    if not path.exists():
        print(f">>> {path.name} not present: CLI never launched yet (no default config file), skipping."); return 3
    raw = path.read_text("utf-8")
    if raw.strip() == "":
        # An existing-but-empty file (0 bytes/whitespace only -- e.g. left
        # behind by an interrupted first launch, 2026-07-30 incident) holds
        # no data worth preserving: initialize it as an empty JSON object so
        # the normal "section missing, insert a placeholder" path below can
        # do its job, instead of STOPping forever on formatting that was
        # never real content to begin with.
        raw = "{}\n"
        print(f">>> {path.name} was present but empty: initialized as an empty JSON object.")
    try:
        live = parse_jsonc(raw) if path.suffix == ".jsonc" else json.loads(raw)
    except (json.JSONDecodeError, ValueError) as e:
        print(f">>> STOP: {path.name} is not valid JSON/JSONC ({e}). Fix it or restore a .bak-* backup before rerunning."); return 2
    if not isinstance(live, dict):
        print(f">>> STOP: {path.name} JSON root is not an object; refusing to patch it."); return 2
    if path.suffix == ".jsonc":
        try:
            span = jsonc_top_level_value_span(raw, key)
            if span is None:
                raw = set_jsonc_top_level_value(raw, key, {})
                live = parse_jsonc(raw)
                span = jsonc_top_level_value_span(raw, key)
                print(f">>> {json.dumps(key)} section was missing entirely (fresh install): inserted an empty placeholder.")
            if span is None:
                raise ValueError("inserted property is still absent")
            value_start, value_end = span
        except ValueError as e:
            print(f">>> STOP: cannot locate or insert the {json.dumps(key)} section ({e}).")
            return 2
        line_start = raw.rfind("\n", 0, value_start) + 1
        indent_match = re.match(r"[ \t]*", raw[line_start:value_start])
        indent = indent_match.group(0) if indent_match else ""
        inner = serialize(new_section)
        lines = inner.split("\n")
        block_val = lines[0] + "\n" + "\n".join(indent + line for line in lines[1:])
        new_text = raw[:value_start] + block_val + raw[value_end:]
    else:
        # indent_exact: if given, only match the key at that exact indentation
        # (for .claude.json, where "mcpServers" also appears nested in projects).
        ind_pat = re.escape(indent_exact) if indent_exact is not None else r'[ \t]*'
        m = re.search(rf'(?m)^({ind_pat}){re.escape(json.dumps(key))}[ \t]*:[ \t]*', raw)
        if not m or raw[m.end():m.end() + 1] != "{":
            if key in live:
                print(f">>> STOP: can't find the {json.dumps(key)} section at the expected indent."); return 2
            # Fresh install: the key is entirely absent, not just at an
            # unexpected indent. Insert an empty placeholder and let the normal
            # surgical-replace logic below fill it in.
            try:
                raw = _insert_new_top_level_key(raw, key, indent_exact)
                live = json.loads(raw)
            except (ValueError, json.JSONDecodeError) as e:
                print(f">>> STOP: cannot insert the {json.dumps(key)} placeholder ({e})."); return 2
            m = re.search(rf'(?m)^({ind_pat}){re.escape(json.dumps(key))}[ \t]*:[ \t]*', raw)
            if not m or raw[m.end():m.end() + 1] != "{":
                print(f">>> STOP: inserted the {json.dumps(key)} placeholder but can't find it again (unexpected file shape)."); return 2
            print(f">>> {json.dumps(key)} section was missing entirely (fresh install): inserted an empty placeholder.")
        indent = m.group(1)
        end = _value_span(raw, m.end())
        inner = serialize(new_section)
        lines = inner.split("\n")
        block_val = lines[0] + "\n" + "\n".join(indent + line for line in lines[1:])
        block = f'{indent}{json.dumps(key)}: ' + block_val
        new_text = raw[:m.start()] + block + raw[end:]

        if new_text[:m.start()] != raw[:m.start()] or not new_text.endswith(raw[end:]):
            print(">>> STOP: the replacement touches text outside the section."); return 2
    try:
        new_parsed = parse_jsonc(new_text) if path.suffix == ".jsonc" else json.loads(new_text)
    except (json.JSONDecodeError, ValueError) as e:
        print(f">>> STOP: result is not valid JSON/JSONC ({e})."); return 2
    for k in live:
        if k != key and new_parsed.get(k) != live[k]:
            print(f">>> STOP: the non-MCP section '{k}' would end up modified."); return 2

    changed = new_text != raw
    safe_live = json.dumps(redact_for_log(live_section), ensure_ascii=False, indent=2, sort_keys=True).splitlines()
    safe_new = json.dumps(redact_for_log(new_section), ensure_ascii=False, indent=2, sort_keys=True).splitlines()
    d = list(difflib.unified_diff(safe_live, safe_new,
                                  f"{path.name} MCP (live, redacted)",
                                  f"{path.name} MCP (generated, redacted)", lineterm=""))
    print("\n".join(d) if d else "(redacted MCP structure unchanged: scalar values or formatting may still differ)")
    sem = []
    diff_struct("", live_section, new_section, sem)
    print("\nSEMANTIC differences in the MCP section (order-independent):")
    print("\n".join(sem) if sem else "    (none: already compliant with the manifest)")
    if not changed:
        print("\n>>> Nothing to write."); return 0

    bak = _secure_backup(path, raw, "utf-8")
    _prune_backups(path)
    _atomic_write_text(path, new_text, "utf-8")
    written = path.read_text("utf-8")
    parse_jsonc(written) if path.suffix == ".jsonc" else json.loads(written)
    print(f"\n>>> WRITTEN and validated ({'JSONC' if path.suffix == '.jsonc' else 'JSON'} ok). Backup: {bak}")
    return 0

def write_opencode():
    path = _opencode_config_path()
    if not path.exists():
        if not _opencode_binary_present():
            # No OpenCode binary anywhere this machine looks for one: it is
            # not installed here, so fabricating a config tree for it would
            # just leave a phantom footprint on disk for nothing.
            print(f">>> {path.name} not present: OpenCode is not installed on this machine, skipping."); return 3
        # OpenCode IS installed but has never been launched once, so nothing
        # has ever created its config directory -- left unattended, it stays
        # unconfigured forever (2026-07-30 incident, same root cause as
        # Antigravity below). Bootstrap the directory and a minimal valid
        # config, then fall through to the normal provisioning path.
        path.parent.mkdir(parents=True, exist_ok=True)
        _secure_create_text(path, "{}\n")
        print(f">>> {path.name} did not exist yet (OpenCode installed but never launched): initialized an empty config.")
    try:
        live = _read_live_json(path)
    except (json.JSONDecodeError, ValueError) as e:
        print(f">>> STOP: {path.name} is not valid JSON/JSONC ({e}). Fix it or restore a .bak-* backup before rerunning."); return 2
    if not isinstance(live, dict):
        print(f">>> STOP: {path.name} JSON root is not an object; refusing to patch it."); return 2
    man = _load_manifest_or_stop()
    if man is None:
        return 2
    gen = {n: r_opencode(s) for n, s in man.items() if "opencode" in s["targets"]}
    gen = preserve_server_fields(gen, live.get("mcp", {}))
    gen = keep_extras(gen, live.get("mcp", {}), "opencode", _retired_keys(man, "opencode"))
    new_mcp = reorder(gen, live.get("mcp", {}))
    return write_json_section(path, "mcp", new_mcp, live.get("mcp", {}), s_inline)

def write_antigravity():
    path = HOME / ".gemini/antigravity/mcp_config.json"
    if not path.exists():
        if not _antigravity_present():
            # Antigravity is not installed here: fabricating a config tree for
            # a CLI that isn't even on the machine would just leave a phantom
            # footprint on disk for nothing.
            print(">>> mcp_config.json not present: Antigravity is not installed on this machine, skipping."); return 3
        # Antigravity IS installed but this MCP config
        # was never created -- nothing writes it until Antigravity is opened
        # at least once, so a machine that never launches it stays
        # unconfigured forever (2026-07-30 incident). Bootstrap the
        # directory and a minimal valid config, then fall through to the
        # normal provisioning path below.
        path.parent.mkdir(parents=True, exist_ok=True)
        _secure_create_text(path, "{}\n")
        print(">>> mcp_config.json did not exist yet (Antigravity installed but never launched): initialized an empty config.")
    try:
        live = _read_live_json(path)
    except json.JSONDecodeError as e:
        print(f">>> STOP: {path.name} is not valid JSON ({e}). Fix it or restore a .bak-* backup before rerunning."); return 2
    if not isinstance(live, dict):
        print(f">>> STOP: {path.name} JSON root is not an object; refusing to patch it."); return 2
    live_servers = live.get("mcpServers", {})
    man = _load_manifest_or_stop()
    if man is None:
        return 2
    gen = {}
    for n, s in man.items():
        if "antigravity" not in s["targets"]:
            continue
        d = r_antigravity(s)
        for k, v in live_servers.get(n, {}).items():   # preserve internal extras (e.g. $typeName)
            d.setdefault(k, v)
        gen[n] = d
    gen = preserve_server_fields(gen, live_servers)
    gen = keep_extras(gen, live_servers, "antigravity", _retired_keys(man, "antigravity"))
    new_servers = reorder(gen, live_servers)
    return write_json_section(path, "mcpServers", new_servers, live_servers, s_standard)

# ---- surgical writer for Codex (TOML, targeted per-section patch) ---------

def _toml_scalar(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return repr(v)            # 120.0, 600.0
    if isinstance(v, int):
        return str(v)
    if isinstance(v, list):
        return "[" + ", ".join(json.dumps(x, ensure_ascii=False) for x in v) + "]"
    return json.dumps(v, ensure_ascii=False)

def _codex_body(d):
    return [f"{k} = {_toml_scalar(v)}" for k, v in d.items()]

def _toml_table_path(header):
    """Return a semantic TOML table path, preserving dots inside quoted keys."""
    try:
        node = toml_loads(f"[{header}]\n")
    except Exception:
        return None
    parts = []
    while isinstance(node, dict):
        if not node:
            return tuple(parts)
        if len(node) != 1:
            return None
        key = next(iter(node))
        parts.append(key)
        node = node[key]
    return None


def _section_headers(lines):
    out = {}
    for i, l in enumerate(lines):
        m = re.match(r'^\[(.+?)\]\s*$', l)
        if m:
            path = _toml_table_path(m.group(1))
            if path is not None:
                out[path] = i
    return out


def _without_toml_tables(lines, table_names):
    """Drop exact TOML tables and their child tables, preserving all others."""
    table_names = set(table_names)
    if not table_names:
        return lines[:]
    out = []
    skipping = False
    for line in lines:
        match = re.match(r'^\[(.+?)\]\s*$', line)
        if match:
            path = _toml_table_path(match.group(1))
            skipping = bool(path and len(path) >= 2 and path[0] == "mcp_servers" and path[1] in table_names)
        if not skipping:
            out.append(line)
    return out


def _toml_key(name):
    """Use a bare TOML key when safe; quote names containing dots."""
    return name if re.fullmatch(r"[A-Za-z0-9_-]+", name) else json.dumps(name, ensure_ascii=False)

def _content_range(lines, header_idx):
    """Content lines right after a section header: up to a blank line, the
    next header, or EOF. Never touches headers or separating blank lines."""
    s = header_idx + 1
    e = s
    while e < len(lines) and lines[e].strip() != "" and not lines[e].startswith("["):
        e += 1
    return s, e

def write_codex(path=None):
    path = path or _codex_config_path()
    if not path.exists():
        print(f">>> {path.name} not present: Codex never launched yet (no default config file), skipping."); return 3
    raw = path.read_text("utf-8")
    lines = raw.split("\n")
    try:
        live = toml_loads(raw)
    except TOMLDecodeError as e:
        print(f">>> STOP: {path.name} is not valid TOML ({e}). Fix it or restore a .bak-* backup before rerunning."); return 2
    live_srv = live.get("mcp_servers", {})
    man = _load_manifest_or_stop()
    if man is None:
        return 2
    retired = _retired_keys(man, "codex")
    lines = _without_toml_tables(lines, retired)

    targets = {}   # cname -> (direct_fields, env_or_None)
    for n, s in man.items():
        if "codex" not in s["targets"]:
            continue
        full = dict(r_codex(s))
        env = full.pop("env", None)
        targets[n.replace("-", "_")] = (full, env)

    headers = _section_headers(lines)
    # insertion point for NEW servers = end of the last mcp_servers.* block
    mcp_ends = [_content_range(lines, idx)[1] for h, idx in headers.items()
                if h and h[0] == "mcp_servers"]
    insert_pos = max(mcp_ends) if mcp_ends else len(lines)

    edits = []        # (start, end, new_lines) — in-place patch of existing sections
    add_block = []    # lines for NEW servers to append to the mcp_servers block
    for cname, (direct, env) in targets.items():
        server_path = ("mcp_servers", cname)
        env_path = ("mcp_servers", cname, "env")
        table_name = f"mcp_servers.{_toml_key(cname)}"
        if server_path in headers:
            s, e = _content_range(lines, headers[server_path])
            direct_body = _codex_body(direct)
            if env is not None:
                if env_path in headers:
                    s2, e2 = _content_range(lines, headers[env_path])
                    edits.append((s2, e2, _codex_body(env)))
                else:
                    # Safe upgrade path: an older rendered stdio entry may
                    # predate a newly required environment block, for example
                    # the bounded Windows PATH added to npm-backed servers.
                    # Append the child table as part of the same surgical
                    # replacement and let the parse/non-MCP guards below
                    # validate the complete result before writing.
                    direct_body += ["", f"[{table_name}.env]", *_codex_body(env)]
            edits.append((s, e, direct_body))
        else:
            add_block += ["", f"[{table_name}]"] + _codex_body(direct)
            if env is not None:
                add_block += ["", f"[{table_name}.env]"] + _codex_body(env)
    if add_block:
        edits.append((insert_pos, insert_pos, add_block))

    new_lines = lines[:]
    for s, e, nl in sorted(edits, key=lambda x: x[0], reverse=True):
        new_lines[s:e] = nl
    new_text = "\n".join(new_lines)

    # --- guard: parsing + non-MCP untouched + tools preserved + fields == manifest
    try:
        np_ = toml_loads(new_text)
    except Exception as ex:
        print(f">>> STOP: result is not valid TOML ({ex})."); return 2
    for k in live:
        if k != "mcp_servers" and np_.get(k) != live[k]:
            print(f">>> STOP: the non-MCP section '{k}' would end up modified."); return 2
    for cname, (direct, env) in targets.items():
        ns = np_["mcp_servers"][cname]
        ls = live_srv.get(cname)              # None if it's a new server
        if ls is not None and ns.get("tools") != ls.get("tools"):
            print(f">>> STOP: the 'tools' overlay of {cname} would end up modified."); return 2
        for kk, vv in direct.items():
            if ns.get(kk) != vv:
                print(f">>> STOP: field {cname}.{kk} does not match the manifest."); return 2
        if env is not None and ns.get("env") != env:
            print(f">>> STOP: env of {cname} does not match."); return 2
    for cname in retired:
        if cname in np_.get("mcp_servers", {}):
            print(f">>> STOP: retired server '{cname}' would remain in Codex.")
            return 2
    for cname in live_srv:                          # no other MCP server touched
        if cname not in targets and cname not in retired and np_["mcp_servers"][cname] != live_srv[cname]:
            print(f">>> STOP: the non-manifest server '{cname}' would end up modified."); return 2

    changed = new_text != raw
    safe_live = json.dumps(redact_for_log(live_srv), ensure_ascii=False, indent=2, sort_keys=True).splitlines()
    safe_new = json.dumps(redact_for_log(np_.get("mcp_servers", {})), ensure_ascii=False, indent=2, sort_keys=True).splitlines()
    d = list(difflib.unified_diff(safe_live, safe_new,
                                  f"{path.name} MCP (live, redacted)",
                                  f"{path.name} MCP (generated, redacted)", lineterm=""))
    print("\n".join(d) if d else "(redacted MCP structure unchanged: scalar values or formatting may still differ)")
    if not changed:
        print("\n>>> Nothing to write."); return 0

    bak = _secure_backup(path, raw, "utf-8")
    _prune_backups(path)
    _atomic_write_text(path, new_text, "utf-8")
    toml_loads(path.read_text("utf-8"))
    print(f"\n>>> WRITTEN and validated (TOML ok). Backup: {bak}")
    return 0

def write_claude(path=None):
    """Surgical patch of ONLY the top-level mcpServers (indent 2) of
    .claude.json. Preserves the live file's literal tokens (the manifest
    doesn't contain them) and ignores the mcpServers nested inside projects.
    Fail-safe: write_json_section's guards block it if anything else would be
    touched. Meant to run while Claude is CLOSED (Claude rewrites .claude.json
    live).
    Deliberately does NOT bootstrap an absent file the way write_antigravity/
    write_opencode now do: .claude.json also carries account/trust-list state
    that no script can regenerate, so a missing file must stay a real "never
    launched" signal here, never a fabricated fresh object (2026-07-30
    incident; see cmd_reset, which refuses --reset for this exact reason)."""
    path = path or HOME / ".claude.json"
    if not path.exists():
        print(">>> .claude.json not present: Claude never launched yet (no default config file), skipping."); return 3
    try:
        live = _read_live_json(path)
    except json.JSONDecodeError as e:
        print(f">>> STOP: {path.name} is not valid JSON ({e}). Fix it or restore a .bak-* backup before rerunning."); return 2
    if not isinstance(live, dict):
        print(f">>> STOP: {path.name} JSON root is not an object; refusing to patch it."); return 2
    live_mcp = live.get("mcpServers", {})
    man = _load_manifest_or_stop()
    if man is None:
        return 2
    gen = {}
    for n, s in man.items():
        if "claude" not in s["targets"]:
            continue
        d = r_claude(s)   # http header already as ${VAR}: no literal token in .claude.json
        gen[n] = d
    gen = preserve_server_fields(gen, live_mcp)
    gen = keep_extras(gen, live_mcp, "claude", _retired_keys(man, "claude"))
    new_mcp = reorder(gen, live_mcp)
    return write_json_section(path, "mcpServers", new_mcp, live_mcp, s_standard, indent_exact="  ")

def cmd_expected_servers(cli):
    """Machine-consumable listing for agent-doctor.sh/.ps1's --strict block:
    the server names from the manifest that target `cli` AND pass the
    require_env filter (so a Local-Only install expects the SAME reduced
    set agent-sync actually writes, not the full manifest). One name per
    line, nothing else on stdout -- no '>>> skip' chatter (quiet=True)."""
    man = _load_manifest_or_stop(quiet=True)
    if man is None:
        return 2
    for n, s in man.items():
        if cli in s["targets"]:
            print(n)
    return 0


def cmd_server_url(name):
    """Print one resolved HTTP endpoint for health checks, never credentials."""
    man = _load_manifest_or_stop(quiet=True)
    if man is None:
        return 2
    server = man.get(name)
    if server is None:
        return 3
    if server.get("transport") != "http" or not isinstance(server.get("url"), str):
        print(f">>> STOP: server '{name}' has no rendered HTTP URL.", file=sys.stderr)
        return 2
    print(server["url"])
    return 0


def _cli_config_path(cli):
    """Native config path whose MCP section render.py writes (and backs up).
    Mirrors load_current()'s per-CLI paths so --revert and --write agree."""
    return {
        "claude": HOME / ".claude.json",
        "codex": _codex_config_path(),
        "antigravity": HOME / ".gemini/antigravity/mcp_config.json",
        "opencode": _opencode_config_path(),
    }[cli]


def _cli_config_candidates(cli):
    """Every filename this CLI's config is known to use, in every directory we
    look in. For three of the four CLIs that is exactly one path; OpenCode
    accepts three names, and _opencode_config_path() collapses to the default
    one as soon as no file exists. Anything that has to find the backups of a
    REMOVED file (--revert after --reset) must search all of them."""
    if cli != "opencode":
        return [_cli_config_path(cli)]
    names = ("opencode.jsonc", "opencode.json", "config.json")
    dirs = [HOME / ".config" / "opencode"]
    if IS_WINDOWS:
        dirs.append(Path(os.environ.get("APPDATA") or (HOME / "AppData" / "Roaming")) / "opencode")
    return [d / n for d in dirs for n in names]

def cmd_revert(cli):
    """Restore a CLI's native config from the most recent render.py backup
    (`<file>.bak-YYYYMMDD-HHMMSS`, written on every --write that changed the
    file). The CURRENT file is itself backed up first, so a revert is
    undoable; the newest backup is then validated in its own format and
    restored atomically. Touches nothing but this one CLI's config and its
    own .bak-* siblings.
    Exit: 0 restored or already-current, 1 no backup to restore, 2 the backup
    itself does not parse, 3 config not present."""
    path = _cli_config_path(cli)
    backups = sorted(path.parent.glob(path.name + ".bak-*"))
    if not backups and not path.exists():
        # --reset removed the live file, so a path resolver that picks the
        # first EXISTING filename (OpenCode supports three) now collapses to
        # the default name -- while the backup --reset just wrote still sits
        # under the name the file actually had. Without this, the undo command
        # --reset itself prints reports "nothing to revert" with the backup
        # lying right next to it, and the user's config is unreachable.
        orphans = [b for c in _cli_config_candidates(cli) for b in c.parent.glob(c.name + ".bak-*")]
        if orphans:
            latest_orphan = max(orphans, key=lambda p: p.name)
            path = latest_orphan.parent / latest_orphan.name.rsplit(".bak-", 1)[0]
            backups = [latest_orphan]
    if not backups:
        if not path.exists():
            print(f">>> {path.name} not present: nothing to revert for {cli}.")
            return 3
        print(f">>> no {path.name}.bak-* backup found: nothing to revert (render.py writes one on every change).")
        return 1
    latest = backups[-1]
    restored = latest.read_text("utf-8")
    try:
        # OpenCode's config is opencode.jsonc and the writer deliberately
        # preserves its // comments (see write_json_section). Parsing the
        # backup with plain json.loads rejected every commented OpenCode
        # backup as "corrupt", and because this check runs BEFORE the
        # `if not path.exists()` branch below, `--reset opencode` followed by
        # `--revert opencode` -- the documented undo pair, OpenCode being one
        # of the two _RESET_RECREATABLE CLIs -- left the config unreachable.
        if path.suffix == ".toml":
            toml_loads(restored)
        elif path.suffix == ".jsonc":
            parse_jsonc(restored)
        else:
            json.loads(restored)
    except (json.JSONDecodeError, TOMLDecodeError, ValueError) as e:
        print(f">>> STOP: the backup {latest.name} does not parse ({e}); refusing to restore a broken file.")
        return 2
    if not path.exists():
        # The file was removed (e.g. by --reset): restore it straight from the
        # newest backup. There is no current state to preserve first.
        _atomic_write_text(path, restored, "utf-8")
        print(f">>> RESTORED {path.name} from {latest.name} (was absent, e.g. after --reset).")
        return 0
    current = path.read_text("utf-8")
    if current == restored:
        print(f">>> {path.name} already matches the latest backup {latest.name}: nothing to revert.")
        return 0
    safety = _secure_backup(path, current, "utf-8")   # so the revert is itself undoable
    _prune_backups(path)
    _atomic_write_text(path, restored, "utf-8")
    print(f">>> REVERTED {path.name} from {latest.name} (current state saved as {safety.name}).")
    return 0


# CLIs whose --write path can recreate an absent config file from scratch
# (BUG-A: write_antigravity/write_opencode now bootstrap a missing file when
# the CLI itself is installed). --reset is only safe for these: for the
# other two, removing the file would strand the user with no re-provision
# path, only --revert.
_RESET_RECREATABLE = {"antigravity", "opencode"}


def cmd_reset(cli):
    """Onboarding reset: back up this CLI's whole native config, then remove it
    so a fresh provision (agent-sync apply / render.py --write) recreates it
    clean. The inverse of --revert, and reversible by it: `render.py --revert
    CLI` restores the file from the backup written here.

    Only allowed for a CLI in _RESET_RECREATABLE. claude and codex still
    refuse: write_claude()/write_codex() never fabricate an absent config
    file (for Claude, ~/.claude.json also carries account/trust-list state
    no script can regenerate), so 'agent-sync apply' cannot actually
    re-provision it after a reset -- the 2026-07-30 incident this closes was
    exactly that: a user reset by following INIT.md, ended up logged out of
    Claude, with the tool's own advice powerless to fix it.
    Exit: 0 reset (or already absent), 2 refused (no re-creation path for
    this CLI yet)."""
    path = _cli_config_path(cli)
    if not path.exists():
        print(f">>> {cli}: {path.name} not present -- already clean, nothing to reset.")
        return 0
    if cli not in _RESET_RECREATABLE:
        note = (
            " it also carries your account/trust-list state, which no script can regenerate;"
            if cli == "claude" else ""
        )
        print(
            f">>> REFUSED: {cli}'s writer cannot recreate {path.name} from scratch if it's "
            f"removed;{note} resetting it now would strand you with no way back except "
            f"render.py --revert {cli}. Not removing anything -- open {cli} yourself for a "
            "genuinely clean slate."
        )
        return 2
    bak = _secure_backup(path, path.read_text("utf-8"), "utf-8")
    path.unlink()
    print(f">>> RESET {cli}: removed {path.name} (backup {bak.name}). Undo with: render.py --revert {cli}. Re-provision clean with: agent-sync apply (or render.py --write {cli}).")
    return 0

def _bearer_var(auth_value):
    """Extract the env-var NAME from a rendered auth header ('Bearer ${VAR}'
    or 'Bearer {env:VAR}'). A var name is not a secret, so it is safe to show;
    a literal token (no ${}/{env:} wrapper) matches nothing and is dropped."""
    if not isinstance(auth_value, str):
        return None
    m = re.search(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", auth_value)
    if not m:
        m = re.search(r"\{env:([A-Za-z_][A-Za-z0-9_]*)\}", auth_value)
    return m.group(1) if m else None

def _adopt_entry(cli, spec):
    """Best-effort inverse of r_<cli>: turn a live MCP server structure back
    into a manifest.yaml stub (transport plus the fields that matter).
    Defensive (.get everywhere) so an unexpected live shape never crashes; the
    caller redacts the result before printing."""
    entry = {}
    args = spec.get("args")
    if cli == "claude":
        if spec.get("type") == "http" or "url" in spec:
            entry["transport"] = "http"
            entry["url"] = spec.get("url")
            var = _bearer_var((spec.get("headers") or {}).get("Authorization"))
            if var:
                entry["auth"] = {"env": var}
        else:
            entry["transport"] = "stdio"
            entry["command"] = spec.get("command")
            if args:
                entry["args"] = args
            if spec.get("env"):
                entry["env"] = spec["env"]
    elif cli == "codex":
        if "url" in spec:
            entry["transport"] = "http"
            entry["url"] = spec.get("url")
            if spec.get("bearer_token_env_var"):
                entry["auth"] = {"env": spec["bearer_token_env_var"]}
            timeouts = {}
            if "startup_timeout_sec" in spec:
                timeouts["startup"] = spec["startup_timeout_sec"]
            if "tool_timeout_sec" in spec:
                timeouts["tool"] = spec["tool_timeout_sec"]
            if timeouts:
                entry["timeouts"] = timeouts
        else:
            entry["transport"] = "stdio"
            entry["command"] = spec.get("command")
            if args:
                entry["args"] = args
            if spec.get("env"):
                entry["env"] = spec["env"]
    elif cli == "opencode":
        if spec.get("type") == "remote" or "url" in spec:
            entry["transport"] = "http"
            entry["url"] = spec.get("url")
            var = _bearer_var((spec.get("headers") or {}).get("Authorization"))
            if var:
                entry["auth"] = {"env": var}
        else:
            command = spec.get("command")
            if isinstance(command, list) and command:
                entry["transport"] = "stdio"
                entry["command"] = command[0]
                if command[1:]:
                    entry["args"] = command[1:]
            else:
                entry["transport"] = "stdio"
                entry["command"] = command
            if spec.get("environment"):
                entry["env"] = spec["environment"]
    elif cli == "antigravity":
        args = args or []
        bridged = spec.get("command") in ("node", "node.exe") and any(
            "mcp-http-bridge" in str(a) for a in args)
        if bridged:
            entry["transport"] = "http"
            if len(args) >= 3:
                entry["url"] = args[1]
                entry["auth"] = {"env": args[2]}
        else:
            entry["transport"] = "stdio"
            entry["command"] = spec.get("command")
            if args:
                entry["args"] = args
            if spec.get("env"):
                entry["env"] = spec["env"]
    entry["targets"] = [cli]
    return entry

def _adopt_yaml_scalar(v):
    # JSON is a subset of YAML 1.2, so a json-encoded scalar is valid YAML and
    # already quotes anything that needs quoting -- no hand-rolled escaping.
    return json.dumps(v, ensure_ascii=False)

def _emit_manifest_stub(name, entry):
    lines = [f"  {_adopt_yaml_scalar(name)}:"]
    for key, value in entry.items():
        if isinstance(value, dict):
            lines.append(f"    {key}:")
            for kk, vv in value.items():
                lines.append(f"      {_adopt_yaml_scalar(kk)}: {_adopt_yaml_scalar(vv)}")
        elif isinstance(value, list):
            lines.append(f"    {key}: [{', '.join(_adopt_yaml_scalar(x) for x in value)}]")
        else:
            lines.append(f"    {key}: {_adopt_yaml_scalar(value)}")
    return "\n".join(lines)

def _insert_under_servers(text, stubs):
    """Insert manifest stub blocks at the end of the top-level `servers:`
    mapping, preserving the file's comments and layout. Returns the new text,
    or None if no top-level `servers:` key is found."""
    lines = text.splitlines()
    servers_idx = next((i for i, ln in enumerate(lines) if ln.startswith("servers:")), None)
    if servers_idx is None:
        return None
    end = len(lines)
    for j in range(servers_idx + 1, len(lines)):
        ln = lines[j]
        if ln and not ln[0].isspace() and not ln.lstrip().startswith("#"):
            end = j   # a new top-level key ends the servers mapping
            break
    new_lines = lines[:end] + stubs + lines[end:]
    tail = "\n" if text.endswith("\n") else ""
    return "\n".join(new_lines) + tail


def _adopt_apply(extras, stubs):
    """Append the drafted stubs into the vault manifest under `servers:`,
    backing the file up first and re-validating after. On any validation
    failure the original is restored, so the canonical manifest is never left
    broken. Refuses outright if a literal secret (<AUTH>) is present."""
    if any("<AUTH>" in s for s in stubs):
        print(">>> STOP: a server carries a literal secret (shown as <AUTH>). Convert it to an env-var reference in the live config before adopting, so no secret lands in the manifest.", file=sys.stderr)
        return 2
    try:
        raw_text = MANIFEST.read_text(encoding="utf-8")
    except OSError as exc:
        print(f">>> STOP: cannot read the manifest ({exc}).", file=sys.stderr)
        return 2
    new_text = _insert_under_servers(raw_text, stubs)
    if new_text is None:
        print(">>> STOP: could not locate the top-level 'servers:' block; add the drafted entries by hand.", file=sys.stderr)
        return 2
    bak = _secure_backup(MANIFEST, raw_text, "utf-8")
    MANIFEST.write_text(new_text, encoding="utf-8")
    try:
        load_mcp_manifest_document(MANIFEST)
    except ConfigValidationError as exc:
        MANIFEST.write_text(raw_text, encoding="utf-8")
        print(f">>> STOP: the adopted entries broke the manifest ({exc}); restored the original, nothing changed.", file=sys.stderr)
        return 2
    print(f">>> adopted {len(extras)} server(s) into the manifest: {', '.join(sorted(extras))}. Backup: {bak.name}. Review the diff and commit.")
    return 0


def cmd_adopt(cli, apply=False):
    """Onboarding helper: find the MCP servers present in <cli>'s LIVE config
    but absent from the manifest (what render flags as OUTSIDE THE MANIFEST).
    Default (read-only) prints a DRAFT manifest entry for each to review. With
    apply=True it appends them into the vault manifest (backed up first and
    re-validated; restored on any failure). Secrets are redacted to <AUTH>, so
    a literal token is never echoed nor written; an env-var reference's NAME is
    kept, since a name is not a secret.
    Exit: 0 (incl. nothing to adopt), 2 manifest error, 3 config not present."""
    try:
        raw, _retired = load_mcp_manifest_document(MANIFEST)
    except ConfigValidationError as exc:
        print(f">>> STOP: invalid MCP manifest ({exc}). Fix the data source before retrying.", file=sys.stderr)
        return 2
    try:
        live = load_current(cli)
    except SystemExit:
        return 2   # load_current already printed a STOP for a corrupted live config
    if live is None:
        print(f">>> {cli} config not present (not installed, or never launched): nothing to adopt.")
        return 3
    manifest_keys = {CLI[cli]["name"](name) for name in raw}
    extras = {k: v for k, v in live.items() if k not in manifest_keys}
    if not extras:
        print(f">>> {cli}: every live MCP server is already in the manifest -- nothing to adopt.")
        return 0
    stubs = []
    for name in sorted(extras):
        entry = _adopt_entry(cli, extras[name])
        auth = entry.get("auth")
        auth_env = auth.get("env") if isinstance(auth, dict) else None
        safe = redact(entry)
        if auth_env:
            safe["auth"] = {"env": auth_env}   # a var name, not a secret: restore after redaction
        stubs.append(_emit_manifest_stub(name, safe))
    if apply:
        return _adopt_apply(extras, stubs)
    print(f">>> {cli}: {len(extras)} server(s) in the live config but NOT in the manifest.")
    print(">>> DRAFT manifest.yaml entries below -- review, adjust, then add under 'servers:'. Secrets shown as <AUTH>.")
    print("servers:")
    for stub in stubs:
        print(stub)
    print(">>> Re-run with --apply to append these under 'servers:' (backed up + re-validated first).")
    return 0

def cmd_write(cli):
    if cli == "opencode":
        return write_opencode()
    if cli == "antigravity":
        return write_antigravity()
    if cli == "codex":
        return write_codex()
    if cli == "claude":
        return write_claude()
    print(f"--write for '{cli}' not implemented yet.")
    return 1

def cmd_inventory():
    """Read-only onboarding scan across every supported CLI: report the MCP
    servers each one currently mounts, split into canonical (already in the
    manifest) and out-of-manifest extras. This is the foundation the
    adopt/reset onboarding flow builds on; skill, config, and native-memory
    slices are added on top. Exit: 0 always (pure report), 2 manifest error."""
    try:
        raw, _retired = load_mcp_manifest_document(MANIFEST)
    except ConfigValidationError as exc:
        print(f">>> STOP: invalid MCP manifest ({exc}). Fix the data source before retrying.", file=sys.stderr)
        return 2
    print(">>> Onboarding inventory -- MCP servers per CLI (read-only):")
    total_extras = 0
    for cli in CLI:
        manifest_keys = {CLI[cli]["name"](name) for name in raw}
        try:
            live = load_current(cli)
        except SystemExit:
            print(f"  {cli}: live config unreadable (see STOP above) -- skipped")
            continue
        if live is None:
            print(f"  {cli}: not configured on this machine")
            continue
        canonical = sorted(k for k in live if k in manifest_keys)
        extras = sorted(k for k in live if k not in manifest_keys)
        total_extras += len(extras)
        print(f"  {cli}: {len(live)} MCP server(s) -- {len(canonical)} canonical, {len(extras)} out-of-manifest")
        if extras:
            print(f"      out-of-manifest: {', '.join(extras)}")
    print("")
    if total_extras:
        print(f">>> {total_extras} out-of-manifest server(s) across CLIs: adopt them (render.py --adopt <cli>) or drop them in the onboarding flow.")
    else:
        print(">>> Every mounted MCP server is already canonical.")
    return 0

def main():
    ap = argparse.ArgumentParser(description="MCP generator from a single manifest (Vault 2.0 Phase 1).")
    ap.add_argument("--write", metavar="CLI", choices=list(CLI), help="regenerate a CLI's MCP config (default: diff only).")
    ap.add_argument("--expected-servers", metavar="CLI", choices=list(CLI),
                     help="print (one per line) the manifest server names that target CLI and pass require_env filtering; machine-consumable, exit 0.")
    ap.add_argument("--server-url", metavar="SERVER",
                     help="print one resolved HTTP endpoint for a rendered server; exits 3 when it is not active on this host.")
    ap.add_argument("--revert", metavar="CLI", choices=list(CLI),
                     help="restore a CLI's native config from the most recent render.py .bak-* backup (backs up the current file first).")
    ap.add_argument("--adopt", metavar="CLI", choices=list(CLI),
                     help="read-only: print DRAFT manifest.yaml entries for servers in a CLI's live config that aren't in the manifest yet.")
    ap.add_argument("--reset", metavar="CLI", choices=list(CLI),
                     help="onboarding: back up and remove a CLI's whole native config so a fresh provision recreates it clean (reversible via --revert). Refused for a CLI whose writer cannot recreate an absent config (currently claude, codex).")
    ap.add_argument("--inventory", action="store_true",
                     help="read-only onboarding scan across all CLIs: MCP servers per CLI, canonical vs out-of-manifest (foundation of the adopt/reset flow).")
    ap.add_argument("--json", action="store_true",
                     help="with the default diff: emit a machine-readable per-CLI report instead of the human one (same scan, same exit codes).")
    ap.add_argument("--apply", action="store_true",
                     help="with --adopt: append the drafted entries into the vault manifest (backed up + re-validated first) instead of only printing them.")
    args = ap.parse_args()
    if args.inventory:
        return cmd_inventory()
    if args.server_url:
        return cmd_server_url(args.server_url)
    if args.expected_servers:
        return cmd_expected_servers(args.expected_servers)
    if args.adopt:
        return cmd_adopt(args.adopt, apply=args.apply)
    if args.revert:
        return cmd_revert(args.revert)
    if args.reset:
        return cmd_reset(args.reset)
    if args.write:
        return cmd_write(args.write)
    return cmd_diff(as_json=args.json)

if __name__ == "__main__":
    sys.exit(main())
