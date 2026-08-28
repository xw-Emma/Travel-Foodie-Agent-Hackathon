"""
Tiny SQLite response cache for Google API calls.

Why it exists:
  1. Cost control - dev iterations hammer the same queries; cached responses
     do not re-bill against the per-SKU free tiers.
  2. Speed - cache hits are sub-millisecond, which helps the <60 s budget.
  3. Demo insurance - a warmed cache survives flaky venue Wi-Fi.

Disable with FOODIE_CACHE=off.

TWO TTLs, on purpose. Ordinary entries expire after TTL_HOURS, because ratings
and opening hours drift. Entries written by scripts/warm_cache.py are PINNED and
do not expire: the demo-insurance instruction is "warm the cache before demo
day", and with a single 24 h TTL a cache warmed on Monday was already dead by a
Thursday demo - measured at 73% expired on a real machine. Insurance that
silently lapses is worse than none, because you think you have it.
"""
from __future__ import annotations

import contextlib
import contextvars
import hashlib
import json
import sqlite3
import time

from .. import config

TTL_HOURS = 24
# Set while scripts/warm_cache.py runs, so everything the ordinary code path
# writes during a warm-up is pinned without every backend needing to know that
# warming is a thing.
_PIN_WRITES: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "pin_cache_writes", default=False)


@contextlib.contextmanager
def pin_writes():
    """Mark every cache write inside this block as demo insurance."""
    token = _PIN_WRITES.set(True)
    try:
        yield
    finally:
        _PIN_WRITES.reset(token)
# How long a pinned (warmed) entry stays valid. Long enough to cover a whole
# event week, short enough that a stale plan cannot be demoed months later.
PINNED_TTL_HOURS = 24 * 30

_PINNED = 1
_NORMAL = 0


def _conn() -> sqlite3.Connection:
    # busy_timeout: Tier 2 runs its executors through asyncio.to_thread, so
    # several threads write concurrently. WAL lets readers and a writer coexist
    # instead of serialising on a whole-file lock.
    con = sqlite3.connect(config.CACHE_DB_PATH, timeout=10)
    con.execute("PRAGMA busy_timeout = 10000")
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA synchronous = NORMAL")
    con.execute("""CREATE TABLE IF NOT EXISTS api_cache(
        key TEXT PRIMARY KEY, value TEXT, created_at REAL)""")
    # Added after the table shipped, so it has to be tolerated as missing.
    columns = {row[1] for row in con.execute("PRAGMA table_info(api_cache)")}
    if "pinned" not in columns:
        con.execute("ALTER TABLE api_cache ADD COLUMN pinned INTEGER DEFAULT 0")
        con.commit()
    return con


def make_key(*parts) -> str:
    blob = json.dumps(parts, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _ttl_seconds(pinned: int | None) -> float:
    return (PINNED_TTL_HOURS if pinned else TTL_HOURS) * 3600


def get(key: str) -> dict | None:
    if not config.CACHE_ENABLED:
        return None
    con = _conn()
    row = con.execute(
        "SELECT value, created_at, pinned FROM api_cache WHERE key = ?",
        (key,)).fetchone()
    con.close()
    if row is None:
        return None
    value, created_at, pinned = row
    if time.time() - created_at > _ttl_seconds(pinned):
        return None
    return json.loads(value)


def put(key: str, value: dict, pinned: bool = False) -> None:
    """Store a response. pinned=True marks it as demo insurance (long TTL).

    A pinned entry is never demoted by a later ordinary write, so a normal run
    after warming cannot quietly shorten the demo cache's life.
    """
    if not config.CACHE_ENABLED:
        return
    pinned = pinned or _PIN_WRITES.get()
    con = _conn()
    keep_pinned = pinned
    if not pinned:
        row = con.execute("SELECT pinned FROM api_cache WHERE key = ?",
                          (key,)).fetchone()
        keep_pinned = bool(row and row[0])
    con.execute("INSERT OR REPLACE INTO api_cache VALUES(?,?,?,?)",
                (key, json.dumps(value, default=str), time.time(),
                 _PINNED if keep_pinned else _NORMAL))
    con.commit()
    con.close()


def prune(vacuum: bool = False) -> int:
    """Delete expired rows and return how many went.

    The TTL was only ever applied on read, so expired rows accumulated forever
    and the file only grew.
    """
    con = _conn()
    now = time.time()
    cursor = con.execute(
        "DELETE FROM api_cache WHERE (pinned = 0 AND ? - created_at > ?) "
        "OR (pinned = 1 AND ? - created_at > ?)",
        (now, TTL_HOURS * 3600, now, PINNED_TTL_HOURS * 3600))
    removed = cursor.rowcount or 0
    con.commit()
    if vacuum:
        con.execute("VACUUM")
    con.close()
    return removed


def stats() -> dict:
    """What the cache holds, for the diagnostics panel.

    A verification panel has to be able to say WHEN the data behind a claim was
    fetched; "verified" against a two-day-old rating means something different
    from "verified" a minute ago.
    """
    if not config.CACHE_DB_PATH.exists():
        return {"entries": 0, "pinned": 0, "expired": 0, "oldest_hours": None,
                "size_kb": 0, "enabled": config.CACHE_ENABLED}
    con = _conn()
    now = time.time()
    total = con.execute("SELECT COUNT(*) FROM api_cache").fetchone()[0]
    pinned = con.execute("SELECT COUNT(*) FROM api_cache WHERE pinned = 1").fetchone()[0]
    expired = con.execute(
        "SELECT COUNT(*) FROM api_cache WHERE (pinned = 0 AND ? - created_at > ?) "
        "OR (pinned = 1 AND ? - created_at > ?)",
        (now, TTL_HOURS * 3600, now, PINNED_TTL_HOURS * 3600)).fetchone()[0]
    oldest = con.execute("SELECT MIN(created_at) FROM api_cache").fetchone()[0]
    newest = con.execute("SELECT MAX(created_at) FROM api_cache").fetchone()[0]
    con.close()
    return {
        "entries": total,
        "pinned": pinned,
        "expired": expired,
        "oldest_hours": round((now - oldest) / 3600, 1) if oldest else None,
        "newest_hours": round((now - newest) / 3600, 1) if newest else None,
        "size_kb": config.CACHE_DB_PATH.stat().st_size // 1024,
        "enabled": config.CACHE_ENABLED,
        "ttl_hours": TTL_HOURS,
        "pinned_ttl_hours": PINNED_TTL_HOURS,
    }
