"""Resume a supervised task where it stopped: the same steps, one checkpoint, one identity.

One job: walk a task through the steps the owner already runs by hand (unità,
esecuzione, raccolta, revisione, pubblicazione), checkpointing after each one in
a SQLite file inside the task folder, so an interruption costs the step that was
running and nothing else. The steps are not reimplemented here: every node calls
the function the matching `sup` verb calls. The identity of the run is the
objective and the unit's brief; a changed brief, or a different unit, makes
resume refuse instead of silently continuing someone else's run.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, TypedDict

from . import supervision_gates as gates
from . import supervision_harness as harness
from . import supervision_publish as publish_module
from . import supervision_review as review
from . import supervision_state as state
from .supervision_state import SupervisionError

#: The checkpoint of one task, and the identity it belongs to, both in the task folder.
CHECKPOINT_FILE = "60-ripresa.sqlite"
IDENTITY_FILE = "61-ripresa.json"
#: The node a stop re-enters from, so a waiting step is looked at again instead of skipped.
REENTER = {
    "waiting_review": "raccolta", "rework": "unità", "waiting_publish": "revisione",
    "waiting_session": "esecuzione",
}
#: What each stop means to the person reading the last line.
STOP_TEXT = {
    "waiting_review": "in attesa della revisione",
    "waiting_session": "la sessione era occupata: il lavoro è in coda",
    "rework": "rimandata all'esecutore: le correzioni sono nel suo giro",
    "waiting_publish": "approvata, la pubblicazione aspetta il checkout del proprietario",
    "done": "giro chiuso",
    "failed": "fermo: il passo non è riuscito",
}


class GraphError(SupervisionError):
    """No unit to resume, a brief that changed, a checkpoint that cannot be read."""

    def __init__(self, message: str, *, kind: str = "graph") -> None:
        super().__init__(message)
        self.kind = kind


class ResumeState(TypedDict, total=False):
    """Working state only: JSON values, no callables, no paths to processes."""

    objective_hash: str
    brief_hash: str
    unit: str
    head: str
    step: str
    stop_reason: str
    message: str
    trace: list[dict[str, Any]]


def resume(
    task_id: str, *, unit: str = "", repo: str = "", branch: str = "", remote: str = "origin",
    push: bool = False, push_with: str = "", restart: bool = False,
) -> dict:
    """Continue the saved run, or start the first one; refuse when the brief no longer matches."""
    folder = state.open_task(task_id)
    stored = {} if restart else _read_identity(folder)
    chosen = _chosen_unit(task_id, unit or str(stored.get("unit") or ""))
    current = identity(task_id, chosen)
    _settle_identity(folder, stored, current, restart)
    ctx = _Context(task_id, chosen, repo=repo, branch=branch, remote=remote, push=push, push_with=push_with)
    with _open_saver(folder) as saver:
        app = build_app(ctx).compile(checkpointer=saver)
        thread = {"configurable": {"thread_id": folder.name}}
        snapshot = app.get_state(thread)
        final = _continue(app, thread, snapshot.values) if snapshot.values else app.invoke(
            _initial(current), config=thread,
        )
    return _summary(folder, final)


def identity(task_id: str, unit: str) -> dict:
    """What this run is about: the task's objective and the unit's brief, hashed."""
    name = state.slug(unit)
    brief = state.open_task(task_id) / f"20-brief-{name}.md"
    if not brief.is_file():
        raise GraphError(
            f"brief dell'unità {name} sparito ({brief}): senza il brief il giro non ha identità",
            kind="brief_missing",
        )
    return {
        "unit": name,
        "objective_hash": _sha(state.objective(task_id)),
        "brief_hash": _sha(brief.read_text(encoding="utf-8")),
    }


def build_app(ctx: "_Context") -> Any:
    """The static graph: unità -> esecuzione -> raccolta -> revisione -> pubblicazione -> ferma."""
    END, StateGraph = _require_langgraph()
    builder = StateGraph(ResumeState)
    builder.add_node("unità", lambda s: _node_unit(ctx, s))
    builder.add_node("esecuzione", lambda s: _node_run(ctx, s))
    builder.add_node("raccolta", lambda s: _node_collect(ctx, s))
    builder.add_node("revisione", lambda s: _node_review(ctx, s))
    builder.add_node("pubblicazione", lambda s: _node_publish(ctx, s))
    builder.add_node("ferma", lambda s: {})
    builder.set_entry_point("unità")
    for here, then in (("unità", "esecuzione"), ("esecuzione", "raccolta"), ("raccolta", "revisione"),
                       ("revisione", "pubblicazione"), ("pubblicazione", "ferma")):
        builder.add_conditional_edges(
            here, _branch(then), {"ferma": "ferma", then: then},
        )
    builder.add_edge("ferma", END)
    return builder


class _Context:
    """What the nodes need from the command line, bound once; no state crosses in it."""

    def __init__(
        self, task_id: str, unit: str, *, repo: str = "", branch: str = "", remote: str = "origin",
        push: bool = False, push_with: str = "",
    ) -> None:
        self.task_id = task_id
        self.unit = unit
        self.repo = repo
        self.branch = branch
        self.remote = remote
        self.push = push
        self.push_with = push_with


def _branch(then: str):
    """Walk on to `then`, or stop where the previous node said it stopped."""

    def route(current: ResumeState) -> str:
        return "ferma" if current.get("stop_reason") else then

    return route


def _node_unit(ctx: _Context, current: ResumeState) -> dict:
    """The unit this run is about, refused when its copy is gone."""
    record = state.unit_record(ctx.task_id, ctx.unit)
    folder = Path(record.get("worktree") or "")
    if not folder.is_dir():
        return _update(current, "unità", f"copia dell'unità sparita: {folder}", outcome="failed", stop="failed")
    head = gates.head_of(folder, str(record.get("branch") or ""))
    line = f"unità {record['unit']} · ramo {record.get('branch')} · head {head[:12]}"
    return _update(current, "unità", line, head=head, unit=record["unit"])


def _node_run(ctx: _Context, current: ResumeState) -> dict:
    """Send the unit to its session, unless one already took it: a resend is not a resume."""
    session = state.harness_session(ctx.task_id, "opencode")
    if session:
        return _update(current, "esecuzione", f"sessione {session} già agganciata: nessun nuovo invio")
    result = harness.run_unit(ctx.task_id, ctx.unit)
    mode = str(result.get("mode") or "")
    if mode == "refused":
        return _update(current, "esecuzione", f"rifiutata: {result['reason']}", outcome="refused", stop="failed")
    if mode == "queued":
        return _update(
            current, "esecuzione", f"in coda: {result['reason']}", outcome="queued", stop="waiting_session",
        )
    recipient = result.get("session")
    session_id = str(recipient.get("id") if isinstance(recipient, dict) else recipient or "")
    return _update(current, "esecuzione", f"lavoro inviato alla sessione {session_id or '(nuova scheda)'}")


def _node_collect(ctx: _Context, current: ResumeState) -> dict:
    """The engine's gates, run again only when the branch moved since the last collection."""
    head = str(current.get("head") or "")
    last = review.last_collection(ctx.task_id, ctx.unit)
    if last and str(last.get("head") or "") == head and last.get("verdict"):
        line = f"raccolta {last['verdict']} su {head[:12]}: già fatta, non ripetuta"
        return _update(current, "raccolta", line, head=head)
    result = gates.collect(ctx.task_id, ctx.unit)
    line = f"raccolta {result['verdict']} su {result['head'][:12]}"
    if result["verdict"] != "PASS":
        failed = [f"{gate.name}: {gate.reason}" for gate in result["gates"] if not gate.ok]
        return _update(current, "raccolta", line, outcome="failed", stop="failed", head=result["head"],
                       message=line + " · " + " | ".join(failed))
    return _update(current, "raccolta", line, head=result["head"])


