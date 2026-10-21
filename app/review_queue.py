"""Local SQLite queue for synthetic inquiries that require human review."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "review_queue.sqlite3"


class ReviewQueue:
    """Persist the minimum context needed to review a synthetic inquiry locally."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        configured_path = db_path or os.getenv("REVIEW_QUEUE_DB") or DEFAULT_DB_PATH
        self.db_path = Path(configured_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def enqueue(
        self,
        *,
        inquiry_id: str,
        message: str,
        eligibility: str,
        rationale: str,
        escalation_reasons: list[str],
        citations: list[dict[str, str]],
    ) -> None:
        """Add or refresh an escalated inquiry using a generated demo ID."""
        created_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO review_queue (
                    inquiry_id, message, eligibility, rationale, escalation_reasons,
                    citations, status, created_at, reviewed_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, NULL)
                ON CONFLICT(inquiry_id) DO UPDATE SET
                    message = excluded.message,
                    eligibility = excluded.eligibility,
                    rationale = excluded.rationale,
                    escalation_reasons = excluded.escalation_reasons,
                    citations = excluded.citations,
                    status = 'pending',
                    created_at = excluded.created_at,
                    reviewed_at = NULL
                """,
                (
                    inquiry_id,
                    message,
                    eligibility,
                    rationale,
                    json.dumps(escalation_reasons, ensure_ascii=False),
                    json.dumps(citations, ensure_ascii=False),
                    created_at,
                ),
            )

    def list_items(self, *, status: str) -> list[dict[str, Any]]:
        """Return pending or reviewed rows, newest first."""
        if status not in {"pending", "reviewed"}:
            raise ValueError("status must be 'pending' or 'reviewed'")

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT inquiry_id, message, eligibility, rationale, escalation_reasons,
                       citations, status, created_at, reviewed_at
                FROM review_queue
                WHERE status = ?
                ORDER BY created_at DESC
                """,
                (status,),
            ).fetchall()

        return [
            {
                "inquiry_id": row[0],
                "message": row[1],
                "eligibility": row[2],
                "rationale": row[3],
                "escalation_reasons": json.loads(row[4]),
                "citations": json.loads(row[5]),
                "status": row[6],
                "created_at": row[7],
                "reviewed_at": row[8],
            }
            for row in rows
        ]

    def mark_reviewed(self, inquiry_id: str) -> bool:
        """Mark an item reviewed; return False if it no longer exists as pending."""
        reviewed_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE review_queue
                SET status = 'reviewed', reviewed_at = ?
                WHERE inquiry_id = ? AND status = 'pending'
                """,
                (reviewed_at, inquiry_id),
            )
            return cursor.rowcount == 1

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path, timeout=5.0)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS review_queue (
                    inquiry_id TEXT PRIMARY KEY,
                    message TEXT NOT NULL,
                    eligibility TEXT NOT NULL,
                    rationale TEXT NOT NULL,
                    escalation_reasons TEXT NOT NULL,
                    citations TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('pending', 'reviewed')),
                    created_at TEXT NOT NULL,
                    reviewed_at TEXT
                )
                """
            )
