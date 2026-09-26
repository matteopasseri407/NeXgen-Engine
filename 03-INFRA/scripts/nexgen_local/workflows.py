"""Gated runs of named n8n workflows through their webhook triggers.

n8n already reaches every agent through the ``n8n-mcp`` server (waiter-gated);
this module is the engine-side narrow path: only workflows named in a
machine-local allowlist can run, only with explicit ``--yes``/confirm, with
audit intent before and outcome after. The webhook URL is the capability, so
it lives in the allowlist file (0600 dir, never the vault, never the repo)
and is never printed back: previews show the name and description only.

No workflow runs from a model choice: the lane stays read-only. A human (or
an explicitly confirming caller) names the workflow and approves the run.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from .config import LaneConfig
from .patch import _PROPOSAL_ID_RE
from .tools import ToolError, audit_event


class WorkflowError(RuntimeError):
    """The stage or the run was refused."""


@dataclass
class HttpResult:
    status: int
    body: bytes


def _default_http(url: str, payload: bytes, headers: dict[str, str], timeout: int) -> HttpResult:
    req = urllib.request.Request(url, data=payload, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return HttpResult(resp.status, resp.read())
    except urllib.error.HTTPError as exc:
        return HttpResult(exc.code, exc.read()[:2000])
    except OSError as exc:
        raise WorkflowError(f"(workflow non raggiungibile: {exc})") from exc


HttpFn = Callable[[str, bytes, dict[str, str], int], HttpResult]


@dataclass
class WorkflowProposal:
    id: str
    workflow: str
    description: str
    params: dict[str, Any]
    created_at: str = ""
    applied_at: str = ""
    outcome: str = ""


def allowlist_path() -> Path:
    configured = os.environ.get("NEXGEN_WORKFLOWS_ALLOWLIST")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".config" / "nexgen-workflows" / "allowlist.json"


def load_allowlist() -> dict[str, Any]:
    """Name -> {webhook_url, description?, secret?}. Missing file refuses."""
    path = allowlist_path()
    if not path.is_file():
        raise WorkflowError(f"(nessun allowlist in {path}: dichiara i workflow prima di eseguirli)")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise WorkflowError(f"(allowlist illeggibile in {path}: {exc})") from exc
    workflows = data.get("workflows") if isinstance(data, dict) else None
    if not isinstance(workflows, dict) or not workflows:
        raise WorkflowError(f"(allowlist vuoto in {path})")
    return workflows


def _save(cfg: LaneConfig, proposal: WorkflowProposal) -> None:
    cfg.workflows_dir.mkdir(parents=True, exist_ok=True)
    target = cfg.workflows_dir / f"{proposal.id}.json"
    target.write_text(json.dumps(asdict(proposal), ensure_ascii=False, indent=1), encoding="utf-8")


def load_proposal(cfg: LaneConfig, proposal_id: str) -> WorkflowProposal:
    if not _PROPOSAL_ID_RE.fullmatch(str(proposal_id or "")):
        raise WorkflowError(f"id proposta non valido: {proposal_id}")
    target = cfg.workflows_dir / f"{proposal_id}.json"
    if not target.is_file():
        raise WorkflowError(f"proposta inesistente: {proposal_id}")
    return WorkflowProposal(**json.loads(target.read_text(encoding="utf-8")))


def list_proposals(cfg: LaneConfig) -> list[WorkflowProposal]:
    if not cfg.workflows_dir.is_dir():
        return []
    proposals = []
    for path in sorted(cfg.workflows_dir.glob("*.json"), reverse=True):
        try:
            proposals.append(WorkflowProposal(**json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, TypeError, ValueError):
            continue
    return proposals


def _sha(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def propose_run(cfg: LaneConfig, workflow: str, params: dict[str, Any] | None = None) -> WorkflowProposal:
    """Stage a run of an allowlisted workflow; sends nothing."""
    name = str(workflow or "").strip()
    if not name:
        raise WorkflowError("workflow mancante: nominalo tra quelli consentiti")
    workflows = load_allowlist()
    entry = workflows.get(name)
    if not isinstance(entry, dict) or not str(entry.get("webhook_url", "")).startswith("http"):
        raise WorkflowError(f"workflow non consentito: {name}")
    if params is not None and not isinstance(params, dict):
        raise WorkflowError("params deve essere un oggetto JSON")
    proposal = WorkflowProposal(
        id=f"{time.strftime('%Y%m%d-%H%M%S')}-{_sha(name + json.dumps(params or {}, sort_keys=True))[:8]}",
        workflow=name,
        description=str(entry.get("description", "")),
        params=dict(params or {}),
        created_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    )
    _save(cfg, proposal)
    audit_event(cfg, "propose_workflow", {"workflow": name}, ok=True, chars=len(json.dumps(proposal.params)))
    return proposal


def format_gate(proposal: WorkflowProposal) -> str:
    """The approval screen: names the workflow, never its webhook URL."""
    lines = [
        f"Proposta {proposal.id} — fatti macchina",
        f"  workflow: {proposal.workflow}",
        f"  descrizione: {proposal.description or '(nessuna)'}",
        f"  params: {json.dumps(proposal.params, ensure_ascii=False)}",
        f"  Esegui con: nexgen-local wf-run {proposal.id} --yes",
    ]
    return "\n".join(lines)


def confirm_run(cfg: LaneConfig, proposal_id: str, confirm: bool, http: HttpFn | None = None) -> dict[str, Any]:
    """POST the staged params to the allowlisted webhook, once, on confirm."""
    if not confirm:
        raise WorkflowError("esecuzione rifiutata: serve confirm esplicito")
    proposal = load_proposal(cfg, proposal_id)
    if proposal.applied_at:
        raise WorkflowError("proposta gia' eseguita")
    entry = load_allowlist().get(proposal.workflow)
    if not isinstance(entry, dict) or not str(entry.get("webhook_url", "")).startswith("http"):
        raise WorkflowError(f"workflow non piu' consentito: {proposal.workflow}")
    headers = {"Content-Type": "application/json"}
    if entry.get("secret"):
        headers["X-Nexgen-Secret"] = str(entry["secret"])
    audit_event(cfg, "run_workflow", {"proposal": proposal.id, "phase": "intent"}, ok=True, chars=0)
    call = http or _default_http
    try:
        result = call(str(entry["webhook_url"]), json.dumps(proposal.params).encode("utf-8"), headers, 120)
    except WorkflowError:
        raise
    except Exception as exc:  # noqa: BLE001 - a failed POST is a refused outcome
        audit_event(cfg, "run_workflow", {"proposal": proposal.id, "phase": "result"}, ok=False, chars=0)
        raise WorkflowError(f"esecuzione fallita: {exc}") from exc
    if result.status not in (200, 201, 202):
        audit_event(cfg, "run_workflow", {"proposal": proposal.id, "phase": "result"}, ok=False, chars=0)
        raise WorkflowError(f"esecuzione fallita: HTTP {result.status}")
    proposal.applied_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    proposal.outcome = result.body.decode("utf-8", errors="replace")[:2000]
    try:
        _save(cfg, proposal)
        audit_event(
            cfg,
            "run_workflow",
            {"proposal": proposal.id, "phase": "result"},
            ok=True,
            chars=len(proposal.outcome),
        )
    except (OSError, ToolError) as exc:
        raise WorkflowError(f"eseguito, ma stato o ricevuta finale non salvati: {exc}") from exc
    return {"id": proposal.id, "ran": True, "outcome": proposal.outcome}
