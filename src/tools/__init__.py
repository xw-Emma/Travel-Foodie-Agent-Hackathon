"""
Tool facade — one public API, three backends (live / local / auto).

Agents import tools from here ONLY. They never import places_live,
routes_live, or local_catalog directly. That keeps the Live-API ↔ offline
swap as a one-line config change (FOODIE_DATA_BACKEND).
"""
from __future__ import annotations

from .. import config
from . import budget as _budget
from . import local_catalog as _local
from . import places_live as _places
from . import routes_live as _routes

# Telemetry visible in TripState.meta
_LAST_BACKEND: dict[str, str] = {"restaurants": "n/a", "attractions": "n/a",
                                  "travel": "n/a", "fallback_events": 0}


def last_backend_report() -> dict:
    return dict(_LAST_BACKEND)


def _want_live() -> bool:
    if config.DATA_BACKEND == "local":
        return False
    if config.DATA_BACKEND == "live":
        return True
    return config.LIVE_DATA_AVAILABLE  # auto


def _record(kind: str, source: str, fell_back: bool = False) -> None:
    _LAST_BACKEND[kind] = source
    if fell_back:
        _LAST_BACKEND["fallback_events"] = int(_LAST_BACKEND["fallback_events"]) + 1


# ---------------------------------------------------------------- restaurants
def search_restaurants(city: str, meal: str, area: str | None = None,
                       cuisine: str | None = None,
                       price_level_max: int | None = None,
                       exclude_flags: list[str] | None = None,
                       limit: int = 5) -> list[dict]:
    if _want_live():
        try:
            rows = _places.search_restaurants(
                city, meal, area=area, cuisine=cuisine,
                price_level_max=price_level_max,
                exclude_flags=exclude_flags, limit=limit)
            if rows:
                _record("restaurants", "google_places")
                return rows
            # empty live result in auto mode → fall through
            if config.DATA_BACKEND == "live":
                _record("restaurants", "google_places_empty")
                return rows
        except Exception as exc:  # noqa: BLE001 - demo-day resilience
            if config.DATA_BACKEND == "live":
                raise
            _record("restaurants", f"fallback_after_error:{type(exc).__name__}", True)
    rows = _local.search_restaurants(
        city, meal, area=area, cuisine=cuisine,
        price_level_max=price_level_max,
        exclude_flags=exclude_flags, limit=limit)
    _record("restaurants", "local_dataset", fell_back=_want_live())
    return rows


def get_venue_details(venue_id: str) -> dict:
    # Local IDs are short (r1, a1…). Google place IDs are long.
    if len(venue_id) <= 8 or venue_id.startswith(("r", "a")) and venue_id[1:].isdigit():
        _record("details", "local_dataset")
        return _local.get_venue_details(venue_id)
    if _want_live():
        try:
            d = _places.get_venue_details(venue_id)
            _record("details", "google_places")
            return d
        except Exception as exc:  # noqa: BLE001
            if config.DATA_BACKEND == "live":
                raise
            _record("details", f"fallback_after_error:{type(exc).__name__}", True)
    return _local.get_venue_details(venue_id)


# ---------------------------------------------------------------- attractions
def search_attractions(city: str, category: str | None = None,
                       limit: int = 5) -> list[dict]:
    if _want_live():
        try:
            rows = _places.search_attractions(city, category=category, limit=limit)
            if rows:
                _record("attractions", "google_places")
                return rows
            if config.DATA_BACKEND == "live":
                _record("attractions", "google_places_empty")
                return rows
        except Exception as exc:  # noqa: BLE001
            if config.DATA_BACKEND == "live":
                raise
            _record("attractions", f"fallback_after_error:{type(exc).__name__}", True)
    rows = _local.search_attractions(city, category=category, limit=limit)
    _record("attractions", "local_dataset", fell_back=_want_live())
    return rows


# -------------------------------------------------------------------- travel
def estimate_travel(from_lat: float, from_lon: float,
                    to_lat: float, to_lon: float, mode: str = "walk") -> dict:
    if _want_live():
        try:
            r = _routes.estimate_travel(from_lat, from_lon, to_lat, to_lon, mode=mode)
            _record("travel", "google_routes")
            return r
        except Exception as exc:  # noqa: BLE001
            if config.DATA_BACKEND == "live":
                raise
            _record("travel", f"fallback_after_error:{type(exc).__name__}", True)
    r = _local.estimate_travel(from_lat, from_lon, to_lat, to_lon, mode=mode)
    _record("travel", "haversine_fallback", fell_back=_want_live())
    return r


# -------------------------------------------------------------------- budget
def check_budget(items: list[dict], limit: float) -> dict:
    return _budget.check_budget(items, limit)


# Convenience map for the Fuel iX tool loop
TOOL_IMPLS = {
    "search_restaurants": search_restaurants,
    "get_venue_details": get_venue_details,
    "search_attractions": search_attractions,
    "estimate_travel": estimate_travel,
    "check_budget": check_budget,
}
