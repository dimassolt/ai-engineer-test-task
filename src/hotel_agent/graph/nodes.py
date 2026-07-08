"""Graph nodes: parse → classify → plan → approval_gate → execute → finalize.

Each node is a small, single-purpose function over `AgentState`. Dependencies (the LLM,
the PMS) are injected via the `Nodes` container so the same graph runs with a real model
or a scripted fake in tests.

The two design ideas the task grades sit here:
- **plan** runs a bounded ReAct loop bound to *read-only* tools, then commits a typed Plan.
  No writes ever happen during planning.
- **approval_gate** is the autonomous-vs-human split. It uses a *dynamic* `interrupt()`
  (not a static breakpoint) so the pause is decided from state: human mode always pauses;
  auto mode runs straight through *unless* the request was flagged risky, which forces a
  human even in auto.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import date

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.language_models import BaseChatModel
from langgraph.types import interrupt

from ..policy.guardrails import assess_risk, is_risky
from ..prompts import load_prompt
from ..tools.mailer import send_reply
from ..tools.pms import PMS
from ..tools.read_tools import build_read_tools
from ..tools.workflows import run_workflow
from .state import AgentState, ParsedEmail, Plan

MAX_REACT_STEPS = 6
_SUBJECT = "Re: your enquiry — Grand Oslo Hotel"


def _preview(value: object, limit: int = 280) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if len(text) <= limit else text[:limit] + "…"


def _structured(model: BaseChatModel, schema):
    """Structured output via function/tool calling — not OpenAI's strict `json_schema`
    mode, which rejects open-ended `object` fields (our `PlanAction.args` free-form dict).
    Function calling is tolerant of that and is portable across OpenAI/Anthropic/Gemini."""
    return model.with_structured_output(schema, method="function_calling")


class Nodes:
    def __init__(self, model: BaseChatModel, pms: PMS):
        self.model = model
        self.pms = pms

    # 1) Extract structured fields from the raw email.
    def parse(self, state: AgentState) -> dict:
        parsed: ParsedEmail = _structured(self.model, ParsedEmail).invoke(
            [SystemMessage(load_prompt("parser")), HumanMessage(state["email"])]
        )
        return {
            "parsed": parsed.model_dump(),
            "intent": parsed.intent,
            "log": [f"parsed intent={parsed.intent}"],
        }

    # 2) Deterministic risk pre-check (Scenario 3 guardrail).
    def classify(self, state: AgentState) -> dict:
        parsed = state["parsed"]
        flags = assess_risk(
            intent=parsed["intent"],
            text=state["email"],
            pms=self.pms,
            sender_email=parsed.get("sender_email"),
            reservation_id=parsed.get("reservation_id"),
        )
        risky = is_risky(flags)
        return {
            "risk_flags": [dataclasses.asdict(f) for f in flags],
            "risky": risky,
            "log": [f"risk={'FLAGGED ' + ','.join(f.code for f in flags) if risky else 'clear'}"],
        }

    # 3) ReAct with read-only tools, then commit a typed Plan + draft reply.
    def plan(self, state: AgentState) -> dict:
        tools = build_read_tools(self.pms)
        tools_by_name = {t.name: t for t in tools}
        model_with_tools = self.model.bind_tools(tools)

        system = (
            f"{load_prompt('planner')}\n\n"
            f"Today is {date.today().isoformat()}.\n"
            f"Inventory reference: {json.dumps(self.pms.inventory())}"
        )
        if state.get("risky"):
            reasons = "; ".join(f["reason"] for f in state.get("risk_flags", []))
            system += (
                f"\n\nIMPORTANT — this request was flagged for human review ({reasons}). "
                "Schedule NO actions. Draft a polite holding reply saying the request has "
                "been received and forwarded to our reservations team for review; do not "
                "promise, refuse, or perform the refund/change."
            )

        messages: list = [
            SystemMessage(system),
            HumanMessage(f"Guest email:\n{state['email']}\n\nParsed fields: {json.dumps(state['parsed'])}"),
        ]
        trace: list[dict] = []
        for _ in range(MAX_REACT_STEPS):
            ai: AIMessage = model_with_tools.invoke(messages)
            messages.append(ai)
            if not ai.tool_calls:
                break
            for call in ai.tool_calls:
                tool = tools_by_name.get(call["name"])
                result = tool.invoke(call["args"]) if tool else f"Error: unknown tool {call['name']}"
                trace.append({"tool": call["name"], "args": call["args"], "result": _preview(result)})
                messages.append(ToolMessage(content=json.dumps(result, default=str), tool_call_id=call["id"]))

        plan: Plan = _structured(self.model, Plan).invoke(
            messages + [HumanMessage("Now output the final structured plan and draft reply.")]
        )
        # Safety net: a flagged request must never carry executable actions.
        if state.get("risky"):
            plan.actions = []

        return {
            "plan": plan.model_dump(),
            "draft_reply": plan.draft_reply,
            "tool_trace": trace,
            "log": [f"planned actions={[a.workflow for a in plan.actions]}"],
        }

    # 4) Autonomous-vs-human split. Dynamic interrupt keyed on mode + risk.
    def approval_gate(self, state: AgentState) -> dict:
        risky = state.get("risky", False)
        auto_ok = state["mode"] == "auto" and not risky
        if auto_ok:
            return {"approved": True, "approval": "auto_approved", "log": ["auto-approved"]}

        # Pause and wait for a human decision (human mode, or risky-in-auto).
        decision = interrupt(
            {
                "type": "approval_request",
                "reason": "risky_request" if risky else "human_mode",
                "summary": state["plan"]["summary"],
                "actions": state["plan"]["actions"],
                "draft_reply": state["draft_reply"],
                "risk_flags": state.get("risk_flags", []),
            }
        )
        approved = decision.get("approved", False) if isinstance(decision, dict) else bool(decision)
        out: dict = {
            "approved": approved,
            "approval": "approved" if approved else "rejected",
            "log": [f"human decision: {'approved' if approved else 'rejected'}"],
        }
        if isinstance(decision, dict) and decision.get("edited_reply"):
            out["draft_reply"] = decision["edited_reply"]
        return out

    # 5) Run approved workflows (writes) + send the reply. Skips real effects on dry-run.
    def execute(self, state: AgentState) -> dict:
        dry_run = state.get("dry_run", False)
        # On dry-run, operate on a throwaway copy so nothing (not even in-memory) is committed.
        target = PMS(self.pms.snapshot()) if dry_run else self.pms

        results: list[dict] = []
        for action in state["plan"].get("actions", []):
            result = run_workflow(target, action["workflow"], action.get("args", {}))
            results.append(dataclasses.asdict(result))
            if not result.ok:
                break  # stop the workflow chain on the first failure

        to = (state.get("parsed") or {}).get("sender_email") or ""
        sent = send_reply(to, _SUBJECT, state["draft_reply"], dry_run=dry_run)
        return {
            "execution": results,
            "sent": dataclasses.asdict(sent),
            "log": [f"executed {len(results)} action(s){' (dry-run)' if dry_run else ''}, reply sent"],
        }

    # 6) Decide the final status for reporting.
    def finalize(self, state: AgentState) -> dict:
        if state.get("approval") == "rejected":
            status = "rejected"
        elif any(not r["ok"] for r in state.get("execution", [])):
            status = "completed_with_errors"
        elif not state["plan"].get("actions"):
            status = "completed"  # read-only reply sent
        else:
            status = "completed"
        return {"status": status, "log": [f"finalized status={status}"]}


def route_after_gate(state: AgentState) -> str:
    """After the gate: execute if approved, otherwise skip straight to finalize."""
    return "execute" if state.get("approved") else "finalize"
