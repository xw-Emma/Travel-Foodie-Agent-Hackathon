"""Can these constraints be satisfied at all?

WHY THIS EXISTS: affordability was decided per slot, at selection time, with no
view of whether any combination could fit the total. When nothing fit, each slot
took the cheapest option anyway and the budget check reported the overage - which
is a defensible fallback but a poor way to find out. Measured on a real Lisbon
request: a $50 per-slot allowance, a cheapest candidate of $60, and zero
affordable options in all four slots, producing a plan 240% over budget.

This answers the question BEFORE planning, and answers it with arithmetic the
user can act on: the budget that would work, the day count that would work.

A pure function over pools that were already fetched. It makes NO API calls -
adding some would trade the problem it solves for a slower demo.
"""
from __future__ import annotations

MEALS = ("breakfast", "lunch", "dinner")


def _meal_slot(slot: str) -> bool:
    return str(slot).split(".", 1)[-1] in MEALS


def _party_cost(candidate: dict, party_size: int) -> float | None:
    per_person = candidate.get("avg_meal_cost")
    if per_person is None:
        per_person = candidate.get("cost")
    if per_person is None:
        return None
    return round(float(per_person) * max(1, int(party_size)), 2)


def cheapest_per_slot(pools: dict[str, list[dict]], party_size: int) -> dict:
    """Cheapest candidate per meal slot. Pools are already hard-filtered.

    Everything that reaches a pool has cleared the allergen exclusion, the
    quality gate, the cuisine filter and the opening-hours check, so the cheapest
    row in it IS the cheapest plan that satisfies those constraints.
    """
    cheapest = {}
    for slot, pool in (pools or {}).items():
        if not _meal_slot(slot) or not pool:
            continue
        priced = [(cost, candidate) for candidate in pool
                  if (cost := _party_cost(candidate, party_size)) is not None]
        if not priced:
            continue
        cost, candidate = min(priced, key=lambda pair: (pair[0],
                                                        str(pair[1].get("venue_id"))))
        cheapest[slot] = {"slot": slot, "cost": cost,
                          "name": candidate.get("name"),
                          "venue_id": candidate.get("venue_id"),
                          "rating": candidate.get("rating"),
                          "price_level": candidate.get("price_level")}
    return cheapest


def preflight(request: dict, pools: dict[str, list[dict]]) -> dict:
    """Whether the stated constraints can be met, and what would make them met."""
    party_size = max(1, int(request.get("party_size", 1)))
    budget = float(request.get("budget_total") or 0)
    days = max(1, int(request.get("days", 1)))
    per_slot = cheapest_per_slot(pools, party_size)

    if not per_slot:
        return {"checked": False, "feasible": None, "cheapest_total": None,
                "per_slot": [], "blocking": [], "suggestions": [],
                "reason": "no priced candidates were available to check against"}

    rows = sorted(per_slot.values(), key=lambda row: row["slot"])
    cheapest_total = round(sum(row["cost"] for row in rows), 2)
    feasible = cheapest_total <= budget
    report = {
        "checked": True,
        "feasible": feasible,
        "cheapest_total": cheapest_total,
        "budget_total": budget,
        "per_slot": rows,
        "slots_priced": len(rows),
        "blocking": [],
        "suggestions": [],
        "reason": None,
    }
    if feasible:
        return report

    over = round(cheapest_total - budget, 2)
    report["reason"] = (
        f"The cheapest plan that satisfies every stated constraint costs "
        f"${cheapest_total:,.2f}, which is ${over:,.2f} over the ${budget:,.2f} "
        "budget. No choice of venues can fit.")
    # The slots that cost the most are what to look at first.
    report["blocking"] = [
        {"slot": row["slot"], "cost": row["cost"], "name": row["name"]}
        for row in sorted(rows, key=lambda row: -row["cost"])[:3]]

    suggestions = [{
        "change": "budget_total",
        "to": cheapest_total,
        "text": (f"Raise the budget to ${cheapest_total:,.2f} "
                 f"(${cheapest_total / party_size:,.2f} per person) — the "
                 "cheapest plan that meets everything you asked for."),
        "costed": True,
    }]

    # Fewer days is exact arithmetic on the same pool: drop whole days from the
    # most expensive end and see when the remainder fits.
    by_day: dict[int, list[dict]] = {}
    for row in rows:
        head = row["slot"].split(".", 1)[0]
        if head.startswith("day") and head[3:].isdigit():
            by_day.setdefault(int(head[3:]), []).append(row)
    if len(by_day) > 1:
        running = 0.0
        for day in sorted(by_day):
            day_cost = sum(row["cost"] for row in by_day[day])
            if running + day_cost > budget:
                break
            running += day_cost
        else:
            day = len(by_day)
        fits = day - 1
        if fits >= 1:
            suggestions.append({
                "change": "days", "to": fits,
                "text": (f"Plan {fits} day{'s' if fits > 1 else ''} instead of "
                         f"{days} — that costs ${running:,.2f} and fits."),
                "costed": True,
            })

    if request.get("party_size") and party_size > 1:
        per_person_needed = round(cheapest_total / party_size, 2)
        if per_person_needed <= budget:
            suggestions.append({
                "change": "party_size", "to": 1,
                "text": (f"For one person the same plan costs "
                         f"${per_person_needed:,.2f}, which fits."),
                "costed": True,
            })

    # The quality gate cannot be priced here, and saying otherwise would be a
    # guess: the pools were fetched WITH the gate applied, so there is no record
    # of what a lower floor would have returned. Offered as an option, marked as
    # not costed, rather than invented.
    if request.get("min_rating") or request.get("min_reviews"):
        gate = " and ".join(part for part in (
            f"rating >= {request['min_rating']}" if request.get("min_rating") else "",
            f"{request['min_reviews']}+ reviews" if request.get("min_reviews") else "",
        ) if part)
        suggestions.append({
            "change": "quality_gate", "to": None,
            "text": (f"Relax the quality gate ({gate}) to widen the search. "
                     "Not costed here: the candidates were fetched with the gate "
                     "applied, so what a lower floor would return is unknown "
                     "until it is searched."),
            "costed": False,
        })

    report["suggestions"] = suggestions
    return report
