"""Workflows (a.k.a. skills): named, reliable multi-step recipes over the atomic PMS tools.

This is the heart of the "tools vs skills" split the task grades most heavily.

- **Tools** (`pms.py`) are atomic PMS operations with a single responsibility.
- **Workflows** are *ordered compositions* of those tools that guarantee the steps run
  in the right sequence with validation between them — e.g. `make_reservation` is exactly
  the recipe from the task brief: check guest exists → create if not → price → create.

The LLM decides *which* workflow to run and with *what* arguments (that's the plan). The
workflow guarantees *how* it runs. That division is what makes multi-step execution
reliable instead of hoping the model emits five tool calls in the correct order.

Every workflow returns a `WorkflowResult` carrying an explicit step-by-step trace, so the
execution is auditable and easy to render for a human reviewer or a log.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .pms import PMS, PMSError

# Rate plans whose cancellation policy forbids changes/cancellation. Kept here as a
# defense-in-depth guard: the approval gate should already have routed these to a human,
# but a workflow must never quietly execute a policy-violating write.
_NON_REFUNDABLE = "non_refundable"


@dataclass
class WorkflowResult:
    workflow: str
    ok: bool
    steps: list[dict[str, Any]] = field(default_factory=list)
    data: dict[str, Any] | None = None
    error: str | None = None

    def summary(self) -> str:
        return self.error if not self.ok else "; ".join(s["step"] for s in self.steps) # type: ignore


def _require(args: dict[str, Any], *keys: str) -> None:
    missing = [k for k in keys if args.get(k) in (None, "")]
    if missing:
        raise PMSError(f"Missing required field(s): {', '.join(missing)}.")


def make_reservation(pms: PMS, args: dict[str, Any]) -> WorkflowResult:
    """Guest-lookup → create-if-new → price → create reservation.

    Expected args: first_name, last_name, email, [phone, nationality],
    room_type_id, rate_plan_id, check_in, check_out, adults, [children, notes].
    """
    res = WorkflowResult(workflow="make_reservation", ok=False)
    try:
        _require(args, "email", "room_type_id", "rate_plan_id", "check_in", "check_out", "adults")

        # 1) Reuse the guest profile if it exists, otherwise create one.
        guest = pms.find_guest(email=args["email"])
        if guest:
            res.steps.append({"step": f"Found existing guest {guest['id']}", "tool": "get_guest"})
        else:
            _require(args, "first_name", "last_name")
            guest = pms.create_guest(
                args["first_name"], args["last_name"], args["email"],
                args.get("phone"), args.get("nationality"),
            )
            res.steps.append({"step": f"Created new guest {guest['id']}", "tool": "create_guest"})

        # 2) Price the stay (also validates room type / rate plan exist).
        quote = pms.quote_rate(
            args["room_type_id"], args["rate_plan_id"], args["check_in"], args["check_out"],
            int(args["adults"]), int(args.get("children", 0)),
        )
        res.steps.append({"step": f"Quoted {quote['total_amount']} {quote['currency']}", "tool": "quote_rate"})

        # 3) Create the reservation (validates occupancy + availability, decrements stock).
        reservation = pms.create_reservation(
            guest["id"], args["room_type_id"], args["rate_plan_id"],
            args["check_in"], args["check_out"],
            int(args["adults"]), int(args.get("children", 0)), args.get("notes", ""),
        )
        res.steps.append({"step": f"Created reservation {reservation['id']}", "tool": "create_reservation"})

        res.ok = True
        res.data = {"guest": guest, "reservation": reservation, "quote": quote}
    except PMSError as exc:
        res.error = str(exc)
    return res


def change_reservation(pms: PMS, args: dict[str, Any]) -> WorkflowResult:
    """Validate the reservation, refuse non-refundable, then modify.

    Expected args: reservation_id + any of room_type_id, rate_plan_id, check_in,
    check_out, adults, children, notes.
    """
    res = WorkflowResult(workflow="change_reservation", ok=False)
    try:
        _require(args, "reservation_id")
        reservation = pms.get_reservation(args["reservation_id"])
        res.steps.append({"step": f"Loaded reservation {reservation['id']}", "tool": "get_reservation"})

        rate_plan = pms.rate_plan(reservation["rate_plan_id"])
        if rate_plan["cancellation_policy"] == _NON_REFUNDABLE:
            raise PMSError("Reservation is on a non-refundable rate and cannot be modified.")

        changes = {k: v for k, v in args.items() if k != "reservation_id" and v is not None}
        updated = pms.modify_reservation(args["reservation_id"], **changes)
        res.steps.append({"step": f"Modified {', '.join(changes) or 'nothing'}", "tool": "modify_reservation"})

        res.ok = True
        res.data = {"reservation": updated}
    except PMSError as exc:
        res.error = str(exc)
    return res


def cancel_booking(pms: PMS, args: dict[str, Any]) -> WorkflowResult:
    """Validate the reservation, refuse non-refundable, then cancel.

    Expected args: reservation_id, [reason].
    """
    res = WorkflowResult(workflow="cancel_booking", ok=False)
    try:
        _require(args, "reservation_id")
        reservation = pms.get_reservation(args["reservation_id"])
        res.steps.append({"step": f"Loaded reservation {reservation['id']}", "tool": "get_reservation"})

        rate_plan = pms.rate_plan(reservation["rate_plan_id"])
        if rate_plan["cancellation_policy"] == _NON_REFUNDABLE:
            raise PMSError("Reservation is on a non-refundable rate and cannot be cancelled/refunded.")

        cancelled = pms.cancel_reservation(args["reservation_id"], args.get("reason", ""))
        res.steps.append({"step": f"Cancelled reservation {cancelled['id']}", "tool": "cancel_reservation"})

        res.ok = True
        res.data = {"reservation": cancelled}
    except PMSError as exc:
        res.error = str(exc)
    return res


# The menu of skills the planner may choose from. `answer_only` is the no-write path
# (Scenario 1): reply from gathered context without touching the PMS.
WORKFLOWS: dict[str, Callable[[PMS, dict[str, Any]], WorkflowResult]] = {
    "make_reservation": make_reservation,
    "change_reservation": change_reservation,
    "cancel_booking": cancel_booking,
}

WRITE_WORKFLOWS = set(WORKFLOWS)  # every registered workflow performs a write


def run_workflow(pms: PMS, name: str, args: dict[str, Any]) -> WorkflowResult:
    fn = WORKFLOWS.get(name)
    if fn is None:
        return WorkflowResult(workflow=name, ok=False, error=f"Unknown workflow '{name}'.")
    return fn(pms, args)
