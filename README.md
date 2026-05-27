# WiseGolf Agent

Tee-time sniper and scout for WiseGolf clubs. Books tee times via the SPA using Playwright — the only reliable way to detect and book fully empty slots.

## Install

### macOS / Linux

```bash
pip install git+https://github.com/juliuspaulin/wisegolf-agent.git
playwright install chromium
```

### Windows

Requires Python 3.11+ ([python.org](https://www.python.org/downloads/) — check "Add to PATH" during install).

```powershell
pip install git+https://github.com/juliuspaulin/wisegolf-agent.git
playwright install chromium
```

If `pip` isn't recognized, try `py -m pip` instead. If `wisegolf` isn't found after install, use `py -m wisegolf` or add Python's Scripts folder to your PATH.

## First-time setup

```bash
wisegolf setup
```

The setup wizard walks you through:
1. Enter your WiseGolf email and password
2. Pick your club from the full list
3. Authenticate (REST — no browser needed)
4. Pick your course
5. Confirm player IDs

Everything is saved to `.env`. You can also just run `wisegolf` — it detects first launch and runs setup automatically.

## Usage

Run `wisegolf` for the interactive REPL, or use CLI commands directly:

```bash
# Browse
wisegolf slots 2026-06-01           # list tee times
wisegolf my-bookings                # upcoming bookings
wisegolf players                    # player IDs

# Snipe (book immediately, retry every 10s)
wisegolf snipe 01.06.2026 --from 08:00 --to 11:00 --party 2

# Snipe at a scheduled time (e.g. when booking horizon opens)
wisegolf snipe 01.06.2026 --from 08:00 --to 11:00 --snipeat 18.05.2026 --at 10:00

# Scout (poll for open slots, notify when found)
wisegolf scout date 01.06.2026 --from 08:00 --to 11:00 --party 2

# Switch club or course
wisegolf select club                # pick from all WiseGolf clubs
wisegolf select course              # pick course at current club
```

### Queue (scheduled snipes)

```bash
wisegolf queue add 2026-06-01       # fires at booking horizon open
wisegolf queue list
wisegolf queue daemon               # run forever, fires due targets
```

## How it works

Booking uses Playwright headless Chromium driving the WiseGolf SPA directly. At T-0 it polls the DOM for "Vacant" buttons in your time window, clicks the first one, then clicks "Make reservation". End-to-end ~300–700ms.

The REST API only shows rows for already-booked players — fully empty slots are invisible. The browser path is the only way to find and book them.

## Config (.env)

| Variable | Purpose |
|---|---|
| `WISEGOLF_USERNAME` | Login email |
| `WISEGOLF_PASSWORD` | Login password |
| `WISEGOLF_HOST_SLUG` | Club subdomain (e.g. `espoogolf` → `api.espoogolf.fi`) |
| `WISEGOLF_COURSE_ID` | Course/product ID |
| `WISEGOLF_PERSON_IDS` | Comma-separated person IDs for your party |
| `WISEGOLF_HORIZON_DAYS` | Booking horizon in days (default 14) |
| `WISEGOLF_NTFY_TOPIC` | ntfy.sh topic for push notifications (optional) |

## Requirements

- Python 3.11+
- Chromium (installed via `playwright install chromium`)
- Works on macOS, Linux, and Windows
