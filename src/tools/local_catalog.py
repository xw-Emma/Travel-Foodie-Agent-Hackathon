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
import sqlite3

from .. import config
from ..geo import haversine_km
from ..polyline import encode_polyline


# Effective door-to-door speeds, deliberately below vehicle top speeds: city
# driving parks, transit waits. WALK/DRIVE match the original two-mode table.
MODE_SPEED_KMH = {"walk": 4.5, "bicycle": 15.0, "transit": 18.0, "drive": 25.0}


def _speed_kmh(mode: str) -> float:
    return MODE_SPEED_KMH.get((mode or "walk").lower(), MODE_SPEED_KMH["walk"])


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
    "international": {
        "bakery", "bbq", "breakfast", "brunch", "burgers", "cafe", "canadian",
        "chinese", "dessert", "eastern_european", "ethiopian", "french",
        "hawaiian", "indian", "italian", "japanese", "korean", "latin",
        "mediterranean", "mexican", "middle_eastern", "nepalese", "pub",
        "sandwiches", "seafood", "spanish", "steakhouse", "thai", "vegetarian",
        "vietnamese",
    },
}


def search_restaurants(city: str, meal: str, area: str | None = None,
                       cuisine: str | None = None,
                       price_level_max: int | None = None,
                       exclude_flags: list[str] | None = None,
                       limit: int = 5,
                       near: tuple[float, float] | None = None,
                       within_km: float | None = None) -> list[dict]:
    """
    Ranked by rating. `exclude_flags` (e.g. ["peanut_risk"]) drops flagged
    venues IN CODE - the allergen hard constraint is physically enforced at
    the data layer, not just in the prompt.

    `near` anchors the search at a (lat, lon) - normally the previous stop -
    and `within_km` drops anything beyond that radius. Without an anchor the
    Critic can flag a leg as too far but the replacement search has no idea
    where "too far" is measured from, so it cannot converge.
    """
    q = "SELECT * FROM restaurants WHERE lower(city) = lower(?)"
    params: list = [city]
    if meal:
        q += " AND (';' || lower(meal_types) || ';') LIKE '%;' || lower(?) || ';%'"
        params.append(meal)
    if area:
        q += " AND lower(area) = lower(?)"
        params.append(area)
    if price_level_max:
        q += " AND price_level <= ?"
        params.append(price_level_max)
    q += " ORDER BY rating DESC, review_count DESC LIMIT ?"
    # An anchored search re-ranks by distance afterwards, so it needs the whole
    # eligible pool rather than the top slice by rating.
    params.append(10000 if near is not None else limit * 8)

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
        row = {
            "venue_id": r["venue_id"], "name": r["name"], "cuisine": r["cuisine"],
            "price_level": r["price_level"], "avg_meal_cost": r["avg_meal_cost"],
            "rating": r["rating"], "review_count": r["review_count"],
            "area": r.get("area"), "lat": r["lat"], "lon": r["lon"],
            "dietary_flags": flags, "kid_friendly": bool(r.get("kid_friendly")),
            "source": "local_dataset",
        }
        if near is not None:
            row["distance_km"] = round(
                haversine_km(near[0], near[1], r["lat"], r["lon"]), 2)
            if within_km is not None and row["distance_km"] > within_km:
                continue
        out.append(row)
        if near is None and len(out) >= limit:
            break

    # With an anchor, rank by proximity first: the caller is replacing a venue
    # precisely because the current one is too far away.
    if near is not None:
        out.sort(key=lambda row: (row["distance_km"], -float(row["rating"] or 0)))
    return out[:limit]


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
                       limit: int = 5,
                       near: tuple[float, float] | None = None,
                       within_km: float | None = None) -> list[dict]:
    q = "SELECT * FROM attractions WHERE lower(city) = lower(?)"
    params: list = [city]
    if category:
        q += " AND lower(category) = lower(?)"
        params.append(category)
    q += " ORDER BY rating DESC LIMIT ?"
    params.append(10000 if near is not None else limit)
    con = _conn()
    rows = con.execute(q, params).fetchall()
    con.close()
    out = [{
        "venue_id": r["venue_id"], "name": r["name"], "category": r["category"],
        "cost": r["cost"], "rating": r["rating"],
        "visit_duration_min": r["visit_duration_min"],
        "lat": r["lat"], "lon": r["lon"], "kid_friendly": bool(r["kid_friendly"]),
        "source": "local_dataset",
    } for r in rows]
    if near is not None:
        for row in out:
            row["distance_km"] = round(
                haversine_km(near[0], near[1], row["lat"], row["lon"]), 2)
        if within_km is not None:
            out = [row for row in out if row["distance_km"] <= within_km]
        out.sort(key=lambda row: (row["distance_km"], -float(row["rating"] or 0)))
    return out[:limit]


