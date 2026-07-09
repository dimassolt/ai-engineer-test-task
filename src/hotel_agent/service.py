"""Orchestration entrypoint shared by the CLI and the Streamlit UI.

Two operations mirror the two-phase human-in-the-loop flow:
- `run_email` starts a run. It returns `awaiting_approval` if the graph paused at the gate,
  or a terminal status if it ran through (auto mode, not risky).
- `resume` supplies a human's approve/reject decision to a paused run (by `thread_id`).

`stream_run` / `stream_resume` are streaming variants that yield after each graph node so a
UI can show live progress; they finish by yielding the same `RunResult`.

Every terminal run is appended to the SQLite decision log (`history.py`) unless the run
uses the in-memory checkpointer (tests) — so the audit trail only reflects real runs.

All entrypoints return a `RunResult` carrying the full agent state for display.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Iterator

from langgraph.types import Command

from .config import Settings
from .graph.build import build_graph
from .history import record_decision


@dataclass
class RunResult:
    thread_id: str
    status: str                       # awaiting_approval | completed | rejected | ...
    state: dict[str, Any]             # full final/paused AgentState values
    approval_request: dict | None = None  # interrupt payload when awaiting approval

    @property
    def awaiting_approval(self) -> bool:
        return self.status == "awaiting_approval"


def _wrote_pms(state: dict[str, Any]) -> bool:
    """True only if a write workflow actually committed."""
    return any(r.get("ok") for r in state.get("execution", []))


def _maybe_persist(settings: Settings | None, pms, result: RunResult) -> None:
    """Write the mutated PMS back to disk after an approved write.

    Gated to non-memory checkpointers so unit tests (which load the real seed file with the
    in-memory backend) never mutate `data/mock_hotel_data.json`."""
    if settings is None or settings.checkpointer == "memory" or pms is None:
        return
    if _wrote_pms(result.state):
        try:
            pms.save(settings.data_path)
        except Exception:  # noqa: BLE001 — persistence is best-effort, never break a run
            pass


def _maybe_record(settings: Settings | None, result: RunResult) -> None:
    """Append a terminal decision to the audit log (skipped for the in-memory backend)."""
    if settings is None or settings.checkpointer == "memory" or result.awaiting_approval:
        return
    s = result.state
    actions = [a.get("workflow") for a in s.get("plan", {}).get("actions", [])]
    record_decision(
        settings.history_db_path,
        thread_id=result.thread_id,
        intent=s.get("intent"),
        risky=s.get("risky"),
        approval=s.get("approval"),
        status=result.status,
        wrote_pms=_wrote_pms(s),
        actions=actions,
        email=s.get("email"),
        answer=s.get("draft_reply"),
        sent_to=(s.get("sent") or {}).get("to"),
    )


def _collect(graph, thread_id: str, invoke_result: Any,
             settings: Settings | None = None, pms=None) -> RunResult:
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

    result = RunResult(thread_id, values.get("status", "completed"), values)
    _maybe_persist(settings, pms, result)
    _maybe_record(settings, result)
    return result


def _inputs(email: str, settings: Settings) -> dict[str, Any]:
    return {"email": email, "mode": settings.mode}


# ── Blocking entrypoints (used by the CLI) ───────────────────────────────────

def run_email(email: str, settings: Settings, thread_id: str | None = None) -> RunResult:
    graph, pms = build_graph(settings)
    thread_id = thread_id or uuid.uuid4().hex[:12]
    config = {"configurable": {"thread_id": thread_id}}
    result = graph.invoke(_inputs(email, settings), config)  # type: ignore
    return _collect(graph, thread_id, result, settings, pms)


def resume(
    settings: Settings, thread_id: str, approved: bool, edited_reply: str | None = None
) -> RunResult:
    graph, pms = build_graph(settings)
    config = {"configurable": {"thread_id": thread_id}}
    payload: dict[str, Any] = {"approved": approved}
    if edited_reply:
        payload["edited_reply"] = edited_reply
    result = graph.invoke(Command(resume=payload), config)  # type: ignore
    return _collect(graph, thread_id, result, settings, pms)


# ── Streaming entrypoints (used by the UI for live progress) ──────────────────
#
# Each yields ("node", <chunk>) after every graph node completes, where <chunk> is
# `{node_name: state_delta}` (or `{"__interrupt__": (...)}` when the gate pauses), then a
# final ("result", RunResult) once the graph settles or pauses at the approval gate.

def stream_run(
    email: str, settings: Settings, thread_id: str | None = None
) -> Iterator[tuple[str, Any]]:
    graph, pms = build_graph(settings)
    thread_id = thread_id or uuid.uuid4().hex[:12]
    config = {"configurable": {"thread_id": thread_id}}
    last: Any = None
    for chunk in graph.stream(_inputs(email, settings), config, stream_mode="updates"):  # type: ignore
        last = chunk
        yield ("node", chunk)
    yield ("result", _collect(graph, thread_id, last, settings, pms))


def stream_resume(
    settings: Settings, thread_id: str, approved: bool, edited_reply: str | None = None
) -> Iterator[tuple[str, Any]]:
    graph, pms = build_graph(settings)
    config = {"configurable": {"thread_id": thread_id}}
    payload: dict[str, Any] = {"approved": approved}
    if edited_reply:
        payload["edited_reply"] = edited_reply
    last: Any = None
    for chunk in graph.stream(Command(resume=payload), config, stream_mode="updates"):  # type: ignore
        last = chunk
        yield ("node", chunk)
    yield ("result", _collect(graph, thread_id, last, settings, pms))
