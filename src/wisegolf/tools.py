"""Claude Agent SDK tool definitions wrapping WiseGolfClient."""
from __future__ import annotations

from datetime import date as _date
from typing import Any

from .client import WiseGolfClient
from .config import load as load_config
from .models import BookingRequest, TeeSlot


def search_tee_times(day: str, only_sellable: bool = True) -> list[dict[str, Any]]:
    cfg = load_config()
    with WiseGolfClient(cfg) as c:
        slots = c.list_slots(_date.fromisoformat(day))
    if only_sellable:
        slots = [s for s in slots if s.is_sellable]
    return [s.model_dump(mode="json", exclude={"raw"}) for s in slots]


def book_tee_time(day: str, reservation_time_id: int, person_ids: list[int] | None = None, dry_run: bool = True) -> dict[str, Any]:
    cfg = load_config()
    with WiseGolfClient(cfg) as c:
        slot = next((s for s in c.list_slots(_date.fromisoformat(day)) if s.reservation_time_id == reservation_time_id), None)
        if not slot:
            return {"error": f"slot {reservation_time_id} not found on {day}"}
        res = c.create_booking(BookingRequest(slot=slot, person_ids=person_ids or list(cfg.person_ids)), dry_run=dry_run)
    return res.model_dump(mode="json", exclude={"raw"})


def my_bookings() -> list[dict[str, Any]]:
    cfg = load_config()
    with WiseGolfClient(cfg) as c:
        return [b.model_dump(mode="json", exclude={"raw"}) for b in c.my_bookings()]


def list_players() -> list[dict[str, Any]]:
    cfg = load_config()
    with WiseGolfClient(cfg) as c:
        return [p.model_dump(mode="json") for p in c.players()]
