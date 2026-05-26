"""WiseGolf REST client. Read paths verified live; write paths tagged TODO until first booking."""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

import httpx

from .auth import make_async_client, make_client
from .config import Config
from .models import BookingRequest, BookingResult, GolfReservation, Player, TeeSlot

log = logging.getLogger(__name__)


class WiseGolfError(RuntimeError):
    def __init__(self, msg: str, status: int = 0, body: Any = None):
        super().__init__(msg)
        self.status = status
        self.body = body


def _check(resp: httpx.Response) -> dict[str, Any]:
    if resp.status_code >= 400:
        raise WiseGolfError(f"http {resp.status_code}", resp.status_code, resp.text[:500])
    j = resp.json()
    if j.get("success") is False:
        raise WiseGolfError(str(j.get("errors")), resp.status_code, j)
    return j


class WiseGolfClient:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._http = make_client(cfg)

    def __enter__(self) -> "WiseGolfClient":
        return self

    def __exit__(self, *exc) -> None:
        self._http.close()

    # ---- Read ----
    def list_slots(self, day: date, course_id: int | None = None) -> list[TeeSlot]:
        cid = course_id or self.cfg.course_id
        r = self._http.get("/reservations/", params={"productid": cid, "date": day.isoformat(), "golf": 1})
        j = _check(r)
        return [TeeSlot.model_validate({**row, "raw": row}) for row in j.get("rows", [])]

    def players(self) -> list[Player]:
        j = _check(self._http.get("/golf/player/"))
        return [Player.model_validate(p) for p in j.get("rows", [])]

    def my_bookings(self) -> list[GolfReservation]:
        j = _check(self._http.get("/reservations/getusergolfreservations/"))
        return [GolfReservation.model_validate({**r, "raw": r}) for r in j.get("rows", [])]

    def cart(self) -> dict[str, Any]:
        return _check(self._http.get("/carts/my/products"))

    def list_wisegolf_clubs(self) -> list[dict[str, Any]]:
        """All Finnish golf clubs using WiseNetwork (WiseGolf) software."""
        j = _check(self._http.get("/golf/club/"))
        return [r for r in j.get("rows", []) if r.get("softwareVendorName") == "WiseNetwork"]

    def list_courses(self) -> list[dict[str, Any]]:
        j = _check(self._http.get("/products/", params={"type": 6}))
        return j.get("rows", [])

    def calendar_settings(self, day: date, course_id: int | None = None) -> dict[str, Any]:
        cid = course_id or self.cfg.course_id
        return _check(self._http.get("/reservations/calendarsettings/", params={"productid": cid, "date": day.isoformat()}))

    # ---- Write (TODO: confirm payloads on first live booking) ----
    def lock_slot(self, slot: TeeSlot) -> dict[str, Any]:
        """POST /reservations/order/ — disables slot for others while user fills cart.
        Payload schema unconfirmed; sending best-guess. On first real run, capture
        the actual payload from the SPA via scripts/sniff_booking.js and update.
        """
        body = {
            "productid": self.cfg.course_id,
            "reservationTimeId": slot.reservation_time_id,
            "quantity": len(self.cfg.person_ids),
            "start": slot.start.strftime("%Y-%m-%d %H:%M:%S"),
            "end": slot.end.strftime("%Y-%m-%d %H:%M:%S"),
        }
        return _check(self._http.post("/reservations/order/", json=body))

    def add_to_cart(self, slot: TeeSlot) -> dict[str, Any]:
        body = {
            "productid": self.cfg.course_id,
            "reservationTimeId": slot.reservation_time_id,
            "quantity": len(self.cfg.person_ids),
        }
        return _check(self._http.post("/carts/my/products", json=body))

    def assign_players(self, slot: TeeSlot, person_ids: list[int]) -> dict[str, Any]:
        # Best guess based on store action res_golf/saveTeetimePlayers
        body = {
            "reservationTimeId": slot.reservation_time_id,
            "players": [{"personId": pid} for pid in person_ids],
        }
        return _check(self._http.post("/reservations/golfplayerdetails/", json=body))

    def confirm_tee_time(self, slot: TeeSlot) -> dict[str, Any]:
        body = {
            "productid": self.cfg.course_id,
            "reservationTimeId": slot.reservation_time_id,
        }
        return _check(self._http.post("/reservations/confirmgolfteetime/", json=body))

    def cancel_pending_cart(self) -> None:
        # Roll back if multi-step booking fails midway
        try:
            self._http.delete("/carts/order")
        except Exception as e:
            log.warning("cart rollback failed: %s", e)

    def create_booking(self, req: BookingRequest, dry_run: bool = True) -> BookingResult:
        if dry_run:
            log.info("DRY RUN booking slot=%s players=%s", req.slot.start, req.person_ids)
            return BookingResult(
                reservation_time_id=req.slot.reservation_time_id,
                person_ids=req.person_ids,
                confirmed_at=datetime.now(),
                raw={"dry_run": True},
            )
        try:
            self.lock_slot(req.slot)
            self.add_to_cart(req.slot)
            self.assign_players(req.slot, req.person_ids)
            confirm = self.confirm_tee_time(req.slot)
            return BookingResult(
                reservation_time_id=req.slot.reservation_time_id,
                person_ids=req.person_ids,
                confirmed_at=datetime.now(),
                raw=confirm,
            )
        except Exception:
            self.cancel_pending_cart()
            raise


class AsyncWiseGolfClient:
    """Async variant for tight snipe poll loop."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._http = make_async_client(cfg)

    async def __aenter__(self) -> "AsyncWiseGolfClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self._http.aclose()

    async def list_slots(self, day: date, course_id: int | None = None) -> list[TeeSlot]:
        cid = course_id or self.cfg.course_id
        r = await self._http.get("/reservations/", params={"productid": cid, "date": day.isoformat(), "golf": 1})
        j = _check(r)
        return [TeeSlot.model_validate({**row, "raw": row}) for row in j.get("rows", [])]
