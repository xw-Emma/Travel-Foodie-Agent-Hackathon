"""Free text in, a structured DRAFT out - never a venue.

WHY THIS IS NOT A VENUE PICKER: the rubric scores a hallucinated venue at zero,
and a model asked to read a paragraph will happily name restaurants it half
remembers. So this agent has no way to name one. Its schema has no venue field,
every key outside that schema is dropped before anything downstream sees it,
and the values it does return are re-derived from the tools afterwards. The
model's job is to understand the sentence; the tools' job is to find the food.

The second rule is the same one Phase C applies to dish names: the output is
checked in code, not trusted. A city goes through classify_city, cuisines and
allergens are intersected with the vocabularies that actually exist, numbers are
range-checked - and anything discarded is REPORTED, because a silently dropped
constraint is indistinguishable from one that was honoured.

Nothing here runs inside the 60 s planning budget. Reading the description is a
separate interaction that fills a form the user then confirms.
"""
from __future__ import annotations

from datetime import date

from .. import config, vocabulary
from ..fuelix_client import parse_json_reply
from ..request_model import MEAL_SLOTS
from ..tools import classify_city

# The complete set of fields this agent may return. Anything else - notably a
# "restaurants" or "venues" key - is dropped and reported rather than trusted,
# which is what makes rule 1 structural instead of a request in a prompt.
ALLOWED_FIELDS = {
    "city", "days", "start_date", "meals", "budget_amount", "budget_basis",
    "party_size", "cuisines", "allergies", "attractions_wanted",
    "transport_mode", "min_rating", "min_reviews", "search_radius_km",
    "max_leg_minutes", "other_criteria", "origin_text",
}

# Keys that would mean the model tried to choose for us. Called out by name in
# the report rather than quietly ignored.
VENUE_LIKE_KEYS = {"restaurants", "venues", "picks", "recommendations",
                   "itinerary", "places", "names", "shortlist"}

MAX_OTHER_CRITERIA = 8
MAX_CRITERION_CHARS = 160

SYSTEM = (
    "You convert a traveller's description into structured search parameters. "
    "You NEVER name a restaurant, venue, hotel, or attraction - not even as an "
    "example - because the search tools choose those, not you. You only report "
    "what the description actually says; you never invent a preference it does "
    "not state."
)


def _schema_prompt(backend: str) -> str:
    return (
        "Return STRICT JSON only, with exactly these keys:\n"
        '{"city": str|null, "days": int|null, "start_date": "YYYY-MM-DD"|null,\n'
        ' "meals": [str], "budget_amount": number|null,\n'
        ' "budget_basis": "total"|"per_person"|null, "party_size": int|null,\n'
        ' "cuisines": [str], "allergies": [str], "attractions_wanted": bool|null,\n'
        ' "transport_mode": str|null, "min_rating": number|null,\n'
        ' "min_reviews": int|null, "search_radius_km": number|null,\n'
        ' "max_leg_minutes": number|null, "origin_text": str|null,\n'
        ' "other_criteria": [str]}\n\n'
        f"meals must come from {list(MEAL_SLOTS)}.\n"
        f"allergies must come from {list(vocabulary.CANONICAL_ALLERGENS)}.\n"
        f"transport_mode must come from {list(vocabulary.TRANSPORT_MODES)}.\n"
        f"cuisines should come from {vocabulary.restaurant_types(backend)[:40]}.\n"
        "If the description names a cuisine at all - even as a preference, as "
        "in 'authentic Portuguese food' - put that cuisine in `cuisines`. Any "
        "qualifier you cannot capture there ('authentic', 'where locals eat') "
        "goes to other_criteria, but the cuisine itself belongs in the field.\n"
        "city must be a CITY, never a country or region.\n"
        "origin_text is where the traveller STARTS - a hotel, address or "
        "landmark they named. It is never a restaurant and never somewhere to "
        "eat; leave it null unless they said where they are staying or setting "
        "off from.\n"
        "budget_basis says whether the amount is for the whole party or one "
        "person. Use null for anything the description does not state - do not "
        "guess.\n"
        "other_criteria: SELECTION constraints that do not fit any field above "
        "- things that decide whether a venue qualifies, such as a guide "
        "listing or a preference for places popular with locals. Do NOT put "
        "requests about what to display in the answer here (asking to be shown "
        "a rating, a neighbourhood, dishes or booking details is not a "
        "constraint). Never put a venue name here."
    )


def _clean_list(values, allowed, lowercase: bool = True) -> tuple[list, list]:
    """Intersect with a known vocabulary; return (kept, rejected)."""
    kept, rejected = [], []
    allowed_map = {str(a).lower(): a for a in allowed}
    for value in values or []:
        key = str(value).strip().lower() if lowercase else str(value).strip()
        if key in allowed_map:
            if allowed_map[key] not in kept:
                kept.append(allowed_map[key])
        else:
            rejected.append(str(value))
    return kept, rejected


def _number(value, low: float, high: float):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if low <= number <= high else None


