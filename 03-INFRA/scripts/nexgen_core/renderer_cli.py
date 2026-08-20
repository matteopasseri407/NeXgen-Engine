"""CLI del renderer MCP per il v2: write/revert/reset/adopt.

Port fedele delle funzioni di mcp/render.py della release (che resta come
thin wrapper). Usa McpRenderer per la generazione, e implementa qui il
revert dai backup, il reset dei soli CLI ricreabili, e l'adopt delle voci
vive fuori manifest.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.config import load_mcp_manifest
from nexgen_core.jsonc import parse_jsonc
from nexgen_core.renderer import McpRenderer

HOME = Path.home()
# CLI il cui writer puo' ricreare il file da zero (--reset e' sicuro solo qui:
# claude.json porta stato di sessione/trust che nessuno script rigenera).
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
    """Rigenera la sezione MCP di una CLI dal manifest (--write)."""
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
    """Ripristina la config di una CLI dal backup piu' recente (--revert)."""
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
            print(f">>> {path.name} non presente: niente da ripristinare per {cli}.")
            return 3
        print(f">>> nessun backup {path.name}.bak-* trovato: niente da ripristinare.")
        return 1
    latest = backups[-1]
    restored = latest.read_text("utf-8")
    try:
        _validate_config_text(path, restored)
    except (ValueError, json.JSONDecodeError) as exc:
        print(f">>> STOP: il backup {latest.name} non si analizza ({exc}); rifiuto di ripristinare un file rotto.")
        return 2
    if path.exists():
        current = path.read_text("utf-8")
        if current == restored:
            print(f">>> {cli}: la config e' gia' il backup piu' recente; nessun ripristino necessario.")
            return 0
        _secure_backup(path, current)
    _atomic_write_text(path, restored)
    print(f">>> RIPRISTINATO {path.name} da {latest.name}.")
    return 0


def cmd_reset(cli: str) -> int:
    """Onboarding reset: backup + rimozione, solo per CLI ricreabili."""
    path = _cli_config_path(cli)
    if not path.exists():
        print(f">>> {cli}: {path.name} non presente -- gia' pulito, niente da resettare.")
        return 0
    if cli not in RESET_RECREATABLE:
        print(
            f">>> RIFIUTATO: il writer di {cli} non puo' ricreare {path.name} da zero se viene "
            f"rimosso; resetteresti senza via di ritorno se non render.py --revert {cli}. "
            "Niente viene toccato."
        )
        return 2
    bak = _secure_backup(path, path.read_text("utf-8"))
    path.unlink()
    print(
        f">>> RESET {cli}: rimosso {path.name} (backup {bak.name}). Undo con: render.py --revert {cli}. "
        "Riprovisiona pulito con: agent-sync apply (o render.py --write {cli})."
    )
    return 0


def _bearer_var(auth_header: Any) -> str | None:
    if not isinstance(auth_header, str):
        return None
    if auth_header.startswith("Bearer ${"):
        return auth_header[len("Bearer ${"):-1]
    if auth_header.startswith("Bearer {env:"):
        return auth_header[len("Bearer {env:"):-1]
    return None


def _adopt_entry(cli: str, spec: dict) -> dict:
    """Inverso best-effort di r_<cli>: da struttura live a stub manifest."""
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
            if spec.get("bearer_token_env_var"):
                entry["auth"] = {"env": spec["bearer_token_env_var"]}
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
    """Server MCP presenti nella config viva della CLI, o None se non installata."""
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
        print(f">>> STOP: {path.name} non e' JSON/TOML valido ({exc}). Ripristina un backup .bak-* prima di riprovare.", file=sys.stderr)
        sys.exit(2)
    return {}


def cmd_adopt(cli: str, apply: bool = False) -> int:
    """Trova i server vivi fuori manifest e ne propone (o applica) le voci."""
    manifest_path = _manifest_path()
    try:
        raw = load_mcp_manifest(manifest_path)
        manifest_servers = raw.get("servers", {})
    except Exception as exc:
        print(f">>> STOP: manifest MCP non valido ({exc}).", file=sys.stderr)
        return 2
    live = _load_live(cli)
    if live is None:
        print(f">>> {cli} config non presente (non installata, o mai avviata): niente da adottare.")
        return 3
    manifest_keys = {name.replace("-", "_") if cli == "codex" else name for name in manifest_servers}
    extras = {k: v for k, v in live.items() if k not in manifest_keys}
    if not extras:
        print(f">>> {cli}: ogni server vivo e' gia' nel manifest -- niente da adottare.")
        return 0
    stubs = []
    for name in sorted(extras):
        entry = _adopt_entry(cli, extras[name])
        safe = dict(entry)
        auth = safe.get("auth")
        auth_env = auth.get("env") if isinstance(auth, dict) else None
        if auth_env:
            safe["auth"] = {"env": auth_env}
        stubs.append(_emit_manifest_stub(name, safe))
    if apply:
        if any("<AUTH>" in s for s in stubs):
            print(">>> STOP: un server porta un segreto letterale (<AUTH>). Convertilo in riferimento env-var prima di adottare.", file=sys.stderr)
            return 2
        try:
            raw_text = manifest_path.read_text(encoding="utf-8")
        except OSError as exc:
            print(f">>> STOP: impossibile leggere il manifest ({exc}).", file=sys.stderr)
            return 2
        lines = raw_text.splitlines()
        servers_idx = next((i for i, ln in enumerate(lines) if ln.startswith("servers:")), None)
        if servers_idx is None:
            print(">>> STOP: impossibile trovare il blocco top-level 'servers:'; aggiungi le voci a mano.", file=sys.stderr)
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
            print(f">>> STOP: le voci adottate hanno rotto il manifest ({exc}); ripristinato l'originale.", file=sys.stderr)
            return 2
        print(f">>> adottati {len(extras)} server nel manifest: {', '.join(sorted(extras))}. Backup: {bak.name}. Rivedi e committa.")
        return 0
    print(f">>> {cli}: {len(extras)} server nella config viva ma NON nel manifest.")
    print(">>> STUB manifest.yaml di seguito -- rivedi, aggiusta, poi metti sotto 'servers:'. Segreti mostrati come <AUTH>.")
    print("servers:")
    for stub in stubs:
        print(stub)
    print(">>> Rilancia con --apply per aggiungerli sotto 'servers:' (backup + ri-validazione).")
    return 0


def cmd_inventory() -> int:
    """Scan read-only dei server MCP per CLI (usato da agent-sync inventory)."""
    r = _renderer()
    for cli in ("claude", "codex", "antigravity", "opencode"):
        servers = r.load_resolved_servers(cli)
        names = ", ".join(sorted(servers)) if servers else "(nessuno)"
        print(f"  {cli}: {len(servers)} server -- {names}")
    return 0


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        print(
            "Uso: render.py [--write CLI|--revert CLI|--reset CLI|--adopt CLI [--apply]|--inventory]\n"
            "  --write CLI      rigenera la sezione MCP della CLI dal manifest\n"
            "  --revert CLI     ripristina la config della CLI dal backup piu' recente\n"
            "  --reset CLI      backup + rimozione della config (solo antigravity/opencode)\n"
            "  --adopt CLI      stub manifest per i server vivi fuori manifest (--apply per applicare)\n"
            "  --inventory      elenca i server MCP per CLI (read-only)"
        )
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
    print(f"render.py: argomento sconosciuto: {arg}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
