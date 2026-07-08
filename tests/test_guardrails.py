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
