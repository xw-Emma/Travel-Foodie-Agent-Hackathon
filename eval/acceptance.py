"""Lightweight acceptance checks for graded scenarios (run offline against local backend).

Run from the kit root:
  FOODIE_DATA_BACKEND=local python eval/acceptance.py
"""
from __future__ import annotations

import json
import sys
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.orchestrator import run_tier2  # noqa: E402
from src import config  # noqa: E402
from src.state import is_valid_slot, slot_ids  # noqa: E402
from src.tools import get_venue_details  # noqa: E402
from src.polyline import decode_polyline  # noqa: E402

SCENARIOS = Path(__file__).with_name("scenarios.json")


def check_no_allergen_leaks(st, request) -> list[str]:
    fails = []
    allergies = request.get("allergies") or []
    for slot, cands in st.candidates.items():
        for c in cands:
            flags = c.get("dietary_flags") or {}
            for a in allergies:
                if flags.get(f"{a}_risk"):
                    fails.append(f"{slot}:{c.get('name')}:{a}_risk")
    return fails


def check_budget_ok(st, request) -> list[str]:
    if config.DATA_BACKEND != "local":
        return [] if isinstance(st.budget.get("projected"), (int, float)) else [
            "live budget has no numeric projected value"
        ]
    if st.budget.get("status") == "exceeded":
        return [f"budget exceeded: {st.budget}"]
    return []


def check_has_meals(st, request) -> list[str]:
    days = int(request.get("days", 2))
    expected = days * 3
    meals = [it for it in st.itinerary if any(m in it["slot"] for m in ("breakfast", "lunch", "dinner"))]
    if len(meals) < expected:
        return [f"expected {expected} meals, got {len(meals)}"]
    return []


def check_party_size(st, request) -> list[str]:
    if config.DATA_BACKEND != "local":
        return []
    party_size = int(request.get("party_size", 1))
    fails = []
    for item in st.itinerary:
        details = get_venue_details(item["venue_id"])
        per_person = details.get("avg_meal_cost", details.get("cost", 0))
        expected = round(float(per_person or 0) * party_size, 2)
        if round(float(item.get("cost", 0)), 2) != expected:
            fails.append(f"{item['slot']}: cost={item.get('cost')} expected={expected}")
    return fails


def check_no_duplicate_venues(st, request) -> list[str]:
    ids = [item.get("venue_id") for item in st.itinerary if item.get("venue_id")]
    duplicates = sorted({venue_id for venue_id in ids if ids.count(venue_id) > 1})
    return [f"duplicate venue: {venue_id}" for venue_id in duplicates]


def check_opening_hours(st, request) -> list[str]:
    if config.DATA_BACKEND != "local":
        return []
    fails = []
    for item in st.itinerary:
        details = get_venue_details(item["venue_id"])
        if item["slot"].split(".")[-1].startswith("attraction"):
            if not details.get("hours"):
                fails.append(f"{item['slot']}: missing opening hours")
            continue
        meal = item["slot"].split(".")[-1]
        meal_types = str(details.get("meal_types") or "").split(";")
        if meal not in meal_types:
            fails.append(f"{item['slot']}: {item['name']} does not serve {meal}")
        if not details.get("hours"):
            fails.append(f"{item['slot']}: missing opening hours")
    return fails


def check_attraction_trap(st, request) -> list[str]:
    if config.DATA_BACKEND != "local":
        return []
    return ["closed Monday attraction trap selected"] if any(
        item.get("venue_id") == "a002" for item in st.itinerary
    ) else []


