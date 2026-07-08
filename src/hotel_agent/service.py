"""Orchestration entrypoint shared by the CLI and the Streamlit UI.

Two operations mirror the two-phase human-in-the-loop flow:
- `run_email` starts a run. It returns `awaiting_approval` if the graph paused at the gate,
  or a terminal status if it ran through (auto mode, not risky).
- `resume` supplies a human's approve/reject decision to a paused run (by `thread_id`).

Both return a `RunResult` carrying the full agent state for display.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from langgraph.types import Command

from .config import Settings
from .graph.build import build_graph


@dataclass
class RunResult:
    thread_id: str
    status: str                       # awaiting_approval | completed | rejected | ...
    state: dict[str, Any]             # full final/paused AgentState values
    approval_request: dict | None = None  # interrupt payload when awaiting approval

    @property
    def awaiting_approval(self) -> bool:
        return self.status == "awaiting_approval"


def _collect(graph, thread_id: str, invoke_result: Any) -> RunResult:
    config = {"configurable": {"thread_id": thread_id}}
    snapshot = graph.get_state(config)
    values = dict(snapshot.values)

    if snapshot.next:  # graph paused at an interrupt (the approval gate)
        payload = None
        for task in snapshot.tasks:
            if getattr(task, "interrupts", None):
                payload = task.interrupts[0].value
                break
        if payload is None and isinstance(invoke_result, dict):
            interrupts = invoke_result.get("__interrupt__")
            if interrupts:
                payload = interrupts[0].value
        return RunResult(thread_id, "awaiting_approval", values, payload)

    return RunResult(thread_id, values.get("status", "completed"), values)


def run_email(email: str, settings: Settings, thread_id: str | None = None) -> RunResult:
    graph, _ = build_graph(settings)
    thread_id = thread_id or uuid.uuid4().hex[:12]
    config = {"configurable": {"thread_id": thread_id}}
    result = graph.invoke(
        {"email": email, "mode": settings.mode, "dry_run": settings.dry_run},
        config,
    )
    return _collect(graph, thread_id, result)


def resume(
    settings: Settings, thread_id: str, approved: bool, edited_reply: str | None = None
) -> RunResult:
    graph, _ = build_graph(settings)
    config = {"configurable": {"thread_id": thread_id}}
    payload: dict[str, Any] = {"approved": approved}
    if edited_reply:
        payload["edited_reply"] = edited_reply
    result = graph.invoke(Command(resume=payload), config)
    return _collect(graph, thread_id, result)
