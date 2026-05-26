"""SQLite-backed snipe target queue."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

DEFAULT_DB = Path("snipe.db")


SCHEMA = """
CREATE TABLE IF NOT EXISTS targets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id INTEGER NOT NULL,
    target_date TEXT NOT NULL,            -- YYYY-MM-DD
    window_start TEXT NOT NULL,           -- HH:MM
    window_end TEXT NOT NULL,             -- HH:MM
    party_size INTEGER NOT NULL,
    person_ids TEXT NOT NULL,             -- comma-separated personIds
    snipe_at TEXT NOT NULL,               -- ISO datetime in Europe/Helsinki
    status TEXT NOT NULL DEFAULT 'pending',  -- pending|done|failed|skipped
    result TEXT,                          -- JSON details
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_targets_status_snipe ON targets(status, snipe_at);
"""


@dataclass
class Target:
    id: int | None
    course_id: int
    target_date: date
    window_start: str
    window_end: str
    party_size: int
    person_ids: list[int]
    snipe_at: datetime
    status: str = "pending"
    result: str | None = None


@contextmanager
def connect(path: Path = DEFAULT_DB):
    conn = sqlite3.connect(path, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def insert(t: Target, path: Path = DEFAULT_DB) -> int:
    with connect(path) as c:
        cur = c.execute(
            "INSERT INTO targets (course_id, target_date, window_start, window_end, party_size, person_ids, snipe_at) VALUES (?,?,?,?,?,?,?)",
            (t.course_id, t.target_date.isoformat(), t.window_start, t.window_end, t.party_size, ",".join(map(str, t.person_ids)), t.snipe_at.isoformat()),
        )
        return int(cur.lastrowid)


def list_targets(path: Path = DEFAULT_DB, status: str | None = None) -> list[Target]:
    with connect(path) as c:
        q = "SELECT * FROM targets" + (" WHERE status = ?" if status else "") + " ORDER BY snipe_at ASC"
        rows = c.execute(q, (status,) if status else ()).fetchall()
    return [_row_to_target(r) for r in rows]


def delete(target_id: int, path: Path = DEFAULT_DB) -> None:
    with connect(path) as c:
        c.execute("DELETE FROM targets WHERE id = ?", (target_id,))


def mark(target_id: int, status: str, result: str | None = None, path: Path = DEFAULT_DB) -> None:
    with connect(path) as c:
        c.execute(
            "UPDATE targets SET status = ?, result = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (status, result, target_id),
        )


def next_due(now: datetime, path: Path = DEFAULT_DB) -> Target | None:
    """Earliest pending target with snipe_at <= now + 30s."""
    with connect(path) as c:
        row = c.execute(
            "SELECT * FROM targets WHERE status = 'pending' ORDER BY snipe_at ASC LIMIT 1"
        ).fetchone()
    return _row_to_target(row) if row else None


def exists_pending(course_id: int, target_date: date, path: Path = DEFAULT_DB) -> bool:
    """True if a pending target already exists for this course + date."""
    with connect(path) as c:
        row = c.execute(
            "SELECT 1 FROM targets WHERE course_id = ? AND target_date = ? AND status = 'pending'",
            (course_id, target_date.isoformat()),
        ).fetchone()
    return row is not None


def _row_to_target(r: sqlite3.Row) -> Target:
    return Target(
        id=r["id"],
        course_id=r["course_id"],
        target_date=date.fromisoformat(r["target_date"]),
        window_start=r["window_start"],
        window_end=r["window_end"],
        party_size=r["party_size"],
        person_ids=[int(x) for x in r["person_ids"].split(",") if x],
        snipe_at=datetime.fromisoformat(r["snipe_at"]),
        status=r["status"],
        result=r["result"],
    )