def _node_review(ctx: _Context, current: ResumeState) -> dict:
    """The owner's turn: an approval moves on, a rework sends the unit back, no decision waits."""
    collected = review.last_collection(ctx.task_id, ctx.unit)
    if not collected:
        return _update(current, "revisione", "nessuna raccolta da revisionare", outcome="failed", stop="failed")
    if str(collected.get("verdict") or "") != "PASS":
        return _update(
            current, "revisione", f"raccolta {collected.get('verdict') or '?'}: niente da approvare",
            outcome="failed", stop="failed",
        )
    decision = review.last_decision(ctx.task_id, ctx.unit)
    same = decision and str(decision.get("head") or "") == str(collected.get("head") or "")
    if same and str(decision.get("verdict") or "") == state.APPROVED:
        line = f"approvata al giro {decision['round']} su {str(decision.get('head'))[:12]}"
        return _update(current, "revisione", line, outcome="approved")
    if same:
        line = f"rimandata al giro {decision['round']}: le correzioni sono nell'esecutore"
        return _update(current, "revisione", line, outcome="rework", stop="rework")
    line = (f"in attesa della revisione: `sup review {ctx.task_id} {ctx.unit} --approve \"<motivo>\"` "
            f"o `--rework <file>`")
    return _update(current, "revisione", line, outcome="waiting", stop="waiting_review")


def _node_publish(ctx: _Context, current: ResumeState) -> dict:
    """The approved commits on the owner's branch, only when the owner named that checkout."""
    if not (ctx.repo and ctx.branch):
        line = (f"approvata: `sup resume {ctx.task_id} --repo <checkout> --branch <ramo>` "
                "la applica, oppure `sup publish`")
        return _update(current, "pubblicazione", line, outcome="waiting", stop="waiting_publish")
    result = publish_module.publish(
        ctx.task_id, ctx.unit, repo=ctx.repo, branch=ctx.branch, remote=ctx.remote,
        push=ctx.push, push_with=ctx.push_with,
    )
    line = f"pubblicati {len(result['commits'])} commit su {result['branch']} in {result['repo']}"
    if not result.get("pushed"):
        line += " (solo locale: il remoto non è stato toccato)"
    return _update(current, "pubblicazione", line, outcome="published", stop="done")


