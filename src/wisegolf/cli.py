from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import time
from datetime import date as _date
from datetime import datetime, timedelta
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import db, scout as wg_scout, tui
from .browser_auth import login_automated
from .browser_snipe import snipe_via_browser
from .client import WiseGolfClient
from .config import Config, load as load_config
from .runner import daemon as run_daemon, run_one
from .snipe import compute_snipe_moment

app = typer.Typer(help="WiseGolf booking agent")
queue_app = typer.Typer(help="Manage queued snipe targets", no_args_is_help=True)
scout_app = typer.Typer(help="Watch for available tee times", no_args_is_help=True)
select_app = typer.Typer(help="Select course and other settings", no_args_is_help=True)
app.add_typer(queue_app, name="queue")
app.add_typer(scout_app, name="scout")
app.add_typer(select_app, name="select")
console = Console()


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        from . import interactive
        interactive.run()


_WEEKDAY_MAP = {
    "mon": 0, "monday": 0,
    "tue": 1, "tuesday": 1,
    "wed": 2, "wednesday": 2,
    "thu": 3, "thursday": 3,
    "fri": 4, "friday": 4,
    "sat": 5, "saturday": 5,
    "sun": 6, "sunday": 6,
}


def _setup_log():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


def _resolve_person_ids(cfg: Config, party: int, person_ids_str: str | None) -> list[int]:
    """Return person IDs for booking. Party=1 → self only. Party>1 → self + interactive prompts."""
    if person_ids_str:
        return [int(x) for x in person_ids_str.split(",")]
    if not cfg.person_ids:
        console.print("[red]No person IDs configured. Set WISEGOLF_PERSON_IDS in .env or use --person-ids.[/]")
        raise typer.Exit(1)
    if party == 1:
        return [cfg.person_ids[0]]
    pids = [cfg.person_ids[0]]
    console.print(f"[bold]Party of {party}:[/] Player 1 = you (id {cfg.person_ids[0]})")
    for i in range(1, party):
        console.print(f"\n[bold]Player {i + 1}[/]")
        typer.prompt("  Home club (for reference)")
        pid = typer.prompt("  Person ID", type=int)
        pids.append(pid)
    return pids


@app.command()
def slots(day: str, course_id: int | None = None):
    """List tee slots for a day (YYYY-MM-DD)."""
    cfg = load_config()
    with WiseGolfClient(cfg) as c:
        rows = c.list_slots(_date.fromisoformat(day), course_id or cfg.course_id)
    t = Table("time", "id", "qty", "label")
    for s in rows:
        t.add_row(s.hhmm, str(s.reservation_time_id), str(s.quantity), s.label or "")
    console.print(t)


@app.command()
def players():
    cfg = load_config()
    with WiseGolfClient(cfg) as c:
        ps = c.players()
    t = Table("personId", "playerId", "memberNO", "name", "club")
    for p in ps:
        t.add_row(str(p.person_id), p.player_id or "", p.member_no or "",
                  f"{p.first_name or ''} {p.family_name or ''}".strip(), str(p.club_id or ""))
    console.print(t)


@app.command(name="my-bookings")
def my_bookings():
    cfg = load_config()
    with WiseGolfClient(cfg) as c:
        bs = c.my_bookings()
    for b in bs:
        console.print(b.model_dump())


# ---- Queue ----

@queue_app.command("add")
def queue_add(
    day: str = typer.Argument(..., help="Target tee date YYYY-MM-DD"),
    window_start: str = "08:00",
    window_end: str = "11:00",
    party: int = 2,
    person_ids: str = typer.Option(None, help="Comma-separated personIds; default = config"),
):
    """Queue a snipe target."""
    cfg = load_config()
    pids = [int(x) for x in person_ids.split(",")] if person_ids else list(cfg.person_ids)
    target = _date.fromisoformat(day)
    snipe_at = compute_snipe_moment(cfg, target)
    tid = db.insert(db.Target(
        id=None, course_id=cfg.course_id, target_date=target,
        window_start=window_start, window_end=window_end,
        party_size=party, person_ids=pids, snipe_at=snipe_at,
    ))
    console.print(f"[green]queued id={tid}[/] fires {snipe_at.isoformat()}")


@queue_app.command("interactive")
def queue_interactive():
    """Interactive add via prompts."""
    tui.add_target_interactive()


@queue_app.command("list")
def queue_list():
    tui.list_targets()


@queue_app.command("rm")
def queue_rm(target_id: int):
    db.delete(target_id)
    console.print(f"removed {target_id}")


