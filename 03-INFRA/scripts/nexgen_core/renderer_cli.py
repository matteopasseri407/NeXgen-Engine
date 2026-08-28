#!/usr/bin/env python3
"""MCP renderer CLI for v2: write/revert/reset/adopt.

Faithful port of the functions in the release's mcp/render.py (which stays
in place as a thin wrapper). Uses McpRenderer for generation, and implements
here the revert from backups, the reset of only the recreatable CLIs, and
the adoption of live entries outside the manifest.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.config import load_mcp_manifest
from nexgen_core.i18n import t
from nexgen_core.jsonc import parse_jsonc
from nexgen_core.paths import resolve_home
from nexgen_core.renderer import McpRenderer

HOME = resolve_home()
# CLIs whose writer can recreate the file from scratch (--reset is only safe
# here: claude.json carries session/trust state that no script regenerates).
RESET_RECREATABLE = {"antigravity", "opencode"}


def _manifest_path() -> Path:
    vault = Path(os.environ.get("AGENT_VAULT_DATA") or os.environ.get("KNOWLEDGE_VAULT_PATH") or str(HOME / "KnowledgeVault"))
    return vault / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml"


def _renderer() -> McpRenderer:
    return McpRenderer()


def _cli_config_path(cli: str) -> Path:
    return {
        "claude": HOME / ".claude.json",
        "codex": Path(os.environ.get("CODEX_HOME") or str(HOME / ".codex")) / "config.toml",
        "antigravity": HOME / ".gemini" / "antigravity-ide" / "mcp_config.json",
        "opencode": _renderer()._opencode_config_path(),
    }[cli]


def _cli_config_candidates(cli: str) -> list[Path]:
    if cli != "opencode":
        return [_cli_config_path(cli)]
    names = ("opencode.jsonc", "opencode.json", "config.json")
    dirs = [HOME / ".config" / "opencode"]
    appdata = os.environ.get("APPDATA")
    if appdata:
        dirs.append(Path(appdata) / "opencode")
    return [d / name for d in dirs for name in names]


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _secure_backup(path: Path, text: str) -> Path:
    stem = path.name + ".bak-" + time.strftime("%Y%m%d-%H%M%S")
    bak = path.with_name(stem)
    bak.write_text(text, encoding="utf-8")
    return bak


def _backups_for(path: Path) -> list[Path]:
    return sorted(path.parent.glob(path.name + ".bak-*"))


def _validate_config_text(path: Path, text: str) -> None:
    if path.suffix == ".toml":
        try:
            import tomllib
            tomllib.loads(text)
        except Exception as exc:
            raise ValueError(exc)
    elif path.suffix == ".jsonc":
        parse_jsonc(text)
    else:
        json.loads(text)


def cmd_write(cli: str) -> int:
    """Regenerates a CLI's MCP section from the manifest (--write)."""
    r = _renderer()
    funcs = {
        "claude": r.render_claude,
        "codex": r.render_codex,
        "antigravity": r.render_antigravity,
        "opencode": r.render_opencode,
    }
    try:
        ok, msg = funcs[cli](write=True)
    except Exception as exc:
        print(f">>> STOP: {exc}", file=sys.stderr)
        return 2
    print(f">>> {cli}: {msg}")
    return 0 if ok else 2


def cmd_revert(cli: str) -> int:
    """Restores a CLI's config from its most recent backup (--revert)."""
    path = _cli_config_path(cli)
    backups = _backups_for(path)
    if not backups and not path.exists():
        orphans = [b for c in _cli_config_candidates(cli) for b in _backups_for(c)]
        if orphans:
            latest_orphan = max(orphans, key=lambda p: p.name)
            path = latest_orphan.parent / latest_orphan.name.rsplit(".bak-", 1)[0]
            backups = [latest_orphan]
    if not backups:
        if not path.exists():
            print(">>> " + t("{name} not present: nothing to restore for {cli}.", name=path.name, cli=cli))
            return 3
        print(">>> " + t("no backup {pattern} found: nothing to restore.", pattern=f"{path.name}.bak-*"))
        return 1
    latest = backups[-1]
    restored = latest.read_text("utf-8")
    try:
        _validate_config_text(path, restored)
    except (ValueError, json.JSONDecodeError) as exc:
        print(">>> STOP: " + t("backup {name} doesn't parse ({error}); refusing to restore a broken file.", name=latest.name, error=exc))
        return 2
    if path.exists():
        current = path.read_text("utf-8")
        if current == restored:
            print(">>> " + t("{cli}: config already matches the most recent backup; nothing to restore.", cli=cli))
            return 0
        _secure_backup(path, current)
    _atomic_write_text(path, restored)
    print(">>> " + t("RESTORED {name} from {source}.", name=path.name, source=latest.name))
    return 0


