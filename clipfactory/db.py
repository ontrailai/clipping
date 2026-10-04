"""SQLite state: what we've found, what we've clipped, what's queued, what's posted.

Lifecycle:
  sources:  new -> analyzed | skipped | failed
  moments:  candidate -> selected -> rendered | rejected | failed
  clips:    rendered -> approved | pending_review | rejected -> published | failed
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
  id INTEGER PRIMARY KEY,
  platform TEXT NOT NULL,
  external_id TEXT NOT NULL,
  creator TEXT NOT NULL,
  title TEXT,
  url TEXT NOT NULL,
  published_at TEXT,
  duration REAL,
  kind TEXT,                -- vod | video
  status TEXT NOT NULL DEFAULT 'new',
  error TEXT,
  meta TEXT,                -- JSON: community clips, game info, ...
  created_at TEXT NOT NULL,
  UNIQUE(platform, external_id)
);
CREATE TABLE IF NOT EXISTS moments (
  id INTEGER PRIMARY KEY,
  source_id INTEGER NOT NULL REFERENCES sources(id),
  window_start REAL NOT NULL,
  window_end REAL NOT NULL,
  local_path TEXT,          -- downloaded section
  local_offset REAL,        -- global time of local t=0
  clip_start REAL,          -- local seconds chosen by the judge
  clip_end REAL,
  score REAL,
  category TEXT,
  hook TEXT,
  title TEXT,
  summary TEXT,
  reason TEXT,
  signals TEXT,             -- JSON
  words TEXT,               -- JSON word timestamps (local)
  status TEXT NOT NULL DEFAULT 'candidate',
  error TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS clips (
  id INTEGER PRIMARY KEY,
  moment_id INTEGER NOT NULL REFERENCES moments(id),
  creator TEXT NOT NULL,
  path TEXT NOT NULL,
  cover_path TEXT,
  duration REAL,
  score REAL,
  copy TEXT,                -- JSON captions per platform
  qc TEXT,                  -- JSON QC report
  status TEXT NOT NULL DEFAULT 'rendered',
  created_at TEXT NOT NULL,
  published_at TEXT
);
CREATE TABLE IF NOT EXISTS posts (
  id INTEGER PRIMARY KEY,
  clip_id INTEGER NOT NULL REFERENCES clips(id),
  platform TEXT NOT NULL,
  remote_id TEXT,
  url TEXT,
  status TEXT NOT NULL,     -- published | failed
  error TEXT,
  created_at TEXT NOT NULL,
  UNIQUE(clip_id, platform)
);
CREATE TABLE IF NOT EXISTS slots (
  slot_key TEXT PRIMARY KEY, -- 2026-11-19T12:00 (local)
  clip_id INTEGER REFERENCES clips(id),
  filled_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kv (
  key TEXT PRIMARY KEY,
  value TEXT
);
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None = None) -> str:
    return (dt or utcnow()).astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class DB:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self.conn = sqlite3.connect(self.path, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # ------------------------------------------------------------------ generic
    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        cur = self.conn.execute(sql, tuple(params))
        self.conn.commit()
        return cur

    def all(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        return [dict(r) for r in self.conn.execute(sql, tuple(params)).fetchall()]

    def one(self, sql: str, params: Iterable[Any] = ()) -> dict | None:
        row = self.conn.execute(sql, tuple(params)).fetchone()
        return dict(row) if row else None

    def update(self, table: str, row_id: int, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        vals = [json.dumps(v) if isinstance(v, (dict, list)) else v for v in fields.values()]
        self.execute(f"UPDATE {table} SET {cols} WHERE id = ?", [*vals, row_id])

    # ------------------------------------------------------------------ kv
    def kv_get(self, key: str, default: str | None = None) -> str | None:
        row = self.one("SELECT value FROM kv WHERE key = ?", [key])
        return row["value"] if row else default

    def kv_set(self, key: str, value: str) -> None:
        self.execute("INSERT INTO kv(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", [key, value])

    # ------------------------------------------------------------------ sources
    def add_source(
        self,
        platform: str,
        external_id: str,
        creator: str,
        title: str,
        url: str,
        published_at: str | None,
        duration: float | None,
        kind: str,
        meta: dict | None = None,
    ) -> int | None:
        """Insert a source; returns its id, or None if we've already seen it."""
        try:
            cur = self.execute(
                "INSERT INTO sources(platform, external_id, creator, title, url, published_at, duration, kind, meta, created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                [platform, external_id, creator, title, url, published_at, duration, kind, json.dumps(meta or {}), iso()],
            )
            return cur.lastrowid
        except sqlite3.IntegrityError:
            return None

    def pending_sources(self) -> list[dict]:
        return self.all("SELECT * FROM sources WHERE status = 'new' ORDER BY published_at DESC")

    # ------------------------------------------------------------------ moments
    def add_moment(self, source_id: int, window_start: float, window_end: float, signals: dict) -> int:
        cur = self.execute(
            "INSERT INTO moments(source_id, window_start, window_end, signals, created_at) VALUES(?,?,?,?,?)",
            [source_id, window_start, window_end, json.dumps(signals), iso()],
        )
        return cur.lastrowid

    # ------------------------------------------------------------------ clips
    def add_clip(self, moment_id: int, creator: str, path: str, cover_path: str | None, duration: float, score: float) -> int:
        cur = self.execute(
            "INSERT INTO clips(moment_id, creator, path, cover_path, duration, score, created_at) VALUES(?,?,?,?,?,?,?)",
            [moment_id, creator, path, cover_path, duration, score, iso()],
        )
        return cur.lastrowid

    def clips(self, status: str) -> list[dict]:
        return self.all("SELECT * FROM clips WHERE status = ? ORDER BY score DESC, created_at DESC", [status])

    def clip(self, clip_id: int) -> dict | None:
        return self.one("SELECT * FROM clips WHERE id = ?", [clip_id])

    def record_post(self, clip_id: int, platform: str, status: str, remote_id: str | None = None, url: str | None = None, error: str | None = None) -> None:
        self.execute(
            "INSERT INTO posts(clip_id, platform, remote_id, url, status, error, created_at) VALUES(?,?,?,?,?,?,?)"
            " ON CONFLICT(clip_id, platform) DO UPDATE SET remote_id=excluded.remote_id, url=excluded.url,"
            " status=excluded.status, error=excluded.error, created_at=excluded.created_at",
            [clip_id, platform, remote_id, url, status, error, iso()],
        )

    def posts_for(self, clip_id: int) -> list[dict]:
        return self.all("SELECT * FROM posts WHERE clip_id = ?", [clip_id])

    # ------------------------------------------------------------------ slots
    def slot_filled(self, slot_key: str) -> bool:
        return self.one("SELECT 1 FROM slots WHERE slot_key = ?", [slot_key]) is not None

    def fill_slot(self, slot_key: str, clip_id: int | None) -> None:
        self.execute("INSERT OR REPLACE INTO slots(slot_key, clip_id, filled_at) VALUES(?,?,?)", [slot_key, clip_id, iso()])

    def last_post_time(self) -> datetime | None:
        row = self.one("SELECT MAX(filled_at) AS t FROM slots WHERE clip_id IS NOT NULL")
        return parse_iso(row["t"]) if row and row["t"] else None


def loads(value: str | None, default: Any = None) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default
