"""Interactive REPL — launched when `wisegolf` is run with no arguments."""
from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import httpx
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()

_COMMANDS = ["snipe", "scout", "slots", "bookings", "course", "club", "login", "setup", "help", "quit", "exit"]

_HELP = (
    "  [bold cyan]snipe[/]      Book a tee time, retry every 10s\n"
    "  [bold cyan]scout[/]      Watch for open slots, prompt to book\n"
    "  [bold cyan]slots[/]      Browse available tee times for a date\n"
    "  [bold cyan]bookings[/]   My upcoming bookings\n"
    "  [bold cyan]course[/]     Change active course\n"
    "  [bold cyan]club[/]       Change active club\n"
    "  [bold cyan]login[/]      Re-login / switch club in browser\n"
    "  [bold cyan]setup[/]      Re-run first-time setup\n"
    "  [bold cyan]help[/]       Show this help\n"
    "  [bold cyan]quit[/]       Exit"
)


# ── helpers ──────────────────────────────────────────────────────────────────

def _ask(label: str, default: str = "") -> str:
    hint = f" [dim][{default}][/]" if default else ""
    console.print(f"  {label}{hint}", end="  ")
    val = input().strip()
    return val or default


def _ask_date(label: str = "Date") -> str:
    while True:
        val = _ask(label, "DD.MM.YYYY")
        if val in ("", "DD.MM.YYYY"):
            console.print("  [red]Enter a date.[/]")
            continue
        try:
            datetime.strptime(val, "%d.%m.%Y")
            return val
        except ValueError:
            console.print("  [red]Use DD.MM.YYYY[/]")


def _ask_time(label: str, default: str) -> str:
    while True:
        val = _ask(label, default)
        try:
            datetime.strptime(val, "%H:%M")
            return val
        except ValueError:
            console.print("  [red]Use HH:MM[/]")


def _ask_int(label: str, default: int) -> int:
    while True:
        val = _ask(label, str(default))
        try:
            return int(val)
        except ValueError:
            console.print("  [red]Enter a number.[/]")


def _resolve_players(cfg, party: int) -> list[int]:
    """Self for party=1; prompt for extras."""
    if not cfg.person_ids:
        console.print("  [red]No person IDs in config. Set WISEGOLF_PERSON_IDS in .env.[/]")
        raise SystemExit(1)
    if party == 1:
        return [cfg.person_ids[0]]
    pids = [cfg.person_ids[0]]
    console.print(f"  Player 1 = you (id {cfg.person_ids[0]})")
    for i in range(1, party):
        console.print(f"\n  [bold]Player {i + 1}[/]")
        _ask("Home club (reference only)")
        pid = _ask_int("Person ID", 0)
        pids.append(pid)
    return pids


# ── command handlers ──────────────────────────────────────────────────────────

def _do_snipe(cfg) -> None:
    from .browser_snipe import snipe_via_browser

    console.print()
    day        = _ask_date("Date")
    from_time  = _ask_time("From", "08:00")
    to_time    = _ask_time("To",   "11:00")
    party      = _ask_int("Party", 1)
    pids       = _resolve_players(cfg, party)
    snipeat    = _ask("Snipe at (DD.MM.YYYY HH:MM, blank = now)", "")

    now = datetime.now(cfg.snipe_tz)
    if snipeat:
        try:
            snipe_dt = datetime.strptime(snipeat, "%d.%m.%Y %H:%M").replace(tzinfo=cfg.snipe_tz)
            wait_s = (snipe_dt - now).total_seconds()
            if wait_s > 0:
                console.print(f"\n  Waiting {wait_s / 3600:.1f}h until {snipe_dt.strftime('%d.%m.%Y %H:%M')}…")
                time.sleep(wait_s)
        except ValueError:
            console.print("  [yellow]Bad datetime — starting now.[/]")
            snipe_dt = now + timedelta(seconds=2)
    else:
        snipe_dt = now + timedelta(seconds=2)

    target = datetime.strptime(day, "%d.%m.%Y").date()
    console.print(f"\n[bold green]⛳  Sniping {day} {from_time}–{to_time}, party={party}[/]")
    try:
        res = asyncio.run(snipe_via_browser(
            cfg=cfg, target_day=target, snipe_at=snipe_dt,
            window_start=from_time, window_end=to_time,
            party_size=party, person_ids=pids,
            dry_run=False, headless=True, poll_interval_s=10,
        ))
        if res.get("ok"):
            console.print(f"[bold green]✓ Booked {day} @ {res.get('time')}[/]")
        else:
            console.print(f"[red]✗ {res.get('reason')}[/]")
    except KeyboardInterrupt:
        console.print("\n[yellow]Snipe cancelled.[/]")