def cmd_reset(cli: str) -> int:
    """Onboarding reset: backup + removal, only for recreatable CLIs."""
    path = _cli_config_path(cli)
    if not path.exists():
        print(">>> " + t("{cli}: {name} not present -- already clean, nothing to reset.", cli=cli, name=path.name))
        return 0
    if cli not in RESET_RECREATABLE:
        print(">>> REFUSED: " + t(
            "{cli}'s writer can't recreate {name} from scratch if it gets "
            "removed; you'd reset with no way back short of render.py --revert {cli}. "
            "Nothing is touched.",
            cli=cli, name=path.name,
        ))
        return 2
    bak = _secure_backup(path, path.read_text("utf-8"))
    path.unlink()
    print(">>> " + t(
        "RESET {cli}: removed {name} (backup {backup}). Undo with: render.py --revert {cli}. "
        "Reprovision clean with: agent-sync apply (or render.py --write {cli}).",
        cli=cli, name=path.name, backup=bak.name,
    ))
    return 0


#: What a literal secret becomes in a stub, so the caller's refusal to adopt
#: one actually has something to find. Without it a live config carrying a
#: real token was adopted silently, with the token dropped on the floor: the
#: server then failed to authenticate and nothing said why.
LITERAL_SECRET = "<AUTH>"


def _bearer_var(auth_header: Any) -> str | None:
    """The environment variable behind a bearer header, if there is one.

    Returns `LITERAL_SECRET` when the header carries the secret itself, which
    is a different answer from "there is no auth here" and must not be
    confused with it.
    """
    if not isinstance(auth_header, str):
        return None
    if auth_header.startswith("Bearer ${"):
        return auth_header[len("Bearer ${"):-1]
    if auth_header.startswith("Bearer {env:"):
        return auth_header[len("Bearer {env:"):-1]
    if auth_header.startswith("Bearer ") and auth_header[len("Bearer "):].strip():
        return LITERAL_SECRET
    return None


def _adopt_entry(cli: str, spec: dict) -> dict:
    """Best-effort inverse of r_<cli>: from the live structure to a manifest stub."""
    entry: dict[str, Any] = {}
    args = spec.get("args")
    if cli in ("claude", "opencode"):
        if spec.get("type") == "http" or "url" in spec:
            entry["transport"] = "http"
            entry["url"] = spec.get("url")
            var = _bearer_var((spec.get("headers") or {}).get("Authorization"))
            if var:
                entry["auth"] = {"env": var}
        else:
            entry["transport"] = "stdio"
            command = spec.get("command")
            if isinstance(command, list) and command:
                entry["command"] = command[0]
                if command[1:]:
                    entry["args"] = command[1:]
            else:
                entry["command"] = command
            env = spec.get("env") if cli == "claude" else spec.get("environment")
            if env:
                entry["env"] = env
    elif cli == "codex":
        if "url" in spec:
            entry["transport"] = "http"
            entry["url"] = spec.get("url")
            token_ref = spec.get("bearer_token_env_var")
            if token_ref:
                entry["auth"] = {"env": token_ref}
            elif spec.get("bearer_token"):
                entry["auth"] = {"env": LITERAL_SECRET}
        else:
            entry["transport"] = "stdio"
            entry["command"] = spec.get("command")
            if args:
                entry["args"] = args
            if spec.get("env"):
                entry["env"] = spec["env"]
    elif cli == "antigravity":
        args = args or []
        bridged = spec.get("command") in ("node", "node.exe") and any(
            "mcp-http-bridge" in str(a) for a in args
        )
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


