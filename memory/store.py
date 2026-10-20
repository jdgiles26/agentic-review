"""Durable review history (SQLite).

Each stored review keeps the full result, including the agent trajectory in
`raw_messages`, so past reviews can be replayed as offline evaluation sets.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from schemas.models import ReviewRequest, ReviewResult

DEFAULT_DB = ".review/reviews.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS reviews (
    request_id TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    request_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    recommendation TEXT,
    score REAL
);
CREATE INDEX IF NOT EXISTS reviews_created_at ON reviews (created_at);
CREATE TABLE IF NOT EXISTS checkpoints (
    thread_id TEXT PRIMARY KEY,
    updated_at REAL NOT NULL,
    state_json TEXT NOT NULL
);
"""

_initialized: set[Path] = set()
_init_lock = threading.Lock()


def db_path() -> Path:
    """Resolved at call time so REVIEW_DB can change per process, test, or container."""
    return Path(os.getenv("REVIEW_DB", DEFAULT_DB))


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    path = db_path()
    with _init_lock:
        if path not in _initialized or not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            init = sqlite3.connect(path)
            try:
                init.executescript(_SCHEMA)
            finally:
                init.close()
            _initialized.add(path)
    conn = sqlite3.connect(path, timeout=10)
    try:
        with conn:  # commits on success, rolls back on error
            yield conn
    finally:
        conn.close()


def save_review(request: ReviewRequest, result: ReviewResult) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO reviews
            (request_id, created_at, request_json, result_json, recommendation, score)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                result.request_id,
                time.time(),
                request.model_dump_json(),
                result.model_dump_json(),
                result.recommendation,
                result.score,
            ),
        )


def get_review(request_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT result_json FROM reviews WHERE request_id = ?",
            (request_id,),
        ).fetchone()
    return json.loads(row[0]) if row else None


def save_checkpoint(thread_id: str, state: dict[str, Any]) -> None:
    """Persist the latest review state for a conversation/thread id (last write wins)."""
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO checkpoints (thread_id, updated_at, state_json) VALUES (?, ?, ?)",
            (thread_id, time.time(), json.dumps(state, default=str)),
        )


def load_checkpoint(thread_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute("SELECT state_json FROM checkpoints WHERE thread_id = ?", (thread_id,)).fetchone()
    return json.loads(row[0]) if row else None


def list_reviews(limit: int = 20) -> list[dict[str, Any]]:
    """Newest first: id, timestamp, recommendation, score, summary, finding count."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT request_id, created_at, recommendation, score,
                   json_extract(result_json, '$.summary'),
                   json_array_length(json_extract(result_json, '$.findings'))
            FROM reviews ORDER BY created_at DESC, rowid DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [
        {
            "request_id": r[0],
            "created_at": r[1],
            "recommendation": r[2],
            "score": r[3],
            "summary": r[4],
            "findings": r[5] or 0,
        }
        for r in rows
    ]
