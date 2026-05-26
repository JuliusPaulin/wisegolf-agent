# WiseGolf Agent — Project Notes

Tee-time sniper and scout for WiseGolf (default: Espoon Golfseura, course id `28`). Booking transport is always browser-based (Playwright SPA clicks) — REST write paths are stubbed and unused.

## CLI reference

Run `wisegolf` with no arguments to open the interactive REPL.

### Top-level commands

| Command | Description |
|---|---|
| `wisegolf snipe <DD.MM.YYYY> --from HH:MM --to HH:MM` | Book immediately via browser, retry every 10s |
| `wisegolf scout date <DD.MM.YYYY> [...]` | Poll for open slots, prompt to book when found |
| `wisegolf slots <YYYY-MM-DD>` | List tee slots for a day (REST) |
| `wisegolf players` | List registered players and their IDs |
| `wisegolf my-bookings` | Show upcoming bookings |
| `wisegolf browser-login` | Authenticate and save session to `browser_state.json` |
| `wisegolf select course` | Pick course from API, optionally save to `.env` |
| `wisegolf select club` | Detect club from saved session, or show WiseGolf clubs |
| `wisegolf stop` | Kill all running snipe/scout processes |
| `wisegolf help` | Show full command reference |

### snipe

```
wisegolf snipe 26.5.2026 --from 10:00 --to 10:20 --party 2
wisegolf snipe 26.5.2026 --from 10:00 --to 10:20 --snipeat 26.5.2026 --at 02:00
```

- `--snipeat` / `--at` optional — omit to start immediately
- `--party 1` books for self only; `--party 2+` prompts for each extra player's home club and person ID
- Always real booking — no dry-run flag
- Retries every 10s inside the same browser session (keeps page open between attempts)
- `--show` runs with visible browser for debugging

**Why browser and not REST:** The REST API only returns rows for *booked* players. A completely empty slot has zero rows and is invisible to the API. The SPA shows "Vacant" buttons for all open slots regardless of booking count. The browser path is the only reliable way to detect and book empty slots.

### scout date

```
wisegolf scout date 07.06.2026
wisegolf scout date 07.06.2026 14.06.2026 --from 09:00 --to 12:00 --party 3 --poll 30
```

- Polls REST API every `--poll` seconds (default 30, ±25% jitter)
- When a slot appears: sends ntfy notification with Book/Skip/Stop buttons, or prompts in terminal
- `--party 2+` prompts for extra players before watching starts
- Limitation: only detects partially-booked slots (≥1 existing booking). Fully empty slots are invisible to REST — use `snipe` for those.

### select club

Club switching flow (no browser needed):
1. `wisegolf select club` — shows searchable club list, pick by number
2. Slug auto-derived from club email domain, verified against `api.{slug}.fi`
3. REST auth: `POST api.{slug}.fi/api/1.0/auth` with `{username, password, appId, version}`
4. On success: `.env` + `browser_state.json` updated with new slug and token
5. On auth failure (no account at club): slug updated for browsing, warns booking needs `browser-login`
6. `wisegolf select course` — pick the correct course ID for the new club

In the REPL, type `club` for the same flow. Auth is per-club — credentials only work at clubs where the user has an account.

### queue (daemon / scheduled snipes)

```
wisegolf queue add <YYYY-MM-DD>   # queue a target, fires at booking horizon open
wisegolf queue list
wisegolf queue rm <id>
wisegolf queue run <id>           # fire now (testing)
wisegolf queue daemon             # run forever, pick up due targets
```

## Interactive REPL

Run `wisegolf` (no args) to open the REPL. Prompt: `wisegolf ❯`

| Command | Description |
|---|---|
| `snipe` | Guided snipe flow (prompts for date, window, party, optional snipe-at time) |
| `scout` | Guided scout flow |
| `slots` | Browse tee times for a date |
| `bookings` | My upcoming bookings |
| `course` | Change active course (picks from API list, saves to `.env`) |
| `club` | Change active club (detects from session or shows list + login instructions) |
| `login` | Opens browser for re-login / club switch |
| `help` | Show command list |
| `quit` | Exit |

Features: tab-completion, persistent history (`~/.wisegolf_history`), live config reload after course/club change.

## Architecture

```
wisegolf (REPL / CLI)
   │
   ├─ snipe ──────────────────────────────────────────────────────────┐
   │    wait until snipe_at (optional)                                │
   │    └─ browser_snipe.py  ←── poll every 10s, keep browser open   │
   │                                                                   ▼
   ├─ scout date ─────────────────────────────────────────────────── app.wisegolf.fi SPA
   │    REST poll (list_slots) every N seconds                        click Vacant
   │    slot found → ask_to_book → browser_snipe.py                  click Make reservation
   │                                                                   Reservation confirmed
   └─ queue daemon
        SQLite targets table
        runner.py sleeps to snipe_at (horizon open = 10:00 Helsinki on target - horizon_days)
        └─ browser_snipe.py
```