def _validate_stub_entry(name: str, entry: dict) -> str | None:
    """Semantic validation of an adopted stub: reason if hostile, None if sane.

    The stub is generated from a live config someone (or something) else
    wrote, and it is about to be shown as YAML to paste, or written into
    the canonical manifest. Structural validity beyond "it parsed" is what
    keeps an injection out: a server name that means something else in
    YAML, a command that is not a single plain string, a URL whose scheme
    is not http(s), arguments or env values carrying newlines are all
    refused here, on both the stdout path and the --apply path.
    """
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", name):
        return f"server name {name!r} is not a safe manifest key"
    transport = entry.get("transport")
    if transport not in ("stdio", "http"):
        return f"{name}: unknown transport {transport!r}"
    url = entry.get("url")
    if transport == "http":
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            return f"{name}: http transport requires an http(s) URL, got {url!r}"
    command = entry.get("command")
    if transport == "stdio":
        if not isinstance(command, str) or not command.strip():
            return f"{name}: stdio transport requires a non-empty command string"
        if "\n" in command:
            return f"{name}: command carries a newline"
    for arg in entry.get("args") or []:
        if not isinstance(arg, str) or "\n" in arg:
            return f"{name}: args must be single-line strings"
    env = entry.get("env")
    if env is not None:
        if not isinstance(env, dict):
            return f"{name}: env must be a map"
        for key, value in env.items():
            if not isinstance(key, str) or not isinstance(value, str) or "\n" in value:
                return f"{name}: env must map names to single-line strings"
    auth = entry.get("auth")
    if auth is not None and not (isinstance(auth, dict) and isinstance(auth.get("env"), str)):
        return f"{name}: auth must reference an environment variable name"
    return None


def _emit_manifest_stub(name: str, entry: dict) -> str:
    def scalar(v: Any) -> str:
        return json.dumps(v, ensure_ascii=False)

    lines = [f"  {scalar(name)}:"]
    for key, value in entry.items():
        if isinstance(value, dict):
            lines.append(f"    {key}:")
            for kk, vv in value.items():
                lines.append(f"      {scalar(kk)}: {scalar(vv)}")
        elif isinstance(value, list):
            lines.append(f"    {key}: [{', '.join(scalar(x) for x in value)}]")
        else:
            lines.append(f"    {key}: {scalar(value)}")
    return "\n".join(lines)


def _load_live(cli: str) -> dict | None:
    """MCP servers present in the CLI's live config, or None if not installed."""
    path = _cli_config_path(cli)
    if not path.exists():
        return None
    text = path.read_text("utf-8")
    try:
        if cli == "claude":
            return json.loads(text).get("mcpServers", {})
        if cli == "codex":
            import tomllib
            d = tomllib.loads(text)
            return {k: {kk: vv for kk, vv in v.items() if kk != "tools"} for k, v in d.get("mcp_servers", {}).items()}
        if cli == "antigravity":
            d = json.loads(text)
            return {k: {kk: vv for kk, vv in v.items() if kk != "$typeName"} for k, v in d.get("mcpServers", {}).items()}
        if cli == "opencode":
            d = parse_jsonc(text) if path.suffix == ".jsonc" else json.loads(text)
            return d.get("mcp", {})
    except Exception as exc:
        print(">>> STOP: " + t("{name} is not valid JSON/TOML ({error}). Restore a .bak-* backup before retrying.", name=path.name, error=exc), file=sys.stderr)
        sys.exit(2)
    return {}


