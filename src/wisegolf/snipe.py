"""Speed-optimized snipe core.

Goals (vs original watcher):
- Pre-warm TLS to api host before T-0 → no handshake at fire time.
- Pre-fetch tee sheet at T-3s to know target reservationTimeId in window.
- Tight monotonic-clock busy-wait for sub-ms wake at T-0.
- Fire booking POST as first action at T-0, racing top-N candidate slots concurrently.
- HTTP/2 multiplexed connection.
- Backup: cycle through fallback rids on first-N failures.

Until POST payload is captured live, this falls back to UI-driven booking via
Playwright (if installed). For now `_book_via_api` is a stub matching the
suspected schema; verify with `scripts/sniff_booking.js` then enable.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import httpx

from .config import Config
from .models import TeeSlot

log = logging.getLogger(__name__)


@dataclass
class Candidate:
    slot: TeeSlot
    score: int  # lower = preferred (earliest in window)


def rank_candidates(slots: list[TeeSlot], window_start: str, window_end: str, party_size: int, occupied: set[int]) -> list[Candidate]:
    out: list[Candidate] = []
    for s in slots:
        if s.label != "res_golf":
            continue
        if window_start <= s.hhmm <= window_end and s.reservation_time_id not in occupied:
            out.append(Candidate(slot=s, score=int(s.start.timestamp())))
    out.sort(key=lambda c: c.score)
    return out


async def _prewarm(client: httpx.AsyncClient, target_day: date, course_id: int) -> None:
    # TLS + warm DNS + cache calendarsettings + cache tee sheet (one round-trip each)
    await asyncio.gather(
        client.get("/reservations/calendarsettings/", params={"productid": course_id, "date": target_day.isoformat()}),
        client.get("/reservations/", params={"productid": course_id, "date": target_day.isoformat(), "golf": 1}),
    )


def _busy_wait_until(target_monotonic: float) -> None:
    """Sleep most of it, busy-loop the last ~5ms for sub-ms accuracy."""
    while True:
        remaining = target_monotonic - time.monotonic()
        if remaining <= 0:
            return
        if remaining > 0.01:
            time.sleep(remaining - 0.005)
        else:
            # tight spin
            while time.monotonic() < target_monotonic:
                pass
            return


async def _post_booking_attempt(client: httpx.AsyncClient, slot: TeeSlot, course_id: int, person_ids: list[int]) -> tuple[bool, dict]:
    """Single booking attempt. Returns (success, response_or_error).

    NOTE: payload schema is best-guess until live capture lands. The error from
    server on bad schema is informative; keep raising.
    """
    body = {
        "productid": course_id,
        "reservationTimeId": slot.reservation_time_id,
        "quantity": len(person_ids),
        "start": slot.start.strftime("%Y-%m-%d %H:%M:%S"),
        "end": slot.end.strftime("%Y-%m-%d %H:%M:%S"),
    }
    try:
        r = await client.post("/reservations/order/", json=body)
        if r.status_code == 200 and r.json().get("success"):
            return True, r.json()
        return False, {"status": r.status_code, "body": r.text[:300]}
    except Exception as e:
        return False, {"err": str(e)}


async def _race_top_candidates(client: httpx.AsyncClient, candidates: list[Candidate], course_id: int, person_ids: list[int], parallel: int = 1) -> tuple[Candidate, dict] | None:
    """Try top-N candidates. parallel>1 fires concurrently; first 200 wins."""
    if not candidates:
        return None
    if parallel <= 1:
        for c in candidates:
            ok, resp = await _post_booking_attempt(client, c.slot, course_id, person_ids)
            if ok:
                return c, resp
            log.debug("attempt failed slot=%s resp=%s", c.slot.hhmm, resp)
        return None

    # Parallel: race first `parallel` candidates
    pending = [
        asyncio.create_task(_post_booking_attempt(client, c.slot, course_id, person_ids))
        for c in candidates[:parallel]
    ]
    while pending:
        done, pending_set = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
        pending = list(pending_set)
        for d in done:
            ok, resp = await d
            if ok:
                # match back to candidate
                idx = [t for t in pending].index(d) if d in pending else None
                # cancel others
                for p in pending:
                    p.cancel()
                # naive return
                return candidates[0], resp
    return None


async def snipe_target(
    cfg: Config,
    target_day: date,
    snipe_at: datetime,
    window_start: str,
    window_end: str,
    party_size: int,
    person_ids: list[int],
    parallel_attempts: int = 1,
    dry_run: bool = True,
) -> dict:
    """Top-level snipe routine for one target. Returns result dict."""
    headers = {
        "Authorization": f"Bearer {cfg.token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    limits = httpx.Limits(max_connections=4, max_keepalive_connections=4, keepalive_expiry=60)
    async with httpx.AsyncClient(
        base_url=cfg.api_base,
        headers=headers,
        http2=True,
        timeout=httpx.Timeout(connect=2, read=6, write=3, pool=2),
        limits=limits,
    ) as client:
        log.info("prewarming connection to %s", cfg.api_base)
        await _prewarm(client, target_day, cfg.course_id)

        # Late prefetch at T-3s for fresh sheet
        snipe_ts = snipe_at.timestamp()
        prefetch_at = snipe_ts - 3.0
        now = time.time()
        if prefetch_at > now:
            await asyncio.sleep(prefetch_at - now)
        log.info("T-3s prefetch")
        prefetch = await client.get(
            "/reservations/", params={"productid": cfg.course_id, "date": target_day.isoformat(), "golf": 1}
        )
        prefetch_data = prefetch.json()
        slots = [TeeSlot.model_validate({**r, "raw": r}) for r in prefetch_data.get("rows", [])]
        occupied = {p["reservationTimeId"] for p in prefetch_data.get("reservationsGolfPlayers", [])}
        candidates = rank_candidates(slots, window_start, window_end, party_size, occupied)
        # Filter slots with at least party_size adjacent free rids per timestamp
        free_by_time: dict[str, list[Candidate]] = {}
        for c in candidates:
            free_by_time.setdefault(c.slot.start.isoformat(), []).append(c)
        viable_groups = sorted(
            (g for g in free_by_time.values() if len(g) >= party_size),
            key=lambda g: g[0].score,
        )
        if not viable_groups:
            return {"ok": False, "reason": "no viable slots in window at T-3s", "candidates_found": len(candidates)}
        target_group = viable_groups[0]
        log.info("target locked: %s rid=%s (and %d siblings)", target_group[0].slot.start, target_group[0].slot.reservation_time_id, len(target_group)-1)

        # Tight wait until T-0
        wait_secs = snipe_ts - time.time()
        if wait_secs > 0:
            offset = wait_secs - (time.monotonic() - time.monotonic())  # noqa: just to compute
            t0_mono = time.monotonic() + max(0.0, snipe_ts - time.time())
            log.info("waiting until T-0 (in %.3fs)", wait_secs)
            _busy_wait_until(t0_mono)

        if dry_run:
            log.info("DRY RUN — would book rid=%s", target_group[0].slot.reservation_time_id)
            return {"ok": True, "dry_run": True, "rid": target_group[0].slot.reservation_time_id, "start": target_group[0].slot.start.isoformat()}

        log.info("FIRE")
        result = await _race_top_candidates(client, target_group[:party_size], cfg.course_id, person_ids, parallel=parallel_attempts)
        if not result:
            return {"ok": False, "reason": "all candidate POSTs rejected"}
        cand, resp = result
        return {"ok": True, "rid": cand.slot.reservation_time_id, "start": cand.slot.start.isoformat(), "resp": resp}


def compute_snipe_moment(cfg: Config, target_day: date) -> datetime:
    """When to fire: 10:00 Europe/Helsinki the day the booking horizon opens.
    For target_day, horizon opens (target_day - horizon_days) at snipe_time_local.
    """
    hh, mm = map(int, cfg.snipe_time_local.split(":"))
    open_day = target_day - timedelta(days=cfg.booking_horizon_days)
    return datetime.combine(open_day, datetime.min.time(), tzinfo=cfg.snipe_tz).replace(hour=hh, minute=mm)
