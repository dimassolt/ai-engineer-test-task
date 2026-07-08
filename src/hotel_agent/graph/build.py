"""StateGraph assembly + checkpointer.

    START → parse → classify → plan → approval_gate ─(approved)→ execute → finalize → END
                                             └────────(rejected)────────────→ finalize → END

The approval gate pauses via a dynamic `interrupt()` (see nodes.py). The checkpointer
persists state per `thread_id`, so a run paused at the gate can be resumed later — even in
a different process — with full context intact (SQLite backend). Tests use the in-memory
backend for a single process.
"""

from __future__ import annotations

import sqlite3

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langchain_core.language_models import BaseChatModel

from ..config import Settings
from ..llm.providers import get_chat_model
from ..tools.pms import PMS
from .nodes import Nodes, route_after_gate
from .state import AgentState


def make_checkpointer(settings: Settings) -> BaseCheckpointSaver:
    if settings.checkpointer == "memory":
        return MemorySaver()
    conn = sqlite3.connect(settings.db_path, check_same_thread=False)
    saver = SqliteSaver(conn)
    saver.setup()
    return saver


def build_graph(
    settings: Settings,
    *,
    model: BaseChatModel | None = None,
    pms: PMS | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
):
    """Compile the agent graph. `model`/`pms`/`checkpointer` can be injected (tests)."""
    pms = pms or PMS.from_file(settings.data_path)
    model = model or get_chat_model(settings)
    nodes = Nodes(model, pms)

    graph = StateGraph(AgentState)
    graph.add_node("parse", nodes.parse)
    graph.add_node("classify", nodes.classify)
    graph.add_node("plan", nodes.plan)
    graph.add_node("approval_gate", nodes.approval_gate)
    graph.add_node("execute", nodes.execute)
    graph.add_node("finalize", nodes.finalize)

    graph.add_edge(START, "parse")
    graph.add_edge("parse", "classify")
    graph.add_edge("classify", "plan")
    graph.add_edge("plan", "approval_gate")
    graph.add_conditional_edges(
        "approval_gate", route_after_gate, {"execute": "execute", "finalize": "finalize"}
    )
    graph.add_edge("execute", "finalize")
    graph.add_edge("finalize", END)

    compiled = graph.compile(checkpointer=checkpointer or make_checkpointer(settings))
    return compiled, pms
