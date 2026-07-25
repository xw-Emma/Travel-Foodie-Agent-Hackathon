"""
LOCAL backend - the pre-staged offline dataset (mandatory fallback).

This is the demo-day insurance: if Wi-Fi, VPN, or API quota fails, the whole
pipeline runs against data/foodie.sqlite with the exact same tool signatures.
It is also the graded ground truth for the allergen-trap scenario, because
dietary flags here are explicit (Google Places has no allergen fields).

Build the database first:  python data/seed.py
"""
from __future__ import annotations

import json
import math
import sqlite3

from .. import config


def _conn() -> sqlite3.Connection:
    if not config.DB_PATH.exists():
        raise FileNotFoundError(
            f"{config.DB_PATH} not found - run `python data/seed.py` first.")
    con = sqlite3.connect(config.DB_PATH)
    con.row_factory = sqlite3.Row
    return con


# ---------------------------------------------------------------- restaurants
# Broad cuisine buckets so S2 "asian" matches thai/korean/etc. in the seed DB.
CUISINE_ALIASES = {
    "asian": {"thai", "korean", "chinese", "vietnamese", "japanese", "indian",
              "malaysian", "indonesian", "asian"},
    "international": {"international", "fusion", "contemporary"},
}


def search_restaurants(city: str, meal: str, area: str | None = None,
                       cuisine: str | None = None,
                       price_level_max: int | None = None,
                       exclude_flags: list[str] | None = None,
                       limit: int = 5) -> list[dict]:
    """
    Ranked by rating. `exclude_flags` (e.g. ["peanut_risk"]) drops flagged
    venues IN CODE - the allergen hard constraint is physically enforced at
    the data layer, not just in the prompt.
    """
    q = "SELECT * FROM restaurants WHERE lower(city) = lower(?)"
    params: list = [city]
    if area:
        q += " AND lower(area) = lower(?)"
        params.append(area)
    if price_level_max:
        q += " AND price_level <= ?"
        params.append(price_level_max)
    q += " ORDER BY rating DESC, review_count DESC LIMIT ?"
    params.append(limit * 8)

    con = _conn()
    rows = [dict(r) for r in con.execute(q, params).fetchall()]
    con.close()

    wanted = None
    if cuisine:
        key = cuisine.lower().strip()
        wanted = CUISINE_ALIASES.get(key, {key})

    exclude = set(exclude_flags or [])
    out = []
    for r in rows:
        if wanted and r["cuisine"].lower() not in wanted:
            continue
        flags = json.loads(r.get("dietary_flags") or "{}")
        if any(flags.get(f) for f in exclude):
            continue  # hard exclusion
        out.append({
            "venue_id": r["venue_id"], "name": r["name"], "cuisine": r["cuisine"],
            "price_level": r["price_level"], "avg_meal_cost": r["avg_meal_cost"],
            "rating": r["rating"], "review_count": r["review_count"],
            "area": r.get("area"), "lat": r["lat"], "lon": r["lon"],
            "dietary_flags": flags, "kid_friendly": bool(r.get("kid_friendly")),
            "source": "local_dataset",
        })
        if len(out) >= limit:
            break

    # Soft fallback: if cuisine filter emptied the list, return unfiltered
    # (still allergen-safe) so demos never go blank on sparse seed data.
    if cuisine and not out:
        return search_restaurants(city, meal, area=area, cuisine=None,
                                  price_level_max=price_level_max,
                                  exclude_flags=exclude_flags, limit=limit)
    return out


def get_venue_details(venue_id: str) -> dict:
    con = _conn()
    r = con.execute("SELECT * FROM restaurants WHERE venue_id = ?",
                    (venue_id,)).fetchone()
    if r is None:
        r = con.execute("SELECT * FROM attractions WHERE venue_id = ?",
                        (venue_id,)).fetchone()
    con.close()
    if r is None:
        return {"error": f"venue {venue_id} not found in local dataset"}
    d = dict(r)
    d["hours"] = json.loads(d.get("hours") or "{}")
    if "dietary_flags" in d.keys():
        d["dietary_flags"] = json.loads(d.get("dietary_flags") or "{}")
    d["source"] = "local_dataset"
    return d


# ---------------------------------------------------------------- attractions
def search_attractions(city: str, category: str | None = None,
                       limit: int = 5) -> list[dict]:
    q = "SELECT * FROM attractions WHERE lower(city) = lower(?)"
    params: list = [city]
    if category:
        q += " AND lower(category) = lower(?)"
        params.append(category)
    q += " ORDER BY rating DESC LIMIT ?"
    params.append(limit)
    con = _conn()
    rows = con.execute(q, params).fetchall()
    con.close()
    return [{
        "venue_id": r["venue_id"], "name": r["name"], "category": r["category"],
        "cost": r["cost"], "rating": r["rating"],
        "visit_duration_min": r["visit_duration_min"],
        "lat": r["lat"], "lon": r["lon"], "kid_friendly": bool(r["kid_friendly"]),
        "source": "local_dataset",
    } for r in rows]


# --------------------------------------------------------------- travel tool
def estimate_travel(from_lat: float, from_lon: float,
                    to_lat: float, to_lon: float, mode: str = "walk") -> dict:
    """
    Haversine straight-line distance with a 1.4x street-grid detour factor.
    Teaching point: a "tool" is any callable capability - this one needs no
    API at all, yet fills the same contract as the live Routes API backend.
    """
    R = 6371.0
    p1, p2 = math.radians(from_lat), math.radians(to_lat)
    dphi = math.radians(to_lat - from_lat)
    dlmb = math.radians(to_lon - from_lon)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    straight_km = 2 * R * math.asin(math.sqrt(a))
    route_km = straight_km * 1.4
    speed_kmh = {"walk": 4.5, "drive": 25.0}.get(mode, 4.5)
    return {"mode": mode, "km": round(route_km, 2),
            "minutes": round(route_km / speed_kmh * 60, 1),
            "source": "haversine_fallback"}


def is_open_at(details: dict, weekday: str, hhmm: str) -> bool | None:
    """Check local-dataset hours ({'mon': {'open': '08:00', 'close': '22:00'}}).
    Returns None when hours are unknown (live-mode shapes differ)."""
    hours = (details or {}).get("hours") or {}
    day = hours.get(weekday[:3].lower())
    if not isinstance(day, dict):
        return None
    o, c = day.get("open"), day.get("close")
    if not o or not c:
        return False  # closed that day
    return o <= hhmm <= c
