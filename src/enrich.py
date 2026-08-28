"""Per-venue detail, split into what is verifiable and what is not.

THE RULE THIS FILE ENFORCES: every field is either a FACT that can be traced
back to an API response, a COMMENT written by the model and tied to the exact
text it came from, or an explicit UNVERIFIABLE with the reason. Nothing sits in
between, and the split is a data structure rather than a promise about prompts -
"hallucinated venues score zero" has to be structural or it is not a rule.

The model never gets to assert anything free-form here. It is asked for dish
names it saw in review text, and every dish it returns is then checked, IN CODE,
against the review it cited. A dish that is not literally in the quoted text is
dropped, however confident the model was.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from . import config
from .fuelix_client import parse_json_reply
from .tools import cache

# Payload limits for the batched review read. One request carrying eight venues
# at five full reviews each ran ~28k characters and was slower than the eight
# small calls it replaced, so the evidence is trimmed to what is actually needed
# to name a dish.
MAX_REVIEWS_PER_VENUE = 3
MAX_REVIEW_CHARS = 320

# Bump whenever the prompt or the verification below changes. The cache key has
# to cover everything that shapes the output, not just the input: a cached
# result produced by an older parser is served forever otherwise. Exactly the
# bug that made the Routes cache serve responses missing a newly requested
# field.
EXTRACTION_VERSION = 3

# Google Places carries no Michelin data of any kind. There is no heuristic that
# recovers it and no prompt that makes it true, so it is reported as
# unverifiable rather than guessed at.
UNVERIFIABLE_CLAIMS = {
    "michelin": "Google Places exposes no Michelin field. Confirm against the "
                "Michelin Guide directly.",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def venue_facts(candidate: dict, details: dict | None = None) -> dict:
    """Everything traceable to an API response, with its provenance."""
    details = details or {}
    source = details.get("source") or candidate.get("source") or "unknown"
    price_level = candidate.get("price_level", details.get("price_level"))
    neighborhood = candidate.get("neighborhood") or details.get("neighborhood")
    facts = {
        "name": candidate.get("name") or details.get("name"),
        "neighborhood": neighborhood,
        # Distinguishes "we looked and Google has no district for this venue"
        # from "we did not look". Measured on real Lisbon data: many venues
        # carry no neighbourhood component at all.
        "neighborhood_known": neighborhood is not None,
        "address": candidate.get("address") or details.get("address"),
        "rating": candidate.get("rating", details.get("rating")),
        "review_count": candidate.get("review_count", details.get("review_count")),
        "price_level": price_level,
        "cuisine": candidate.get("cuisine") or details.get("cuisine"),
        "website": details.get("website"),
        "phone": details.get("phone"),
        "source": source,
        "fetched_at": _now(),
    }
    cost = candidate.get("avg_meal_cost", details.get("avg_meal_cost"))
    if cost is not None:
        facts["cost_per_person"] = {
            "value": float(cost),
            # Live prices are derived from Google's price BAND, not a menu.
            # Saying so is the difference between an estimate and a quote.
            "basis": ("price_band_estimate" if source == "google_places"
                      else "dataset_value"),
        }
    return facts


def reservation_advice(facts: dict) -> dict:
    """How to book, built only from contact facts - never invented."""
    website, phone = facts.get("website"), facts.get("phone")
    if website and phone:
        text = f"Book via {website} or call {phone}."
    elif website:
        text = f"Book via {website}."
    elif phone:
        text = f"Call {phone} to book."
    else:
        text = ("No booking website or phone number is published on Google for "
                "this venue.")
    return {"text": text, "grounded_in": [k for k in ("website", "phone")
                                          if facts.get(k)]}


def _mentions(dish: str, text: str) -> bool:
    """Whether a dish name really occurs in the quoted review text."""
    dish = (dish or "").strip()
    if len(dish) < 3 or not text:
        return False
    return re.search(re.escape(dish), text, re.IGNORECASE) is not None


def _review_texts(reviews: list[dict]) -> list[str]:
    return [(review.get("text") or "").strip()
            for review in (reviews or []) if (review.get("text") or "").strip()]


def _blank(texts: list[str], note: str | None = None) -> dict:
    return {"dishes": [], "review_count": len(texts), "note": note,
            "source": "google_reviews"}


def dishes_for_venues(client, venues: list[tuple[str, list[dict]]]) -> list[dict]:
    """Dish names mentioned in Google reviews, for a whole itinerary at once.

    Deliberately NOT called "recommended dishes". The evidence is whatever
    Google returns - measured at about five reviews per venue, often in mixed
    languages - which is far too thin to call a recommendation. The honest claim
    is that these dishes were mentioned.

    ONE call for every venue, not one per venue. Eight separate calls cost
    roughly twenty seconds against a sixty second budget, and running them all
    concurrently instead earned a gateway 429. Batching removes both problems.

    Every dish is verified against the exact review it cited before it is kept,
    so a plausible-sounding invention cannot survive however confident the model
    sounded.
    """
    per_venue = [_review_texts(reviews) for _, reviews in venues]
    results = [_blank(texts) for texts in per_venue]
    usable = [index for index, texts in enumerate(per_venue) if texts]
    for index, texts in enumerate(per_venue):
        if not texts:
            results[index]["note"] = "No review text was returned for this venue."
    if not usable:
        return results
    if client is None:
        for index in usable:
            results[index]["note"] = ("Review text is available but no LLM was "
                                      "reachable to read it.")
        return results

    # The one LLM result worth caching. Everywhere else a cached completion
    # would hide a real failure, but this is a temperature-0 extraction from
    # fixed review text whose output is re-verified in code below, so a hit
    # cannot smuggle anything past the guard.
    cache_key = cache.make_key(
        "dishes", EXTRACTION_VERSION, MAX_REVIEWS_PER_VENUE, MAX_REVIEW_CHARS,
        [(venues[i][0], per_venue[i]) for i in usable])
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    blocks = []
    for index in usable:
        name = venues[index][0] or f"venue {index}"
        reviews = "\n".join(
            f"  [{r}] {text[:MAX_REVIEW_CHARS]}"
            for r, text in enumerate(per_venue[index][:MAX_REVIEWS_PER_VENUE]))
        blocks.append(f"VENUE {index} — {name}\n{reviews}")
    system = ("You extract dish names from restaurant reviews. You never infer, "
              "translate, or generalise. If no dish is named, return an empty "
              "list.")
    user = (
        "For each venue below, list the dishes named in its reviews.\n\n"
        + "\n\n".join(blocks) + "\n\n"
        "Return STRICT JSON only: {\"dishes\": [{\"venue_index\": int, "
        "\"review_index\": int, \"dish\": str, \"quote\": str}]}\n"
        "Rules: `dish` must be copied EXACTLY as written in that venue's review "
        "with that index. `quote` must be a verbatim substring of that same "
        "review containing the dish. Do not translate. Never include a dish "
        "that is not literally written in the text. At most 5 dishes per venue."
    )
    try:
        message = client.chat(model=config.MODEL_ROUTING["restaurant"],
                              system=system, user=user, temperature=0.0,
                              max_tokens=900)
        parsed = parse_json_reply(message.get("content", ""))
    except Exception as exc:  # noqa: BLE001 - enrichment must never fail a plan
        for index in usable:
            results[index]["note"] = (
                f"Could not read the reviews ({type(exc).__name__}).")
        return results

    rejected = [0] * len(venues)
    for entry in (parsed.get("dishes") or []):
        dish = str(entry.get("dish") or "").strip()
        # venue_index is optional: a single-venue response has no reason to
        # carry one, and defaulting to 0 keeps dishes_from_reviews usable.
        try:
            venue_index = int(entry.get("venue_index") or 0)
        except (TypeError, ValueError):
            venue_index = 0
        if not (0 <= venue_index < len(venues)):
            continue
        texts = per_venue[venue_index]
        try:
            review_index = int(entry.get("review_index"))
        except (TypeError, ValueError):
            rejected[venue_index] += 1
            continue
        if not (0 <= review_index < len(texts)):
            rejected[venue_index] += 1
            continue
        # The citation is checked, not trusted. This is the line that makes the
        # separation structural instead of a request in a prompt.
        if not _mentions(dish, texts[review_index]):
            rejected[venue_index] += 1
            continue
        if len(results[venue_index]["dishes"]) >= 5:
            continue
        results[venue_index]["dishes"].append({
            "dish": dish, "review_index": review_index,
            "quote": str(entry.get("quote") or "")[:200]})

    for index in usable:
        if rejected[index]:
            results[index]["note"] = (
                f"{rejected[index]} suggested dish(es) were dropped: not found "
                "in the review they cited.")
        elif not results[index]["dishes"]:
            results[index]["note"] = "No dish was named in the available reviews."
    cache.put(cache_key, results)
    return results


def dishes_from_reviews(client, reviews: list[dict], venue_name: str = "") -> dict:
    """Single-venue convenience wrapper over dishes_for_venues."""
    return dishes_for_venues(client, [(venue_name, reviews)])[0]


def enrich_stop(item: dict, candidate: dict, details: dict | None,
                client=None, dishes: dict | None = None) -> dict:
    """Facts, comment, and unverifiable claims for one itinerary stop.

    `dishes` lets a caller pass evidence already gathered for the whole
    itinerary in one batched call; without it, this falls back to reading just
    this venue's reviews.
    """
    facts = venue_facts(candidate or {}, details)
    if dishes is None:
        dishes = dishes_from_reviews(
            client, (details or {}).get("reviews") or [], facts.get("name") or "")
    return {
        "slot": item.get("slot"),
        "facts": facts,
        "comment": {
            "why_selected": item.get("why"),
            "reservation": reservation_advice(facts),
            "dishes_mentioned_in_reviews": dishes,
        },
        "unverifiable": dict(UNVERIFIABLE_CLAIMS),
    }
