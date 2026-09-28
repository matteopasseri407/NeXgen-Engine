"""Persistent research loop: the steps operations, checkpointed per session.

``run_steps`` forgets everything when it returns. This module runs the SAME
operations (``steps.decide_step`` / ``steps._execute`` / ``steps.finish_answer``:
one implementation, never a third execution cycle) under a LangGraph that
persists the loop state to SQLite after every node, so "apri il secondo",
"continua la lettura" and "riprendi il confronto" continue where the
previous interaction stopped instead of re-searching.

State holds working state only: sources read, chunks consumed with their
coverage, staged (never applied) proposals, receipts. The approval gates
stay outside: this module stages proposals through the same propose paths
the loop uses and never calls any apply/confirm, so a resumed session
cannot double-apply. Authorization remains the engine's job, in the gates.

langgraph is optional (the ``[local]`` extra, same as the loop) and is
imported lazily.
"""
from __future__ import annotations

import re
import secrets
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable, TypedDict

from .config import LaneConfig
from .engine import _pinned, route_task
from .llm import LLM
from .steps import (
    MAX_STEPS,
    REPLY_INTENT_RE,
    UPLOAD_INTENT_RE,
    Decision,
    LoopState,
    StepResult,
    _execute,
    build_menu,
    decide_step,
    finish_answer,
)
from .tools import ToolError, ToolRegistry

SESSION_TTL_DAYS = 30


class ResearchError(RuntimeError):
    """Unknown session, unusable instruction, or storage failure."""


class ResearchState(TypedDict, total=False):
    """Persisted working state: JSON values only, no models, no registries."""

    session_id: str
    task: str
    route: dict[str, Any]
    max_steps: int
    canaries: list[str]
    loop: dict[str, Any]
    receipts: list[dict[str, Any]]
    refusals: list[str]
    reads_log: list[dict[str, Any]]
    pending: dict[str, str] | None
    stop: str
    answer: str
    escalated: bool
    #: Steps consumed by the CURRENT interaction: the cap applies per
    #: instruction, not to the session lifetime. ``loop.step`` keeps
    #: numbering every decision globally; this bounds one run.
    used: int
    #: Claim-check verdict of the last finished answer: surfaced in the
    #: summary (and CLI/MCP warnings) instead of dropped. A confabulating
    #: answer is still an answer, never a silent success.
    problems: list[str]
    confabulation: bool
    injection: bool


def _session_file(cfg: LaneConfig, session_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", session_id or ""):
        raise ResearchError(f"id sessione non valido: {session_id}")
    return cfg.research_dir / f"research-{session_id}.sqlite"


def _new_session_id() -> str:
    return f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(4)}"


def _hydrate_registry(tools: ToolRegistry, receipts: list[dict[str, Any]], refusals: list[str]) -> None:
    """Restore history into a fresh registry so menus see prior receipts.

    A continued interaction starts with an empty registry; without this,
    a stageless action like ``draft_mail`` (no tool call of its own) would
    leave ``state.receipts`` empty and the menu would restart from zero.
    Refusal texts are restored too: the empty-vs-error taxonomy needs them.
    """
    from .tools import ToolCall

    for receipt in receipts:
        tools.calls.append(
            ToolCall(
                name=str(receipt.get("tool", "")),
                args=dict(receipt.get("args", {}) or {}),
                ok=bool(receipt.get("ok", False)),
                chars=0,
            )
        )
    tools.refusals.extend(refusals)
