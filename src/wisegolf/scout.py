"""Token-free tee time watcher. Polls REST, notifies via ntfy with Book/Skip buttons."""
from __future__ import annotations

import json
import logging
import os
import random
import time
from datetime import date, datetime, timedelta

import httpx

from .client import WiseGolfClient, WiseGolfError
from .config import Config
from .models import TeeSlot

log = logging.getLogger(__name__)

_TEE_CAPACITY = 4  # standard foursome — API rows are BOOKED slots, open = capacity - row_count


def _bookable(slots: list[TeeSlot], window_start: str, window_end: str, party: int) -> list[tuple[TeeSlot, int]]:
    """Returns list of (representative_slot, open_count) sorted by time."""
    in_window = [s for s in slots if window_start <= s.hhmm <= window_end]
    by_time: dict[str, list[TeeSlot]] = {}
    for s in in_window:
        by_time.setdefault(s.hhmm, []).append(s)
    result = []
    for hhmm in sorted(by_time):
        group = by_time[hhmm]
        if any(s.label == "Pakkasvaraus" for s in group):
            continue
        open_count = _TEE_CAPACITY - len(group)
        if open_count >= party:
            result.append((group[0], open_count))
    return result


def _fmt_date(d: date) -> str:
    return d.strftime("%d.%m.%Y")


def _push_pushover(msg: str, title: str) -> None:
    token = os.getenv("WISEGOLF_PUSHOVER_TOKEN", "")
    user = os.getenv("WISEGOLF_PUSHOVER_USER", "")
    if not token or not user:
        return
    try:
        httpx.post(
            "https://api.pushover.net/1/messages.json",
            data={"token": token, "user": user, "title": title, "message": msg, "priority": 1},
            timeout=5,
        )
    except Exception as e:
        log.warning("pushover push failed: %s", e)


def _ntfy_send_with_actions(topic: str, msg: str, title: str) -> int:
    """Send ntfy notification with Book/Skip/Stop action buttons. Returns timestamp before send."""
    response_topic = f"{topic}-response"
    since = int(time.time())
    httpx.post(
        f"https://ntfy.sh/{topic}",
        content=msg.encode(),
        headers={
            "Title": title,
            "Priority": "high",
            "Tags": "golf",
            "Actions": (
                f"http, Book, https://ntfy.sh/{response_topic}, method=POST, body=book; "
                f"http, Skip, https://ntfy.sh/{response_topic}, method=POST, body=skip; "
                f"http, Stop watching, https://ntfy.sh/{response_topic}, method=POST, body=stop"
            ),
        },
        timeout=5,
    )
    return since


def _ntfy_poll_response(topic: str, since: int, timeout_s: int) -> str | None:
    """Poll {topic}-response. Returns 'book', 'skip', 'stop', or None on timeout."""
    response_topic = f"{topic}-response"
    deadline = time.time() + timeout_s
    print(f"Tap Book, Skip, or Stop on your phone ({timeout_s}s timeout)...")
    while time.time() < deadline:
        try:
            r = httpx.get(
                f"https://ntfy.sh/{response_topic}/json",
                params={"poll": "1", "since": str(since)},
                timeout=10,
            )
            for line in r.text.strip().splitlines():
                try:
                    obj = json.loads(line)
                    if obj.get("event") == "message":
                        body = obj.get("message", "").strip().lower()
                        if body in ("book", "skip", "stop"):
                            print(f"→ {body.capitalize()}")
                            return body
                except json.JSONDecodeError:
                    pass
        except Exception as e:
            log.warning("ntfy response poll: %s", e)
        time.sleep(3)
    print("No response — skipping.")
    return None


