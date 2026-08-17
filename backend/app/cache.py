from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class CacheLookupResult:
    payload: dict | None
    status: str


class SqliteResponseCache:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._lock = threading.Lock()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS source_cache (
                    cache_key TEXT NOT NULL,
                    source TEXT NOT NULL,
                    response_json TEXT,
                    created_at INTEGER NOT NULL,
                    PRIMARY KEY (cache_key, source)
                )
                """
            )
            conn.commit()

    def _build_key(self, doi: str, title: str) -> str:
        norm_doi = (doi or "").strip().lower()
        norm_title = " ".join((title or "").lower().split())
        return f"doi={norm_doi}|title={norm_title}"

    def get(self, source: str, doi: str, title: str) -> CacheLookupResult:
        cache_key = self._build_key(doi=doi, title=title)
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT response_json FROM source_cache WHERE cache_key = ? AND source = ?",
                    (cache_key, source),
                ).fetchone()
        if not row:
            return CacheLookupResult(payload=None, status="miss")
        payload = json.loads(row["response_json"]) if row["response_json"] else None
        return CacheLookupResult(payload=payload, status="hit")

    def set(self, source: str, doi: str, title: str, payload: dict | None) -> None:
        cache_key = self._build_key(doi=doi, title=title)
        encoded = json.dumps(payload) if payload is not None else ""
        now_ts = int(time.time())
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO source_cache (cache_key, source, response_json, created_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(cache_key, source)
                    DO UPDATE SET response_json = excluded.response_json, created_at = excluded.created_at
                    """,
                    (cache_key, source, encoded, now_ts),
                )
                conn.commit()