def _do_scout(cfg) -> None:
    from . import scout as wg_scout

    console.print()
    dates_raw  = _ask("Date(s) space-separated", "DD.MM.YYYY")
    from_time  = _ask_time("From", "08:00")
    to_time    = _ask_time("To",   "11:00")
    party      = _ask_int("Party", 2)
    poll       = _ask_int("Poll interval (seconds)", 30)
    pids       = _resolve_players(cfg, party)

    try:
        targets = [datetime.strptime(d, "%d.%m.%Y").date() for d in dates_raw.split()]
    except ValueError as e:
        console.print(f"  [red]Bad date: {e}[/]")
        return

    console.print(f"\n[bold green]Watching {len(targets)} date(s) — Ctrl+C to stop[/]")
    try:
        wg_scout.watch(cfg=cfg, targets=targets, window_start=from_time,
                       window_end=to_time, party=party, person_ids=pids, poll_s=poll)
    except KeyboardInterrupt:
        console.print("\n[yellow]Scout stopped.[/]")


def _do_slots(cfg) -> None:
    from .client import WiseGolfClient

    console.print()
    day    = _ask_date("Date")
    target = datetime.strptime(day, "%d.%m.%Y").date()

    with WiseGolfClient(cfg) as c:
        rows = c.list_slots(target)

    t = Table("Time", "ID", "Qty", "Label", box=box.SIMPLE_HEAD)
    for s in rows:
        t.add_row(s.hhmm, str(s.reservation_time_id), str(s.quantity), s.label or "")
    console.print(t)
    console.print(f"  [dim]{len(rows)} rows[/]")


def _do_bookings(cfg) -> None:
    from .client import WiseGolfClient

    console.print()
    with WiseGolfClient(cfg) as c:
        bs = c.my_bookings()

    if not bs:
        console.print("  No upcoming bookings.")
        return

    t = Table("Date", "Time", "ID", box=box.SIMPLE_HEAD)
    for b in bs:
        date_str = b.start.strftime("%d.%m.%Y") if b.start else "?"
        time_str = b.start.strftime("%H:%M") if b.start else "?"
        t.add_row(date_str, time_str, str(b.reservation_time_id or "?"))
    console.print(t)


def _do_course(cfg):
    from .client import WiseGolfClient
    from dotenv import set_key

    console.print()
    with WiseGolfClient(cfg) as c:
        courses = c.list_courses()

    if not courses:
        console.print("  [red]No courses returned.[/]")
        return

    t = Table("№", "ID", "Name", box=box.SIMPLE_HEAD)
    for i, course in enumerate(courses, 1):
        name = course.get("name", "?").strip(".")
        active = " ◀" if course.get("productId") == cfg.course_id else ""
        t.add_row(str(i), str(course.get("productId")), name + active)
    console.print(t)

    choice = _ask_int(f"Pick (1–{len(courses)})", 1)
    if not 1 <= choice <= len(courses):
        console.print("  [red]Invalid choice.[/]")
        return

    picked   = courses[choice - 1]
    cid      = picked.get("productId")
    name     = picked.get("name", "?").strip(".")
    console.print(f"  Selected: [bold]{name}[/] (id {cid})")

    save = _ask("Set as default? [y/N]", "n").lower()
    if save in ("y", "yes"):
        env_path = Path(__file__).resolve().parents[2] / ".env"
        set_key(str(env_path), "WISEGOLF_COURSE_ID", str(cid))
        console.print(f"  [green]Saved WISEGOLF_COURSE_ID={cid} to .env[/]")
        from .config import load as load_config
        return load_config()

    return None


def _do_login() -> None:
    import asyncio as _asyncio
    from .browser_auth import login_automated
    console.print()
    console.print("  Opening browser — pick your club and log in…")
    try:
        _asyncio.run(login_automated(headless=False))
        console.print("  [green]Login saved.[/] Type [bold]club[/] to sync the new club to .env.")
    except Exception as e:
        console.print(f"  [red]Login failed: {e}[/]")


