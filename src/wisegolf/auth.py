"""Auth = preloaded bearer token from browser. Token refresh tbd (likely auto-renews on use)."""
from __future__ import annotations

import random

import httpx

from .config import Config

_USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
]


def _browser_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "fi-FI,fi;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "User-Agent": random.choice(_USER_AGENTS),
        "Origin": "https://app.wisegolf.fi",
        "Referer": "https://app.wisegolf.fi/",
        "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"macOS"',
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "cross-site",
    }


def make_client(cfg: Config) -> httpx.Client:
    return httpx.Client(
        base_url=cfg.api_base,
        headers=_browser_headers(cfg.token),
        timeout=httpx.Timeout(connect=5, read=15, write=10, pool=5),
    )


def make_async_client(cfg: Config) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=cfg.api_base,
        headers=_browser_headers(cfg.token),
        timeout=httpx.Timeout(connect=3, read=8, write=5, pool=3),
        http2=True,
    )