def cmd_adopt(cli: str, apply: bool = False) -> int:
    """Finds live servers outside the manifest and proposes (or applies) their entries."""
    manifest_path = _manifest_path()
    try:
        raw = load_mcp_manifest(manifest_path)
        manifest_servers = raw.get("servers", {})
    except Exception as exc:
        print(">>> STOP: " + t("invalid MCP manifest ({error}).", error=exc), file=sys.stderr)
        return 2
    live = _load_live(cli)
    if live is None:
        print(">>> " + t("{cli} config not present (not installed, or never launched): nothing to adopt.", cli=cli))
        return 3
    manifest_keys = {name.replace("-", "_") if cli == "codex" else name for name in manifest_servers}
    extras = {k: v for k, v in live.items() if k not in manifest_keys}
    if not extras:
        print(">>> " + t("{cli}: every live server is already in the manifest -- nothing to adopt.", cli=cli))
        return 0
    stubs = []
    for name in sorted(extras):
        entry = _adopt_entry(cli, extras[name])
        safe = dict(entry)
        auth = safe.get("auth")
        auth_env = auth.get("env") if isinstance(auth, dict) else None
        if auth_env:
            safe["auth"] = {"env": auth_env}
        problem = _validate_stub_entry(name, safe)
        if problem:
            print(">>> STOP: " + t("the live config carries an entry I refuse to import ({reason}).", reason=problem), file=sys.stderr)
            return 2
        stubs.append(_emit_manifest_stub(name, safe))
    if apply:
        if any("<AUTH>" in s for s in stubs):
            print(">>> STOP: " + t("a server carries a literal secret (<AUTH>). Convert it to an env-var reference before adopting."), file=sys.stderr)
            return 2
        try:
            raw_text = manifest_path.read_text(encoding="utf-8")
        except OSError as exc:
            print(">>> STOP: " + t("could not read the manifest ({error}).", error=exc), file=sys.stderr)
            return 2
        lines = raw_text.splitlines()
        servers_idx = next((i for i, ln in enumerate(lines) if ln.startswith("servers:")), None)
        if servers_idx is None:
            print(">>> STOP: " + t("could not find the top-level 'servers:' block; add the entries by hand."), file=sys.stderr)
            return 2
        end = len(lines)
        for j in range(servers_idx + 1, len(lines)):
            ln = lines[j]
            if ln and not ln[0].isspace() and not ln.lstrip().startswith("#"):
                end = j
                break
        new_lines = lines[:end] + [""] + stubs + lines[end:]
        new_text = "\n".join(new_lines) + ("\n" if raw_text.endswith("\n") else "")
        bak = _secure_backup(manifest_path, raw_text)
        manifest_path.write_text(new_text, encoding="utf-8")
        try:
            load_mcp_manifest(manifest_path)
        except Exception as exc:
            manifest_path.write_text(raw_text, encoding="utf-8")
            print(">>> STOP: " + t("the adopted entries broke the manifest ({error}); restored the original.", error=exc), file=sys.stderr)
            return 2
        print(">>> " + t(
            "adopted {count} servers into the manifest: {names}. Backup: {backup}. Review and commit.",
            count=len(extras), names=", ".join(sorted(extras)), backup=bak.name,
        ))
        return 0
    print(">>> " + t("{cli}: {count} servers in the live config but NOT in the manifest.", cli=cli, count=len(extras)))
    print(">>> " + t("manifest.yaml STUB below -- review it, adjust it, then place it under 'servers:'. Secrets shown as <AUTH>."))
    print("servers:")
    for stub in stubs:
        print(stub)
    print(">>> " + t("Rerun with --apply to add them under 'servers:' (backup + re-validation)."))
    return 0


def cmd_inventory() -> int:
    """Read-only scan of the MCP servers per CLI (used by agent-sync inventory)."""
    r = _renderer()
    for cli in ("claude", "codex", "antigravity", "opencode"):
        servers = r.load_resolved_servers(cli)
        names = ", ".join(sorted(servers)) if servers else t("(none)")
        print(f"  {cli}: {len(servers)} server -- {names}")
    return 0


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        print(t(
            "Usage: render.py [--write CLI|--revert CLI|--reset CLI|--adopt CLI [--apply]|--inventory]\n"
            "  --write CLI      regenerate the CLI's MCP section from the manifest\n"
            "  --revert CLI     restore the CLI's config from its most recent backup\n"
            "  --reset CLI      backup + remove the config (antigravity/opencode only)\n"
            "  --adopt CLI      manifest stub for live servers outside the manifest (--apply to apply)\n"
            "  --inventory      list the MCP servers per CLI (read-only)"
        ))
        return 0
    arg = argv[0]
    if arg == "--write" and len(argv) > 1:
        return cmd_write(argv[1])
    if arg == "--revert" and len(argv) > 1:
        return cmd_revert(argv[1])
    if arg == "--reset" and len(argv) > 1:
        return cmd_reset(argv[1])
    if arg == "--adopt" and len(argv) > 1:
        return cmd_adopt(argv[1], apply="--apply" in argv[2:])
    if arg == "--inventory":
        return cmd_inventory()
    print(t("render.py: unknown argument: {arg}", arg=arg), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