def _read_slug_from_state() -> str | None:
    """Read the club slug saved by the last browser-login."""
    state_path = Path(__file__).resolve().parents[2] / "browser_state.json"
    if not state_path.exists():
        return None
    try:
        state = json.loads(state_path.read_text())
        for origin in state.get("origins", []):
            for item in origin.get("localStorage", []):
                if "selectedHost" in item.get("name", ""):
                    return item["value"].strip('"')
    except Exception:
        return None


def slug_from_club(club: dict) -> str | None:
    """Derive host slug from club email domain (e.g. egs@espoogolf.fi → espoogolf)."""
    email = club.get("email", "")
    if "@" not in email:
        return None
    domain = email.split("@")[-1].lower()
    if domain.split(".")[0] in ("gmail", "hotmail", "outlook", "yahoo"):
        return None
    return domain.rsplit(".", 1)[0]


def verify_slug(slug: str) -> bool:
    """Check if api.{slug}.fi resolves to a live WiseGolf instance."""
    try:
        r = httpx.get(f"https://api.{slug}.fi/api/1.0/products/?type=6", timeout=5)
        return r.status_code == 200
    except Exception:
        return False


def _get_wisegolf_email(cfg) -> str:
    """Get the actual WiseGolf-registered email from the current session.

    Tries the current API host first, then falls back to the host stored
    in browser_state.json (in case slug was changed but token is still
    from the old host).
    """
    state_slug = _read_slug_from_state()
    bases = [cfg.api_base]
    if state_slug and state_slug != cfg.host_slug:
        bases.append(f"https://api.{state_slug}.fi/api/1.0")
    for base in bases:
        try:
            r = httpx.get(
                f"{base}/auth/session/",
                headers={"Authorization": f"Bearer {cfg.token}"},
                timeout=5,
            )
            j = r.json()
            if j.get("success") and j.get("user"):
                return j["user"].get("email") or j["user"].get("username") or cfg.username
        except Exception:
            continue
    return cfg.username


def rest_auth(slug: str, username: str, password: str | None = None, cfg=None) -> str | None:
    """Authenticate via REST API. Returns token or None.

    Pass password directly, or pass cfg to read from cfg.password.
    """
    if password is None and cfg is not None:
        password = cfg.password
    if not password:
        return None
    try:
        r = httpx.post(
            f"https://api.{slug}.fi/api/1.0/auth",
            json={"username": username, "password": password, "appId": "affbfa03", "version": "2.18.1"},
            timeout=10,
        )
        j = r.json()
        if j.get("success"):
            return j.get("access_token")
    except Exception:
        pass
    return None


def _update_browser_state(slug: str, token: str | None = None) -> None:
    """Update selectedHost (and optionally access_token) in browser_state.json."""
    state_path = Path(__file__).resolve().parents[2] / "browser_state.json"
    if not state_path.exists():
        if token:
            _create_browser_state(slug, token)
        return
    state = json.loads(state_path.read_text())
    for origin in state.get("origins", []):
        for item in origin.get("localStorage", []):
            name = item.get("name", "")
            if "selectedHost" in name:
                item["value"] = slug
            elif "selectedGolfClub" in name:
                item["value"] = f'"{slug}"'
            elif token and "access_token" in name:
                item["value"] = token
    state_path.write_text(json.dumps(state, indent=2))


def _create_browser_state(slug: str, token: str) -> None:
    """Build browser_state.json from scratch — no browser needed."""
    state_path = Path(__file__).resolve().parents[2] / "browser_state.json"
    state = {
        "cookies": [],
        "origins": [{
            "origin": "https://app.wisegolf.fi",
            "localStorage": [
                {"name": "CapacitorStorage.access_token-affbfa03", "value": token},
                {"name": "CapacitorStorage.selectedHost-affbfa03", "value": slug},
                {"name": "CapacitorStorage.selectedGolfClub-affbfa03", "value": f'"{slug}"'},
                {"name": "CapacitorStorage.screenMode-affbfa03", "value": "dark"},
            ],
        }],
    }
    state_path.write_text(json.dumps(state, indent=2))


