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


def estimate_travel(from_lat: float, from_lon: float,
                    to_lat: float, to_lon: float, mode: str = "walk") -> dict:
    if not config.GOOGLE_MAPS_API_KEY:
        raise RoutesError("GOOGLE_MAPS_API_KEY not set")

    travel_mode = "WALK" if mode == "walk" else "DRIVE"
    payload = {
        "origin": {"location": {"latLng": {"latitude": from_lat, "longitude": from_lon}}},
        "destination": {"location": {"latLng": {"latitude": to_lat, "longitude": to_lon}}},
        "travelMode": travel_mode,
        "computeAlternativeRoutes": False,
        "languageCode": "en-US",
        "units": "METRIC",
    }
    key = cache.make_key("routes_v2_polyline", payload)
    hit = cache.get(key)
    if hit is not None:
        hit["_cache_hit"] = True
        return hit

    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": config.GOOGLE_MAPS_API_KEY,
        "X-Goog-FieldMask": "routes.duration,routes.distanceMeters,routes.polyline.encodedPolyline",
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
    # duration is a string like "123s"
    dur = r0.get("duration") or "0s"
    seconds = float(str(dur).rstrip("s") or 0)
    meters = float(r0.get("distanceMeters") or 0)
    result = {
        "mode": mode,
        "km": round(meters / 1000.0, 2),
        "minutes": round(seconds / 60.0, 1),
        "polyline": (r0.get("polyline") or {}).get("encodedPolyline"),
        "source": "google_routes",
    }
    cache.put(key, result)
    return result
