"""Automated WiseGolf login → save storage_state.json for headless reuse.

WiseGolf onboarding flow on a fresh context:
  1. "Hello! Thank you for downloading our app" — click Next
  2. Possibly more onboarding screens — click Next on each
  3. Possibly club picker — type/select club (espoogolf)
  4. Email-only screen — fill email, click Next
  5. Password screen — fill password, click Login
  6. Logged in (token in localStorage)
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from .config import load as load_config

log = logging.getLogger(__name__)

STATE_PATH = Path("browser_state.json")


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


async def _select_club_if_present(page, club_name: str = "Espoon Golfseura") -> bool:
    """Click club option if visible. Type into search first if a search box is present."""
    txt = await _visible_text(page)
    looks_like_picker = any(s in txt for s in (
        "select club", "select your club", "valitse seura", "choose club", "choose your club"
    )) or club_name.lower() in txt
    if not looks_like_picker:
        return False
    search_term = club_name.split()[0].lower()
    for sel in ('input[type="search"]', 'input[placeholder*="seura" i]', 'input[placeholder*="club" i]', 'input[placeholder*="search" i]', 'input[placeholder*="haku" i]'):
        try:
            loc = page.locator(sel).first
            if await loc.count():
                await loc.fill(search_term)
                await asyncio.sleep(0.6)
                break
        except Exception:
            continue
    if await _click_text(page, club_name):
        await asyncio.sleep(0.6)
        await _click_text(page, "Next", "Continue", "Confirm", "Select", "Valitse", "Jatka")
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

        # Club picker
        await _select_club_if_present(page, club_name)

        inputs = await _all_inputs(page)
        types = [i.get("type") for i in inputs]
        url = page.url
        if url != last_url:
            log.info("step %d url=%s inputs=%s", step, url, types)
            last_url = url

        # Password screen?
        pw_idx = next((i for i, info in enumerate(inputs) if info.get("type") == "password"), None)
        if pw_idx is not None and not filled_password:
            log.info("filling password")
            await _fill_input_index(page, pw_idx, password)
            await asyncio.sleep(0.3)
            if not await _click_text(page, "Login", "Log in", "Sign in", "Submit", "Kirjaudu", "Continue", "Next", "Confirm"):
                await page.keyboard.press("Enter")
            filled_password = True
            await asyncio.sleep(1.2)
            continue

        # Email screen?
        email_idx = None
        for i, info in enumerate(inputs):
            t = info.get("type") or ""
            ac = info.get("ac") or ""
            ph = (info.get("ph") or "").lower()
            name = (info.get("name") or "").lower()
            if t == "password":
                continue
            if t == "email" or ac in ("username", "email") or "mail" in ph or "user" in ph or "käyttäjä" in ph or "email" in name or "user" in name:
                email_idx = i
                break
        if email_idx is None and inputs:
            # fallback: first non-password input
            email_idx = next((i for i, info in enumerate(inputs) if info.get("type") != "password"), None)

        if email_idx is not None and not filled_email:
            log.info("filling email")
            await _fill_input_index(page, email_idx, username)
            await asyncio.sleep(0.3)
            if not await _click_text(page, "Next", "Continue", "Submit", "Seuraava", "Jatka"):
                await page.keyboard.press("Enter")
            filled_email = True
            await asyncio.sleep(1.2)
            continue

        # Onboarding gate: just a Next/Continue button
        if not inputs:
            if await _click_text(page, "Next", "Continue", "Seuraava", "Jatka", "Skip", "Get started"):
                await asyncio.sleep(0.8)
                continue
            # Maybe accept terms
            if await _click_text(page, "I accept", "Accept", "Hyväksyn", "Hyväksy", "Agree"):
                await asyncio.sleep(0.6)
                continue

        # Couldn't progress this step — short wait and retry
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