# --------------------------------------------------------------- travel tool
def estimate_travel(from_lat: float, from_lon: float,
                    to_lat: float, to_lon: float, mode: str = "walk") -> dict:
    """
    Haversine straight-line distance with a 1.4x street-grid detour factor.
    Teaching point: a "tool" is any callable capability - this one needs no
    API at all, yet fills the same contract as the live Routes API backend.
    """
    route_km = haversine_km(from_lat, from_lon, to_lat, to_lon)
    return {"mode": mode, "km": round(route_km, 2),
            "minutes": round(route_km / _speed_kmh(mode) * 60, 1),
            "polyline": encode_polyline([(from_lat, from_lon), (to_lat, to_lon)]),
            "source": "haversine_fallback"}


def compute_day_route(origin: dict, stops: list[dict], mode: str = "WALK",
                      optimize: bool = True) -> dict:
    """Offline twin of routes_live.compute_day_route - same returned contract.

    Orders the stops greedily by nearest-neighbour from the origin, which is
    adequate for the four to six stops a day actually has and keeps the core
    dependency-free (no OR-Tools). Legs carry a two-point straight line in
    Google's own polyline encoding so the UI has exactly one decode path.

    `order` indexes into `stops` in visiting order. The origin is not part of
    it - it is where the day starts, not a stop to be scheduled.
    """
    points = [s for s in stops if s.get("lat") is not None and s.get("lon") is not None]
    if not points:
        return {"order": [], "legs": [], "totals": {"km": 0.0, "minutes": 0.0},
                "optimized": False, "source": "haversine_fallback"}

    index_of = {id(stop): i for i, stop in enumerate(stops)}
    # With no origin the first stop anchors the day and cannot move, which is
    # also how routes_live.compute_day_route treats it - the two backends must
    # agree on what "optimized" means or local and live plans diverge.
    if origin and origin.get("lat") is not None:
        anchor, movable, leading = origin, list(points), []
    else:
        anchor, movable, leading = points[0], list(points[1:]), [points[0]]

    if optimize and movable:
        remaining = list(movable)
        current = anchor
        ordered = []
        while remaining:
            nearest = min(remaining, key=lambda s: (
                haversine_km(current["lat"], current["lon"], s["lat"], s["lon"]),
                str(s.get("name", ""))))  # name breaks ties deterministically
            ordered.append(nearest)
            remaining.remove(nearest)
            current = nearest
    else:
        ordered = list(movable)

    ordered = leading + ordered
    sequence = ([origin] + ordered) if origin and origin.get("lat") is not None else ordered
    legs = []
    for source, target in zip(sequence, sequence[1:]):
        leg = estimate_travel(source["lat"], source["lon"],
                              target["lat"], target["lon"], mode=mode)
        legs.append({**leg, "from_slot": source.get("slot"), "to_slot": target.get("slot"),
                     "from": source.get("name"), "to": target.get("name")})
    return {
        "order": [index_of[id(stop)] for stop in ordered],
        "legs": legs,
        "totals": {"km": round(sum(leg["km"] for leg in legs), 2),
                   "minutes": round(sum(leg["minutes"] for leg in legs), 1)},
        "optimized": bool(optimize and len(movable) >= 2),
        "source": "haversine_fallback",
    }


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
