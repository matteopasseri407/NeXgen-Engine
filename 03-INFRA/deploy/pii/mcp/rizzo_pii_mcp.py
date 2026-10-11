#!/usr/bin/env python3
"""MCP bridge (stdio, JSON-RPC 2.0) per Rizzo PII.

Primario: sidecar Rizzo PII 2.0.0 sulla VPS via tunnel SSH.
Fallback: AppImage Rizzo PII locale, avviata su richiesta se il primario
non risponde. Solo stdlib, nessun modello caricato qui.

Basato su Rizzo PII di Rizzo-AI-Academy (licenza MIT):
https://github.com/Rizzo-AI-Academy/rizzo-pii — modello rizzo-pii-0.3B:
https://huggingface.co/rizzoaiacademy/rizzo-pii-0.3B

Tool esposto:
  pii_anonymize(text: str, mapping: bool = True)
    -> testo anonimizzato con placeholder + conteggi per categoria.
     Il mapping originale resta lato sidecar solo se mapping=true
     (flusso reversibile in locale); con false e definitivo.

Config via ENV:
  PII_API_URL     (default http://127.0.0.1:33004, tunnel verso la VPS)
  PII_LOCAL_APP   (default ~/Applications/Rizzo-PII-2.0.0-Linux-x86_64.AppImage)
  PII_LOCAL_PORT  (default 33005, per non scontrarsi col tunnel)
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("PII_API_URL", "http://127.0.0.1:33004").rstrip("/")
LOCAL_APP = os.environ.get(
    "PII_LOCAL_APP",
    os.path.expanduser("~/Applications/Rizzo-PII-2.0.0-Linux-x86_64.AppImage"),
)
LOCAL_PORT = int(os.environ.get("PII_LOCAL_PORT", "33005"))
LOCAL_URL = f"http://127.0.0.1:{LOCAL_PORT}"
SPAWN_WAIT_SECONDS = int(os.environ.get("PII_SPAWN_WAIT", "240"))
PROTO = "2024-11-05"

TOOLS = [{
    "name": "pii_anonymize",
    "description": ("Anonimizza dati personali italiani in un testo (nomi, CF, "
                    "IBAN, telefoni, indirizzi, ...; modello Rizzo PII). Usalo "
                    "prima di condividere o pubblicare testi che potrebbero "
                    "contenere PII. Se la VPS non risponde parte in automatico "
                    "l'app locale. Ritorna il testo con placeholder e i "
                    "conteggi per categoria."),
    "inputSchema": {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Testo da anonimizzare."},
            "mapping": {"type": "boolean", "default": True,
                         "description": "Se false, nessun mapping originale (irreversibile)."},
        },
        "required": ["text"],
    },
}]

_RETRYABLE = (urllib.error.URLError, TimeoutError, ConnectionError, OSError)


def _post(url, payload, timeout):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url + "/analyze", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _health_ok(url, timeout=5):
    try:
        with urllib.request.urlopen(url + "/health", timeout=timeout) as r:
            return r.status == 200
    except _RETRYABLE:
        return False


def _spawn_local():
    """Avvia l'AppImage locale e attende /health. Solleva se non parte."""
    if not (os.path.isfile(LOCAL_APP) and os.access(LOCAL_APP, os.X_OK)):
        raise RuntimeError(f"app locale assente o non eseguibile: {LOCAL_APP}")
    env = dict(os.environ, PII_PORT=str(LOCAL_PORT))
    with open(os.devnull, "wb") as null:
        subprocess.Popen([LOCAL_APP], env=env, stdout=null, stderr=null,
                         stdin=subprocess.DEVNULL, start_new_session=True)
    deadline = time.monotonic() + SPAWN_WAIT_SECONDS
    while time.monotonic() < deadline:
        if _health_ok(LOCAL_URL):
            return LOCAL_URL
        time.sleep(5)
    raise RuntimeError(f"app locale non pronta dopo {SPAWN_WAIT_SECONDS}s")


def _endpoint():
    if _health_ok(BASE):
        return BASE
    if _health_ok(LOCAL_URL):
        return LOCAL_URL
    return _spawn_local()


def anonymize(text, mapping=True):
    """Prova il primario, poi il fallback locale. Solleva all'ultimo errore."""
    payload = {"text": text, "include_mapping": mapping}
    try:
        return _post(BASE, payload, timeout=300), "vps"
    except _RETRYABLE as first:
        try:
            return _post(_endpoint(), payload, timeout=300), "locale"
        except Exception:
            raise first


def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def reply(_id, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": _id}
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result
    send(msg)


def handle(req):
    method = req.get("method")
    _id = req.get("id")
    if method == "initialize":
        reply(_id, {"protocolVersion": PROTO,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "rizzo-pii", "version": "2.0.0"}})
    elif method == "notifications/initialized":
        pass
    elif method == "tools/list":
        reply(_id, {"tools": TOOLS})
    elif method == "tools/call":
        params = req.get("params", {})
        if params.get("name") != "pii_anonymize":
            reply(_id, error={"code": -32601, "message": "tool sconosciuto"})
            return
        args = params.get("arguments", {})
        text = (args.get("text") or "").strip()
        mapping = bool(args.get("mapping", True))
        if not text:
            reply(_id, error={"code": -32602, "message": "testo mancante"})
            return
        try:
            data, _via = anonymize(text, mapping)
            lines = [data.get("anonymized_text", "")]
            counts = data.get("by_label", {})
            if counts:
                lines.append("Entita: " + ", ".join(f"{k} x{v}" for k, v in sorted(counts.items())))
            reply(_id, {"content": [{"type": "text", "text": "\n".join(lines)}]})
        except Exception as e:
            reply(_id, error={"code": -32000, "message": f"anonimizzatore non raggiungibile: {e!r}"})
    elif _id is not None:
        reply(_id, error={"code": -32601, "message": f"metodo non gestito: {method}"})


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        handle(req)


if __name__ == "__main__":
    main()