def _do_club(cfg):
    from .client import WiseGolfClient
    from dotenv import set_key

    env_path = Path(__file__).resolve().parents[2] / ".env"
    console.print()

    current = _read_slug_from_state() or cfg.host_slug
    console.print(f"  Current club: [bold]{current}[/] (api.{current}.fi)")
    console.print()

    console.print("  Fetching clubs…")
    try:
        with WiseGolfClient(cfg) as c:
            clubs = c.list_wisegolf_clubs()
    except Exception as e:
        console.print(f"  [red]Could not fetch clubs: {e}[/]")
        return None

    if not clubs:
        console.print("  [red]No clubs found.[/]")
        return None

    search = _ask("Search (blank = show all)", "").lower()
    if search:
        words = search.split()
        filtered = [c for c in clubs if all(
            w in c.get("name", "").lower() or w in c.get("city", "").lower()
            for w in words
        )]
    else:
        filtered = clubs

    if not filtered:
        console.print("  [yellow]No matches.[/]")
        return None

    t = Table("№", "Name", "City", "Slug", box=box.SIMPLE_HEAD)
    for i, club in enumerate(filtered, 1):
        slug = slug_from_club(club) or "?"
        active = " ◀" if slug == current else ""
        t.add_row(str(i), club.get("name", "?") + active, club.get("city", "?"), slug)
    console.print(t)

    choice = _ask(f"Pick (1–{len(filtered)}, blank = cancel)", "")
    if not choice:
        return None
    try:
        idx = int(choice) - 1
        if not 0 <= idx < len(filtered):
            console.print("  [red]Invalid choice.[/]")
            return None
    except ValueError:
        console.print("  [red]Enter a number.[/]")
        return None

    picked = filtered[idx]
    name = picked.get("name", "?")
    slug = slug_from_club(picked)

    if not slug:
        slug = _ask(f"Can't auto-detect slug for '{name}'. Enter manually (e.g. 'espoogolf')", "")
        if not slug:
            return None

    if slug == current:
        console.print(f"  [dim]Already on {slug}.[/]")
        return None

    console.print(f"  Verifying api.{slug}.fi… ", end="")
    if not verify_slug(slug):
        console.print("[red]✗ not reachable[/]")
        slug = _ask("Enter correct slug manually", "")
        if not slug or not verify_slug(slug):
            console.print("  [red]Cannot verify slug.[/]")
            return None
        console.print(f"  api.{slug}.fi [green]✓[/]")
    else:
        console.print("[green]✓[/]")

    # Authenticate via REST
    console.print(f"  Authenticating to {slug}… ", end="")
    wisegolf_email = _get_wisegolf_email(cfg)
    token = rest_auth(slug, wisegolf_email, cfg.password)
    if token:
        console.print("[green]✓[/]")
        _update_browser_state(slug, token)
    else:
        console.print("[yellow]✗ no account at this club[/]")
        console.print("  [yellow]Browsing works. Booking requires [bold]login[/].[/]")
        _update_browser_state(slug)

    set_key(str(env_path), "WISEGOLF_HOST_SLUG", slug)
    set_key(str(env_path), "WISEGOLF_COURSE_ID", "")
    os.environ["WISEGOLF_HOST_SLUG"] = slug
    os.environ.pop("WISEGOLF_COURSE_ID", None)
    console.print(f"  [green]Saved WISEGOLF_HOST_SLUG={slug}[/]")
    console.print("  [yellow]Course ID cleared — type [bold]course[/] to pick one.[/]")
    from .config import load as load_config
    return load_config()


# ── setup (first launch) ─────────────────────────────────────────────────────

_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
_ENV_EXAMPLE = Path(__file__).resolve().parents[2] / ".env.example"


