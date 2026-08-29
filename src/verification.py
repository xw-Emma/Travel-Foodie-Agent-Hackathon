"""Requirement-by-requirement verification of a finished plan.

A pure function: request in, finished state in, a list of verdicts out. No I/O,
no API calls, no model - so it can be reasoned about and tested directly, and it
reports on exactly the plan that was shipped.

FOUR STATES, NOT THREE. The obvious design has pass / fail / unverifiable, and
it is not honest enough. Some requirements are met by INFERENCE rather than by
data: live allergen filtering works from cuisine type because Google Places has
no allergen field, and live costs come from a price band rather than a menu.
Showing a green tick for those would claim a verification that never happened -
and for an allergen that is the most consequential lie this system could tell.
So the state depends on where the answer came from, not only on the outcome.

The list is deliberately a contract: anything that can produce
[{requirement, expected, actual, state, source, reason}] can feed the panel.
The form produces it today; an intent extractor can produce it later without
this file changing.
"""
from __future__ import annotations

VERIFIED = "verified"          # checked against data from an API or the dataset
INFERRED = "inferred"          # met, but by heuristic or estimate, not by data
FAILED = "failed"              # checked, and not met
UNVERIFIABLE = "unverifiable"  # no data source exists to check it
NOT_REQUESTED = "not_requested"

STATE_ORDER = (FAILED, UNVERIFIABLE, INFERRED, VERIFIED, NOT_REQUESTED)

MEALS = ("breakfast", "lunch", "dinner")


def _verdict(requirement, expected, actual, state, source=None, reason=None,
             fetched_at=None) -> dict:
    return {"requirement": requirement, "expected": expected, "actual": actual,
            "state": state, "source": source, "reason": reason,
            "fetched_at": fetched_at}


def _meal_slots(itinerary):
    return [item for item in itinerary
            if str(item.get("slot", "")).split(".", 1)[-1] in MEALS]


def _is_live(meta) -> bool:
    return (meta.get("tool_backends") or {}).get("restaurants") == "google_places"


def _fetched_at(meta):
    entries = meta.get("enrichment") or []
    stamps = [entry.get("facts", {}).get("fetched_at") for entry in entries]
    return next((stamp for stamp in stamps if stamp), None)


def _data_source(meta) -> str:
    return (meta.get("tool_backends") or {}).get("restaurants") or "unknown"


def _restaurant_facts(meta):
    """Enrichment for meal stops only.

    The quality gate is a constraint on RESTAURANTS. Counting an attraction
    against it fails the check for the wrong reason - a landmark rated 4.5 is
    not a restaurant that missed a 4.8 floor.
    """
    return [entry.get("facts") or {} for entry in (meta.get("enrichment") or [])
            if str(entry.get("slot", "")).split(".", 1)[-1] in MEALS]


# --------------------------------------------------------------- checks
def _check_meals(request, state, meta):
    wanted = [m for m in MEALS if m in set(request.get("meals") or MEALS)]
    days = int(request.get("days", 2))
    expected_count = days * len(wanted)
    planned = _meal_slots(state.get("itinerary") or [])
    extra = sorted({item["slot"] for item in planned
                    if item["slot"].split(".", 1)[-1] not in wanted})
    if extra:
        return _verdict("Meals planned", f"{', '.join(wanted)} only",
                        f"also planned {', '.join(extra)}", FAILED,
                        _data_source(meta),
                        "meals were planned that were not requested")
    state_value = VERIFIED if len(planned) >= expected_count else FAILED
    return _verdict(
        "Meals planned", f"{expected_count} ({', '.join(wanted)} x {days} day(s))",
        f"{len(planned)} planned", state_value, _data_source(meta),
        None if state_value == VERIFIED else "not every requested meal was filled")


def _check_budget(request, state, meta):
    budget = state.get("budget") or {}
    limit = float(budget.get("limit") or 0)
    projected = float(budget.get("projected") or 0)
    within = budget.get("status") != "exceeded"
    actual = f"${projected:,.2f} of ${limit:,.2f}"
    if not within:
        return _verdict("Total within budget", f"<= ${limit:,.2f}", actual, FAILED,
                        _data_source(meta), "the plan costs more than the budget")
    if _is_live(meta):
        # Google reports a price BAND, never a menu price. Calling this verified
        # would dress an estimate up as a checked fact.
        return _verdict("Total within budget", f"<= ${limit:,.2f}", actual, INFERRED,
                        "google_places",
                        "live costs are estimated from Google's price level, not "
                        "actual menu prices", _fetched_at(meta))
    return _verdict("Total within budget", f"<= ${limit:,.2f}", actual, VERIFIED,
                    "local_dataset")