@queue_app.command("run")
def queue_run(
    target_id: int = typer.Argument(..., help="Run this target now (ignores snipe_at)"),
    confirm: bool = typer.Option(False, "--confirm"),
    parallel: int = 2,
):
    """Execute a queued target right now (for testing)."""
    _setup_log()
    cfg = load_config()
    rows = [t for t in db.list_targets() if t.id == target_id]
    if not rows:
        console.print(f"[red]no target {target_id}[/]")
        raise typer.Exit(1)
    t = rows[0]
    # override snipe_at to now
    t.snipe_at = datetime.now(cfg.snipe_tz) + timedelta(seconds=4)
    res = run_one(cfg, t, dry_run=not confirm, parallel=parallel)
    db.mark(t.id, "done" if res.get("ok") else "failed", json.dumps(res))
    console.print_json(json.dumps(res))


@queue_app.command("daemon")
def queue_daemon(
    confirm: bool = typer.Option(False, "--confirm", help="Real bookings. Default dry-run."),
    parallel: int = 2,
    poll_seconds: int = 30,
):
    """Run forever; pick up due targets, fire snipes, mark status."""
    _setup_log()
    run_daemon(dry_run=not confirm, parallel=parallel, poll_s=poll_seconds)


# ---- One-off (legacy) ----

@app.command("browser-login")
def browser_login(
    show: bool = typer.Option(False, help="Run with visible browser (debug)"),
):
    """Automated login → saves browser_state.json. Uses WISEGOLF_USERNAME/PASSWORD from .env."""
    _setup_log()
    asyncio.run(login_automated(headless=not show))


@app.command("snipe")
def snipe(
    day: str = typer.Argument(..., help="Target tee date DD.MM.YYYY"),
    window_start: str = typer.Option(..., "--from", help="Earliest tee time HH:MM"),
    window_end: str = typer.Option(..., "--to", help="Latest tee time HH:MM"),
    party: int = typer.Option(2, help="Minimum spots needed"),
    snipeat_date: str = typer.Option(None, "--snipeat", help="Date to start sniping: DD.MM.YYYY (default: now)"),
    snipeat_time: str = typer.Option("00:00", "--at", help="Time to start sniping: HH:MM"),
    person_ids: str = typer.Option(None, "--person-ids", help="Comma-separated personIds; default = config"),
    show: bool = typer.Option(False, "--show", help="Run with visible browser (debug)"),
):
    """Snipe a tee time via browser. Retries every 10s until booked.

    Optionally wait until --snipeat --at before starting. Omit to start immediately.

    Examples:
      wisegolf snipe 26.5.2026 --from 10:00 --to 10:20 --party 2
      wisegolf snipe 26.5.2026 --from 10:00 --to 10:20 --snipeat 26.5.2026 --at 02:00
    """
    _setup_log()
    cfg = load_config()
    pids = _resolve_person_ids(cfg, party, person_ids)

    try:
        target = datetime.strptime(day, "%d.%m.%Y").date()
    except ValueError:
        console.print("[red]Bad date (use DD.MM.YYYY)[/]")
        raise typer.Exit(1)

    now = datetime.now(cfg.snipe_tz)
    if snipeat_date:
        try:
            snipe_dt = datetime.strptime(f"{snipeat_date} {snipeat_time}", "%d.%m.%Y %H:%M").replace(tzinfo=cfg.snipe_tz)
        except ValueError:
            console.print("[red]Bad --snipeat/--at (use DD.MM.YYYY and HH:MM)[/]")
            raise typer.Exit(1)
        wait_s = (snipe_dt - now).total_seconds()
        if wait_s > 0:
            console.print(f"Waiting {wait_s / 3600:.1f}h until {snipe_dt.strftime('%d.%m.%Y %H:%M')}…")
            time.sleep(wait_s)
    else:
        snipe_dt = now + timedelta(seconds=2)

    console.print(f"[bold green]Sniping {target.strftime('%d.%m.%Y')} {window_start}–{window_end}, party={party}[/]")

    try:
        res = asyncio.run(snipe_via_browser(
            cfg=cfg,
            target_day=target,
            snipe_at=snipe_dt,
            window_start=window_start,
            window_end=window_end,
            party_size=party,
            person_ids=pids,
            dry_run=False,
            headless=not show,
            poll_interval_s=10,
        ))
        console.print_json(json.dumps(res))
    except KeyboardInterrupt:
        console.print("\n[yellow]Stopped.[/]")