def _sweep_old_sessions(cfg: LaneConfig) -> None:
    """Remove research sessions older than the TTL; cheap, best-effort."""
    try:
        root = cfg.research_dir
        if not root.is_dir():
            return
        cutoff = time.time() - SESSION_TTL_DAYS * 86400
        for path in root.glob("research-*.sqlite"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                continue
    except OSError:
        pass


def _loop_to_persisted(state: LoopState) -> dict[str, Any]:
    return asdict(state)


def _loop_from_persisted(data: dict[str, Any]) -> LoopState:
    payload = dict(data)
    payload["decisions"] = [Decision(**item) for item in data.get("decisions", [])]
    return LoopState(**payload)


def _require_langgraph():
    try:
        from langgraph.graph import END, StateGraph
    except ImportError as exc:
        raise ResearchError(
            "ricerca persistente richiede l'extra [local]: pip install 'nexgen-engine[local]'"
        ) from exc
    return END, StateGraph


def _open_saver(session_file: Path):
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver
    except ImportError:
        try:
            from langgraph_checkpoint_sqlite import SqliteSaver  # noqa: F401 - legacy layout
        except ImportError as exc:
            raise ResearchError(
                "ricerca persistente richiede l'extra [local]: pip install 'nexgen-engine[local]'"
            ) from exc
    _secure_storage(session_file.parent, None)
    return SqliteSaver.from_conn_string(str(session_file))


def _secure_storage(directory: Path, session_file: Path | None) -> None:
    """Private permissions on research state: dir 700, sqlite files 600.

    The parent chain (``~/.local/state``) is 700 on this host, but a fresh
    install must not rely on that: checkpoints name mail subjects and
    bodies, so they get the same treatment as council sessions.
    The directory is created on every platform; only chmod is POSIX-only.
    """
    import os as _os

    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    if _os.name == "nt":
        # No POSIX modes on Windows: the directory above is still created,
        # otherwise the first persistent search fails with
        # "unable to open database file".
        return
    try:
        _os.chmod(directory, 0o700)
    except OSError:
        pass
    if session_file is not None:
        for path in directory.glob(session_file.stem + ".sqlite*"):
            try:
                _os.chmod(path, 0o600)
            except OSError:
                continue


class _Ctx:
    """Non-persisted operation context: model, registry, config, result."""

    def __init__(
        self, llm: LLM, tools: ToolRegistry, cfg: LaneConfig,
        canaries: list[str], result: StepResult,
    ) -> None:
        self.llm = llm
        self.tools = tools
        self.cfg = cfg
        self.canaries = canaries
        self.result = result


def _load_loop(state: ResearchState) -> tuple[LoopState, StepResult]:
    loop = _loop_from_persisted(state["loop"])
    result = StepResult(task=loop.task)
    result.decisions = list(loop.decisions)
    result.steps = loop.step
    return loop, result


def _route_of(state: ResearchState) -> dict[str, Any]:
    return dict(state.get("route", {}))


def _node_decide(ctx_factory, state: ResearchState) -> dict[str, Any]:
    ctx = ctx_factory(state)
    loop, result = _load_loop(state)
    loop.step += 1
    menu = build_menu(ctx.cfg, loop)
    decided = decide_step(ctx.llm, ctx.cfg, loop, menu, result, time.time())
    loop.decisions = list(result.decisions)
    update: dict[str, Any] = {
        "loop": _loop_to_persisted(loop),
        "used": int(state.get("used", 0)) + 1,
    }
    if decided is None:
        update["stop"] = "escalate"
        update["escalated"] = True
        return update
    update["pending"] = {"action": decided[0], "arg": decided[1]}
    return update


def _node_act(ctx_factory, state: ResearchState) -> dict[str, Any]:
    started = time.time()
    ctx = ctx_factory(state)
    loop, result = _load_loop(state)
    pending = state.get("pending") or {}
    action, arg = str(pending.get("action", "")), str(pending.get("arg", ""))
    already = len(ctx.tools.calls)
    refused_before = len(ctx.tools.refusals)
    try:
        _execute(ctx.llm, ctx.tools, loop, action, arg)
    except ToolError as exc:
        result.decisions[-1].ok = False
        result.decisions[-1].detail = str(exc)
        result.decisions[-1].elapsed_s = round(time.time() - started, 2)
        loop.decisions = list(result.decisions)
        return {
            "loop": _loop_to_persisted(loop),
            "receipts": state.get("receipts", []),
            "refusals": state.get("refusals", []),
            "stop": "escalate",
            "escalated": True,
            "pending": None,
        }
    if result.decisions:
        result.decisions[-1].elapsed_s = round(time.time() - started, 2)
    loop.decisions = list(result.decisions)
    update: dict[str, Any] = {"loop": _loop_to_persisted(loop), "pending": None}
    # The registry accumulates within one operation exactly like run_steps;
    # only the calls made by THIS node extend the persisted receipts.
    update["receipts"] = state.get("receipts", []) + [
        {"tool": call.name, "args": call.args, "ok": call.ok} for call in ctx.tools.calls[already:]
    ]
    update["refusals"] = state.get("refusals", []) + list(ctx.tools.refusals[refused_before:])
    reads_log = list(state.get("reads_log", []))
    coverage = dict(ctx.tools.last_coverage or {})
    if coverage.get("tool"):
        args = coverage.get("args", {}) or {}
        reads_log.append(
            {
                "tool": coverage.get("tool", ""),
                "target": str(args.get("path") or args.get("id", "")),
                "offset": int(coverage.get("offset", 0)),
                "total": int(coverage.get("total", 0)),
                "truncated": bool(coverage.get("truncated", False)),
            }
        )
    update["reads_log"] = reads_log
    return update


def _node_finish(ctx_factory, state: ResearchState) -> dict[str, Any]:
    ctx = ctx_factory(state)
    loop, result = _load_loop(state)
    route = _route_of(state)
    finish_answer(
        ctx.llm, ctx.tools, ctx.cfg, loop, route, loop.task, ctx.canaries,
        result, time.time(), receipts=state.get("receipts", []), refusals=state.get("refusals", []),
    )
    loop.decisions = list(result.decisions)
    return {
        "loop": _loop_to_persisted(loop),
        "answer": result.answer,
        "escalated": False,
        "stop": "answer",
        "problems": list(result.problems),
        "confabulation": bool(result.confabulation),
        "injection": bool(result.injection),
    }


def _node_cap(ctx_factory, state: ResearchState) -> dict[str, Any]:
    loop, _ = _load_loop(state)
    loop.decisions.append(
        Decision(loop.step, "escalate", "", "", ok=False, detail="cap", elapsed_s=0.0)
    )
    return {"loop": _loop_to_persisted(loop), "stop": "escalate", "escalated": True}


def build_research_app(ctx_factory) -> Any:
    """Static graph over the shared loop operations; persistence is the job."""
    END, StateGraph = _require_langgraph()

    def decide(state: ResearchState) -> dict[str, Any]:
        return _node_decide(ctx_factory, state)

    def act(state: ResearchState) -> dict[str, Any]:
        return _node_act(ctx_factory, state)

    def finish(state: ResearchState) -> dict[str, Any]:
        return _node_finish(ctx_factory, state)

    def cap(state: ResearchState) -> dict[str, Any]:
        return _node_cap(ctx_factory, state)

    def route_decide(state: ResearchState) -> str:
        if state.get("stop") == "escalate":
            return "end"
        pending = state.get("pending") or {}
        if pending.get("action") == "answer":
            return "finish"
        if pending.get("action") == "escalate":
            return "end_escalate"
        # Per-interaction budget, mirroring run_steps (6 steps run 6 actions):
        # the 6th decision still executes; only the 7th is capped.
        if int(state.get("used", 0)) > int(state.get("max_steps", MAX_STEPS)):
            return "cap"
        return "act"

    builder = StateGraph(ResearchState)
    builder.add_node("decide", decide)
    builder.add_node("act", act)
    builder.add_node("finish", finish)
    builder.add_node("cap", cap)
    builder.set_entry_point("decide")
    builder.add_conditional_edges(
        "decide", route_decide,
        {"act": "act", "finish": "finish", "end": END, "end_escalate": END, "cap": "cap"},
    )
    builder.add_edge("act", "decide")
    builder.add_edge("finish", END)
    builder.add_edge("cap", END)
    return builder


def _initial_research_state(
    session_id: str, task: str, route: dict[str, Any],
    max_steps: int, canaries: list[str],
) -> ResearchState:
    loop = LoopState(
        task=task,
        route=str(route.get("source") or "none"),
        named_path="",
        want_reply=bool(REPLY_INTENT_RE.search(task)),
        want_upload=bool(UPLOAD_INTENT_RE.search(task)),
    )
    return ResearchState(
        session_id=session_id,
        task=task,
        route=dict(route),
        max_steps=max_steps,
        canaries=list(canaries),
        loop=_loop_to_persisted(loop),
        receipts=[],
        refusals=[],
        reads_log=[],
        pending=None,
        stop="",
        answer="",
        escalated=False,
        used=0,
        problems=[],
        confabulation=False,
        injection=False,
    )


def _status_block(session_id: str, state: ResearchState) -> str:
    """Engine-worded session status: sources, partial reads, proposals."""
    lines = [f"[motore: sessione {session_id}]"]
    reads = state.get("reads_log", [])
    if reads:
        lines.append("Fonti lette:")
        for item in reads:
            flag = "parziale" if item.get("truncated") else "completa"
            lines.append(f"  - {item.get('tool', '')} {item.get('target', '')} ({flag}, {item.get('total', 0)} caratteri)")
    else:
        lines.append("Fonti lette: nessuna")
    loop = state.get("loop", {})
    if loop.get("mail_draft"):
        lines.append(f"Bozza pronta: {loop['mail_draft']} (da approvare, mai inviata dal motore)")
    if loop.get("upload_proposal"):
        lines.append(f"Proposta upload pronta: {loop['upload_proposal']} (da approvare, mai applicata)")
    return "\n".join(lines)


def _summary(session_id: str, state: ResearchState) -> dict[str, Any]:
    loop = state.get("loop", {})
    return {
        "session_id": session_id,
        "status": state.get("stop") or "error",
        "answer": state.get("answer", ""),
        "collected_proposals": {
            "mail_draft": loop.get("mail_draft", ""),
            "upload_proposal": loop.get("upload_proposal", ""),
        },
        "reads": list(state.get("reads_log", [])),
        "receipts": list(state.get("receipts", [])),
        "escalated": bool(state.get("escalated", False)),
        "problems": list(state.get("problems", [])),
        "confabulation": bool(state.get("confabulation", False)),
        "injection": bool(state.get("injection", False)),
        "status_block": _status_block(session_id, state),
    }


def research_task(
    llm: LLM,
    cfg: LaneConfig,
    task: str,
    *,
    session_id: str = "",
    max_steps: int = MAX_STEPS,
    canaries: Iterable[str] = (),
) -> dict[str, Any]:
    """One persistent research interaction: start or continue a session.

    Empty ``session_id`` starts a new session and returns its id; otherwise
    the instruction continues the stored loop (new task text, same sources,
    receipts and proposals). Only staging operations run here: nothing is
    ever applied or confirmed.
    """
    _sweep_old_sessions(cfg)
    tools = ToolRegistry(cfg)
    canary_list = [str(c) for c in canaries]
    if not session_id:
        session_id = _new_session_id()
        route = route_task(llm, cfg, task)
        named_path = str(route.get("path") or "")
        initial = _initial_research_state(session_id, task, route, max_steps, canary_list)
        if named_path and str(route.get("root") or ""):
            loop = _loop_from_persisted(initial["loop"])
            loop.named_path = _pinned(str(route.get("root")), named_path)
            initial["loop"] = _loop_to_persisted(loop)
        created: ResearchState | None = initial
    else:
        session_file = _session_file(cfg, session_id)
        if not session_file.is_file():
            raise ResearchError(f"sessione inesistente: {session_id}")
        created = None

    def ctx_factory(state: ResearchState) -> _Ctx:
        return _Ctx(llm, tools, cfg, canary_list, StepResult(task=state.get("task", task)))

    session_file = _session_file(cfg, session_id)
    with _open_saver(session_file) as saver:
        try:
            app = build_research_app(ctx_factory).compile(checkpointer=saver)
            thread = {"configurable": {"thread_id": session_id}}
            if created is not None:
                final = app.invoke(created, config=thread)
            else:
                snapshot = app.get_state(thread)
                if not snapshot.values:
                    raise ResearchError(f"sessione senza checkpoint: {session_id}")
                # Same registry, continued history: menus and the claim check
                # see every prior receipt, not just this interaction's calls.
                _hydrate_registry(tools, snapshot.values.get("receipts", []), snapshot.values.get("refusals", []))
                current = dict(snapshot.values)
                loop = _loop_from_persisted(current["loop"])
                loop.task = task
                # New instruction, fresh run budget: the cap applies per
                # interaction, and continuations reset (the previous read's
                # resume point stays available via last_read).
                loop.continuations = 0
                # Intents follow the NEW instruction, not the first one: a
                # "confronta" continuation must not inherit "rispondi", and a
                # late "rispondi" must arm the draft menu.
                loop.want_reply = bool(REPLY_INTENT_RE.search(task))
                loop.want_upload = bool(UPLOAD_INTENT_RE.search(task))
                # Re-route when the new instruction names a real source: a
                # "confronta col contratto" after a mail run pivots the menu's
                # re-search to Drive instead of re-offering mail.
                new_route = route_task(llm, cfg, task)
                if str(new_route.get("source") or "") not in ("", "none"):
                    current["route"] = dict(new_route)
                    loop.route = str(new_route.get("source"))
                current["loop"] = _loop_to_persisted(loop)
                current["task"] = task
                current["stop"] = ""
                current["pending"] = None
                current["answer"] = ""
                current["escalated"] = False
                current["used"] = 0
                current["problems"] = []
                current["confabulation"] = False
                current["injection"] = False
                # New run on the same thread with the full carried-over state:
                # entry runs decide fresh (an as_node rewind would skip it and
                # strand the router with no pending decision).
                final = app.invoke(current, config=thread)
        finally:
            # Checkpoints may hold mail bodies: lock them down even when
            # the run raised midway.
            _secure_storage(cfg.research_dir, session_file)
        summary = _summary(session_id, final)
        if summary["answer"]:
            summary["answer"] = summary["answer"] + "\n\n" + summary["status_block"]
        return summary