def setup() -> "Config":
    """Interactive first-time setup. Creates .env, authenticates, picks course."""
    from dotenv import set_key

    console.print()
    console.print(Panel(
        "[bold green]WiseGolf Agent — First-time setup[/]",
        border_style="green", expand=False, padding=(0, 2),
    ))
    console.print()

    # Create .env from example if needed
    if not _ENV_PATH.exists():
        if _ENV_EXAMPLE.exists():
            _ENV_PATH.write_text(_ENV_EXAMPLE.read_text())
        else:
            _ENV_PATH.write_text("")

    # 1. Credentials
    console.print("  [bold]Step 1:[/] WiseGolf credentials")
    email = _ask("Email", "")
    if not email:
        console.print("  [red]Email required.[/]")
        raise SystemExit(1)
    import getpass
    try:
        password = getpass.getpass("  Password:  ")
    except EOFError:
        password = _ask("Password", "")
    if not password:
        console.print("  [red]Password required.[/]")
        raise SystemExit(1)

    set_key(str(_ENV_PATH), "WISEGOLF_USERNAME", email)
    set_key(str(_ENV_PATH), "WISEGOLF_PASSWORD", password)
    os.environ["WISEGOLF_USERNAME"] = email
    os.environ["WISEGOLF_PASSWORD"] = password
    console.print("  [green]Saved credentials to .env[/]")
    console.print()

    # 2. Pick club
    console.print("  [bold]Step 2:[/] Select your club")
    console.print("  Fetching WiseGolf clubs…")
    clubs = _fetch_clubs_unauthenticated()
    if not clubs:
        console.print("  [red]Could not fetch club list. Set WISEGOLF_HOST_SLUG in .env manually.[/]")
        raise SystemExit(1)

    search = _ask("Search club name", "").lower()
    if search:
        words = search.split()
        filtered = [c for c in clubs if all(
            w in c.get("name", "").lower() or w in c.get("city", "").lower()
            for w in words
        )]
    else:
        filtered = clubs
    if not filtered:
        console.print("  [yellow]No matches. Showing all.[/]")
        filtered = clubs

    t = Table("№", "Name", "City", box=box.SIMPLE_HEAD)
    for i, club in enumerate(filtered, 1):
        t.add_row(str(i), club.get("name", "?"), club.get("city", "?"))
    console.print(t)

    choice = _ask_int(f"Pick (1–{len(filtered)})", 1)
    if not 1 <= choice <= len(filtered):
        console.print("  [red]Invalid.[/]")
        raise SystemExit(1)

    picked = filtered[choice - 1]
    club_name = picked.get("name", "?")
    slug = slug_from_club(picked)
    if not slug:
        slug = _ask(f"Enter club slug for '{club_name}' (e.g. 'espoogolf')", "")
    if not slug:
        raise SystemExit(1)

    console.print(f"  Verifying api.{slug}.fi… ", end="")
    if not verify_slug(slug):
        console.print("[red]✗[/]")
        raise SystemExit(1)
    console.print("[green]✓[/]")

    set_key(str(_ENV_PATH), "WISEGOLF_HOST_SLUG", slug)
    os.environ["WISEGOLF_HOST_SLUG"] = slug
    console.print()

    # 3. Authenticate via REST
    console.print("  [bold]Step 3:[/] Authenticating…")
    token = rest_auth(slug, email, cfg=None, password=password)
    if token:
        console.print(f"  [green]✓ Logged in to {club_name}[/]")
        _create_browser_state(slug, token)
        os.environ["WISEGOLF_TOKEN"] = token
    else:
        console.print("  [yellow]REST auth failed — trying browser login…[/]")
        try:
            import asyncio as _aio
            from .browser_auth import login_automated
            _aio.run(login_automated(headless=True, club_name=club_name))
            console.print(f"  [green]✓ Logged in via browser[/]")
        except Exception as e:
            console.print(f"  [yellow]Browser login also failed: {e}[/]")
            console.print("  [yellow]Run [bold]wisegolf browser-login --show[/] to login manually.[/]")

    # 4. Pick course
    console.print()
    console.print("  [bold]Step 4:[/] Select course")
    from .config import load as load_config
    cfg = load_config()
    try:
        from .client import WiseGolfClient
        with WiseGolfClient(cfg) as c:
            courses = c.list_courses()
    except Exception:
        courses = []

    if courses:
        t2 = Table("№", "ID", "Name", box=box.SIMPLE_HEAD)
        for i, course in enumerate(courses, 1):
            t2.add_row(str(i), str(course.get("productId", "?")), course.get("name", "?").strip("."))
        console.print(t2)
        cc = _ask_int(f"Pick (1–{len(courses)})", 1)
        if 1 <= cc <= len(courses):
            cid = courses[cc - 1].get("productId")
            set_key(str(_ENV_PATH), "WISEGOLF_COURSE_ID", str(cid))
            os.environ["WISEGOLF_COURSE_ID"] = str(cid)
            console.print(f"  [green]Saved WISEGOLF_COURSE_ID={cid}[/]")
    else:
        console.print("  [yellow]Could not fetch courses. Set WISEGOLF_COURSE_ID in .env manually.[/]")

    # 5. Person IDs
    console.print()
    console.print("  [bold]Step 5:[/] Player IDs")
    cfg = load_config()
    try:
        from .client import WiseGolfClient
        with WiseGolfClient(cfg) as c:
            players = c.players()
        if players:
            t3 = Table("personId", "Name", "Club", box=box.SIMPLE_HEAD)
            for p in players:
                t3.add_row(
                    str(p.person_id),
                    f"{p.first_name or ''} {p.family_name or ''}".strip(),
                    str(p.club_id or ""),
                )
            console.print(t3)
            default_pids = ",".join(str(p.person_id) for p in players)
            pids = _ask("Person IDs (comma-separated)", default_pids)
            set_key(str(_ENV_PATH), "WISEGOLF_PERSON_IDS", pids)
            os.environ["WISEGOLF_PERSON_IDS"] = pids
            console.print(f"  [green]Saved WISEGOLF_PERSON_IDS={pids}[/]")
        else:
            console.print("  [yellow]No players found. Set WISEGOLF_PERSON_IDS in .env manually.[/]")
    except Exception:
        console.print("  [yellow]Could not fetch players. Set WISEGOLF_PERSON_IDS in .env after login.[/]")

    console.print()
    console.print(Panel(
        "[bold green]Setup complete![/] Run [bold]wisegolf[/] to start.",
        border_style="green", expand=False, padding=(0, 2),
    ))
    console.print()

    return load_config()


