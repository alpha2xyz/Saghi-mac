"""
SQLite-backed transcript history (`saghi.db` in the data dir, see paths.py).

Kept deliberately simple for a single-process server: one short-lived
connection per call (opened, used, closed), WAL mode so a slow write never
blocks concurrent reads, `check_same_thread=False` not needed since we never
share a connection across threads in the first place.

Schema (one row per completed transcription):

    id             INTEGER PRIMARY KEY
    created_at     TEXT    UTC ISO-8601, e.g. "2026-08-20T12:34:56.789012+00:00"
    source         TEXT    e.g. "api", "cli"
    language       TEXT    "ar" / "en"
    cleanup_level  TEXT    "none" / "light" / "medium"
    duration_s     REAL    audio duration in seconds
    inference_s    REAL    model inference wall time in seconds
    raw_text       TEXT    pre-cleanup transcript
    text           TEXT    cleaned transcript (the primary result)
    audio_filename TEXT    original uploaded filename, NULL if unknown
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Iterator, Optional

from .paths import db_path, ensure_dirs

_SCHEMA = """
CREATE TABLE IF NOT EXISTS history (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at     TEXT NOT NULL,
    source         TEXT NOT NULL,
    language       TEXT NOT NULL,
    cleanup_level  TEXT NOT NULL,
    duration_s     REAL NOT NULL,
    inference_s    REAL NOT NULL,
    raw_text       TEXT NOT NULL,
    text           TEXT NOT NULL,
    audio_filename TEXT
);
"""


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    ensure_dirs()
    conn = sqlite3.connect(str(db_path()))
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.row_factory = sqlite3.Row
        conn.execute(_SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "source": row["source"],
        "language": row["language"],
        "cleanup_level": row["cleanup_level"],
        "duration_s": row["duration_s"],
        "inference_s": row["inference_s"],
        "raw_text": row["raw_text"],
        "text": row["text"],
        "audio_filename": row["audio_filename"],
    }


def add_entry(
    *,
    source: str,
    language: str,
    cleanup_level: str,
    duration_s: float,
    inference_s: float,
    raw_text: str,
    text: str,
    audio_filename: Optional[str] = None,
) -> dict:
    """Insert one history row and return it (with its assigned id/created_at) as a dict."""
    created_at = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO history
                (created_at, source, language, cleanup_level, duration_s,
                 inference_s, raw_text, text, audio_filename)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                created_at,
                source,
                language,
                cleanup_level,
                duration_s,
                inference_s,
                raw_text,
                text,
                audio_filename,
            ),
        )
        row_id = cur.lastrowid
        row = conn.execute("SELECT * FROM history WHERE id = ?", (row_id,)).fetchone()
        return _row_to_dict(row)


def latest() -> Optional[dict]:
    """Most recent history entry, or None if history is empty."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM history ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return _row_to_dict(row) if row else None


def search(limit: int = 20, query: str = "") -> list[dict]:
    """
    Most recent `limit` entries, optionally filtered by a case-insensitive
    substring match against `text` OR `raw_text`. Empty query = most recent
    N entries, unfiltered.
    """
    with _connect() as conn:
        if query:
            like = f"%{_escape_like(query)}%"
            rows = conn.execute(
                """
                SELECT * FROM history
                WHERE text LIKE ? ESCAPE '\\' COLLATE NOCASE
                   OR raw_text LIKE ? ESCAPE '\\' COLLATE NOCASE
                ORDER BY id DESC
                LIMIT ?
                """,
                (like, like, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM history ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [_row_to_dict(r) for r in rows]


def delete_entry(entry_id: int) -> bool:
    """Delete one history row. Returns whether a row was actually removed."""
    with _connect() as conn:
        cur = conn.execute("DELETE FROM history WHERE id = ?", (entry_id,))
        return cur.rowcount > 0


def clear_all() -> int:
    """Delete every history row. Returns how many rows were removed."""
    with _connect() as conn:
        cur = conn.execute("DELETE FROM history")
        return cur.rowcount


def _cutoff(days: int, now: Optional[datetime]) -> str:
    now = now or datetime.now(timezone.utc)
    return (now - timedelta(days=days)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")


def count_older_than(days: int, now: Optional[datetime] = None) -> int:
    """How many rows prune_older_than(days) would delete (0 for days <= 0)."""
    if days <= 0:
        return 0
    with _connect() as conn:
        row = conn.execute("SELECT COUNT(*) FROM history WHERE created_at < ?", (_cutoff(days, now),)).fetchone()
        return int(row[0])


def prune_older_than(days: int, now: Optional[datetime] = None) -> int:
    """
    Delete rows created more than `days` days ago (settings.py's
    `history_retention_days`). `days <= 0` means keep forever: nothing is
    touched. Returns how many rows were removed.

    created_at is always written by add_entry() as a UTC ISO-8601 string
    with the same fixed format, so a plain string comparison against the
    cutoff in that same format is a correct chronological comparison.
    """
    if days <= 0:
        return 0
    with _connect() as conn:
        cur = conn.execute("DELETE FROM history WHERE created_at < ?", (_cutoff(days, now),))
        return cur.rowcount


def count() -> int:
    """Total number of history rows."""
    with _connect() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM history").fetchone()[0])


def _escape_like(pattern: str) -> str:
    # Escape LIKE metacharacters (% and _) that might appear literally in a
    # user's search query, using backslash as the escape char (declared via
    # ESCAPE '\' in the queries above). SQLite's COLLATE NOCASE handles
    # ASCII case-insensitivity; Arabic has no case distinction so this is
    # sufficient for both languages this app supports.
    return pattern.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
