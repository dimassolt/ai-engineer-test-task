"""Read-only LangChain tools bound to a PMS instance.

These are the *only* tools exposed to the LLM during planning (`plan` node). They gather
facts — availability, rates, guest and reservation records, policies — but never mutate
state. Writes happen later, deterministically, through the workflows in `workflows.py`,
and only after the approval gate. Keeping this tier read-only is what makes the planning
phase safe to run autonomously.
"""

from __future__ import annotations

from langchain_core.tools import StructuredTool

from .pms import PMS, PMSError


def build_read_tools(pms: PMS) -> list[StructuredTool]:
    """Return read-only tools closed over `pms`. Each returns JSON-serializable data."""

    def find_availability(
        check_in: str, check_out: str, adults: int = 1, children: int = 0,
        room_type_id: str | None = None,
    ) -> list[dict]:
        """Find room types with availability for a date range (dates as YYYY-MM-DD,
        check_out exclusive). Optionally filter to a room_type_id or party size."""
        return pms.find_availability(check_in, check_out, adults, children, room_type_id)

    def quote_rate(
        room_type_id: str, rate_plan_id: str, check_in: str, check_out: str,
        adults: int = 1, children: int = 0,
    ) -> dict:
        """Price a stay: returns a full breakdown (room subtotal, breakfast, total,
        cancellation policy) for a room type + rate plan over the date range."""
        return pms.quote_rate(room_type_id, rate_plan_id, check_in, check_out, adults, children)

    def get_guest(email: str | None = None, name: str | None = None) -> dict | None:
        """Look up an existing guest by email or full name. Returns the guest record or
        null if they are not yet in the PMS (a new profile would be needed to book)."""
        return pms.find_guest(email=email, name=name)

    def get_reservation(reservation_id: str | None = None, guest_email: str | None = None) -> dict | list | None:
        """Fetch a reservation by id, or all active reservations for a guest email."""
        if reservation_id:
            return pms.get_reservation(reservation_id)
        if guest_email:
            return pms.reservations_for_guest(email=guest_email)
        return None

    def get_policy(topic: str) -> dict | str:
        """Get a hotel policy by topic: cancellation, pets, breakfast, parking,
        extra_bed, or children."""
        return pms.get_policy(topic)

    def list_inventory() -> dict:
        """List all room types and rate plans (ids, names, prices, breakfast/cancellation
        flags) so requests like 'double with breakfast' can be mapped to ids."""
        return pms.inventory()

    raw = [find_availability, quote_rate, get_guest, get_reservation, get_policy, list_inventory]
    # Surface PMSError messages to the model instead of crashing the ReAct loop.
    return [
        StructuredTool.from_function(fn, handle_tool_error=lambda e: f"Error: {e}")
        for fn in raw
    ]
