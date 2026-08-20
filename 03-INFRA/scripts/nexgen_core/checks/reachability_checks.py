"""Controlli di raggiungibilità dei connettori MCP di livello 'core'.

Port della sezione "MCP connectors — reachability" del doctor bash della
release, ma generico: invece di sondare porte cablate per nome di
connettore, ricava l'endpoint da sondare direttamente dal manifest
risolto. Usa solo stdlib (socket/urllib) con un timeout breve e per-server:
un connettore lento non deve mai tenere fermo l'intero doctor.
"""
from __future__ import annotations

import re
import socket
from pathlib import Path
from urllib.parse import urlparse

from nexgen_core.renderer import McpRenderer
from nexgen_core.report import CheckOutcome, Severity

#: Timeout per-server per la prova di raggiungibilità (secondi). Deliberatamente
#: breve: la prova è per-connettore, non cumulativa su tutto il manifest, ed
#: è già successo che un connettore lento tenesse fermo l'intero doctor.
PROBE_TIMEOUT_SECONDS = 2.5

_URL_RE = re.compile(r"https?://[^\s\"']+")


def _extract_probe_target(entry: dict) -> str | None:
    """Ricava un URL da sondare da un connettore MCP risolto.

    Un connettore http lo dichiara direttamente in `url`. Un connettore
    stdio può comunque dipendere da un backend locale raggiungibile via
    HTTP, dichiarato in `env` (es. FIRECRAWL_API_URL) o negli `args` (es. il
    CDP endpoint di Playwright): lo cerchiamo lì invece di assumere quale
    connettore lo espone in che modo.
    """
    if entry.get("url"):
        return str(entry["url"])

    candidates: list[str] = []
    env = entry.get("env")
    if isinstance(env, dict):
        candidates.extend(str(v) for v in env.values())
    args = entry.get("args")
    if isinstance(args, list):
        candidates.extend(str(a) for a in args)

    for candidate in candidates:
        match = _URL_RE.search(candidate)
        if match:
            return match.group(0)
    return None


def _probe_tcp(url: str, timeout: float) -> tuple[Severity, str]:
    """Prova solo la raggiungibilità TCP: nessuna assunzione sul protocollo
    applicativo del connettore (percorso di health, verbo HTTP, corpo atteso).
    """
    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        return Severity.UNDETERMINED, f"impossibile determinare l'host da verificare in '{url}'"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)

    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
        return Severity.OK, f"{host}:{port} risponde"
    except ConnectionRefusedError:
        return Severity.BROKEN, f"{host}:{port} rifiuta la connessione (nessun servizio in ascolto)"
    except TimeoutError:
        return Severity.UNDETERMINED, f"{host}:{port} non ha risposto entro {timeout}s"
    except OSError as exc:
        return Severity.UNDETERMINED, f"{host}:{port} non raggiungibile ({exc})"


def check_mcp_reachability(
    vault_data: Path, home: Path, timeout: float = PROBE_TIMEOUT_SECONDS
) -> list[CheckOutcome]:
    """Verifica che ogni connettore MCP `tier: core` con precondizione
    soddisfatta risponda davvero.

    Un connettore la cui `require_env` non è soddisfatta non viene mai
    considerato: non è un guasto, è una configurazione che l'utente non ha
    scelto di attivare su questa macchina.
    """
    manifest_path = vault_data / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml"
    if not manifest_path.is_file():
        return []

    renderer = McpRenderer(vault_data=vault_data, home=home)
    resolved: dict[str, dict] = {}
    for cli in ("claude", "codex", "antigravity", "opencode"):
        try:
            for name, entry in renderer.load_resolved_servers(cli).items():
                resolved.setdefault(name, entry)
        except (OSError, ValueError):
            continue

    outcomes: list[CheckOutcome] = []
    for name, entry in sorted(resolved.items()):
        if entry.get("tier") != "core":
            continue
        target = _extract_probe_target(entry)
        if not target:
            continue

        severity, detail = _probe_tcp(target, timeout)
        id_ = f"mcp.reachable.{name}"
        if severity == Severity.OK:
            outcomes.append(CheckOutcome(
                id=id_, severity=Severity.OK,
                message=f"Connettore MCP '{name}' raggiungibile",
            ))
        elif severity == Severity.BROKEN:
            outcomes.append(CheckOutcome(
                id=id_,
                severity=Severity.BROKEN,
                message=f"Il connettore MCP '{name}' non risponde ({detail}).",
                action=f"Verifica che il servizio dietro '{name}' sia avviato, poi riesegui il doctor.",
            ))
        else:
            outcomes.append(CheckOutcome(
                id=id_,
                severity=Severity.UNDETERMINED,
                message=f"Non è stato possibile verificare la raggiungibilità di '{name}' ({detail}).",
            ))
    return outcomes