@scout_app.command("date")
def scout_date(
    dates: list[str] = typer.Argument(..., help="One or more target dates: DD.MM.YYYY"),
    window_start: str = typer.Option("08:00", "--from", help="Earliest tee time HH:MM"),
    window_end: str = typer.Option("11:00", "--to", help="Latest tee time HH:MM"),
    party: int = typer.Option(2, help="Minimum spots needed"),
    poll: int = typer.Option(30, help="Seconds between polls"),
    person_ids: str = typer.Option(None, "--person-ids", help="Comma-separated personIds; default = config"),
):
    """Poll for available tee times on given date(s). Notifies + prompts to book when slot appears.

    Party=1 books for you only. Party>1 prompts for each extra player's home club and person ID.

    Examples:
      wisegolf scout date 07.06.2026
      wisegolf scout date 07.06.2026 14.06.2026 --from 09:00 --to 12:00
      wisegolf scout date 07.06.2026 --party 3 --poll 10
    """
    _setup_log()
    cfg = load_config()
    pids = _resolve_person_ids(cfg, party, person_ids)
    try:
        target_dates = [datetime.strptime(d, "%d.%m.%Y").date() for d in dates]
    except ValueError as e:
        console.print(f"[red]Bad date (use DD.MM.YYYY): {e}[/]")
        raise typer.Exit(1)

    try:
        wg_scout.watch(
            cfg=cfg,
            targets=target_dates,
            window_start=window_start,
            window_end=window_end,
            party=party,
            person_ids=pids,
            poll_s=poll,
        )
    except KeyboardInterrupt:
        console.print("\n[yellow]Stopped.[/]")


_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"


@select_app.command("course")
def select_course():
    """List available courses and switch to one. Optionally save as default."""
    cfg = load_config()
    with WiseGolfClient(cfg) as c:
        courses = c.list_courses()

    if not courses:
        console.print("[red]No courses returned from API.[/]")
        raise typer.Exit(1)

    t = Table("№", "ID", "Name", show_header=True, header_style="bold cyan")
    for i, course in enumerate(courses, 1):
        t.add_row(str(i), str(course.get("productId", "?")), course.get("name", "?"))
    console.print(t)

    choice = typer.prompt(f"Pick course (1–{len(courses)})", type=int)
    if not 1 <= choice <= len(courses):
        console.print("[red]Invalid choice.[/]")
        raise typer.Exit(1)

    picked = courses[choice - 1]
    course_id = picked.get("productId")
    name = picked.get("name", str(course_id))
    console.print(f"Selected: [bold]{name}[/] (id {course_id})")

    if typer.confirm("Set as default course in .env?"):
        from dotenv import set_key
        set_key(str(_ENV_PATH), "WISEGOLF_COURSE_ID", str(course_id))
        console.print(f"[green]Saved WISEGOLF_COURSE_ID={course_id} to .env[/]")
    else:
        console.print(f"Not saved. Use --course-id {course_id} to override per command (not yet wired).")


@select_app.command("club")
def select_club():
    """Pick a WiseGolf club from the full list. Re-authenticates automatically."""
    from .interactive import _read_slug_from_state, slug_from_club, verify_slug
    from dotenv import set_key

    cfg = load_config()
    current = _read_slug_from_state() or cfg.host_slug
    console.print(f"Current club: [bold]{current}[/] (api.{current}.fi)")
    console.print()

    with WiseGolfClient(cfg) as c:
        clubs = c.list_wisegolf_clubs()

    if not clubs:
        console.print("[red]No clubs returned from API.[/]")
        raise typer.Exit(1)

    search = typer.prompt("Search club name (blank = show all)", default="").lower()
    if search:
        words = search.split()
        filtered = [c for c in clubs if all(
            w in c.get("name", "").lower() or w in c.get("city", "").lower()
            for w in words
        )]
    else:
        filtered = clubs

    if not filtered:
        console.print("[yellow]No matches.[/]")
        raise typer.Exit(1)

    t = Table("№", "Name", "City", "Slug", show_header=True, header_style="bold cyan")
    for i, club in enumerate(filtered, 1):
        slug = slug_from_club(club) or "?"
        active = " ◀" if slug == current else ""
        t.add_row(str(i), club.get("name", "?") + active, club.get("city", "?"), slug)
    console.print(t)

    choice = typer.prompt(f"Pick club (1–{len(filtered)})", type=int)
    if not 1 <= choice <= len(filtered):
        console.print("[red]Invalid choice.[/]")
        raise typer.Exit(1)

    picked = filtered[choice - 1]
    name = picked.get("name", "?")
    slug = slug_from_club(picked)

    if not slug:
        slug = typer.prompt(f"Can't auto-detect slug for '{name}'. Enter manually (e.g. 'espoogolf')")

    if slug == current:
        console.print(f"[dim]Already on {slug}.[/]")
        return

    console.print(f"Verifying api.{slug}.fi… ", end="")
    if not verify_slug(slug):
        console.print("[red]✗ not reachable[/]")
        slug = typer.prompt("Enter correct slug manually")
        if not verify_slug(slug):
            console.print("[red]Cannot verify slug.[/]")
            raise typer.Exit(1)
        console.print(f"api.{slug}.fi [green]✓[/]")
    else:
        console.print("[green]✓[/]")

    from .interactive import _get_wisegolf_email, rest_auth, _update_browser_state

    console.print(f"Authenticating to {slug}… ", end="")
    wisegolf_email = _get_wisegolf_email(cfg)
    token = rest_auth(slug, wisegolf_email, cfg.password)
    if token:
        console.print("[green]✓[/]")
        _update_browser_state(slug, token)
    else:
        console.print("[yellow]✗ no account at this club[/]")
        console.print("[yellow]Browsing works. Booking requires wisegolf browser-login.[/]")
        _update_browser_state(slug)

    set_key(str(_ENV_PATH), "WISEGOLF_HOST_SLUG", slug)
    set_key(str(_ENV_PATH), "WISEGOLF_COURSE_ID", "")
    os.environ["WISEGOLF_HOST_SLUG"] = slug
    os.environ.pop("WISEGOLF_COURSE_ID", None)
    console.print(f"[green]Saved WISEGOLF_HOST_SLUG={slug}[/]")
    console.print("[yellow]Course ID cleared — run wisegolf select course next.[/]")


