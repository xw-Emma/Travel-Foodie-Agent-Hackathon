"""
LIVE backend — Google Places API (New).

Uses Text Search + Place Details with field masks so calls stay in the
cheapest SKU band that still returns ratings / hours / location.

IMPORTANT: Places has NO allergen fields. We apply a conservative cuisine
heuristic from config.ALLERGEN_RISK_CUISINES and always attach
verify_with_restaurant=True. Graded allergen-trap scenarios must run against
the local dataset (explicit peanut_risk flags).
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from .. import config
from ..geo import bounding_box, haversine_km
from . import cache


class PlacesError(RuntimeError):
    pass


def _headers(field_mask: str) -> dict:
    if not config.GOOGLE_MAPS_API_KEY:
        raise PlacesError(
            "GOOGLE_MAPS_API_KEY not set. Put the per-team restricted key in "
            ".env (see .env.example).")
    return {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": config.GOOGLE_MAPS_API_KEY,
        "X-Goog-FieldMask": field_mask,
    }


def _post(url: str, payload: dict, field_mask: str) -> dict:
    key = cache.make_key("places", url, payload, field_mask)
    hit = cache.get(key)
    if hit is not None:
        hit["_cache_hit"] = True
        return hit
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), method="POST",
        headers=_headers(field_mask))
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise PlacesError(
            f"Places HTTP {e.code}: {e.read().decode('utf-8', 'ignore')[:300]}") from e
    cache.put(key, data)
    return data


def _price_level_int(pl: str | int | None) -> int:
    if isinstance(pl, int):
        return pl
    mapping = {
        "PRICE_LEVEL_FREE": 0, "PRICE_LEVEL_INEXPENSIVE": 1,
        "PRICE_LEVEL_MODERATE": 2, "PRICE_LEVEL_EXPENSIVE": 3,
        "PRICE_LEVEL_VERY_EXPENSIVE": 4,
    }
    return mapping.get(pl or "", 2)


def _infer_flags(cuisine: str, allergies: list[str] | None) -> dict:
    cuisine_l = (cuisine or "").lower()
    flags = {}
    for allergen, risky in config.ALLERGEN_RISK_CUISINES.items():
        flags[f"{allergen}_risk"] = any(r in cuisine_l for r in risky)
    return flags


def _meal_cost(price_level: int) -> float:
    return config.PRICE_LEVEL_MEAL_COST.get(price_level, 30.0)


def _location_restriction(near: tuple[float, float] | None,
                          within_km: float | None) -> dict:
    """Narrow the search at the API level rather than filtering afterwards.

    Text Search takes locationRestriction as a RECTANGLE only. A circle - which
    is what Nearby Search and locationBias accept - is rejected outright with
    HTTP 400 "Unknown name \"circle\"", so the radius becomes a bounding box.
    The corners reach past the radius, so _within_radius still trims the result
    to true distance and the live backend matches the offline one.
    """
    if near is None or within_km is None:
        return {}
    low_lat, low_lon, high_lat, high_lon = bounding_box(
        near[0], near[1], max(0.1, min(float(within_km), 50.0)))
    return {"locationRestriction": {"rectangle": {
        "low": {"latitude": low_lat, "longitude": low_lon},
        "high": {"latitude": high_lat, "longitude": high_lon},
    }}}


def _within_radius(rows: list[dict], near: tuple[float, float] | None,
                   within_km: float | None) -> list[dict]:
    """Trim a rectangle result back to the circle the caller actually asked for,
    tagging each row with distance_km and ordering nearest first - the same
    contract local_catalog returns."""
    if near is None:
        return rows
    for row in rows:
        if row.get("lat") is None:
            continue
        row["distance_km"] = round(
            haversine_km(near[0], near[1], row["lat"], row["lon"]), 2)
    kept = [row for row in rows if row.get("distance_km") is not None
            and (within_km is None or row["distance_km"] <= within_km)]
    kept.sort(key=lambda row: (row["distance_km"], -float(row.get("rating") or 0)))
    return kept


def _neighborhood(components: list[dict] | None) -> str | None:
    """Best available neighbourhood, or None.

    Measured against real Lisbon data: many venues carry no sublocality or
    neighborhood component at all, only a locality. None means unknown and must
    stay unknown - a district name invented to fill the gap is a fabricated
    fact about a real business.
    """
    if not components:
        return None
    wanted = ("neighborhood", "sublocality_level_1", "sublocality")
    for kind in wanted:
        for component in components:
            if kind in (component.get("types") or []):
                return component.get("longText")
    return None


DAYS_FROM_SUNDAY = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")


def is_open_at(hours: dict, weekday: str, hhmm: str) -> bool | None:
    """Opening-hours check against Google's regularOpeningHours shape.

    Google returns `periods` with numeric weekdays (0 = Sunday) and separate
    open/close times; the offline dataset uses {"mon": {"open", "close"}}. The
    two are unrelated, which is why the local checker answered None here and
    live plans went out with their opening hours never checked at all.
    """
    periods = (hours or {}).get("periods")
    if not periods:
        return None
    try:
        target_day = DAYS_FROM_SUNDAY.index(str(weekday)[:3].lower())
        hour, minute = (int(part) for part in str(hhmm).split(":"))
    except (ValueError, IndexError):
        return None
    target = hour * 60 + minute
    open_that_day = False
    for period in periods:
        start, end = period.get("open") or {}, period.get("close") or {}
        if start.get("day") != target_day:
            continue
        open_that_day = True
        start_minutes = int(start.get("hour", 0)) * 60 + int(start.get("minute", 0))
        if not end:
            return True   # open 24h from this point
        end_minutes = int(end.get("hour", 0)) * 60 + int(end.get("minute", 0))
        if end.get("day") != start.get("day"):
            end_minutes += 24 * 60   # closes after midnight
        if start_minutes <= target <= end_minutes:
            return True
    return False if open_that_day or periods else None


def classify_place(query: str) -> dict:
    """Ask Places what kind of thing a name refers to.

    Used to catch a country typed into the city box. "Portugal" builds the text
    query "restaurant dinner in Portugal", which returns whatever Google feels
    like across a whole nation - that is how a venue literally named "Restaurant
    International" ended up in a Lisbon itinerary.
    """
    data = _post(f"{config.PLACES_BASE_URL}/places:searchText",
                 {"textQuery": query, "maxResultCount": 1},
                 "places.id,places.displayName,places.formattedAddress,places.types")
    places = data.get("places") or []
    if not places:
        return {"kind": "unknown", "name": query, "types": []}
    place = places[0]
    types = place.get("types") or []
    for kind in ("locality", "country", "administrative_area_level_1",
                 "administrative_area_level_2"):
        if kind in types:
            return {"kind": kind, "name": (place.get("displayName") or {}).get("text"),
                    "address": place.get("formattedAddress"), "types": types}
    return {"kind": "other", "name": (place.get("displayName") or {}).get("text"),
            "address": place.get("formattedAddress"), "types": types}


def search_restaurants(city: str, meal: str, area: str | None = None,
                       cuisine: str | None = None,
                       price_level_max: int | None = None,
                       exclude_flags: list[str] | None = None,
                       limit: int = 5,
                       near: tuple[float, float] | None = None,
                       within_km: float | None = None,
                       min_rating: float | None = None,
                       min_reviews: int | None = None) -> list[dict]:
    bits = ["restaurant", meal]
    if cuisine:
        bits.insert(0, cuisine)
    if area:
        bits.append(area)
    bits.append(f"in {city}")
    text_query = " ".join(bits)

    field_mask = ",".join([
        "places.id", "places.displayName", "places.formattedAddress",
        "places.location", "places.rating", "places.userRatingCount",
        "places.priceLevel", "places.types", "places.primaryType",
        "places.addressComponents",
    ])
    data = _post(f"{config.PLACES_BASE_URL}/places:searchText",
                 {"textQuery": text_query, "maxResultCount": max(limit * 2, 8),
                  **_location_restriction(near, within_km)},
                 field_mask)

    exclude = set(exclude_flags or [])
    out = []
    for p in data.get("places") or []:
        cuisine_guess = p.get("primaryType") or (
            (p.get("types") or ["restaurant"])[0])
        flags = _infer_flags(cuisine_guess.replace("_", " "), None)
        if any(flags.get(f) for f in exclude):
            continue
        pl = _price_level_int(p.get("priceLevel"))
        if price_level_max is not None and pl > price_level_max:
            continue
        # rating and userRatingCount are already in the field mask, so this
        # quality gate costs nothing extra - no second call, no bigger SKU.
        rating = float(p.get("rating") or 0)
        reviews = int(p.get("userRatingCount") or 0)
        if min_rating is not None and rating < float(min_rating):
            continue
        if min_reviews is not None and reviews < int(min_reviews):
            continue
        loc = p.get("location") or {}
        name = (p.get("displayName") or {}).get("text") or "Unknown"
        out.append({
            "venue_id": p.get("id") or "",
            "name": name,
            "cuisine": cuisine_guess.replace("_", " "),
            "price_level": pl,
            "avg_meal_cost": _meal_cost(pl),
            "rating": p.get("rating") or 0.0,
            "review_count": p.get("userRatingCount") or 0,
            "area": area,
            "neighborhood": _neighborhood(p.get("addressComponents")),
            "address": p.get("formattedAddress"),
            "lat": loc.get("latitude"),
            "lon": loc.get("longitude"),
            "dietary_flags": flags,
            "verify_with_restaurant": True,  # live allergen uncertainty
            "source": "google_places",
        })
        if len(out) >= limit and near is None:
            break
    return _within_radius(out, near, within_km)[:limit]


def get_venue_details(venue_id: str) -> dict:
    # Places New: GET https://places.googleapis.com/v1/places/{place_id}
    field_mask = ",".join([
        "id", "displayName", "formattedAddress", "location", "rating",
        "userRatingCount", "priceLevel", "regularOpeningHours",
        "primaryType", "types", "reviews", "addressComponents",
        # Reservation facts. These push the call into a higher SKU band, so it
        # is made ONLY for the venues that made the final itinerary - never for
        # a whole candidate pool (20 candidates x 6 slots would be ~33 s).
        "websiteUri", "nationalPhoneNumber",
    ])
    key = cache.make_key("places_details", venue_id, field_mask)
    hit = cache.get(key)
    if hit is not None:
        hit["_cache_hit"] = True
        return hit

    # Places New resource names look like "places/ChIJ...". Accept either form.
    resource = venue_id if str(venue_id).startswith("places/") else f"places/{venue_id}"
    url = f"{config.PLACES_BASE_URL}/{resource}"
    req = urllib.request.Request(url, method="GET", headers=_headers(field_mask))
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            p = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise PlacesError(
            f"Place details HTTP {e.code}: {e.read().decode('utf-8', 'ignore')[:300]}") from e

    cuisine = (p.get("primaryType") or "restaurant").replace("_", " ")
    pl = _price_level_int(p.get("priceLevel"))
    loc = p.get("location") or {}
    details = {
        "venue_id": p.get("id") or venue_id,
        "name": (p.get("displayName") or {}).get("text"),
        "address": p.get("formattedAddress"),
        "lat": loc.get("latitude"), "lon": loc.get("longitude"),
        "rating": p.get("rating") or 0.0,
        "review_count": p.get("userRatingCount") or 0,
        "price_level": pl,
        "avg_meal_cost": _meal_cost(pl),
        "cuisine": cuisine,
        "hours": p.get("regularOpeningHours") or {},
        "neighborhood": _neighborhood(p.get("addressComponents")),
        "website": p.get("websiteUri"),
        "phone": p.get("nationalPhoneNumber"),
        # Already inside the field mask and previously discarded, so this data
        # was being paid for and thrown away.
        "reviews": [{
            "text": ((review.get("text") or {}).get("text") or "").strip(),
            "language": (review.get("text") or {}).get("languageCode"),
            "rating": review.get("rating"),
        } for review in (p.get("reviews") or [])],
        "dietary_flags": _infer_flags(cuisine, None),
        "verify_with_restaurant": True,
        "source": "google_places",
    }
    cache.put(key, details)
    return details


def search_attractions(city: str, category: str | None = None,
                       limit: int = 5,
                       near: tuple[float, float] | None = None,
                       within_km: float | None = None) -> list[dict]:
    q = f"{category or 'tourist attraction'} in {city}"
    field_mask = ",".join([
        "places.id", "places.displayName", "places.formattedAddress",
        "places.location", "places.rating", "places.userRatingCount",
        "places.primaryType",
    ])
    data = _post(f"{config.PLACES_BASE_URL}/places:searchText",
                 {"textQuery": q, "maxResultCount": limit,
                  **_location_restriction(near, within_km)},
                 field_mask)
    out = []
    for p in data.get("places") or []:
        loc = p.get("location") or {}
        out.append({
            "venue_id": p.get("id") or "",
            "name": (p.get("displayName") or {}).get("text"),
            "category": (p.get("primaryType") or category or "attraction").replace("_", " "),
            "cost": 0.0,  # unknown from Places; Critic / Formatter treat as TBD
            "rating": p.get("rating") or 0.0,
            "visit_duration_min": 60,
            "lat": loc.get("latitude"), "lon": loc.get("longitude"),
            "kid_friendly": True,
            "source": "google_places",
        })
    return _within_radius(out, near, within_km)[:limit]
