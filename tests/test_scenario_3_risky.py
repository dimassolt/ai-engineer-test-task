"""Scenario 3 — refund on a non-refundable booking is never executed autonomously."""

from conftest import ScriptedModel, run_graph
from hotel_agent.graph.state import ParsedEmail, Plan, PlanAction


def _refund_model() -> ScriptedModel:
    parsed = ParsedEmail(
        summary="Guest requests a refund on a non-refundable booking.",
        intent="refund_request",
        sender_email="maria.gonzalez@email.com",
        reservation_id="RES002",
    ) # type: ignore
    # Even if the model were tricked into proposing a cancel, the guardrail must strip it.
    plan = Plan(
        summary="Guest wants a refund on RES002 (non-refundable).",
        actions=[PlanAction(workflow="cancel_booking", args={"reservation_id": "RES002"})], # type: ignore
        draft_reply="Thank you — your request has been forwarded to our reservations team.",
    )
    return ScriptedModel(parsed, plan)


EMAIL = "I want a refund on my non-refundable booking RES002. — Maria (maria.gonzalez@email.com)"


def test_risky_request_pauses_even_in_auto_mode(make_graph):
    graph, pms = make_graph(_refund_model())
    before = pms.snapshot()

    snapshot = run_graph(graph, EMAIL, mode="auto")
    state = snapshot.values

    # Flagged risky, forced to a human even though mode is auto.
    assert state["risky"] is True
    assert snapshot.next, "risky request must pause for human review"
    # Actions were stripped by the guardrail: nothing to auto-execute.
    assert state["plan"]["actions"] == []
    assert "execution" not in state or state["execution"] == []
    # PMS is completely untouched — the non-refundable reservation is intact.
    assert pms.snapshot()["reservations"] == before["reservations"]
    assert pms.get_reservation("RES002")["status"] == "confirmed"


def test_risky_flags_are_reported(make_graph):
    graph, _ = make_graph(_refund_model())
    state = run_graph(graph, EMAIL, mode="auto").values
    codes = {f["code"] for f in state["risk_flags"]}
    assert "financial_request" in codes
    assert "non_refundable_change" in codes
