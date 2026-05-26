"""Daemon-style runner. Sleeps until next pending target, executes, marks status."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime

from . import db
from .browser_snipe import snipe_via_browser
from .config import Config, load as load_config

log = logging.getLogger(__name__)


def run_one(cfg: Config, t: db.Target, dry_run: bool, parallel: int = 1, headless: bool = True) -> dict:
    return asyncio.run(snipe_via_browser(
        cfg=cfg,
        target_day=t.target_date,
        snipe_at=t.snipe_at,
        window_start=t.window_start,
        window_end=t.window_end,
        party_size=t.party_size,
        person_ids=t.person_ids,
        dry_run=dry_run,
        headless=headless,
    ))


def daemon(dry_run: bool = False, parallel: int = 2, poll_s: int = 30) -> None:
    cfg = load_config()
    log.info("snipe daemon up — poll=%ss dry_run=%s parallel=%s", poll_s, dry_run, parallel)
    while True:
        nxt = db.next_due(datetime.now(cfg.snipe_tz))
        if not nxt:
            time.sleep(poll_s)
            continue
        wait = (nxt.snipe_at - datetime.now(cfg.snipe_tz)).total_seconds()
        if wait > 60:
            time.sleep(min(poll_s, wait - 30))
            continue
        log.info("target id=%s firing at %s", nxt.id, nxt.snipe_at.isoformat())
        try:
            res = run_one(cfg, nxt, dry_run=dry_run, parallel=parallel)
            db.mark(nxt.id, "done" if res.get("ok") else "failed", json.dumps(res))
        except Exception as e:
            log.exception("snipe crash")
            db.mark(nxt.id, "failed", json.dumps({"crash": str(e)}))
