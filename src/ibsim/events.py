from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ibsim.models import EventEnvelope


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", by_alias=True)
    return str(value)


class SQLiteEventStore:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        with self.conn:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                  event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                  event_type TEXT NOT NULL,
                  aggregate_id TEXT,
                  ts TEXT NOT NULL,
                  payload TEXT NOT NULL
                )
                """
            )
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type)")
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_events_aggregate ON events(aggregate_id)")

    def append(
        self,
        event_type: str,
        payload: dict[str, Any] | BaseModel | None = None,
        *,
        aggregate_id: str | None = None,
        ts: datetime | None = None,
    ) -> EventEnvelope:
        timestamp = ts or datetime.now(timezone.utc)
        if isinstance(payload, BaseModel):
            payload_dict = payload.model_dump(mode="json", by_alias=True)
        else:
            payload_dict = payload or {}
        body = json.dumps(payload_dict, default=_json_default, sort_keys=True)
        with self.conn:
            cursor = self.conn.execute(
                "INSERT INTO events(event_type, aggregate_id, ts, payload) VALUES (?, ?, ?, ?)",
                (event_type, aggregate_id, timestamp.isoformat(), body),
            )
        return EventEnvelope(
            eventId=int(cursor.lastrowid),
            eventType=event_type,
            aggregateId=aggregate_id,
            ts=timestamp,
            payload=payload_dict,
        )

    def list_events(
        self,
        *,
        event_type: str | None = None,
        aggregate_id: str | None = None,
        limit: int = 1000,
    ) -> list[EventEnvelope]:
        clauses: list[str] = []
        params: list[Any] = []
        if event_type:
            clauses.append("event_type = ?")
            params.append(event_type)
        if aggregate_id:
            clauses.append("aggregate_id = ?")
            params.append(aggregate_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        rows = self.conn.execute(
            f"SELECT * FROM events {where} ORDER BY event_id ASC LIMIT ?",
            params,
        ).fetchall()
        return [
            EventEnvelope(
                eventId=row["event_id"],
                eventType=row["event_type"],
                aggregateId=row["aggregate_id"],
                ts=datetime.fromisoformat(row["ts"]),
                payload=json.loads(row["payload"]),
            )
            for row in rows
        ]
