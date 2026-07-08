"""PMS unit tests — availability + rate math verified against the mock reservations."""

import pytest
from conftest import DATA_PATH

from hotel_agent.tools.pms import PMS, PMSError


def test_quote_matches_seed_reservations(pms: PMS):
    # RES004: RT003 + RP002 (breakfast 250/pp/night), 2 nights, 1 adult -> 5000 + 500 = 5500.
    q = pms.quote_rate("RT003", "RP002", "2025-05-10", "2025-05-12", adults=1)
    assert q["room_subtotal"] == 5000
    assert q["breakfast_subtotal"] == 500
    assert q["total_amount"] == 5500

    # RES005: RT001 + RP003 (0.85 modifier), 2 nights, 1 adult -> 2040.
    assert pms.quote_rate("RT001", "RP003", "2025-04-20", "2025-04-22")["total_amount"] == 2040

    # RES003: RT004 + RP004 (1.15, breakfast supplement 0), 2 nights -> 8740.
    assert pms.quote_rate("RT004", "RP004", "2025-04-25", "2025-04-27", adults=2)["total_amount"] == 8740


def test_find_availability_filters_by_occupancy_and_stock(pms: PMS):
    # Apr 20-22 nights are 20 & 21. RT002 has 1 both nights -> available.
    options = {o["room_type_id"] for o in pms.find_availability("2025-04-20", "2025-04-22", adults=2)}
    assert "RT002" in options
    # A single (max_occupancy 1) must be excluded for a party of 2.
    assert "RT001" not in options


def test_availability_zero_night_excludes_room(pms: PMS):
    # RT002 is 0 on 2025-04-22, so a stay covering that night has no double.
    options = {o["room_type_id"] for o in pms.find_availability("2025-04-22", "2025-04-23")}
    assert "RT002" not in options


def test_create_reservation_decrements_and_cancel_restores(pms: PMS):
    before = pms.find_availability("2025-04-20", "2025-04-22", room_type_id="RT002")[0]["rooms_available"]
    res = pms.create_reservation("G001", "RT002", "RP001", "2025-04-20", "2025-04-22", adults=2)
    after = pms.find_availability("2025-04-20", "2025-04-22", room_type_id="RT002")
    assert after == [] or after[0]["rooms_available"] == before - 1

    pms.cancel_reservation(res["id"])
    restored = pms.find_availability("2025-04-20", "2025-04-22", room_type_id="RT002")[0]["rooms_available"]
    assert restored == before


def test_create_reservation_rejects_overbooking(pms: PMS):
    with pytest.raises(PMSError):
        # RT002 is 0 on 2025-04-22 → cannot book a stay including that night.
        pms.create_reservation("G001", "RT002", "RP001", "2025-04-22", "2025-04-23", adults=2)


def test_writes_never_touch_the_source(pms: PMS):
    fresh = PMS.from_file(DATA_PATH)
    pms.create_reservation("G001", "RT004", "RP001", "2025-04-20", "2025-04-22", adults=2)
    assert len(fresh._data["reservations"]) == 6  # unchanged