def check_routes(st, request) -> list[str]:
    """Every leg that breaches a travel limit must carry a matching Critic issue.

    Both limits are asserted: max_leg_minutes (mode-independent, the Phase 1
    constraint) and max_walk_km (still honoured when a caller supplies it).
    """
    fails = []
    max_leg_minutes = float(request.get("max_leg_minutes") or 25.0)
    raw_km = request.get("max_walk_km")
    max_walk_km = float(raw_km) if raw_km is not None else None
    max_daily_minutes = float(request.get("max_daily_travel_minutes") or 120.0)
    travel_issues = {issue.get("slot") for issue in st.critic.get("issues", [])
                     if issue.get("type") == "travel"}
    daily_issues = {issue.get("slot") for issue in st.critic.get("issues", [])
                    if issue.get("type") == "daily_travel"}
    for day_route in st.routes:
        for route in day_route.get("legs", []):
            if "km" not in route or "minutes" not in route:
                fails.append(f"invalid route result: {route}")
            over = float(route.get("minutes") or 0) > max_leg_minutes or (
                max_walk_km is not None and float(route.get("km") or 0) > max_walk_km)
            if over:
                target = route.get("to_slot")
                if target not in travel_issues:
                    fails.append(f"missing travel Critic issue for {target}: {route}")
        day_minutes = float((day_route.get("totals") or {}).get("minutes") or 0)
        if day_minutes > max_daily_minutes:
            scope = f"day{day_route.get('day')}"
            if scope not in daily_issues:
                fails.append(f"missing daily_travel Critic issue for {scope}: "
                             f"{day_minutes} min")
    return fails


def check_route_geometry(st, request) -> list[str]:
    """Every leg must be drawable, in both backends. Without geometry there is
    no map, and the local fallback is only insurance if it renders too."""
    fails = []
    for day_route in st.routes:
        for leg in day_route.get("legs", []):
            if not leg.get("polyline"):
                fails.append(f"leg without geometry: {leg.get('from_slot')} -> "
                             f"{leg.get('to_slot')}")
            elif not decode_polyline(leg["polyline"]):
                fails.append(f"undecodable polyline on {leg.get('to_slot')}")
        order = day_route.get("stop_order") or []
        if len(order) != len(set(order)):
            fails.append(f"day{day_route.get('day')} visits a stop twice: {order}")
    return fails


def check_unresolved_reported(st, request) -> list[str]:
    """A shipped constraint violation must be recorded, never silent."""
    unresolved = st.meta.get("unresolved_issues")
    if unresolved is None:
        return ["meta.unresolved_issues missing"]
    shipped_with_issues = st.critic.get("verdict") == "revise" and st.critic.get("issues")
    if shipped_with_issues and not unresolved:
        return [f"shipped {len(st.critic['issues'])} issues without recording them"]
    if unresolved and not any(entry.get("agent") == "ship" for entry in st.trace):
        return ["unresolved issues were not announced in the trace"]
    return []


def check_budget_utilisation(st, request) -> list[str]:
    """Guards B7. The plan used to land near a third of the budget, which reads
    as a broken budget rather than a frugal one."""
    limit = float(st.budget.get("limit") or 0)
    if not limit:
        return ["no budget limit"]
    used = float(st.budget.get("projected") or 0) / limit
    return [] if used > 0.60 else [f"underspending: {used:.0%} of budget used"]


def check_valid_slots(st, request) -> list[str]:
    days = int(request.get("days", 2))
    # An itinerary entry must be a fillable slot — never a day scope or origin.
    fillable = set(slot_ids(days, attractions_per_day=1))
    fails = [f"invalid itinerary slot: {slot}" for slot
             in sorted({item.get("slot") for item in st.itinerary} - fillable) if slot]
    # A Critic issue may additionally name a day-level scope.
    fails += [f"invalid critic slot: {slot}" for slot
              in sorted({issue.get("slot") for issue in st.critic.get("issues", [])}
                        - fillable)
              if slot and not is_valid_slot(str(slot), days=days)]
    return fails


def check_critic_bound(st, request) -> list[str]:
    critic_count = sum(1 for entry in st.trace if entry.get("agent") == "critic")
    iteration = int(st.critic.get("iteration", 0))
    return ([f"critic calls={critic_count}, limit={config.CRITIC_MAX_ITERATIONS}"]
            if critic_count > config.CRITIC_MAX_ITERATIONS else []) + (
                [f"critic iteration={iteration}"]
                if iteration > config.CRITIC_MAX_ITERATIONS else [])


