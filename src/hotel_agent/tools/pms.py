"""Mock PMS: the atomic read/write operations the agent's tools and workflows call.

This module is the single source of truth for hotel data logic. It has **no LLM and no
LangChain dependency** — it is pure Python over the loaded JSON, so it is trivially unit
testable and the availability/rate math can be verified against the mock data.

Design:
- `PMS` wraps an **in-memory copy** of the data. Writes mutate the copy, never the file
  on disk (see `CLAUDE.md` §8). Load with `PMS.from_file(path)`.
- Read methods are side-effect free. Write methods validate first and raise `PMSError`
  with a guest-safe message on any invalid step.
- The LangChain tool wrappers used by the planner's ReAct loop live in `read_tools()`.
  Write operations are intentionally *not* exposed as free LLM tools — they are only
  reached through the workflows in `workflows.py`, behind the approval gate.
"""

from __future__ import annotations

import copy
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any


class PMSError(ValueError):
    """Raised when a PMS operation is invalid (bad dates, no availability, etc.)."""


def _parse_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (ValueError, TypeError) as exc:
        raise PMSError(f"Invalid date '{value}'. Expected ISO format YYYY-MM-DD.") from exc


def _nights(check_in: str, check_out: str) -> list[str]:
    """Return the list of occupied night dates: [check_in, check_out) — check_out exclusive."""
    ci, co = _parse_date(check_in), _parse_date(check_out)
    if co <= ci:
        raise PMSError("check_out must be after check_in.")
    return [(date.fromordinal(d)).isoformat() for d in range(ci.toordinal(), co.toordinal())]