def _update(current: ResumeState, step: str, line: str, *, outcome: str = "ok", stop: str = "", **extra) -> dict:
    """One step's row in the trace, plus the stop it decided and anything it learned."""
    row = {"step": step, "outcome": outcome, "line": line}
    update: dict[str, Any] = {"step": step, "trace": list(current.get("trace") or []) + [row], **extra}
    if stop:
        update["stop_reason"] = stop
        update["message"] = extra.get("message") or line
    return update


def _initial(current: dict) -> ResumeState:
    """The first state of a run: who it is about, and nothing done yet."""
    return ResumeState(
        objective_hash=current["objective_hash"], brief_hash=current["brief_hash"],
        unit=current["unit"], head="", step="", stop_reason="", message="", trace=[],
    )


def _continue(app: Any, thread: dict, values: ResumeState) -> ResumeState:
    """Re-enter the step that stopped, so a wait is looked at again instead of skipped."""
    reason = str(values.get("stop_reason") or "")
    if reason == "done":
        return values
    node = REENTER.get(reason)
    if not node:
        raise GraphError(
            f"ripresa non possibile: il giro salvato è fermo su «{STOP_TEXT.get(reason, reason)}» "
            "e non c'è un passo da rientrare",
            kind="not_resumable",
        )
    app.update_state(thread, {"stop_reason": "", "message": ""}, as_node=node)
    return app.invoke(None, config=thread)


def _summary(folder: Path, values: ResumeState) -> dict:
    reason = str(values.get("stop_reason") or "")
    return {
        "task": folder.name,
        "unit": str(values.get("unit") or ""),
        "step": str(values.get("step") or ""),
        "stop_reason": reason,
        "state": STOP_TEXT.get(reason, reason or "in corso"),
        "message": str(values.get("message") or ""),
        "trace": list(values.get("trace") or []),
        "checkpoint": folder / CHECKPOINT_FILE,
    }


def _chosen_unit(task_id: str, wanted: str) -> str:
    """The unit the run is about: the one asked for, else the first one not published yet."""
    units = state.status(task_id)["units"]
    if not units:
        raise GraphError(
            f"compito {task_id}: nessuna unità agganciata, non c'è niente da riprendere "
            "(`sup unit ...` prima)",
            kind="no_unit",
        )
    if wanted:
        state.unit_record(task_id, wanted)
        return state.slug(wanted)
    for record in units:
        if not record.get("published"):
            return str(record["unit"])
    return str(units[-1]["unit"])


def _read_identity(folder: Path) -> dict:
    """The identity of the saved run, or an empty one when this task never ran one."""
    target = folder / IDENTITY_FILE
    if not target.is_file():
        return {}
    try:
        parsed = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise GraphError(f"identità del giro non leggibile ({target}): {exc}", kind="identity_unreadable") from exc
    if not isinstance(parsed, dict):
        raise GraphError(f"identità del giro non è un oggetto: {target}", kind="identity_unreadable")
    return parsed


def _settle_identity(folder: Path, stored: dict, current: dict, restart: bool) -> None:
    """Write the identity of a first run, and refuse to continue a different one."""
    if restart or not stored:
        if restart:
            (folder / CHECKPOINT_FILE).unlink(missing_ok=True)
        (folder / IDENTITY_FILE).write_text(
            json.dumps({**current, "created_at": state.now_iso()}, indent=1) + "\n", encoding="utf-8",
        )
        return
    if str(stored.get("unit") or "") != current["unit"]:
        raise GraphError(
            f"la ripresa riguarda l'unità {stored.get('unit')}: `--unita {current['unit']}` aprirebbe "
            f"un altro giro. Chiudi quello salvato, o riparti con `--riparti`",
            kind="unit_mismatch",
        )
    if str(stored.get("brief_hash") or "") != current["brief_hash"]:
        raise GraphError(
            "il brief dell'unità è cambiato da quando il giro è iniziato: la ripresa è rifiutata. "
            "Rimetti il brief com'era, o riparti con `--riparti`",
            kind="brief_mismatch",
        )
    if str(stored.get("objective_hash") or "") != current["objective_hash"]:
        raise GraphError(
            "l'obiettivo del compito è cambiato da quando il giro è iniziato: la ripresa è rifiutata",
            kind="objective_mismatch",
        )


def _require_langgraph():
    try:
        from langgraph.graph import END, StateGraph
    except ImportError as exc:
        raise GraphError(
            "ripresa senza dipendenze: reinstalla il motore (langgraph manca)", kind="missing_dependency",
        ) from exc
    return END, StateGraph


def _open_saver(folder: Path):
    """Checkpointer bound to the task folder; use as a context manager."""
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver
    except ImportError:
        try:
            from langgraph_checkpoint_sqlite import SqliteSaver  # noqa: F401 - legacy layout
        except ImportError as exc:
            raise GraphError(
                "ripresa senza dipendenze: reinstalla il motore (langgraph-checkpoint-sqlite manca)",
                kind="missing_dependency",
            ) from exc
    return SqliteSaver.from_conn_string(str(folder / CHECKPOINT_FILE))


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
