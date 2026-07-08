"""Guardrail unit tests — the deterministic Scenario 3 risk detection."""

from hotel_agent.policy.guardrails import assess_risk, is_risky
from hotel_agent.tools.pms import PMS


def test_refund_keyword_is_flagged(pms: PMS):
    flags = assess_risk(intent="refund_request", text="I want a refund please", pms=pms)
    assert is_risky(flags)
    assert any(f.code == "financial_request" for f in flags)


def test_cancel_on_non_refundable_booking_is_flagged(pms: PMS):
    # G002 / maria owns RES002 which is on the non-refundable rate (RP003).
    flags = assess_risk(
        intent="cancel_booking",
        text="Please cancel my booking",
        pms=pms,
        sender_email="maria.gonzalez@email.com",
    )
    assert any(f.code == "non_refundable_change" for f in flags)


def test_plain_availability_question_is_clear(pms: PMS):
    flags = assess_risk(intent="availability_inquiry", text="Any rooms free Apr 20-22?", pms=pms)
    assert not is_risky(flags)


def test_ambiguous_intent_is_flagged(pms: PMS):
    flags = assess_risk(intent="other", text="Hello, a question", pms=pms)
    assert any(f.code == "ambiguous" for f in flags)


def test_new_booking_on_non_refundable_rate_is_not_flagged(pms: PMS):
    # Booking a non-refundable option is a normal request — 'refund' inside 'non-refundable'
    # must NOT trip the financial guard, and a create is never routed to a human for this.
    flags = assess_risk(
        intent="new_booking",
        text="I'd like to book the non-refundable double for April 20-22, 2 adults.",
        pms=pms,
    )
    assert not is_risky(flags)


def test_new_booking_is_not_blocked_by_financial_keywords(pms: PMS):
    # Only modify/cancel/refund are gated; a create always proceeds.
    flags = assess_risk(intent="new_booking", text="Please book and I'll pay the full charge.", pms=pms)
    assert not any(f.code == "financial_request" for f in flags)
