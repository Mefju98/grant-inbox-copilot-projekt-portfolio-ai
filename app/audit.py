"""Minimal SQLite audit trail that avoids storing inquiry text or extracted IDs."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app.llm import LLMCallResult
from app.schemas import EscalationReason, TriageResult


class AuditStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)

    @classmethod
    def from_env(cls) -> "AuditStore":
        default_path = Path(__file__).resolve().parent.parent / "data" / "audit.sqlite3"
        return cls(os.getenv("AUDIT_DB_PATH", str(default_path)))

    def record_success(self, result: TriageResult, call: LLMCallResult) -> None:
        self._insert(
            inquiry_id=result.inquiry_id,
            outcome="success",
            eligibility=result.eligibility.value,
            escalate=result.escalate,
            escalation_reasons=[reason.value for reason in result.escalation_reasons],
            citation_ids=[citation.document_id for citation in result.citations],
            model=call.model,
            prompt_version=call.prompt_version,
            latency_ms=call.latency_ms,
            attempts=call.attempts,
            input_tokens=call.input_tokens,
            output_tokens=call.output_tokens,
            cost_usd=call.cost_usd,
            error_code=None,
        )

    def record_failure(self, inquiry_id: str, error_code: str, attempts: int) -> None:
        self._insert(
            inquiry_id=inquiry_id,
            outcome="failure",
            eligibility=None,
            escalate=True,
            escalation_reasons=["model_or_validation_error"],
            citation_ids=[],
            model=None,
            prompt_version=None,
            latency_ms=None,
            attempts=attempts,
            input_tokens=None,
            output_tokens=None,
            cost_usd=None,
            error_code=error_code,
        )

    def record_no_match(self, result: TriageResult, latency_ms: int) -> None:
        self._insert(
            inquiry_id=result.inquiry_id,
            outcome="success",
            eligibility=result.eligibility.value,
            escalate=True,
            escalation_reasons=[reason.value for reason in result.escalation_reasons],
            citation_ids=[],
            model=None,
            prompt_version=None,
            latency_ms=latency_ms,
            attempts=0,
            input_tokens=None,
            output_tokens=None,
            cost_usd=None,
            error_code=None,
        )

    def list_records(self) -> list[dict]:
        if not self.db_path.exists():
            return []
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute("SELECT * FROM audit_events ORDER BY audit_id").fetchall()
            return [dict(row) for row in rows]
        finally:
            connection.close()

    def _insert(self, **record) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path, timeout=5)
        try:
            connection.execute("PRAGMA busy_timeout = 5000")
            connection.execute(
                """CREATE TABLE IF NOT EXISTS audit_events (
                    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at_utc TEXT NOT NULL,
                    inquiry_id TEXT NOT NULL,
                    outcome TEXT NOT NULL CHECK(outcome IN ('success', 'failure')),
                    eligibility TEXT,
                    escalate INTEGER NOT NULL CHECK(escalate IN (0, 1)),
                    escalation_reasons_json TEXT NOT NULL,
                    citation_ids_json TEXT NOT NULL,
                    model TEXT,
                    prompt_version TEXT,
                    latency_ms INTEGER,
                    attempts INTEGER NOT NULL,
                    input_tokens INTEGER,
                    output_tokens INTEGER,
                    cost_usd REAL,
                    error_code TEXT
                )"""
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_events_inquiry_id ON audit_events(inquiry_id)"
            )
            connection.execute(
                """INSERT INTO audit_events (
                    created_at_utc, inquiry_id, outcome, eligibility, escalate,
                    escalation_reasons_json, citation_ids_json, model, prompt_version,
                    latency_ms, attempts, input_tokens, output_tokens, cost_usd, error_code
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    datetime.now(timezone.utc).isoformat(),
                    record["inquiry_id"], record["outcome"], record["eligibility"],
                    int(record["escalate"]), json.dumps(record["escalation_reasons"]),
                    json.dumps(record["citation_ids"]), record["model"], record["prompt_version"],
                    record["latency_ms"], record["attempts"], record["input_tokens"],
                    record["output_tokens"], record["cost_usd"], record["error_code"],
                ),
            )
            connection.commit()
        finally:
            connection.close()
