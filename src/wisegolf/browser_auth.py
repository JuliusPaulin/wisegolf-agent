"""Automated WiseGolf login → save storage_state.json for headless reuse.

WiseGolf onboarding flow on a fresh context:
  1. "Hello! Thank you for downloading our app" — click Next
  2. Possibly more onboarding screens — click Next on each
  3. Club picker (Choices.js <select> dropdown) — search + click
  4. Login page — email + password on same screen
  5. Logged in (token in localStorage)
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from .config import load as load_config

log = logging.getLogger(__name__)

STATE_PATH = Path(__file__).resolve().parents[2] / "browser_state.json"


async def _has_token(page) -> bool:
    try:
        tok = await page.evaluate(
            "() => { const k = Object.keys(localStorage).find(x => x.startsWith('CapacitorStorage.access_token-')); return k ? localStorage.getItem(k) : ''; }"
        )
        return bool(tok)
    except Exception:
        return False


async def _all_inputs(page):
    return await page.evaluate(
        """() => {
          const all = (root) => {
            let r = [];
            for (const el of root.querySelectorAll('input')) r.push({type: el.type, name: el.name, ph: el.placeholder, ac: el.autocomplete});
            for (const el of root.querySelectorAll('*')) if (el.shadowRoot) r = r.concat(all(el.shadowRoot));
            return r;
          };
          return all(document);
        }"""
    )


async def _fill_input_index(page, idx: int, value: str) -> None:
    await page.evaluate(
        """([idx, val]) => {
          const all = (root) => {
            let r = [];
            for (const el of root.querySelectorAll('input')) r.push(el);
            for (const el of root.querySelectorAll('*')) if (el.shadowRoot) r = r.concat(all(el.shadowRoot));
            return r;
          };
          const el = all(document)[idx];
          const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el), 'value').set;
          setter.call(el, val);
          el.dispatchEvent(new Event('input', { bubbles: true }));
          el.dispatchEvent(new Event('change', { bubbles: true }));
          el.focus();
        }""",
        [idx, value],
    )


async def _visible_text(page) -> str:
    try:
        return (await page.evaluate("() => document.body.innerText || ''")).lower()
    except Exception:
        return ""


async def _click_text(page, *labels: str) -> bool:
    for label in labels:
        for sel in (
            f'button:has-text("{label}")',
            f'[role=button]:has-text("{label}")',
            f'text="{label}"',
        ):
            try:
                loc = page.locator(sel).first
                if await loc.count():
                    await loc.click(timeout=1500)
                    return True
            except Exception:
                continue
    return False


async def _click_ion_button(page, *labels: str) -> bool:
    """Click an ion-button by its text content (more reliable than _click_text for Ionic apps)."""
    for label in labels:
        clicked = await page.evaluate("""
            (label) => {
                const buttons = [...document.querySelectorAll('ion-button, button, [role=button]')];
                const match = buttons.find(b => b.textContent.trim().toLowerCase() === label.toLowerCase());
                if (match) { match.click(); return true; }
                return false;
            }
        """, label)
        if clicked:
            return True
    return False


async def _select_club_choices_js(page, club_name: str) -> bool:
    """Select a club from the Choices.js dropdown used in the WiseGolf onboarding."""
    return await page.evaluate("""
        async (clubName) => {
            const container = document.querySelector('.choices');
            if (!container) return false;

            // Open dropdown
            const inner = container.querySelector('.choices__inner');
            if (inner) inner.click();
            await new Promise(r => setTimeout(r, 400));

            // Type in search to filter
            const search = container.querySelector('input.choices__input--cloned');
            if (search) {
                search.value = clubName;
                search.dispatchEvent(new Event('input', { bubbles: true }));
                await new Promise(r => setTimeout(r, 600));
            }

            // Click matching option
            const items = [...container.querySelectorAll('.choices__item--choice')];
            const visible = items.filter(i => i.offsetHeight > 0 && !i.classList.contains('choices__placeholder'));
            const match = visible.find(i => i.textContent.toLowerCase().includes(clubName.toLowerCase()));
            if (match) {
                match.click();
                return true;
            }
            return false;
        }
    """, club_name)


async def _select_club_if_present(page, club_name: str = "Espoon Golfseura") -> bool:
    """Detect and handle the club picker page (Choices.js dropdown)."""
    txt = await _visible_text(page)
    is_picker = any(kw in txt for kw in (
        "select the club", "select a club", "valitse seura",
        "choose club", "choose your club",
    ))
    if not is_picker:
        return False

    log.info("club picker detected, selecting '%s'", club_name)
    selected = await _select_club_choices_js(page, club_name)
    if not selected:
        log.warning("could not select club '%s' in Choices.js dropdown", club_name)
        return False

    await asyncio.sleep(0.5)
    await _click_ion_button(page, "Login", "Kirjaudu", "Next", "Continue", "Jatka")
    await asyncio.sleep(1.5)
    return True


def _is_login_input(info: dict) -> bool:
    """True if the input looks like an email/username field (not search/hidden/checkbox)."""
    t = info.get("type") or ""
    if t in ("password", "search", "hidden", "checkbox", "radio"):
        return False
    ac = (info.get("ac") or "").lower()
    ph = (info.get("ph") or "").lower()
    name = (info.get("name") or "").lower()
    if t == "email":
        return True
    if ac in ("username", "email"):
        return True
    if any(kw in ph for kw in ("mail", "user", "käyttäjä")):
        return True
    if any(kw in name for kw in ("email", "user", "login")):
        return True
    # Bare text input on a login page — accept as fallback
    if t in ("text", ""):
        return True
    return False


async def _step_through(page, username: str, password: str, club_name: str, max_steps: int = 25) -> None:
    """State machine: each iteration looks at page state and advances."""
    filled_email = False
    filled_password = False
    last_url = ""
    for step in range(max_steps):
        if await _has_token(page):
            return
        await asyncio.sleep(0.6)

        # Dismiss any cookie/terms overlay
        await _click_text(page, "Accept all", "Accept", "Hyväksy", "Hyväksy kaikki", "Hyväksyn", "Got it", "OK", "I agree")

        # Club picker (Choices.js dropdown)
        if await _select_club_if_present(page, club_name):
            continue

        inputs = await _all_inputs(page)
        # Filter out search/hidden/checkbox — only real form inputs
        real_inputs = [(i, info) for i, info in enumerate(inputs) if info.get("type") not in ("search", "hidden", "checkbox", "radio")]
        types = [info.get("type") for _, info in real_inputs]
        url = page.url
        if url != last_url:
            log.info("step %d url=%s inputs=%s", step, url, types)
            last_url = url

        pw_entries = [(i, info) for i, info in real_inputs if info.get("type") == "password"]
        email_entries = [(i, info) for i, info in real_inputs if _is_login_input(info)]

        # Same-page login: email + password both visible
        if email_entries and pw_entries and not filled_email and not filled_password:
            log.info("filling email + password (same page)")
            await _fill_input_index(page, email_entries[0][0], username)
            await asyncio.sleep(0.2)
            await _fill_input_index(page, pw_entries[0][0], password)
            await asyncio.sleep(0.3)
            filled_email = True
            filled_password = True
            if not await _click_ion_button(page, "Login", "Log in", "Sign in", "Kirjaudu", "Submit"):
                await page.keyboard.press("Enter")
            await asyncio.sleep(2.0)
            continue

        # Password-only screen
        if pw_entries and not filled_password:
            log.info("filling password")
            await _fill_input_index(page, pw_entries[0][0], password)
            await asyncio.sleep(0.3)
            if not await _click_ion_button(page, "Login", "Log in", "Sign in", "Kirjaudu", "Submit", "Continue", "Next"):
                await page.keyboard.press("Enter")
            filled_password = True
            await asyncio.sleep(1.2)
            continue

        # Email-only screen
        if email_entries and not filled_email:
            log.info("filling email")
            await _fill_input_index(page, email_entries[0][0], username)
            await asyncio.sleep(0.3)
            if not await _click_ion_button(page, "Next", "Continue", "Submit", "Seuraava", "Jatka"):
                await page.keyboard.press("Enter")
            filled_email = True
            await asyncio.sleep(1.2)
            continue

        # Onboarding gate: no real inputs, just advance buttons
        if not real_inputs:
            # Check terms checkbox first
            await page.evaluate("""
                () => document.querySelectorAll('input[type=checkbox]').forEach(c => { if (!c.checked) c.click(); })
            """)
            if await _click_ion_button(page, "Next", "Continue", "Seuraava", "Jatka", "Skip", "Get started"):
                await asyncio.sleep(0.8)
                continue
            if await _click_text(page, "Next", "Continue", "Seuraava", "Jatka", "Skip", "Get started"):
                await asyncio.sleep(0.8)
                continue
            if await _click_ion_button(page, "I accept", "Accept", "Hyväksyn", "Hyväksy", "Agree"):
                await asyncio.sleep(0.6)
                continue

        await asyncio.sleep(0.8)

    raise RuntimeError("login: could not reach logged-in state within step budget")


async def login_automated(state_path: Path = STATE_PATH, headless: bool = True, club_name: str | None = None) -> None:
    from playwright.async_api import async_playwright

    cfg = load_config()
    if not cfg.username or not cfg.password:
        raise RuntimeError("WISEGOLF_USERNAME / WISEGOLF_PASSWORD missing in .env")
    if club_name is None:
        club_name = cfg.host_slug

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        log.info("→ %s", "https://app.wisegolf.fi/")
        await page.goto("https://app.wisegolf.fi/", wait_until="domcontentloaded")
        await asyncio.sleep(2.0)

        try:
            await _step_through(page, cfg.username, cfg.password, club_name)
        except Exception as e:
            try:
                await page.screenshot(path="login_fail.png", full_page=True)
                Path("login_fail.html").write_text((await page.content())[:200000])
            except Exception:
                pass
            raise

        if cfg.course_id:
            await page.goto(f"https://app.wisegolf.fi/#/golf/reservation/{cfg.course_id}", wait_until="networkidle")
        else:
            await page.goto("https://app.wisegolf.fi/", wait_until="networkidle")
        await asyncio.sleep(2.0)
        await ctx.storage_state(path=str(state_path))
        await browser.close()
        log.info("saved %s", state_path)


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    asyncio.run(login_automated(headless=False))


async def login_interactively(state_path: Path = STATE_PATH, club_name: str | None = None) -> None:
    await login_automated(state_path=state_path, headless=False, club_name=club_name)
