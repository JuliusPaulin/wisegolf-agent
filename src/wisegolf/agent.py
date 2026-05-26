"""Claude Agent SDK loop. Lets user book/snipe in natural language."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, timedelta

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, query, tool

from . import tools as wg_tools
from .config import load as load_config

log = logging.getLogger(__name__)


@tool("search_tee_times", "List tee slots for a given date. only_sellable filters to slots you can book right now.", {"day": str, "only_sellable": bool})
async def search_tee_times(args):
    return {"content": [{"type": "text", "text": json.dumps(wg_tools.search_tee_times(args["day"], args.get("only_sellable", True)))}]}


@tool("book_tee_time", "Book a tee time. dry_run=true is safe — flips to false only when user confirms.", {"day": str, "reservation_time_id": int, "person_ids": list, "dry_run": bool})
async def book_tee_time(args):
    return {"content": [{"type": "text", "text": json.dumps(wg_tools.book_tee_time(args["day"], args["reservation_time_id"], args.get("person_ids"), args.get("dry_run", True)))}]}


@tool("my_bookings", "List my upcoming golf bookings.", {})
async def my_bookings(_args):
    return {"content": [{"type": "text", "text": json.dumps(wg_tools.my_bookings())}]}


@tool("list_players", "List players linked to my account (with personId, memberNO).", {})
async def list_players(_args):
    return {"content": [{"type": "text", "text": json.dumps(wg_tools.list_players())}]}


def system_prompt(cfg) -> str:
    horizon = (date.today() + timedelta(days=cfg.booking_horizon_days)).isoformat()
    return f"""You help me book tee times on WiseGolf at Espoon Golfseura (course id {cfg.course_id}).
Booking horizon: {cfg.booking_horizon_days} days. Today's edge = {horizon}.
Default party: 2 players, person ids {list(cfg.person_ids)}.
Snipe window: 10:00 Europe/Helsinki for slots {cfg.booking_horizon_days} days out.
Always default dry_run=true. Only set dry_run=false after explicit user confirmation in same message."""


async def run(prompt: str) -> None:
    cfg = load_config()
    opts = ClaudeAgentOptions(
        system_prompt=system_prompt(cfg),
        tools=[search_tee_times, book_tee_time, my_bookings, list_players],
        model="claude-haiku-4-5-20251001",
    )
    async for msg in query(prompt=prompt, options=opts):
        if isinstance(msg, AssistantMessage):
            for b in msg.content:
                if hasattr(b, "text"):
                    print(b.text)


def main(prompt: str) -> None:
    asyncio.run(run(prompt))
