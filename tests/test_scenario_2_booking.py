"""Scenario 2 — booking with a write. Covers both approval modes."""

from conftest import ScriptedModel, resume_graph, run_graph
from hotel_agent.graph.state import ParsedEmail, Plan, PlanAction


def _booking_model() -> ScriptedModel:
    parsed = ParsedEmail(
        summary="Guest wants to book a double with breakfast, 20-22 April, 2 adults.",
        intent="new_booking",
        sender_email="ola@example.com", sender_name="Ola Nordmann",
        check_in="2025-04-20", check_out="2025-04-22", adults=2,
        room_preference="double with breakfast",
    ) # type: ignore
    plan = Plan(
        summary="Book a Standard Double with breakfast for 2 adults, 20-22 April (4600 NOK).",
        actions=[PlanAction(
            workflow="make_reservation",
            args={
                "first_name": "Ola", "last_name": "Nordmann", "email": "ola@example.com",
                "room_type_id": "RT002", "rate_plan_id": "RP002",
                "check_in": "2025-04-20", "check_out": "2025-04-22", "adults": 2,
            },
            rationale="Standard Double (RT002) + Breakfast Included (RP002).",
        )],
        draft_reply="Happy to confirm your Standard Double with breakfast for 20-22 April.",
    )
    return ScriptedModel(parsed, plan)


EMAIL = "Hi, we'd like to book a double room with breakfast for 2 adults, April 20-22. — Ola (ola@example.com)"


def test_auto_mode_books_end_to_end(make_graph):
    graph, pms = make_graph(_booking_model())
    before = len(pms.snapshot()["reservations"])

    state = run_graph(graph, EMAIL, mode="auto").values

    assert state["status"] == "completed"
    assert state["approval"] == "auto_approved"
    assert state["execution"][0]["ok"]
    assert len(pms.snapshot()["reservations"]) == before + 1
    assert state["sent"] is not None


def test_human_mode_pauses_then_executes_on_approval(make_graph):
    graph, pms = make_graph(_booking_model())
    before = len(pms.snapshot()["reservations"])

    snapshot = run_graph(graph, EMAIL, mode="human")
    # Paused at the gate: nothing written yet.
    assert snapshot.next  # graph is interrupted
    assert len(pms.snapshot()["reservations"]) == before

    final = resume_graph(graph, approved=True).values
    assert final["status"] == "completed"
    assert final["approval"] == "approved"
    assert len(pms.snapshot()["reservations"]) == before + 1


def test_human_mode_reject_writes_nothing(make_graph):
    graph, pms = make_graph(_booking_model())
    before = len(pms.snapshot()["reservations"])

    run_graph(graph, EMAIL, mode="human")
    final = resume_graph(graph, approved=False).values

    assert final["status"] == "rejected"
    assert final.get("execution", []) == []
    assert len(pms.snapshot()["reservations"]) == before  # no write on reject