def check_real_backend(st, request) -> list[str]:
    backends = st.meta.get("tool_backends") or {}
    if config.DATA_BACKEND == "local":
        expected = "local_dataset"
        return [f"restaurants backend={backends.get('restaurants')} expected={expected}"] \
            if backends.get("restaurants") != expected else []
    required = {"restaurants": "google_places", "attractions": "google_places",
                "travel": "google_routes"}
    return [f"{key} backend={backends.get(key)} expected={value}"
            for key, value in required.items() if backends.get(key) != value]


def check_llm_calls(st, request) -> list[str]:
    calls = int(st.meta.get("llm_calls", -1))
    if config.DATA_BACKEND == "local":
        return [f"local mode made {calls} LLM calls"] if calls != 0 else []
    return ["real backend made no LLM calls"] if calls <= 0 else []


def check_elapsed(st, request) -> list[str]:
    elapsed = float(st.meta.get("elapsed_s", 0))
    return [f"elapsed={elapsed}s exceeds {config.LATENCY_BUDGET_S}s"] \
        if elapsed >= config.LATENCY_BUDGET_S else []


CHECKS = {
    "no_allergen_leaks": check_no_allergen_leaks,
    "budget_ok": check_budget_ok,
    "has_meals": check_has_meals,
    "party_size": check_party_size,
    "no_duplicate_venues": check_no_duplicate_venues,
    "opening_hours": check_opening_hours,
    "attraction_trap": check_attraction_trap,
    "route_distance": check_routes,
    "route_geometry": check_route_geometry,
    "unresolved_reported": check_unresolved_reported,
    "budget_utilisation": check_budget_utilisation,
    "critic_revision_bound": check_critic_bound,
    "valid_slot_ids": check_valid_slots,
    "real_backend": check_real_backend,
    "llm_call_count": check_llm_calls,
    "elapsed_under_60": check_elapsed,
}

LIVE_ONLY_CHECKS = {"no_allergen_leaks", "has_meals", "no_duplicate_venues",
                    "route_distance", "route_geometry", "unresolved_reported",
                    "critic_revision_bound", "valid_slot_ids",
                    "real_backend", "llm_call_count", "elapsed_under_60"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true",
                        help="run against live APIs instead of the local gate")
    args = parser.parse_args()
    config.DATA_BACKEND = "auto" if args.live else "local"
    if args.live and not config.LIVE_DATA_AVAILABLE:
        print("SKIP: --live requires GOOGLE_MAPS_API_KEY")
        return
    scenarios = json.loads(SCENARIOS.read_text())
    all_ok = True
    for sc in scenarios:
        st = run_tier2(sc["request"])
        print(f"\n== {sc['id']} ==")
        for name in sc.get("checks", []):
            fails = CHECKS[name](st, sc["request"])
            status = "PASS" if not fails else "FAIL"
            if fails:
                all_ok = False
            print(f"  {status} {name} {fails or ''}")
        follow_up_checks = ("party_size", "no_duplicate_venues", "opening_hours",
                     "attraction_trap", "route_distance", "route_geometry",
                     "unresolved_reported", "budget_utilisation",
                     "critic_revision_bound",
                     "valid_slot_ids", "real_backend", "llm_call_count",
                     "elapsed_under_60")
        if args.live:
            follow_up_checks = tuple(name for name in follow_up_checks
                                     if name in LIVE_ONLY_CHECKS)
        for name in follow_up_checks:
            fails = CHECKS[name](st, sc["request"])
            status = "PASS" if not fails else "FAIL"
            if fails:
                all_ok = False
            print(f"  {status} {name} {fails or ''}")
        print(f"  elapsed={st.meta['elapsed_s']}s budget={st.budget['status']} "
              f"llm_calls={st.meta.get('llm_calls')}")
    raise SystemExit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