`compute_snipe_moment(cfg, target_day)` = 10:00 Europe/Helsinki on `target_day − horizon_days`.

Booking transport is always the SPA. REST write paths (`lock_slot`, `add_to_cart`, etc.) are stubbed and not called in production.

## Key files

| Path | Role |
|---|---|
| [src/wisegolf/interactive.py](src/wisegolf/interactive.py) | Interactive REPL |
| [src/wisegolf/cli.py](src/wisegolf/cli.py) | Typer CLI entrypoints |
| [src/wisegolf/browser_snipe.py](src/wisegolf/browser_snipe.py) | Production snipe: Playwright DOM-driven booking, poll loop |
| [src/wisegolf/browser_auth.py](src/wisegolf/browser_auth.py) | Automated login → `browser_state.json` |
| [src/wisegolf/scout.py](src/wisegolf/scout.py) | REST-based slot polling + booking prompt loop |
| [src/wisegolf/runner.py](src/wisegolf/runner.py) | Daemon loop, picks due targets from SQLite |
| [src/wisegolf/db.py](src/wisegolf/db.py) | SQLite queue (`targets` table) |
| [src/wisegolf/client.py](src/wisegolf/client.py) | REST client: reads confirmed, writes stubbed |
| [src/wisegolf/config.py](src/wisegolf/config.py) | `.env`-loaded config |
| [src/wisegolf/snipe.py](src/wisegolf/snipe.py) | REST fast-snipe core (unused — POST schema unconfirmed) |
| [recon/api-map.md](recon/api-map.md) | API inventory + Vuex store actions |
| [scripts/sniff_booking.js](scripts/sniff_booking.js) | Capture real booking POST payload |

## Config (`.env`)

| Var | Purpose |
|---|---|
| `WISEGOLF_USERNAME` / `WISEGOLF_PASSWORD` | Login credentials (used by `browser-login`) |
| `WISEGOLF_HOST_SLUG` | Club subdomain, e.g. `espoogolf` → `api.espoogolf.fi` |
| `WISEGOLF_COURSE_ID` | Course/product ID, default `28` |
| `WISEGOLF_PERSON_IDS` | Default party person IDs, comma-separated |
| `WISEGOLF_HORIZON_DAYS` | Booking horizon in days, default `14` |
| `SNIPE_TIME_LOCAL` | Horizon open time, default `10:00` |
| `SNIPE_TZ` | Timezone, default `Europe/Helsinki` |
| `WISEGOLF_NTFY_TOPIC` | ntfy topic for push notifications (optional) |
| `WISEGOLF_TOKEN` | Bearer token for REST reads (optional; falls back to `browser_state.json`) |

`WISEGOLF_HOST_SLUG` and `WISEGOLF_COURSE_ID` are updated automatically by `wisegolf select club` / `wisegolf select course`.

## Known IDs

- Course: `28` (Espoon Golfseura 18r), `29` (Gumböle 18r), `32` (Gumböle 9r)
- Julius: `personId=37105`, `memberNO=16968`, club `Kurk`
- Anton Söderblom: `personId=37125`, `memberNO=16899`, club `Kurk`
- Access category `198` ("NUORISO2 pelioikeus 2026 EGS")

## Open Items

1. Capture real `POST /reservations/order/` payload via `scripts/sniff_booking.js` — would enable `snipe.py` as a faster alternative to the browser path.
2. `scout date` REST polling misses fully-empty slots (zero API rows). Consider using `snipe` for time windows likely to be empty.
3. Map `status` codes (2/3/4) on `/reservations/` rows.
4. `isSellable` is not a reliable bookability flag in this instance — SPA books rows where it's `false`.
5. Bearer-token refresh: `POST api.{slug}.fi/api/1.0/auth` with `{username, password, appId: "affbfa03", version: "2.18.1"}` returns a fresh token. Used by `select club` for seamless switching. The WiseGolf-registered email (from `/auth/session/` `user.email`) may differ from the `.env` `WISEGOLF_USERNAME`.

## Conventions

- Python 3.11+. Install: `pip install -e .` then `playwright install chromium`.
- All snipe paths are real bookings — no dry-run mode.
- Times are `Europe/Helsinki` throughout; SQLite stores ISO strings.
- `browser_state.json` holds the Playwright storage state (token + cookies). Re-run `browser-login` when it expires.