def _check_rating(request, state, meta):
    minimum = request.get("min_rating")
    if minimum is None:
        return _verdict("Minimum rating", "not requested", "-", NOT_REQUESTED)
    ratings = [facts.get("rating") for facts in _restaurant_facts(meta)
               if facts.get("rating") is not None]
    shortfall = [s for s in (meta.get("quality_shortfall") or [])
                 if "rating" in (s.get("detail") or "")]
    if shortfall:
        return _verdict("Minimum rating", f">= {minimum}",
                        "; ".join(s["detail"] for s in shortfall), FAILED,
                        _data_source(meta),
                        "no venue met the rating you asked for", _fetched_at(meta))
    if not ratings:
        return _verdict("Minimum rating", f">= {minimum}", "no ratings returned",
                        UNVERIFIABLE, _data_source(meta),
                        "no rating data was available for the chosen venues")
    return _verdict("Minimum rating", f">= {minimum}",
                    f"{min(ratings)} - {max(ratings)} across {len(ratings)} restaurants",
                    VERIFIED if min(ratings) >= float(minimum) else FAILED,
                    _data_source(meta), None, _fetched_at(meta))


def _check_reviews(request, state, meta):
    minimum = request.get("min_reviews")
    if minimum is None:
        return _verdict("Minimum review count", "not requested", "-", NOT_REQUESTED)
    counts = [facts.get("review_count") for facts in _restaurant_facts(meta)
              if facts.get("review_count") is not None]
    shortfall = [s for s in (meta.get("quality_shortfall") or [])
                 if "review" in (s.get("detail") or "")]
    if shortfall:
        return _verdict("Minimum review count", f">= {minimum}",
                        "; ".join(s["detail"] for s in shortfall), FAILED,
                        _data_source(meta),
                        "no venue met the review count you asked for",
                        _fetched_at(meta))
    if not counts:
        return _verdict("Minimum review count", f">= {minimum}", "no counts returned",
                        UNVERIFIABLE, _data_source(meta),
                        "no review-count data was available")
    return _verdict("Minimum review count", f">= {minimum}",
                    f"{min(counts)} - {max(counts)} across {len(counts)} restaurants",
                    VERIFIED if min(counts) >= int(minimum) else FAILED,
                    _data_source(meta), None, _fetched_at(meta))


def _check_allergens(request, state, meta):
    allergies = request.get("allergies") or []
    if not allergies:
        return _verdict("Allergen exclusion", "not requested", "-", NOT_REQUESTED)
    listed = ", ".join(allergies)
    if _is_live(meta):
        # The single most important honesty call in this file.
        return _verdict(
            "Allergen exclusion", f"no {listed}",
            "filtered by cuisine type", INFERRED, "google_places",
            "Google Places has no allergen data. Live filtering infers risk from "
            "cuisine and CANNOT confirm a venue is safe - always confirm with "
            "the restaurant.", _fetched_at(meta))
    return _verdict("Allergen exclusion", f"no {listed}",
                    "excluded at the data layer on explicit flags", VERIFIED,
                    "local_dataset",
                    "the offline dataset carries an explicit true/false for all "
                    "nine canonical allergens")


def _check_travel(request, state, meta):
    limit = float(request.get("max_leg_minutes") or 25.0)
    legs = [leg for route in (state.get("routes") or [])
            for leg in route.get("legs", [])]
    if not legs:
        return _verdict("Travel between stops", f"<= {limit:.0f} min",
                        "no legs computed", UNVERIFIABLE, None,
                        "no route was computed for this plan")
    worst = max(float(leg.get("minutes") or 0) for leg in legs)
    breaches = [issue for issue in (meta.get("unresolved_issues") or [])
                if issue.get("type") == "travel"]
    source = legs[0].get("source")
    if worst > limit:
        return _verdict("Travel between stops", f"<= {limit:.0f} min",
                        f"worst leg {worst:.0f} min", FAILED, source,
                        f"{len(breaches)} leg(s) still over the limit when the "
                        "plan shipped" if breaches else "a leg exceeds the limit")
    return _verdict("Travel between stops", f"<= {limit:.0f} min",
                    f"worst leg {worst:.0f} min", VERIFIED, source)


def _check_daily_travel(request, state, meta):
    limit = float(request.get("max_daily_travel_minutes") or 120.0)
    routes = state.get("routes") or []
    if not routes:
        return _verdict("Travel per day", f"<= {limit:.0f} min", "no routes",
                        UNVERIFIABLE, None, "no route was computed")
    worst = max(float((route.get("totals") or {}).get("minutes") or 0)
                for route in routes)
    return _verdict("Travel per day", f"<= {limit:.0f} min",
                    f"busiest day {worst:.0f} min",
                    VERIFIED if worst <= limit else FAILED,
                    (routes[0].get("legs") or [{}])[0].get("source"))


