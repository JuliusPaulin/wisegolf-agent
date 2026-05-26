"""Snipe watcher. Sleeps until 10:00 Europe/Helsinki, then races to grab opening tee time."""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta

from .client import AsyncWiseGolfClient, WiseGolfClient
from .config import Config
from .models import BookingRequest, TeeSlot

log = logging.getLogger(__name__)


def next_snipe_moment(cfg: Config) -> datetime:
    tz = cfg.snipe_tz
    hh, mm = map(int, cfg.snipe_time_local.split(":"))
    now = datetime.now(tz)
    target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target


def pick_slot(slots: list[TeeSlot], window_start: str, window_end: str, min_quantity: int) -> TeeSlot | None:
    matches = [
        s for s in slots
        if s.is_sellable
        and not s.is_user_reservation
        and window_start <= s.hhmm <= window_end
        and s.quantity >= min_quantity
    ]
    return min(matches, key=lambda s: s.start) if matches else None


async def _race(cfg: Config, target_day: date, window_start: str, window_end: str, min_quantity: int, deadline: float) -> TeeSlot | None:
    async with AsyncWiseGolfClient(cfg) as c:
        loop = asyncio.get_running_loop()
        while loop.time() < deadline:
            try:
                slots = await c.list_slots(target_day)
            except Exception as e:
                log.warning("poll err: %s", e)
                await asyncio.sleep(0.2)
                continue
            pick = pick_slot(slots, window_start, window_end, min_quantity)
            if pick:
                return pick
            await asyncio.sleep(0.25)
    return None


def snipe(
    cfg: Config,
    target_day: date,
    window_start: str = "06:00",
    window_end: str = "12:00",
    min_quantity: int = 2,
    max_wait_s: int = 30,
    dry_run: bool = True,
) -> None:
    target = next_snipe_moment(cfg)
    log.info("snipe waking at %s", target.isoformat())
    while True:
        delta = (target - datetime.now(cfg.snipe_tz)).total_seconds()
        if delta <= 0:
            break
        # Sleep most of it, then tight loop the last 1s for sub-second accuracy
        if delta > 1.5:
            __import__("time").sleep(delta - 1.0)
        else:
            __import__("time").sleep(0.005)

    log.info("FIRE — racing for sellable slots in %s..%s", window_start, window_end)
    loop = asyncio.new_event_loop()
    try:
        deadline = loop.time() + max_wait_s
        slot = loop.run_until_complete(
            _race(cfg, target_day, window_start, window_end, min_quantity, deadline)
        )
    finally:
        loop.close()

    if not slot:
        log.error("no sellable slot found in window after %ds", max_wait_s)
        return
    log.info("locked %s (id=%s)", slot.start, slot.reservation_time_id)
    with WiseGolfClient(cfg) as c:
        res = c.create_booking(
            BookingRequest(slot=slot, person_ids=list(cfg.person_ids)), dry_run=dry_run
        )
    log.info("booking %s slot=%s persons=%s", "(dry-run)" if dry_run else "confirmed", res.reservation_time_id, res.person_ids)
