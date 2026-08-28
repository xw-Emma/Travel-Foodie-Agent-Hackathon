"""
LIVE backend — Google Routes API (computeRoutes).

Replaces legacy Directions / Distance Matrix. Falls through to haversine in
the facade when the key is missing or the call fails (auto mode).
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from .. import config
from . import cache


class RoutesError(RuntimeError):
    pass


# The Routes API rejects anything outside this set. Mapping every non-walk mode
# to DRIVE (as this module used to) silently turned a bike ride or a bus trip
# into a car journey, which is the one number the whole travel constraint reads.
TRAVEL_MODES = {"walk": "WALK", "drive": "DRIVE",
                "transit": "TRANSIT", "bicycle": "BICYCLE"}


def _travel_mode(mode: str) -> str:
    return TRAVEL_MODES.get((mode or "walk").lower(), "WALK")


# Named so the cache key can include it: a key that ignores the field mask
# serves a response missing the fields the caller now asks for.
LEG_FIELD_MASK = ("routes.duration,routes.distanceMeters,"
                  "routes.polyline.encodedPolyline")


def _seconds(duration) -> float:
    """Routes returns duration as a string like "1234s"."""
    return float(str(duration or "0s").rstrip("s") or 0)


def estimate_travel(from_lat: float, from_lon: float,
                    to_lat: float, to_lon: float, mode: str = "walk") -> dict:
    if not config.GOOGLE_MAPS_API_KEY:
        raise RoutesError("GOOGLE_MAPS_API_KEY not set")

    travel_mode = _travel_mode(mode)
    payload = {
        "origin": {"location": {"latLng": {"latitude": from_lat, "longitude": from_lon}}},
        "destination": {"location": {"latLng": {"latitude": to_lat, "longitude": to_lon}}},
        "travelMode": travel_mode,
        "computeAlternativeRoutes": False,
        "languageCode": "en-US",
        "units": "METRIC",
    }
    key = cache.make_key("routes", payload, LEG_FIELD_MASK)
    hit = cache.get(key)
    if hit is not None:
        hit["_cache_hit"] = True
        return hit

    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": config.GOOGLE_MAPS_API_KEY,
        "X-Goog-FieldMask": LEG_FIELD_MASK,
    }
    req = urllib.request.Request(
        config.ROUTES_BASE_URL, data=json.dumps(payload).encode("utf-8"),
        method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RoutesError(
            f"Routes HTTP {e.code}: {e.read().decode('utf-8', 'ignore')[:300]}") from e

    routes = data.get("routes") or []
    if not routes:
        raise RoutesError("Routes API returned no routes")
    r0 = routes[0]
    meters = float(r0.get("distanceMeters") or 0)
    result = {
        "mode": mode,
        "km": round(meters / 1000.0, 2),
        "minutes": round(_seconds(r0.get("duration")) / 60.0, 1),
        "polyline": (r0.get("polyline") or {}).get("encodedPolyline"),
        "source": "google_routes",
    }
    cache.put(key, result)
    return result


def _waypoint(stop: dict) -> dict:
    return {"location": {"latLng": {"latitude": stop["lat"],
                                    "longitude": stop["lon"]}}}


DAY_ROUTE_FIELD_MASK = ",".join([
    "routes.duration", "routes.distanceMeters",
    "routes.polyline.encodedPolyline",
    "routes.optimizedIntermediateWaypointIndex",
    "routes.legs.duration", "routes.legs.distanceMeters",
    "routes.legs.polyline.encodedPolyline",
])


def compute_day_route(origin: dict, stops: list[dict], mode: str = "WALK",
                      optimize: bool = True) -> dict:
    """One Routes call for a whole day, optionally reordering the stops.

    origin / stops entries need 'lat', 'lon', 'name' and (for the orchestrator)
    'slot'. Returns {"order", "legs", "totals", "optimized", "source"}.

    `order` indexes into `stops` in visiting order. When optimize=True it comes
    from Google's optimizedIntermediateWaypointIndex, so no hand-rolled TSP.

    Replacing the per-pair estimate_travel calls with this collapses a day into
    a single request and is the only way to get per-leg geometry in one call.
    """
    if not config.GOOGLE_MAPS_API_KEY:
        raise RoutesError("GOOGLE_MAPS_API_KEY not set")
    points = [s for s in stops if s.get("lat") is not None and s.get("lon") is not None]
    if not points:
        return {"order": [], "legs": [], "totals": {"km": 0.0, "minutes": 0.0},
                "optimized": False, "source": "google_routes"}
    if origin is None or origin.get("lat") is None:
        # No origin: the first stop starts the day and cannot be reordered.
        origin, points = points[0], points[1:]
        leading = [origin]
        if not points:
            return {"order": [0], "legs": [], "totals": {"km": 0.0, "minutes": 0.0},
                    "optimized": False, "source": "google_routes"}
    else:
        leading = []

    # optimizeWaypointOrder only reorders intermediates, so it needs at least
    # two of them to have anything to decide.
    optimize = bool(optimize) and len(points) >= 3
    payload = {
        "origin": _waypoint(origin),
        "destination": _waypoint(points[-1]),
        "intermediates": [_waypoint(s) for s in points[:-1]],
        "travelMode": _travel_mode(mode),
        "optimizeWaypointOrder": optimize,
        "computeAlternativeRoutes": False,
        "languageCode": "en-US",
        "units": "METRIC",
    }
    key = cache.make_key("routes_day", payload, DAY_ROUTE_FIELD_MASK)
    hit = cache.get(key)
    if hit is None:
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": config.GOOGLE_MAPS_API_KEY,
            "X-Goog-FieldMask": DAY_ROUTE_FIELD_MASK,
        }
        req = urllib.request.Request(
            config.ROUTES_BASE_URL, data=json.dumps(payload).encode("utf-8"),
            method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=25) as resp:
                hit = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise RoutesError(
                f"Routes HTTP {e.code}: {e.read().decode('utf-8', 'ignore')[:300]}") from e
        cache.put(key, hit)

    routes = hit.get("routes") or []
    if not routes:
        raise RoutesError("Routes API returned no routes")
    r0 = routes[0]

    # optimizedIntermediateWaypointIndex[i] is the ORIGINAL index of the
    # intermediate now visited i-th. The destination always stays last.
    optimized_index = r0.get("optimizedIntermediateWaypointIndex")
    if optimize and optimized_index:
        visiting = [points[i] for i in optimized_index] + [points[-1]]
    else:
        visiting = list(points)

    index_of = {id(stop): i for i, stop in enumerate(stops)}
    sequence = [origin] + visiting
    legs = []
    for (source, target), leg in zip(zip(sequence, sequence[1:]), r0.get("legs") or []):
        meters = float(leg.get("distanceMeters") or 0)
        legs.append({
            "mode": mode, "km": round(meters / 1000.0, 2),
            "minutes": round(_seconds(leg.get("duration")) / 60.0, 1),
            "polyline": (leg.get("polyline") or {}).get("encodedPolyline"),
            "source": "google_routes",
            "from_slot": source.get("slot"), "to_slot": target.get("slot"),
            "from": source.get("name"), "to": target.get("name"),
        })
    return {
        "order": [index_of[id(stop)] for stop in leading + visiting],
        "legs": legs,
        "totals": {"km": round(float(r0.get("distanceMeters") or 0) / 1000.0, 2),
                   "minutes": round(_seconds(r0.get("duration")) / 60.0, 1)},
        "optimized": bool(optimize and optimized_index),
        "source": "google_routes",
    }
