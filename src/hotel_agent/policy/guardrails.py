"""Risk guardrails (Scenario 3).

The single rule the task is most explicit about: ambiguous, policy-sensitive, or
financially risky requests must **never execute autonomously** — in either mode. This
module makes that decision *deterministically* (no LLM), so it is reliable and unit
testable. `classify` calls `assess_risk`; if it returns any flag, the approval gate forces
a human review even in `auto` mode.

Two complementary signals are used so the guard is hard to slip past:
1. **Keyword signals** on the raw email text (refund / chargeback / dispute ...). Robust
   even if intent parsing is wrong.
2. **Data signals** from the PMS (the guest's booking is on a non-refundable rate). Precise
   when we can identify the reservation.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..tools.pms import PMS

# Financial / dispute language → always route to a human.
_FINANCIAL_KEYWORDS = (
    "refund", "charge back", "chargeback", "compensation", "compensate",
    "reimburse", "money back", "waive", "dispute", "cancel the charge",
)

# Intents that touch an existing booking (so a non-refundable rate is relevant).
_CHANGE_INTENTS = {"cancel_booking", "modify_booking", "refund_request"}
_AMBIGUOUS_INTENTS = {"other", "unknown", "ambiguous", ""}


@dataclass(frozen=True)
class RiskFlag:
    code: str
    reason: str


def assess_risk(
    *,
    intent: str,
    text: str,
    pms: PMS,
    sender_email: str | None = None,
    reservation_id: str | None = None,
) -> list[RiskFlag]:
    """Return risk flags for a parsed email. Empty list == safe to automate."""
    flags: list[RiskFlag] = []
    low = (text or "").lower()

    # 1) Financial / dispute language.
    hit = next((kw for kw in _FINANCIAL_KEYWORDS if kw in low), None)
    if hit or intent == "refund_request":
        flags.append(RiskFlag(
            "financial_request",
            f"Mentions a financial/refund action ('{hit or 'refund'}') — needs human review.",
        ))

    # 2) Change/cancel against a non-refundable booking (data signal).
    if intent in _CHANGE_INTENTS:
        reservations = []
        if reservation_id:
            try:
                reservations = [pms.get_reservation(reservation_id)]
            except Exception:
                reservations = []
        elif sender_email:
            reservations = pms.reservations_for_guest(email=sender_email)
        for r in reservations:
            if pms.rate_plan(r["rate_plan_id"])["cancellation_policy"] == "non_refundable":
                flags.append(RiskFlag(
                    "non_refundable_change",
                    f"Reservation {r['id']} is on a non-refundable rate; "
                    f"cancelling/refunding it is not automatable.",
                ))
                break

    # 2b) The guest themselves calls the booking non-refundable (text signal).
    if intent in _CHANGE_INTENTS and "non-refundable" in low and not any(
        f.code == "non_refundable_change" for f in flags
    ):
        flags.append(RiskFlag(
            "non_refundable_change",
            "Request references a non-refundable booking — not automatable.",
        ))

    # 3) Ambiguous / unclassifiable intent.
    if intent in _AMBIGUOUS_INTENTS:
        flags.append(RiskFlag("ambiguous", "Intent is unclear — a human should decide."))

    return flags


def is_risky(flags: list[RiskFlag]) -> bool:
    return len(flags) > 0
