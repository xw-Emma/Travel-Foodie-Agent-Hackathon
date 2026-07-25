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


def search_restaurants(city: str, meal: str, area: str | None = None,
                       cuisine: str | None = None,
                       price_level_max: int | None = None,
                       exclude_flags: list[str] | None = None,
                       limit: int = 5) -> list[dict]:
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
    ])
    data = _post(f"{config.PLACES_BASE_URL}/places:searchText",
                 {"textQuery": text_query, "maxResultCount": max(limit * 2, 8)},
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
            "address": p.get("formattedAddress"),
            "lat": loc.get("latitude"),
            "lon": loc.get("longitude"),
            "dietary_flags": flags,
            "verify_with_restaurant": True,  # live allergen uncertainty
            "source": "google_places",
        })
        if len(out) >= limit:
            break
    return out


def get_venue_details(venue_id: str) -> dict:
    # Places New: GET https://places.googleapis.com/v1/places/{place_id}
    field_mask = ",".join([
        "id", "displayName", "formattedAddress", "location", "rating",
        "userRatingCount", "priceLevel", "regularOpeningHours",
        "primaryType", "types", "reviews",
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
        "dietary_flags": _infer_flags(cuisine, None),
        "verify_with_restaurant": True,
        "source": "google_places",
    }
    cache.put(key, details)
    return details


def search_attractions(city: str, category: str | None = None,
                       limit: int = 5) -> list[dict]:
    q = f"{category or 'tourist attraction'} in {city}"
    field_mask = ",".join([
        "places.id", "places.displayName", "places.formattedAddress",
        "places.location", "places.rating", "places.userRatingCount",
        "places.primaryType",
    ])
    data = _post(f"{config.PLACES_BASE_URL}/places:searchText",
                 {"textQuery": q, "maxResultCount": limit},
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
    return out