class PMS:
    """In-memory view over the mock hotel PMS with atomic read + write operations."""

    def __init__(self, data: dict[str, Any]):
        # Deep copy so writes never touch the caller's dict (or the source file).
        self._data = copy.deepcopy(data)
        self._room_types = {rt["id"]: rt for rt in self._data.get("room_types", [])}
        self._rate_plans = {rp["id"]: rp for rp in self._data.get("rate_plans", [])}
        self._source_path: Path | None = None  # set by from_file; enables save()

    @classmethod
    def from_file(cls, path: str | Path) -> "PMS":
        pms = cls(json.loads(Path(path).read_text()))
        pms._source_path = Path(path)
        return pms

    def save(self, path: str | Path | None = None) -> None:
        """Persist the current state back to JSON (defaults to the file it was loaded from).

        Off by default — the service only calls this after an approved, non-dry-run write, so
        the mock PMS file reflects reservations the agent actually made."""
        target = path or self._source_path
        if target is None:
            raise PMSError("No path to save the PMS to (loaded from a dict, not a file).")
        Path(target).write_text(json.dumps(self._data, indent=2, ensure_ascii=False))

    # ---- reference data -------------------------------------------------------------

    @property
    def hotel(self) -> dict[str, Any]:
        return self._data["hotel"]

    @property
    def currency(self) -> str:
        return self._data["hotel"].get("currency", "NOK")

    def room_type(self, room_type_id: str) -> dict[str, Any]:
        rt = self._room_types.get(room_type_id)
        if not rt:
            raise PMSError(f"Unknown room type '{room_type_id}'.")
        return rt

    def rate_plan(self, rate_plan_id: str) -> dict[str, Any]:
        rp = self._rate_plans.get(rate_plan_id)
        if not rp:
            raise PMSError(f"Unknown rate plan '{rate_plan_id}'.")
        return rp

    def inventory(self) -> dict[str, Any]:
        """Compact catalogue of room types + rate plans (for the planner prompt/tools)."""
        return {
            "room_types": [
                {
                    "id": rt["id"],
                    "name": rt["name"],
                    "max_occupancy": rt["max_occupancy"],
                    "base_rate_per_night": rt["base_rate_per_night"],
                    "extra_bed_available": rt.get("extra_bed_available", False),
                }
                for rt in self._data["room_types"]
            ],
            "rate_plans": [
                {
                    "id": rp["id"],
                    "name": rp["name"],
                    "cancellation_policy": rp["cancellation_policy"],
                    "includes_breakfast": rp["includes_breakfast"],
                    "rate_modifier": rp["rate_modifier"],
                }
                for rp in self._data["rate_plans"]
            ],
            "currency": self.currency,
        }

    def get_policy(self, topic: str | None = None) -> dict[str, Any] | str:
        policies = self._data["policies"]
        if topic is None:
            return policies
        key = topic.strip().lower().replace(" ", "_").replace("-", "_")
        aliases = {"cancellation_policy": "cancellation", "cancel": "cancellation", "pet": "pets"}
        key = aliases.get(key, key)
        if key not in policies:
            raise PMSError(f"Unknown policy topic '{topic}'. Known: {', '.join(policies)}.")
        return policies[key]

    # ---- guests ---------------------------------------------------------------------

    def find_guest(
        self,
        email: str | None = None,
        guest_id: str | None = None,
        name: str | None = None,
    ) -> dict[str, Any] | None:
        for g in self._data["guests"]:
            if guest_id and g["id"] == guest_id:
                return g
            if email and g["email"].lower() == email.lower():
                return g
            if name and f"{g['first_name']} {g['last_name']}".lower() == name.lower():
                return g
        return None

    # ---- reservations ---------------------------------------------------------------

    def get_reservation(self, reservation_id: str) -> dict[str, Any]:
        for r in self._data["reservations"]:
            if r["id"] == reservation_id:
                return r
        raise PMSError(f"Unknown reservation '{reservation_id}'.")

    def reservations_for_guest(
        self, guest_id: str | None = None, email: str | None = None, include_cancelled: bool = False
    ) -> list[dict[str, Any]]:
        if email and not guest_id:
            guest = self.find_guest(email=email)
            guest_id = guest["id"] if guest else None
        if not guest_id:
            return []
        out = [r for r in self._data["reservations"] if r["guest_id"] == guest_id]
        if not include_cancelled:
            out = [r for r in out if r["status"] != "cancelled"]
        return out

    # ---- availability + pricing -----------------------------------------------------

    def _available_count(self, room_type_id: str, nights: list[str]) -> int:
        """Minimum free rooms across the requested nights (0 if any night is unlisted)."""
        av = self._data["availability"]
        return min((av.get(n, {}).get(room_type_id, 0) for n in nights), default=0)

    def find_availability(
        self,
        check_in: str,
        check_out: str,
        adults: int = 1,
        children: int = 0,
        room_type_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Room types with at least one free room every night, optionally fitting the party."""
        nights = _nights(check_in, check_out)
        party = adults + children
        candidates = [self.room_type(room_type_id)] if room_type_id else self._data["room_types"]
        options = []
        for rt in candidates:
            fits = rt["max_occupancy"] >= party if party else True
            free = self._available_count(rt["id"], nights)
            if free > 0 and fits:
                options.append(
                    {
                        "room_type_id": rt["id"],
                        "name": rt["name"],
                        "max_occupancy": rt["max_occupancy"],
                        "rooms_available": free,
                        "nights": len(nights),
                        "base_rate_per_night": rt["base_rate_per_night"],
                    }
                )
        return options

    def quote_rate(
        self,
        room_type_id: str,
        rate_plan_id: str,
        check_in: str,
        check_out: str,
        adults: int = 1,
        children: int = 0,
    ) -> dict[str, Any]:
        """Full price breakdown. Mirrors the math baked into the mock reservations."""
        rt, rp = self.room_type(room_type_id), self.rate_plan(rate_plan_id)
        nights = len(_nights(check_in, check_out))
        room_subtotal = round(rt["base_rate_per_night"] * rp["rate_modifier"] * nights)
        persons = adults + children
        supp = rp.get("breakfast_supplement_per_person", 0) if rp["includes_breakfast"] else 0
        breakfast_subtotal = supp * persons * nights
        return {
            "room_type_id": rt["id"],
            "room_type_name": rt["name"],
            "rate_plan_id": rp["id"],
            "rate_plan_name": rp["name"],
            "check_in": check_in,
            "check_out": check_out,
            "nights": nights,
            "persons": persons,
            "base_rate_per_night": rt["base_rate_per_night"],
            "rate_modifier": rp["rate_modifier"],
            "room_subtotal": room_subtotal,
            "breakfast_per_person_per_night": supp,
            "breakfast_subtotal": breakfast_subtotal,
            "total_amount": room_subtotal + breakfast_subtotal,
            "currency": self.currency,
            "includes_breakfast": rp["includes_breakfast"],
            "cancellation_policy": rp["cancellation_policy"],
        }

    # ---- write operations (atomic) --------------------------------------------------
    # Reached only through workflows.py, behind the approval gate.

    def _next_id(self, collection: str, prefix: str) -> str:
        nums = [int(x["id"][len(prefix):]) for x in self._data[collection] if x["id"].startswith(prefix)]
        return f"{prefix}{(max(nums) + 1 if nums else 1):03d}"

    def create_guest(
        self,
        first_name: str,
        last_name: str,
        email: str,
        phone: str | None = None,
        nationality: str | None = None,
    ) -> dict[str, Any]:
        if self.find_guest(email=email):
            raise PMSError(f"A guest with email {email} already exists.")
        guest = {
            "id": self._next_id("guests", "G"),
            "first_name": first_name,
            "last_name": last_name,
            "email": email,
            "phone": phone or "",
            "nationality": nationality or "",
            "created_at": date.today().isoformat(),
        }
        self._data["guests"].append(guest)
        return guest

    def _apply_availability(self, room_type_id: str, nights: list[str], delta: int) -> None:
        av = self._data["availability"]
        for n in nights:
            av.setdefault(n, {})
            av[n][room_type_id] = av[n].get(room_type_id, 0) + delta

    def create_reservation(
        self,
        guest_id: str,
        room_type_id: str,
        rate_plan_id: str,
        check_in: str,
        check_out: str,
        adults: int = 1,
        children: int = 0,
        notes: str = "",
    ) -> dict[str, Any]:
        if not self.find_guest(guest_id=guest_id):
            raise PMSError(f"Unknown guest '{guest_id}'.")
        rt = self.room_type(room_type_id)
        if adults + children > rt["max_occupancy"]:
            raise PMSError(
                f"{rt['name']} holds max {rt['max_occupancy']} guests; "
                f"requested {adults + children}."
            )
        nights = _nights(check_in, check_out)
        if self._available_count(room_type_id, nights) < 1:
            raise PMSError(f"No {rt['name']} available for {check_in} to {check_out}.")
        quote = self.quote_rate(room_type_id, rate_plan_id, check_in, check_out, adults, children)
        reservation = {
            "id": self._next_id("reservations", "RES"),
            "guest_id": guest_id,
            "room_type_id": room_type_id,
            "rate_plan_id": rate_plan_id,
            "check_in": check_in,
            "check_out": check_out,
            "adults": adults,
            "children": children,
            "status": "confirmed",
            "total_amount": quote["total_amount"],
            "notes": notes,
            "created_at": date.today().isoformat(),
        }
        self._data["reservations"].append(reservation)
        self._apply_availability(room_type_id, nights, -1)
        return reservation

    def modify_reservation(self, reservation_id: str, **changes: Any) -> dict[str, Any]:
        res = self.get_reservation(reservation_id)
        if res["status"] == "cancelled":
            raise PMSError(f"Reservation {reservation_id} is cancelled and cannot be modified.")
        allowed = {"room_type_id", "rate_plan_id", "check_in", "check_out", "adults", "children", "notes"}
        unknown = set(changes) - allowed
        if unknown:
            raise PMSError(f"Cannot modify fields: {', '.join(sorted(unknown))}.")

        updated = {**res, **{k: v for k, v in changes.items() if v is not None}}
        old_nights = _nights(res["check_in"], res["check_out"])
        new_nights = _nights(updated["check_in"], updated["check_out"])
        rt = self.room_type(updated["room_type_id"])
        if updated["adults"] + updated["children"] > rt["max_occupancy"]:
            raise PMSError(f"{rt['name']} holds max {rt['max_occupancy']} guests.")

        # Release the old hold, then check the new one can be satisfied.
        self._apply_availability(res["room_type_id"], old_nights, +1)
        if self._available_count(updated["room_type_id"], new_nights) < 1:
            self._apply_availability(res["room_type_id"], old_nights, -1)  # roll back
            raise PMSError(f"No {rt['name']} available for the requested change.")
        self._apply_availability(updated["room_type_id"], new_nights, -1)

        quote = self.quote_rate(
            updated["room_type_id"], updated["rate_plan_id"],
            updated["check_in"], updated["check_out"], updated["adults"], updated["children"],
        )
        updated["total_amount"] = quote["total_amount"]
        res.update(updated)
        return res

    def cancel_reservation(self, reservation_id: str, reason: str = "") -> dict[str, Any]:
        res = self.get_reservation(reservation_id)
        if res["status"] == "cancelled":
            raise PMSError(f"Reservation {reservation_id} is already cancelled.")
        res["status"] = "cancelled"
        if reason:
            res["notes"] = (res.get("notes", "") + f" | Cancelled: {reason}").strip(" |")
        self._apply_availability(res["room_type_id"], _nights(res["check_in"], res["check_out"]), +1)
        return res

    def snapshot(self) -> dict[str, Any]:
        """Deep copy of the current state — used for dry-run diffing / debugging."""
        return copy.deepcopy(self._data)
