"""Risk guardrails (Scenario 3).

Ambiguous, policy-sensitive, or financially risky requests must **never execute autonomously** — in either mode. 
This module makes that decision *deterministically* (no LLM), so it is reliable and unit
testable. `classify` calls `assess_risk`; if it returns any flag, the approval gate forces
a human review even in `auto` mode.

Scope: the financial/refund and non-refundable checks apply **only to changes to an existing
booking** (modify / cancel / refund). A **new booking is a normal request and always
proceeds** — even when the guest picks a non-refundable rate (that is a rate *choice*, not a
refund). Two complementary signals catch the risky changes:
1. **Keyword signals** on the raw email text (refund / chargeback / dispute ...), matched on
   word boundaries so "non-refundable" never trips "refund".
2. **Data signals** from the PMS (the guest's booking is on a non-refundable rate). Precise
   when we can identify the reservation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..tools.pms import PMS

# Financial / dispute language → route an existing-booking change to a human. Word-boundary
# matched, so "non-refundable" (a valid rate choice when booking) never matches "refund".
_FINANCIAL_RE = re.compile(
    r"\b(refunds?|charge ?backs?|compensat(?:e|ion)|reimburse\w*|money back|"
    r"waive\w*|disput\w*|cancel the charge)\b",
    re.IGNORECASE,
)

# Intents that touch an existing booking (where a non-refundable rate / refund is relevant).
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
    """Return risk flags for a parsed email. Empty list == safe to automate.

    New bookings (and availability/policy questions) are never flagged here — only changes
    to an existing reservation can be risky.
    """
    flags: list[RiskFlag] = []
    low = (text or "").lower()

    # Financial/refund + non-refundable checks: only for changes to an existing booking.
    # A create request always passes this gate.
    if intent in _CHANGE_INTENTS:
        # 1) Financial / dispute language (or an explicit refund intent).
        match = _FINANCIAL_RE.search(low)
        if match or intent == "refund_request":
            hit = match.group(0) if match else "refund"
            flags.append(RiskFlag(
                "financial_request",
                f"Mentions a financial/refund action ('{hit}') — needs human review.",
            ))

        # 2) Change/cancel against a non-refundable booking (data signal).
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

        # 2b) The guest themselves calls the existing booking non-refundable (text signal).
        if "non-refundable" in low and not any(f.code == "non_refundable_change" for f in flags):
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
