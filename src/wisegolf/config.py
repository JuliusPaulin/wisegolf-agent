from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

# Load .env from project root regardless of cwd
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_PROJECT_ROOT / ".env")
load_dotenv()


def _load_token() -> str:
    token = os.getenv("WISEGOLF_TOKEN", "").strip()
    if token:
        return token
    # Fall back to browser_state.json (written by `wisegolf browser-login`)
    state_path = _PROJECT_ROOT / "browser_state.json"
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text())
            for origin in state.get("origins", []):
                for item in origin.get("localStorage", []):
                    if "access_token" in item.get("name", ""):
                        return item["value"].strip()
        except Exception:
            pass
    return ""


@dataclass(frozen=True)
class Config:
    # Bearer token only used by REST client paths — browser snipe doesn't need it.
    token: str = field(default_factory=_load_token)
    username: str = field(default_factory=lambda: os.getenv("WISEGOLF_USERNAME", ""))
    password: str = field(default_factory=lambda: os.getenv("WISEGOLF_PASSWORD", ""))
    host_slug: str = field(default_factory=lambda: os.getenv("WISEGOLF_HOST_SLUG", "espoogolf"))
    course_id: int = field(default_factory=lambda: int(os.getenv("WISEGOLF_COURSE_ID") or "0"))
    member_ids: tuple[int, ...] = field(
        default_factory=lambda: tuple(int(x) for x in os.getenv("WISEGOLF_MEMBER_IDS", "").split(",") if x.strip())
    )
    person_ids: tuple[int, ...] = field(
        default_factory=lambda: tuple(int(x) for x in os.getenv("WISEGOLF_PERSON_IDS", "").split(",") if x.strip())
    )
    snipe_time_local: str = field(default_factory=lambda: os.getenv("SNIPE_TIME_LOCAL", "10:00"))
    snipe_tz: ZoneInfo = field(default_factory=lambda: ZoneInfo(os.getenv("SNIPE_TZ", "Europe/Helsinki")))
    booking_horizon_days: int = field(default_factory=lambda: int(os.getenv("WISEGOLF_HORIZON_DAYS", "14")))
    session_path: Path = field(default_factory=lambda: Path(os.getenv("SESSION_PATH", "session.json")))

    @property
    def api_base(self) -> str:
        return f"https://api.{self.host_slug}.fi/api/1.0"


def load() -> Config:
    return Config()