def _fetch_clubs_unauthenticated() -> list[dict]:
    """Fetch club list without needing auth (uses espoogolf as default API host)."""
    try:
        r = httpx.get(
            "https://api.espoogolf.fi/api/1.0/golf/club/",
            headers={"Accept": "application/json"},
            timeout=10,
        )
        j = r.json()
        return [c for c in j.get("rows", []) if c.get("softwareVendorName") == "WiseNetwork"]
    except Exception:
        return []


# ── welcome ───────────────────────────────────────────────────────────────────

def _welcome(cfg) -> None:
    course_label = str(cfg.course_id)
    try:
        from .client import WiseGolfClient
        with WiseGolfClient(cfg) as c:
            courses = c.list_courses()
        match = next((r for r in courses if r.get("productId") == cfg.course_id), None)
        if match:
            course_label = match.get("name", course_label).strip(".")
    except Exception:
        pass

    console.print()
    console.print(Panel(
        f"[bold green]WiseGolf Agent[/]\n"
        f"[dim]Course  {course_label}\n"
        f"User    {cfg.username}[/]",
        border_style="green",
        expand=False,
        padding=(0, 2),
    ))
    console.print()
    console.print(_HELP)
    console.print()


# ── main loop ─────────────────────────────────────────────────────────────────

def run() -> None:
    from .config import load as load_config

    if not _ENV_PATH.exists() or not os.getenv("WISEGOLF_USERNAME"):
        cfg = setup()
    else:
        cfg = load_config()

    _welcome(cfg)

    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.completion import WordCompleter
        from prompt_toolkit.history import FileHistory
        from prompt_toolkit.styles import Style

        session = PromptSession(
            history=FileHistory(str(Path.home() / ".wisegolf_history")),
            completer=WordCompleter(_COMMANDS, ignore_case=True),
            style=Style.from_dict({"prompt": "ansibrightgreen bold"}),
        )

        def _get_line() -> str:
            return session.prompt("wisegolf ❯ ")

    except ImportError:
        def _get_line() -> str:
            return input("wisegolf ❯ ")

    while True:
        try:
            line = _get_line().strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]bye[/]")
            break

        if not line:
            continue

        cmd = line.split()[0].lower()

        if cmd in ("quit", "exit", "q"):
            console.print("[dim]bye[/]")
            break
        elif cmd == "snipe":
            _do_snipe(cfg)
        elif cmd == "scout":
            _do_scout(cfg)
        elif cmd in ("slots", "slot"):
            _do_slots(cfg)
        elif cmd in ("bookings", "booking"):
            _do_bookings(cfg)
        elif cmd == "course":
            new_cfg = _do_course(cfg)
            if new_cfg:
                cfg = new_cfg
        elif cmd == "club":
            new_cfg = _do_club(cfg)
            if new_cfg:
                cfg = new_cfg
        elif cmd == "login":
            _do_login()
        elif cmd == "setup":
            cfg = setup()
        elif cmd == "help":
            console.print()
            console.print(_HELP)
            console.print()
        else:
            console.print(f"  [yellow]Unknown command: {cmd!r} — type [bold]help[/][/]")
