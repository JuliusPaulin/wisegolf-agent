"""Browser-driven snipe via Playwright. Reliable booking through the SPA itself.

Strategy:
1. Launch headless Chromium with saved storage_state (token + cookies).
2. Navigate to /reservation/{course} and pick target date in the calendar.
3. Pre-warm: wait for tee sheet rendered + group-toggle (JP+AS) on.
4. T-1s: tight poll the DOM looking for the first 'Vacant' button at-or-after window_start.
5. T-0+: click Vacant → click 'Make reservation' as soon as it appears.

Total time after T-0 ≈ click + dialog render + click = 200–700ms.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import date, datetime
from pathlib import Path

log = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATE_PATH_DEFAULT = _PROJECT_ROOT / "browser_state.json"


async def _pick_date(page, target: date) -> None:
    day_str = f"{target.day:02d}"
    # Date pills are ion-segment-button elements with a span.day-number child.
    # Clicking the span fails (ion-segment-button intercepts pointer events),
    # so dispatch a click event on the button itself via JS.
    clicked = await page.evaluate("""
        (dayStr) => {
            const buttons = [...document.querySelectorAll('ion-segment-button')];
            const btn = buttons.find(b => {
                const span = b.querySelector('.day-number');
                return span && span.textContent.trim() === dayStr;
            });
            if (!btn) return false;
            btn.click();
            return true;
        }
    """, day_str)
    if clicked:
        log.info("picked date day=%s via JS click", day_str)
    else:
        log.warning("_pick_date: day %s not found in ion-segment-button calendar", day_str)


_VACANT_LABELS = {"Vacant", "Vapaa", "Free", "Available"}
_RESERVATION_LABELS = {"Make reservation", "Tee varaus", "Book", "Varaa", "Varaus"}


async def _select_first_vacant_after(page, window_start: str, window_end: str, party_size: int) -> dict | None:
    """Find first time row in [window_start..window_end] with >= party_size open slots, click first one.
    Handles both English (Vacant/Make reservation) and Finnish (Vapaa/Tee varaus).
    Returns chosen time dict, or None if not found.
    """
    js = """
    ({windowStart, windowEnd, partySize, vacantLabels}) => {
      const vacantSet = new Set(vacantLabels);
      // Each tee row lives in a div.timeblock-inner; the time is in span.title inside div.title-area
      const timeRows = [...document.querySelectorAll('.timeblock-inner')];
      const groups = timeRows.map(row => {
        const timeEl = row.querySelector('.title-area .title');
        if (!timeEl) return null;
        const time = timeEl.textContent.trim();
        if (!/^([0-1][0-9]|2[0-3]):[0-5][0-9]$/.test(time)) return null;
        if (time < windowStart || time > windowEnd) return null;
        const vacants = [...row.querySelectorAll('*')].filter(n =>
          n.children.length === 0 && vacantSet.has(n.textContent.trim()));
        return { time, count: vacants.length, target: vacants[0] || null };
      }).filter(Boolean).sort((a,b) => a.time.localeCompare(b.time));
      const fit = groups.find(g => g.count >= partySize);
      if (!fit) return null;
      fit.target.scrollIntoView({block: 'center'});
      fit.target.click();
      return { time: fit.time, vacants: fit.count };
    }
    """
    result = await page.evaluate(js, {"windowStart": window_start, "windowEnd": window_end, "partySize": party_size, "vacantLabels": list(_VACANT_LABELS)})
    if result:
        log.info("found slot @ %s with %d open", result["time"], result["vacants"])
    return result


async def _click_make_reservation(page, timeout_s: float = 8.0) -> bool:
    labels = list(_RESERVATION_LABELS)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            for label in labels:
                btn = page.get_by_text(label, exact=True).first
                if await btn.count() > 0:
                    log.info("clicking reservation button: %s", label)
                    await btn.click()
                    return True
        except Exception:
            pass
        await asyncio.sleep(0.05)
    log.warning("reservation button not found after %.1fs (tried: %s)", timeout_s, labels)
    return False


async def _await_success(page, timeout_s: float = 8.0) -> bool:
    success_texts = ["Reservation succeeded", "Varaus onnistui", "Booking confirmed", "Varaus tehty"]
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            for t in success_texts:
                if await page.get_by_text(t, exact=False).count() > 0:
                    log.info("success text found: %s", t)
                    return True
        except Exception:
            pass
        await asyncio.sleep(0.1)
    return False


def _busy_wait_until(target_monotonic: float) -> None:
    while True:
        rem = target_monotonic - time.monotonic()
        if rem <= 0:
            return
        if rem > 0.02:
            time.sleep(rem - 0.01)
        else:
            while time.monotonic() < target_monotonic:
                pass
            return


async def _load_tee_sheet(page, url: str, target_day: date) -> None:
    """Navigate to tee sheet URL and pick the target date."""
    await page.goto(url, wait_until="networkidle")
    try:
        await page.evaluate("""
          () => {
            const toggles = [...document.querySelectorAll('input[type=checkbox],[role=switch]')];
            for (const t of toggles) {
              if (t.checked === false || t.getAttribute('aria-checked') === 'false') t.click();
            }
          }
        """)
    except Exception:
        pass
    await _pick_date(page, target_day)
    await asyncio.sleep(1.5)
    await page.wait_for_selector('text=/[0-2][0-9]:[0-5][0-9]/', timeout=20000)


async def snipe_via_browser(
    cfg,
    target_day: date,
    snipe_at: datetime,
    window_start: str,
    window_end: str,
    party_size: int,
    person_ids: list[int],
    dry_run: bool = True,
    state_path: Path = STATE_PATH_DEFAULT,
    headless: bool = True,
    poll_interval_s: int = 0,
) -> dict:
    """Book a tee time via the SPA.

    poll_interval_s=0: try once at snipe_at.
    poll_interval_s>0: after snipe_at, retry every N seconds until booked (keeping browser open).
    """
    from playwright.async_api import async_playwright

    if not state_path.exists():
        return {"ok": False, "reason": f"missing {state_path} — run `wisegolf browser-login` first"}

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless)
        ctx = await browser.new_context(storage_state=str(state_path))
        page = await ctx.new_page()
        url = f"https://app.wisegolf.fi/#/golf/reservation/{cfg.course_id}"

        log.info("loading tee sheet for %s", target_day)
        await _load_tee_sheet(page, url, target_day)
        log.info("tee sheet ready")

        # Sleep until T-2s
        snipe_ts = snipe_at.timestamp()
        wait = snipe_ts - time.time() - 2.0
        if wait > 0:
            log.info("idle %.2fs until T-2s", wait)
            await asyncio.sleep(wait)

        log.info("tight loop until T-0")
        t0_mono = time.monotonic() + max(0.0, snipe_ts - time.time())
        _busy_wait_until(t0_mono)

        attempt = 0
        chosen = None
        while True:
            attempt += 1
            log.info("attempt %d — searching [%s..%s] party=%d", attempt, window_start, window_end, party_size)

            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline:
                chosen = await _select_first_vacant_after(page, window_start, window_end, party_size)
                if chosen:
                    break
                await asyncio.sleep(0.2)

            if chosen:
                break

            if poll_interval_s <= 0:
                # Dump visible text for debugging
                try:
                    visible = await page.evaluate("() => document.body.innerText.slice(0, 500)")
                    log.warning("page text sample: %s", visible.replace("\n", " "))
                except Exception:
                    pass
                await browser.close()
                return {"ok": False, "reason": f"no open slot found in [{window_start}..{window_end}]"}

            print(f"  attempt {attempt}: no slot yet — reload in {poll_interval_s}s…")
            await asyncio.sleep(poll_interval_s)
            await _load_tee_sheet(page, url, target_day)

        if dry_run:
            log.info("DRY RUN — would book %s", chosen)
            await browser.close()
            return {"ok": True, "dry_run": True, "time": chosen["time"]}

        if not await _click_make_reservation(page):
            await browser.close()
            return {"ok": False, "reason": "Make reservation button never appeared", "time": chosen["time"]}

        ok = await _await_success(page)
        await browser.close()
        return {"ok": ok, "time": chosen["time"], "confirmed": ok}
