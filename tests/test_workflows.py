"""Workflow (skill) unit tests — ordered multi-step execution + policy guards."""

from hotel_agent.tools.pms import PMS
from hotel_agent.tools.workflows import cancel_booking, change_reservation, make_reservation


def test_make_reservation_creates_guest_then_books(pms: PMS):
    # New guest (not in seed data) → workflow must create the profile, then the reservation.
    result = make_reservation(pms, {
        "first_name": "Ola", "last_name": "Nordmann", "email": "ola@example.com",
        "room_type_id": "RT002", "rate_plan_id": "RP002",
        "check_in": "2025-04-20", "check_out": "2025-04-22", "adults": 2,
    })
    assert result.ok, result.error
    steps = [s["tool"] for s in result.steps]
    assert steps == ["create_guest", "quote_rate", "create_reservation"]
    assert result.data["reservation"]["status"] == "confirmed"
    # RT002 + RP002, 2 nights, 2 adults: 1800*2 + 250*2*2 = 4600.
    assert result.data["reservation"]["total_amount"] == 4600


def test_make_reservation_reuses_existing_guest(pms: PMS):
    result = make_reservation(pms, {
        "email": "erik.hansen@email.com", "first_name": "Erik", "last_name": "Hansen",
        "room_type_id": "RT003", "rate_plan_id": "RP001",
        "check_in": "2025-04-20", "check_out": "2025-04-22", "adults": 2,
    })
    assert result.ok
    assert result.steps[0]["tool"] == "get_guest"  # reused, not created
    assert result.data["guest"]["id"] == "G001"


def test_cancel_booking_refuses_non_refundable(pms: PMS):
    # RES002 is on RP003 (non-refundable) — the workflow must refuse (defense in depth).
    result = cancel_booking(pms, {"reservation_id": "RES002"})
    assert not result.ok
    assert "non-refundable" in result.error.lower()
    assert pms.get_reservation("RES002")["status"] == "confirmed"  # untouched


def test_change_reservation_updates_party(pms: PMS):
    # RES001 (RT002, standard rate) — reduce party to 1 adult; same room/dates stays available.
    result = change_reservation(pms, {"reservation_id": "RES001", "adults": 1})
    assert result.ok, result.error
    assert result.steps[-1]["tool"] == "modify_reservation"
    assert pms.get_reservation("RES001")["adults"] == 1
    assert pms.get_reservation("RES001")["total_amount"] == 5400  # 1800 * 3 nights, unchanged
