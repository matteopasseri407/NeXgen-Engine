"""LangGraph driver: the same helpers, mounted on an explicit state machine.

The graph is three nodes and two edges on purpose. The model never chooses
a tool freely: routing is validated, retrieval is engine-built with a single
repair, and the graph's only conditional is "there is nothing to retrieve".
That conditional edge is the whole reason a graph earns its place here.
"""
from __future__ import annotations

from typing import Any, Iterable, TypedDict

from .config import LaneConfig
from .engine import LaneResult, answer_task, check_canary, retrieve, route_task, sources_from_receipts
from .llm import LLM
from .tools import ToolRegistry


class LaneState(TypedDict, total=False):
    task: str
    canaries: list[str]
    route: dict[str, Any]
    collected: str
    answer: str
    injection: bool


def build_graph(llm: LLM, tools: ToolRegistry, cfg: LaneConfig):
    """Compile the lane graph. Imported lazily so the core stays framework-free."""
    from langgraph.graph import END, START, StateGraph

    def node_route(state: LaneState) -> dict[str, Any]:
        return {"route": route_task(llm, cfg, state["task"])}

    def node_retrieve(state: LaneState) -> dict[str, Any]:
        return {"collected": retrieve(tools, cfg, state["route"], state["task"])}

    def node_answer(state: LaneState) -> dict[str, Any]:
        answer = answer_task(
            llm,
            cfg,
            state["task"],
            state.get("collected", ""),
            str(state["route"].get("source")),
            sources=sources_from_receipts(tools.calls),
        )
        return {"answer": answer, "injection": check_canary(answer, state.get("canaries", []))}

    graph = StateGraph(LaneState)
    graph.add_node("route", node_route)
    graph.add_node("retrieve", node_retrieve)
    graph.add_node("answer", node_answer)
    graph.add_edge(START, "route")
    graph.add_conditional_edges(
        "route",
        lambda state: "retrieve" if state["route"].get("source") not in (None, "", "none") else "answer",
        {"retrieve": "retrieve", "answer": "answer"},
    )
    graph.add_edge("retrieve", "answer")
    graph.add_edge("answer", END)
    return graph.compile()


def run_graph(
    llm: LLM,
    tools: ToolRegistry,
    cfg: LaneConfig,
    task: str,
    canaries: Iterable[str] = (),
) -> LaneResult:
    tools.calls.clear()
    app = build_graph(llm, tools, cfg)
    final = app.invoke({"task": task, "canaries": [str(c) for c in canaries]})
    return LaneResult(
        task=task,
        route=final.get("route", {}),
        collected=final.get("collected", ""),
        answer=final.get("answer", ""),
        receipts=[{"tool": call.name, "args": call.args, "ok": call.ok} for call in tools.calls],
        injection=bool(final.get("injection", False)),
    )
