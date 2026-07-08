"""Agent state + the typed schemas the LLM must fill.

`AgentState` is the context object carried between graph nodes and persisted by the
checkpointer per `thread_id` (this is how context survives a pause at the approval gate).
Values are stored as plain dicts/lists so they serialize cleanly across process restarts.

The Pydantic models are used with `model.with_structured_output(...)` to force the LLM to
return well-formed, typed results (a parsed email and an action plan) instead of free text.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, TypedDict

from pydantic import BaseModel, Field

INTENTS = Literal[
    "availability_inquiry",
    "new_booking",
    "modify_booking",
    "cancel_booking",
    "refund_request",
    "policy_question",
    "other",
]

WORKFLOW_NAMES = Literal["make_reservation", "change_reservation", "cancel_booking"]


class ParsedEmail(BaseModel):
    """Structured fields extracted from an inbound guest email."""

    summary: str = Field(description="One sentence: what the guest is asking for.")
    intent: INTENTS = Field(description="Primary intent of the email.")
    sender_email: str | None = Field(None, description="Guest email if present in the text/signature.")
    sender_name: str | None = Field(None, description="Guest full name if present.")
    check_in: str | None = Field(None, description="Check-in date, ISO YYYY-MM-DD.")
    check_out: str | None = Field(None, description="Check-out date, ISO YYYY-MM-DD (exclusive).")
    adults: int | None = Field(None, description="Number of adults if stated.")
    children: int | None = Field(None, description="Number of children if stated.")
    room_preference: str | None = Field(None, description="Free-text room/rate wish, e.g. 'double with breakfast'.")
    reservation_id: str | None = Field(None, description="Reservation id like RES001 if referenced.")


class PlanAction(BaseModel):
    """One workflow the agent proposes to run (executed only after approval)."""

    workflow: WORKFLOW_NAMES
    args: dict[str, Any] = Field(default_factory=dict, description="Arguments for the workflow.")
    rationale: str = Field("", description="Why this action, in one line.")


class Plan(BaseModel):
    """The agent's structured proposal: what to do + the draft reply."""

    summary: str = Field(description="Plain-language summary of what will happen.")
    actions: list[PlanAction] = Field(
        default_factory=list,
        description="Ordered workflows to execute. Empty for read-only requests.",
    )
    draft_reply: str = Field(description="The full draft email reply to the guest.")


class AgentState(TypedDict, total=False):
    # --- inputs ---
    email: str
    mode: str            # "human" | "auto"
    dry_run: bool
    # --- parse ---
    parsed: dict
    # --- classify (risk) ---
    intent: str
    risk_flags: list[dict]
    risky: bool
    # --- plan ---
    plan: dict           # serialized Plan
    draft_reply: str
    tool_trace: list[dict]
    # --- approval gate ---
    approved: bool
    approval: str        # "auto_approved" | "approved" | "rejected" | "not_required" | "pending"
    # --- execute ---
    execution: list[dict]
    sent: dict | None
    # --- finalize ---
    status: str          # "completed" | "awaiting_approval" | "rejected" | "no_action" | "error"
    # `log` accumulates across nodes (each node appends one line) so the finished state
    # carries the whole decision trail; the `operator.add` reducer concatenates the lists.
    log: Annotated[list[str], operator.add]