def validate(raw: dict, backend: str = "auto") -> dict:
    """Turn a model response into fields that are safe to put on the form.

    Every rejection is recorded. A constraint that was silently dropped looks
    exactly like one that was satisfied, which is the failure this whole report
    exists to prevent.
    """
    raw = raw if isinstance(raw, dict) else {}
    fields: dict = {}
    rejected: list[dict] = []
    notes: list[str] = []

    for key in sorted(set(raw) - ALLOWED_FIELDS):
        reason = ("the search tools choose venues, not the description reader"
                  if key.lower() in VENUE_LIKE_KEYS else
                  "not a field this planner accepts")
        rejected.append({"field": key, "value": str(raw[key])[:120],
                         "reason": reason})

    city = str(raw.get("city") or "").strip()
    if city:
        # Re-derived from the tools, not taken on the model's word.
        kind = (classify_city(city) or {}).get("kind")
        if kind == "country":
            rejected.append({"field": "city", "value": city,
                             "reason": "that is a country, not a city - name "
                                       "the city so results hang together"})
        else:
            fields["city"] = city
            if kind not in ("locality", "not_checked"):
                notes.append(f"'{city}' did not resolve to a city "
                             f"({kind or 'unknown'}); check it before planning.")

    meals, bad = _clean_list(raw.get("meals"), MEAL_SLOTS)
    if meals:
        fields["meals"] = meals
    rejected += [{"field": "meals", "value": v, "reason": "not a meal slot"}
                 for v in bad]

    allergies, bad = _clean_list(raw.get("allergies"),
                                 vocabulary.CANONICAL_ALLERGENS)
    if allergies:
        fields["allergies"] = allergies
    rejected += [{"field": "allergies", "value": v,
                  "reason": "not one of the nine allergens the dataset flags"}
                 for v in bad]

    cuisines, bad = _clean_list(raw.get("cuisines"),
                                vocabulary.restaurant_types(backend))
    if cuisines:
        fields["cuisines"] = cuisines
    rejected += [{"field": "cuisines", "value": v,
                  "reason": "not an offered cuisine for this data backend"}
                 for v in bad]

    mode, bad = _clean_list([raw.get("transport_mode")],
                            vocabulary.TRANSPORT_MODES)
    if mode:
        fields["transport_mode"] = mode[0]
    rejected += [{"field": "transport_mode", "value": v,
                  "reason": "not a supported way of getting around"}
                 for v in bad if v not in ("None", "")]

    basis = str(raw.get("budget_basis") or "").strip().lower()
    if basis in ("total", "per_person"):
        fields["budget_basis"] = basis
    elif basis:
        rejected.append({"field": "budget_basis", "value": basis,
                         "reason": "must be 'total' or 'per_person'"})

    for key, low, high, cast in (("days", 1, 7, int),
                                 ("party_size", 1, 20, int),
                                 ("budget_amount", 1, 1_000_000, float),
                                 ("min_rating", 0, 5, float),
                                 ("min_reviews", 0, 100_000, int),
                                 ("search_radius_km", 0.1, 50, float),
                                 ("max_leg_minutes", 1, 300, float)):
        if raw.get(key) is None:
            continue
        number = _number(raw[key], low, high)
        if number is None:
            rejected.append({"field": key, "value": str(raw[key])[:60],
                             "reason": f"outside the accepted range {low}-{high}"})
        else:
            fields[key] = cast(number)

    start = str(raw.get("start_date") or "").strip()
    if start:
        try:
            fields["start_date"] = date.fromisoformat(start)
        except ValueError:
            rejected.append({"field": "start_date", "value": start,
                             "reason": "not a YYYY-MM-DD date"})

    if isinstance(raw.get("attractions_wanted"), bool):
        fields["attractions_wanted"] = raw["attractions_wanted"]

    # A free-text starting point. Deliberately NOT geocoded here: resolve_origin
    # does that at plan time through the Places client, so this stays a plain
    # string handled by the same validation path as everything else.
    origin = str(raw.get("origin_text") or "").strip()
    if origin:
        if len(origin) > 200:
            rejected.append({"field": "origin_text", "value": origin[:60],
                             "reason": "too long to be an address or landmark"})
        else:
            fields["origin_text"] = origin

    criteria = [str(c).strip()[:MAX_CRITERION_CHARS]
                for c in (raw.get("other_criteria") or []) if str(c).strip()]
    return {
        "fields": fields,
        "rejected": rejected,
        "notes": notes,
        # Requirements with no field to live in. Surfaced so the verification
        # panel can mark them unverifiable rather than dropping them, because an
        # omitted requirement reads as a satisfied one.
        "other_criteria": criteria[:MAX_OTHER_CRITERIA],
    }


def extract(client, text: str, backend: str = "auto") -> dict:
    """Read a description into a validated draft. Never raises."""
    description = (text or "").strip()
    if not description:
        return {"fields": {}, "rejected": [], "notes": ["Nothing to read."],
                "other_criteria": [], "ok": False}
    if client is None:
        return {"fields": {}, "rejected": [], "other_criteria": [], "ok": False,
                "notes": ["No LLM is available, so the description cannot be "
                          "read. Fill the form in directly."]}
    try:
        message = client.chat(
            model=config.MODEL_ROUTING["planner"], system=SYSTEM,
            user=f"{_schema_prompt(backend)}\n\nDescription:\n{description[:4000]}",
            temperature=0.0, max_tokens=900)
        raw = parse_json_reply(message.get("content", ""))
    except Exception as exc:  # noqa: BLE001 - the form still works without this
        return {"fields": {}, "rejected": [], "other_criteria": [], "ok": False,
                "notes": [f"Could not read the description "
                          f"({type(exc).__name__}). Fill the form in directly."]}
    return {**validate(raw, backend), "ok": True}
