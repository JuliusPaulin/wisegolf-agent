# WiseGolf Scout

Token-free tee time watcher. Polls the WiseGolf REST API on a schedule, sends a push notification with Book / Skip / Stop buttons when a slot opens, and books via Playwright when you tap Book.

Zero LLM tokens consumed during operation.

---

## Setup

### 1. Install dependencies

```bash
pip install -e .
playwright install chromium
```

### 2. Configure `.env`

```env
WISEGOLF_USERNAME=your@email.com
WISEGOLF_PASSWORD=yourpassword
WISEGOLF_COURSE_ID=28
WISEGOLF_PERSON_IDS=37105,37125          # personId for each player in your group
WISEGOLF_NTFY_TOPIC=wisegolf-scout-yourname   # pick a unique name
```

Full list of supported variables: see `.env.example`.

### 3. Log in (saves browser session)

```bash
wisegolf browser-login
```

Saves `browser_state.json` in the project root. The scout uses this for both the REST token (tee sheet polling) and the Playwright session (booking). Re-run if you get `API error — 401`.

### 4. Install ntfy on your phone

1. Install the **ntfy** app (iOS / Android — free).
2. Open the app → tap **+** → subscribe to your topic, e.g. `wisegolf-scout-yourname`.

That's it. Notifications will arrive with three action buttons.

---

## Usage

```bash
wisegolf scout watch <date> [<date> ...] [options]
```

Dates are in EU format: `dd.mm.yyyy`.

| Option | Default | Description |
|---|---|---|
| `--from HH:MM` | `08:00` | Earliest acceptable tee time |
| `--to HH:MM` | `11:00` | Latest acceptable tee time |
| `--party N` | `2` | Minimum open spots needed |
| `--poll N` | `30` | Seconds between polls |
| `--person-ids X,Y` | from config | Override player IDs for this run |

### Examples

```bash
# Watch one date, default window 08:00–11:00
wisegolf scout watch 07.06.2026

# Watch two dates, narrower window, faster poll
wisegolf scout watch 07.06.2026 14.06.2026 --from 09:00 --to 12:00 --poll 10

# Evening round
wisegolf scout watch 25.05.2026 --from 17:00 --to 20:00 --party 2
```

---

## What happens when a slot is found

1. Terminal prints `⛳ SLOT FOUND: 25.05.2026 @ 18:30 — 2 spots open`.
2. Your phone receives a push notification (ntfy) with three buttons:
   - **Book** — book it immediately
   - **Skip** — skip this occurrence, keep watching
   - **Stop watching** — remove this date from the watch list
3. You have 120 seconds to respond. No response = Stop (same as tapping Stop).
4. If ntfy is not configured or fails, the terminal prompts instead (`Y/n/q`).

---

## Booking flow

When you tap **Book**, the scout:

1. Launches a headless Chromium browser with your saved `browser_state.json`.
2. Navigates to `https://app.wisegolf.fi/#/golf/reservation/{course_id}`.
3. Clicks the target date pill via JS (bypasses Ionic component pointer interception).
4. Waits 1.5 s for the SPA to reload the tee sheet.
5. Scans `div.timeblock-inner` rows for the first time in `[window_start..window_end]` with ≥ `party_size` **Vacant** buttons.
6. Clicks the Vacant button, then clicks **Make reservation**.
7. Waits for "Reservation succeeded" confirmation text.

Success is logged and printed green in the terminal. The date is removed from the watch list.

If booking fails (slot gone, session expired, etc.) the scout keeps watching and will notify again on the next poll.

---

## Architecture

```
wisegolf scout watch <dates>
        │
        ▼
  WiseGolfClient.list_slots(date)        ← REST GET, zero tokens
        │
        ▼
  _bookable(slots, window, party)
  • groups rows by time (API rows = BOOKED player slots)
  • open = 4 (capacity) − booked_row_count
  • excludes Pakkasvaraus (frost block)
        │
   slot found?
        │ yes
        ▼
  ask_to_book()
  • ntfy: sends notification with Book/Skip/Stop buttons
  • polls {topic}-response topic for reply
  • fallback: terminal prompt Y/n/q
        │
   Book tapped?
        │ yes
        ▼
  snipe_via_browser()                    ← Playwright, headless Chromium
  • JS click ion-segment-button for target date
  • querySelector('.timeblock-inner') to find tee rows
  • click Vacant → click Make reservation
  • await "Reservation succeeded"
        │
        ▼
  date removed from watch list
```

---

## API semantics (important)

The WiseGolf REST API at `GET /reservations/?productid=28&date=YYYY-MM-DD` returns **booked** player slots, not open ones. Each row = one booked player. A tee time with 1 row has 3 open spots (capacity 4 − 1 booked). Filtering by `isSellable` is unreliable — the SPA books rows where it is `false`. The scout ignores `isSellable`.

```
open_spots = 4 − len(rows_at_this_time)
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `API error — 401` | Session expired. Run `wisegolf browser-login`. |
| No ntfy notification arrives | Check `WISEGOLF_NTFY_TOPIC` in `.env`. Ensure phone is subscribed to that exact topic. |
| `_pick_date: day XX not found` | Calendar only shows the next 14 days. Don't watch dates beyond the booking horizon. |
| `no open slot found in [...]` | Slot may have been booked between poll and booking attempt. Scout will notify again on next poll. |
| Booking reports `ok: False, reason: Make reservation button never appeared` | Session may be stale. Run `wisegolf browser-login` and retry. |

---

## Environment variables (full list)

| Variable | Default | Purpose |
|---|---|---|
| `WISEGOLF_USERNAME` | — | Login email |
| `WISEGOLF_PASSWORD` | — | Login password |
| `WISEGOLF_HOST_SLUG` | `espoogolf` | Club API subdomain |
| `WISEGOLF_COURSE_ID` | `28` | Course ID (Espoon Golfseura = 28) |
| `WISEGOLF_PERSON_IDS` | — | Comma-separated personIds for default party |
| `WISEGOLF_NTFY_TOPIC` | — | ntfy topic name (leave blank to use terminal only) |
| `WISEGOLF_PUSHOVER_TOKEN` | — | Pushover app token (optional, notify-only fallback) |
| `WISEGOLF_PUSHOVER_USER` | — | Pushover user key |
| `SNIPE_TZ` | `Europe/Helsinki` | Timezone for all times |
| `WISEGOLF_HORIZON_DAYS` | `14` | Days ahead the booking window opens |
