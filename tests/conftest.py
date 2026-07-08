"""Shared test fixtures + a scripted (offline) LLM.

Tests must not hit a real provider. `ScriptedModel` implements the two methods the graph
nodes actually use — `with_structured_output(...)` and `bind_tools(...)` — and returns
pre-canned results, so the full graph (parse → classify → plan → gate → execute) runs
deterministically without network access.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from hotel_agent.config import Settings
from hotel_agent.graph.build import build_graph
from hotel_agent.graph.state import ParsedEmail, Plan
from hotel_agent.tools.pms import PMS

DATA_PATH = str(Path(__file__).resolve().parents[1] / "data" / "mock_hotel_data.json")


class _Structured:
    def __init__(self, schema, parsed: ParsedEmail, plan: Plan):
        self._schema, self._parsed, self._plan = schema, parsed, plan

    def invoke(self, _messages):
        return self._parsed if self._schema is ParsedEmail else self._plan


class _Bound:
    """Returns scripted ReAct turns; each `.invoke` yields the next AIMessage."""

    def __init__(self, turns: list[list[dict]]):
        self._turns = list(turns)

    def invoke(self, _messages):
        calls = self._turns.pop(0) if self._turns else []
        tool_calls = [
            {"name": c["name"], "args": c.get("args", {}), "id": f"call_{i}", "type": "tool_call"}
            for i, c in enumerate(calls)
        ]
        return AIMessage(content="", tool_calls=tool_calls)


class ScriptedModel:
    def __init__(self, parsed: ParsedEmail, plan: Plan, tool_turns: list[list[dict]] | None = None):
        self._parsed = parsed
        self._plan = plan
        # Default: one turn that makes no tool calls (ReAct loop stops immediately).
        self._tool_turns = tool_turns if tool_turns is not None else [[]]

    def with_structured_output(self, schema, **_kwargs):
        return _Structured(schema, self._parsed, self._plan)

    def bind_tools(self, _tools):
        return _Bound(self._tool_turns)


@pytest.fixture
def pms() -> PMS:
    return PMS.from_file(DATA_PATH)


@pytest.fixture
def make_graph(pms):
    """Build a compiled graph with an injected scripted model + in-memory checkpointer."""

    def _factory(model: ScriptedModel):
        settings = Settings(checkpointer="memory", data_path=DATA_PATH)
        graph, bound_pms = build_graph(
            settings, model=model, pms=pms, checkpointer=MemorySaver()
        )
        return graph, bound_pms

    return _factory


def run_graph(graph, email: str, mode: str, dry_run: bool = False, thread_id: str = "t1"):
    config = {"configurable": {"thread_id": thread_id}}
    graph.invoke({"email": email, "mode": mode, "dry_run": dry_run}, config)
    return graph.get_state(config)


def resume_graph(graph, approved: bool, thread_id: str = "t1"):
    config = {"configurable": {"thread_id": thread_id}}
    graph.invoke(Command(resume={"approved": approved}), config)
    return graph.get_state(config)