def ask_to_book(slot: TeeSlot, open_count: int, target: date, timeout_s: int = 120) -> bool | None:
    """Notify phone + terminal, wait for decision.
    Returns True to book, False to skip+continue watching, None to stop watching this date.
    """
    msg = f"{_fmt_date(target)} @ {slot.hhmm} — {open_count} spot{'s' if open_count != 1 else ''} open"
    title = "WiseGolf Scout"
    print(f"\n\033[1;32m⛳  SLOT FOUND: {msg}\033[0m")

    topic = os.getenv("WISEGOLF_NTFY_TOPIC", "")
    if topic:
        try:
            since = _ntfy_send_with_actions(topic, msg, title)
            result = _ntfy_poll_response(topic, since, timeout_s)
            if result == "book":
                return True
            if result == "skip":
                return False
            return None  # "stop" or timeout
        except Exception as e:
            log.warning("ntfy failed, falling back to terminal: %s", e)

    # Fallback: pushover (notify only) + terminal prompt
    _push_pushover(msg, title)
    try:
        ans = input(f"Book {_fmt_date(target)} @ {slot.hhmm} ({open_count} open)? [Y/n/q=stop] ").strip().lower()
        if ans in ("", "y", "yes"):
            return True
        if ans in ("q", "quit", "stop"):
            return None
        return False
    except (EOFError, KeyboardInterrupt):
        return None


def _book_now(cfg: Config, slot: TeeSlot, target: date, person_ids: list[int]) -> bool:
    from .browser_snipe import snipe_via_browser
    import asyncio
    snipe_at = datetime.now(cfg.snipe_tz) + timedelta(seconds=4)
    result = asyncio.run(snipe_via_browser(
        cfg=cfg,
        target_day=target,
        snipe_at=snipe_at,
        window_start=slot.hhmm,
        window_end=slot.hhmm,
        party_size=len(person_ids),
        person_ids=person_ids,
        dry_run=False,
        headless=True,
    ))
    ok = result.get("ok", False)
    if ok:
        print(f"\033[1;32m✓ Booked {_fmt_date(target)} @ {result.get('time')}\033[0m")
    else:
        print(f"\033[1;31m✗ Booking failed: {result.get('reason')}\033[0m")
    return ok


def _jitter_sleep(base_s: int) -> None:
    """Sleep base_s with Gaussian jitter (σ=25%) so polling looks human. Min 10s."""
    delay = max(10.0, random.gauss(base_s, base_s * 0.25))
    print(f"  (next poll in {delay:.0f}s)")
    time.sleep(delay)


def watch(
    cfg: Config,
    targets: list[date],
    window_start: str,
    window_end: str,
    party: int,
    person_ids: list[int],
    poll_s: int = 30,
    auto_book: bool = False,
) -> None:
    """Poll until all targets are booked or list is empty. Blocking."""
    remaining = list(targets)
    print(f"Watching {len(remaining)} date(s) — base interval {poll_s}s ±25%. Ctrl+C to stop.\n")
    for d in remaining:
        print(f"  {_fmt_date(d)}  window {window_start}–{window_end}  party={party}")
    print()

    with WiseGolfClient(cfg) as client:
        while remaining:
            now_str = datetime.now(cfg.snipe_tz).strftime("%H:%M:%S")
            found_dates = []

            for i, target in enumerate(remaining):
                # Small inter-request jitter so multi-date polls don't fire simultaneously
                if i > 0:
                    time.sleep(random.uniform(0.5, 2.5))
                try:
                    slots = client.list_slots(target)
                    available = _bookable(slots, window_start, window_end, party)
                    if available:
                        slot, open_count = available[0]
                        decision = True if auto_book else ask_to_book(slot, open_count, target)
                        if decision is True:
                            booked = _book_now(cfg, slot, target, person_ids)
                            if booked:
                                found_dates.append(target)
                        elif decision is None:
                            print(f"Stopped watching {_fmt_date(target)}.")
                            found_dates.append(target)
                        else:
                            print("Skipped. Continuing to watch.")
                    else:
                        print(f"[{now_str}] {_fmt_date(target)}: no slots in {window_start}–{window_end} (checked {len(slots)} rows)")
                except WiseGolfError as e:
                    print(f"[{now_str}] {_fmt_date(target)}: API error — {e}  (token expired? run `wisegolf browser-login`)")
                except Exception as e:
                    log.warning("%s poll error: %s", _fmt_date(target), e)

            for d in found_dates:
                remaining.remove(d)

            if remaining:
                _jitter_sleep(poll_s)

    print("\nAll targets done.")