def _check_opening_hours(request, state, meta):
    if not request.get("start_date"):
        return _verdict("Open when visited", "checked against trip dates",
                        "no dates given", UNVERIFIABLE, None,
                        "opening hours can only be checked once the trip has "
                        "real dates - pick them to enable this check")
    breaches = [issue for issue in (meta.get("unresolved_issues") or [])
                if issue.get("type") == "hours"]
    if breaches:
        return _verdict("Open when visited", "every stop open",
                        "; ".join(b.get("detail", "") for b in breaches), FAILED,
                        _data_source(meta), "a stop is closed when it is visited")
    return _verdict("Open when visited", "every stop open",
                    "no stop is closed at its visit time", VERIFIED,
                    _data_source(meta), None, _fetched_at(meta))


def _check_michelin(request, state, meta):
    """Permanently unverifiable, and said so plainly.

    Kept in the list rather than dropped: an omitted requirement reads as a
    satisfied one, which is worse than admitting there is no data for it.
    """
    unverifiable = {}
    for entry in (meta.get("enrichment") or []):
        unverifiable.update(entry.get("unverifiable") or {})
    reason = unverifiable.get(
        "michelin", "Google Places exposes no Michelin field.")
    return _verdict("Michelin listing", "as requested", "cannot be checked",
                    UNVERIFIABLE, None, reason)


def _check_duplicates(request, state, meta):
    ids = [item.get("venue_id") for item in (state.get("itinerary") or [])
           if item.get("venue_id")]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    return _verdict("No repeated venues", "every stop distinct",
                    f"{len(ids)} stops, {len(set(ids))} distinct",
                    VERIFIED if not dupes else FAILED, _data_source(meta),
                    f"repeated: {dupes}" if dupes else None)


def _check_attractions(request, state, meta):
    wanted = request.get("attractions_per_day")
    wanted = 1 if wanted is None else int(wanted)
    found = len([item for item in (state.get("itinerary") or [])
                 if ".attraction" in str(item.get("slot", ""))])
    if wanted == 0:
        return _verdict("Attractions", "none (food only)",
                        f"{found} planned", VERIFIED if found == 0 else FAILED,
                        _data_source(meta),
                        None if found == 0 else "attractions were planned anyway")
    expected = wanted * int(request.get("days", 2))
    return _verdict("Attractions", f"{expected}", f"{found} planned",
                    VERIFIED if found >= expected else FAILED,
                    _data_source(meta),
                    None if found >= expected else "not every day got one")


def _check_extra_criteria(request, state, meta):
    """Requirements the description asked for that no field can express.

    A trip description routinely asks for things this planner has no data for -
    a guide listing, "popular with locals", "no chains". Dropping them would
    leave the panel silently claiming a clean sweep, so each one is listed as
    unverifiable in its own words.
    """
    criteria = request.get("extra_criteria") or []
    if not criteria:
        return []
    return [_verdict(f"“{str(criterion)[:80]}”", "as described",
                     "cannot be checked", UNVERIFIABLE, None,
                     "no data source in this planner can confirm this - it was "
                     "carried over from your description so it is not forgotten")
            for criterion in criteria]


CHECKS = (_check_meals, _check_budget, _check_rating, _check_reviews,
          _check_allergens, _check_travel, _check_daily_travel,
          _check_opening_hours, _check_attractions, _check_duplicates,
          _check_michelin)


def verify(request: dict, state: dict) -> dict:
    """Every stated requirement, with how it was checked and what it cost to know."""
    meta = state.get("meta") or {}
    verdicts = []
    for check in CHECKS:
        try:
            verdicts.append(check(request or {}, state, meta))
        except Exception as exc:  # noqa: BLE001 - a broken check must not hide the rest
            verdicts.append(_verdict(check.__name__, "-", "-", UNVERIFIABLE, None,
                                     f"this check errored: {type(exc).__name__}"))
    try:
        verdicts.extend(_check_extra_criteria(request or {}, state, meta))
    except Exception:  # noqa: BLE001
        pass
    counts = {state_name: sum(1 for v in verdicts if v["state"] == state_name)
              for state_name in STATE_ORDER}
    checked = len(verdicts) - counts[NOT_REQUESTED]
    return {
        "requirements": sorted(verdicts,
                               key=lambda v: STATE_ORDER.index(v["state"])),
        "summary": {
            **counts, "checked": checked, "total": len(verdicts),
            "headline": (
                f"{counts[VERIFIED]} of {checked} requirements verified"
                + (f", {counts[INFERRED]} inferred" if counts[INFERRED] else "")
                + (f", {counts[UNVERIFIABLE]} unverifiable" if counts[UNVERIFIABLE] else "")
                + (f", {counts[FAILED]} NOT met" if counts[FAILED] else "")),
        },
    }
