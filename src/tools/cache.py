"""
Tiny SQLite response cache for Google API calls.

Why it exists:
  1. Cost control - dev iterations hammer the same queries; cached responses
     do not re-bill against the per-SKU free tiers.
  2. Speed - cache hits are sub-millisecond, which helps the <60 s budget.
  3. Demo insurance - a warmed cache survives flaky venue Wi-Fi.

Disable with FOODIE_CACHE=off. Entries expire after TTL_HOURS (opening hours
and ratings drift slowly; 24 h is fine for a hackathon).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time

from .. import config

TTL_HOURS = 24


def _conn() -> sqlite3.Connection:
    con = sqlite3.connect(config.CACHE_DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS api_cache(
        key TEXT PRIMARY KEY, value TEXT, created_at REAL)""")
    return con


def make_key(*parts) -> str:
    blob = json.dumps(parts, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def get(key: str) -> dict | None:
    if not config.CACHE_ENABLED:
        return None
    con = _conn()
    row = con.execute("SELECT value, created_at FROM api_cache WHERE key = ?",
                      (key,)).fetchone()
    con.close()
    if row is None:
        return None
    value, created_at = row
    if time.time() - created_at > TTL_HOURS * 3600:
        return None
    return json.loads(value)


def put(key: str, value: dict) -> None:
    if not config.CACHE_ENABLED:
        return
    con = _conn()
    con.execute("INSERT OR REPLACE INTO api_cache VALUES(?,?,?)",
                (key, json.dumps(value, default=str), time.time()))
    con.commit()
    con.close()
