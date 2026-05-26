"""Interactive TUI to add a snipe target. Lightweight prompt-based form via rich."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from rich.console import Console
from rich.prompt import IntPrompt, Prompt

from . import db
from .config import load as load_config
from .snipe import compute_snipe_moment

console = Console()


def add_target_interactive() -> int:
    cfg = load_config()
    console.print(f"[bold]New snipe target[/] (course {cfg.course_id}, party defaults {len(cfg.person_ids)})")

    default_date = (date.today() + timedelta(days=cfg.booking_horizon_days)).isoformat()
    while True:
        d_str = Prompt.ask("Target date (YYYY-MM-DD)", default=default_date)
        try:
            target_d = date.fromisoformat(d_str)
            break
        except ValueError:
            console.print("[red]bad date[/]")

    window_start = Prompt.ask("Earliest tee time (HH:MM)", default="08:00")
    window_end = Prompt.ask("Latest tee time (HH:MM)", default="11:00")
    party = IntPrompt.ask("Party size", default=len(cfg.person_ids))
    person_ids_str = Prompt.ask("Person IDs (comma-separated)", default=",".join(map(str, cfg.person_ids)))
    person_ids = [int(x.strip()) for x in person_ids_str.split(",") if x.strip()]
    if len(person_ids) != party:
        console.print(f"[yellow]warn:[/] {len(person_ids)} ids vs party {party}")

    snipe_at = compute_snipe_moment(cfg, target_d)
    console.print(f"snipe will fire at [bold cyan]{snipe_at.isoformat()}[/]")
    confirm = Prompt.ask("Save?", choices=["y", "n"], default="y")
    if confirm != "y":
        return 0

    tid = db.insert(db.Target(
        id=None,
        course_id=cfg.course_id,
        target_date=target_d,
        window_start=window_start,
        window_end=window_end,
        party_size=party,
        person_ids=person_ids,
        snipe_at=snipe_at,
    ))
    console.print(f"[green]saved id={tid}[/]")
    return tid


def list_targets() -> None:
    from rich.table import Table
    rows = db.list_targets()
    t = Table("id", "date", "window", "party", "snipe_at", "status")
    for r in rows:
        t.add_row(str(r.id), r.target_date.isoformat(), f"{r.window_start}-{r.window_end}", str(r.party_size), r.snipe_at.isoformat(), r.status)
    console.print(t)
