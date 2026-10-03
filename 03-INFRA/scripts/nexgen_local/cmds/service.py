"""Domain commands: mcp server + doctor. Owned here, re-exported by nexgen_local.cli."""
from __future__ import annotations

import argparse
import json

from . import base as _base
from ..relay import available_clis
from ..tools import audit_writable
import shutil
import urllib.request

def _config(args):
    """Resolve via cli facade when patched in tests, else base owner."""
    try:
        from .. import cli as _cli

        func = getattr(_cli, "_config", None)
        if func is not None and func.__module__ != __name__:
            return func(args)
    except ImportError:
        pass
    return _base.get_config(args)


def _llm(cfg):
    try:
        from .. import cli as _cli

        func = getattr(_cli, "_llm", None)
        if func is not None and func.__module__ != __name__:
            return func(cfg)
    except ImportError:
        pass
    return _base.make_llm(cfg)


_result_payload = _base.result_payload
_warn_unverified = _base.warn_unverified
_print_receipts = _base.print_receipts






def cmd_mcp(args: argparse.Namespace) -> int:
    from ..mcp_server import run_server

    return run_server(_config(args), include_ask=not args.jobs_only)


def cmd_doctor(args: argparse.Namespace) -> int:
    checks: list[tuple[str, bool, str, bool]] = []

    def add(label: str, ok: bool, detail: str = "", required: bool = True) -> None:
        checks.append((label, ok, detail, required))

    try:
        import langchain_ollama  # noqa: F401
        import langgraph  # noqa: F401

        add("dipendenze local (langgraph, langchain-ollama)", True)
    except ImportError as exc:
        add("dipendenze local (langgraph, langchain-ollama)", False, str(exc))

    cfg = _config(args)
    add("vault raggiungibile", cfg.vault_root.is_dir(), str(cfg.vault_root))
    add("audit scrivibile", audit_writable(cfg), str(cfg.audit_path))

    import os

    host = os.environ.get("OLLAMA_HOST") or "http://127.0.0.1:11434"
    if "://" not in host:
        host = "http://" + host
    try:
        with urllib.request.urlopen(host + "/api/tags", timeout=5) as response:
            models = [str(item.get("name", "")) for item in json.loads(response.read()).get("models", [])]
        add("ollama raggiungibile", True, host)
        base = cfg.model.split(":")[0]
        present = cfg.model in models or any(name.split(":")[0] == base for name in models)
        add(f"modello {cfg.model}", present, "" if present else "assente dall'inventario")
    except Exception as exc:  # noqa: BLE001 - doctor reports, never raises
        add("ollama raggiungibile", False, str(exc))

    add("pdftotext (PDF)", bool(shutil.which(cfg.pdftotext_cmd)), "opzionale", required=False)
    add("firecrawl-local (web)", bool(shutil.which(cfg.firecrawl_cmd)), "opzionale", required=False)
    from ..connectors.auth import status as _connector_status

    _gmail_ok, _gmail_detail = _connector_status()
    add("gmail/drive/calendar (connettori personali)", _gmail_ok, _gmail_detail, required=False)
    from ..connectors.outlook import status as _outlook_status

    _outlook_ok, _outlook_detail = _outlook_status()
    add("outlook (connettore personale)", _outlook_ok, _outlook_detail, required=False)
    add("git (proposte patch)", bool(shutil.which("git")), "opzionale", required=False)
    add("relay (CLI installate)", bool(available_clis()), ", ".join(available_clis()) or "nessuna", required=False)
    models_detail = f"router={cfg.router_tag} answer={cfg.answer_tag}"
    if cfg.router_tag != cfg.answer_tag:
        # Two resident models on one GPU: every switch pays a load/unload.
        # Same tag for both keeps one model hot; Ollama evicts the idle one
        # on its own schedule (this driver exposes no keep_alive knob).
        models_detail += " (due modelli residenti: possibili attese di load/unload ad ogni cambio)"
    add("modelli", True, models_detail, required=False)
    add("superficie sola lettura", True, "nessun tool montato scrive")

    if args.json:
        print(json.dumps([{"check": c[0], "ok": c[1], "detail": c[2], "required": c[3]} for c in checks]))
    else:
        print("nexgen-local doctor — lane locale in sola lettura")
        for label, ok, detail, required in checks:
            mark = "OK  " if ok else ("FAIL" if required else "WARN")
            suffix = f"  ({detail})" if detail else ""
            print(f"  {mark} {label}{suffix}")
    return 1 if any(not ok and required for _, ok, _, required in checks) else 0