@app.command("setup")
def setup_cmd():
    """Interactive first-time setup: credentials, club, course, player IDs."""
    from .interactive import setup
    setup()


@app.command("stop")
def stop():
    """Kill all running wisegolf snipe and scout date processes."""
    import signal
    my_pid = os.getpid()

    result = subprocess.run(
        ["pgrep", "-Ef", "wisegolf.*(snipe|scout)"],
        capture_output=True, text=True,
    )
    pids = [int(p) for p in result.stdout.split() if p and int(p) != my_pid]

    if not pids:
        console.print("No running wisegolf snipe/scout processes found.")
        return

    killed = 0
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
            console.print(f"[green]Stopped pid {pid}[/]")
            killed += 1
        except ProcessLookupError:
            console.print(f"[yellow]pid {pid} already gone[/]")
        except PermissionError:
            console.print(f"[red]No permission to stop pid {pid}[/]")

    console.print(f"{killed} process(es) stopped.")


@app.command("help")
def help_cmd():
    """Show all available commands."""
    t = Table("Command", "Description", show_header=True, header_style="bold cyan")
    rows = [
        ("wisegolf slots <DD.MM.YYYY>", "List tee slots for a day"),
        ("wisegolf players", "List your registered players and their IDs"),
        ("wisegolf my-bookings", "Show your upcoming bookings"),
        ("wisegolf browser-login", "Login and save browser session (run once)"),
        ("", ""),
        ("wisegolf scout date <DD.MM.YYYY> [...]", "Watch date(s) for open slots, prompt to book"),
        ("  --from HH:MM", "  Earliest tee time (default 08:00)"),
        ("  --to HH:MM", "  Latest tee time (default 11:00)"),
        ("  --party N", "  Spots needed; >1 prompts for extra players"),
        ("  --poll N", "  Seconds between checks (default 30)"),
        ("", ""),
        ("wisegolf snipe <DD.MM.YYYY>", "Browser-snipe a slot, retry every 10s until booked"),
        ("  --from HH:MM", "  Earliest tee time"),
        ("  --to HH:MM", "  Latest tee time"),
        ("  --party N", "  Spots needed; >1 prompts for extra players"),
        ("  --snipeat DD.MM.YYYY --at HH:MM", "  Wait until this moment before starting (optional)"),
        ("  --show", "  Show browser window (debug)"),
        ("", ""),
        ("wisegolf select course", "Pick a course; optionally set as default in .env"),
        ("wisegolf select club",   "Pick a WiseGolf club; updates host slug in .env"),
        ("", ""),
        ("wisegolf queue add <DD.MM.YYYY>", "Queue a snipe target (fires at booking horizon open)"),
        ("wisegolf queue list", "List all queued targets"),
        ("wisegolf queue rm <id>", "Remove a queued target"),
        ("wisegolf queue run <id>", "Run a queued target right now (for testing)"),
        ("wisegolf queue daemon", "Run background daemon (picks up due targets)"),
        ("", ""),
        ("wisegolf setup", "Interactive first-time setup"),
        ("wisegolf stop", "Kill all running snipe/scout processes"),
        ("wisegolf help", "Show this help"),
    ]
    for cmd, desc in rows:
        t.add_row(cmd, desc)
    console.print(t)


if __name__ == "__main__":
    app()
