"""Scenario 1 — read-only availability lookup: plan uses read tools, performs no write."""

from conftest import ScriptedModel, run_graph
from hotel_agent.graph.state import ParsedEmail, Plan


def test_availability_lookup_makes_no_write(make_graph, pms):
    parsed = ParsedEmail(
        summary="Guest asks about room availability 20-22 April.",
        intent="availability_inquiry",
        check_in="2025-04-20", check_out="2025-04-22", adults=2,
    ) # type: ignore
    plan = Plan(
        summary="Answer availability for 20-22 April; no booking requested.",
        actions=[],
        draft_reply="We have Standard Double and Superior Double available for those dates.",
    )
    # Script the ReAct loop to actually call a read tool, then stop.
    model = ScriptedModel(parsed, plan, tool_turns=[
        [{"name": "find_availability", "args": {"check_in": "2025-04-20", "check_out": "2025-04-22", "adults": 2}}],
        [],
    ])
    graph, bound_pms = make_graph(model)

    reservations_before = len(bound_pms.snapshot()["reservations"])
    state = run_graph(graph, "Any rooms free April 20-22 for 2?", mode="auto").values

    assert state["status"] == "completed"
    assert state["plan"]["actions"] == []                 # no write scheduled
    assert state["execution"] == []                       # nothing executed
    assert state["tool_trace"], "planner should have called a read tool"
    assert state["sent"] is not None                      # reply was 'sent'
    assert len(bound_pms.snapshot()["reservations"]) == reservations_before  # PMS untouched
