"""Durable review history and lightweight checkpoint store."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from schemas.models import ReviewRequest, ReviewResult

_DEFAULT = Path(os.getenv("REVIEW_DB", ".review/reviews.db"))


def _conn() -> sqlite3.Connection:
    _DEFAULT.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_DEFAULT)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS reviews (
            request_id TEXT PRIMARY KEY,
            created_at REAL NOT NULL,
            request_json TEXT NOT NULL,
            result_json TEXT NOT NULL,
            recommendation TEXT,
            score REAL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS checkpoints (
            thread_id TEXT PRIMARY KEY,
            updated_at REAL NOT NULL,
            state_json TEXT NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def save_review(request: ReviewRequest, result: ReviewResult) -> None:
    with _conn() as conn:
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
    with _conn() as conn:
        row = conn.execute(
            "SELECT result_json FROM reviews WHERE request_id = ?",
            (request_id,),
        ).fetchone()
    if not row:
        return None
    return json.loads(row[0])


def save_checkpoint(thread_id: str, state: dict[str, Any]) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO checkpoints (thread_id, updated_at, state_json) VALUES (?, ?, ?)",
            (thread_id, time.time(), json.dumps(state, default=str)),
        )


def load_checkpoint(thread_id: str) -> dict[str, Any] | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT state_json FROM checkpoints WHERE thread_id = ?",
            (thread_id,),
        ).fetchone()
    if not row:
        return None
    return json.loads(row[0])
